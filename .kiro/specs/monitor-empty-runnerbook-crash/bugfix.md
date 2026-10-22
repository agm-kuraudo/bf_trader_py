# Bugfix Requirements Document

## Introduction

The Betfair monitor service (`monitor_service.py`, which runs on the Raspberry Pi under Rundeck) crashed repeatedly from the morning of 2026-10-20 with an `IndexError: list index out of range` originating from `MonitorService.update_runner_odds`. Every 15-minute run since ~09:00 that day failed identically, so **no odds were being captured for any target** — a live outage, not a one-off historic incident.

The surface symptom is the blind indexing in `update_runner_odds`: `json_resp["result"][0]["status"]` and `json_resp["result"][0]["runners"][0]["ex"]`. When `listRunnerBook` returns an empty `result` list (`{"result": []}`), `[0]` raises `IndexError`; the method's `try/except` re-raises it as `MonitorServiceException`, which propagates up through `run()` and aborts the **entire** run so no remaining target gets its odds updated.

But the empty `result` is not a benign Betfair state — it is caused by a data-corruption bug in how the request is built. A live, read-only investigation against Betfair (hand-built probes run inside the deployed container) proved that the crashing market **does have odds**, and that the code is sending Betfair a **corrupted market id**. This document therefore covers **two coordinated fixes** under SP-403:

- **Part 1 (primary fix):** correct `RequestBody.populate_template` in `api/request_body.py` so a string substitution value is always rendered as a proper quoted JSON string and can never be reparsed as a number. This is what actually restores odds capture.
- **Part 2 (defense-in-depth):** add an empty-result guard to `update_runner_odds` in `monitor_service.py` so that if `listRunnerBook` ever genuinely returns an empty `result`, the run logs a warning and skips that runner (`continue`) rather than crashing with `IndexError`.

The DNS-lookup failures mentioned elsewhere on the ticket are a separate, independent connectivity issue and were not the cause here.

## Root Cause (confirmed by live investigation)

A live investigation on the Pi and against the Betfair API established a root cause that is **different from and deeper than** earlier notes on this ticket. The earlier conclusion — that the market was a "genuine pre-match `OPEN` market with no runner book yet" — **was wrong** and is replaced by the following.

### The market has odds; the request is corrupted

- **The crashing market has full odds.** For market `1.263913210` (Como v Man Utd), `listMarketBook` returns `status OPEN`, 3 `ACTIVE` runners, ~£542k matched, and real back prices (Como 2.54 / Man Utd 2.82 / Draw 3.90), matching the Betfair UI. The runner/selection ids did not change (60298 / 48351 / 58805, consistent across `bf.target`, `marketCatalogue`, and `listMarketBook`).
- **The code sends a corrupted market id to `listRunnerBook`.** The request body the code actually builds is:

  ```json
  {"params": {"marketId": 1.26391321, "selectionId": 60298, ...}}
  ```

  The market id `"1.263913210"` (a string) has become the **float `1.26391321`** — the trailing zero is dropped and it is no longer a string. Betfair cannot resolve that id, so `listRunnerBook` returns `{"result": []}` (empty, no error).
- **Proven by direct probe.** A hand-built request with `marketId` as the string `"1.263913210"` returns `result_len=1` with full odds; the code's corrupted version returns `result_len=0`.

### Why it happens — `populate_template` (api/request_body.py)

`populate_template` does `json.dumps(template)` → string → `str.replace('"<KEY>"', value)` → `json.loads`. With the default `add_quotes=False` **and a string value**, it replaces the **quoted** placeholder `"<MarketID>"` with the **bare** characters of the value, producing:

```
"marketId": 1.263913210
```

with no surrounding quotes. `json.loads` then parses that unquoted token as a JSON **number** → float `1.26391321`, dropping the trailing zero and the string type.

### Why only some games crash

Market ids ending in `0` (e.g. `...913210`) lose the trailing digit during the float round-trip and become a **different, non-existent** market → empty result → crash. Ids that do not end in `0` happen to survive the round-trip and still resolve — that is why `1.263914615` worked in the same run. The bug is therefore **intermittent by market id**, and it began when the 09:00 target refresh brought in a market id ending in `0`.

### Why `listMarketBook` is immune

`process_targets` passes the market as a **list** (`{"<ListOfMarketIDs>": [market]}`). A list value is `json.dumps`-ed by `populate_template`, so it keeps its quotes/string type inside a JSON array and resolves correctly. Only the **singular** `<MarketID>` string substitution in `listRunnerBook` is corrupted. The existing template test (`tests/template_tests.py`) even calls `listRunnerBook` with `add_quotes=True`, so it never exercised the real (default `add_quotes=False`) production path — which is why the bug was never caught.

### The fix (user-approved) — two parts

**Part 1 — fix `populate_template` (primary, future-proofing).** A string substitution value must always render as a proper quoted JSON string regardless of `add_quotes`, so market ids and any other string substitution can never again be reparsed as a number. This must preserve all existing correct behaviours and not break any caller:

- **List values** (`<ListOfMarketIDs>`, `<list_of_event_ids>`, `<list_of_competition_ids>`, `<list_of_market_types>`) must still render as JSON arrays.
- **Auth** (`CertAuth` `<USERID>`/`<PWD>`, currently called with `add_quotes=True`) must still render as quoted strings.
- **`<RunnerID>`** currently renders as a bare int (`60298`); Betfair accepts `selectionId` as both int and string (verified live), so rendering it as a quoted string is safe and correct.

Full call-site contract that must continue to hold:

| Caller | Value / `add_quotes` | Required rendering |
|---|---|---|
| `CertAuth` `<USERID>`, `<PWD>` | str, `add_quotes=True` | quoted string |
| `listCompetitions` / `listEvents` / `marketCatalogue` / `listMarketBook` | list, `add_quotes=False` | JSON array |
| `listRunnerBook` `<MarketID>` (str), `<RunnerID>` (str) | `add_quotes=False` | `<MarketID>` **must stay a quoted string** (the one broken today); `<RunnerID>` quoted string |

**Part 2 — empty-result guard in `update_runner_odds` (defense-in-depth, the original ticket ask).** If `listRunnerBook` ever genuinely returns an empty `result`, log a warning naming the market and runner and `continue` rather than crashing the whole run with `IndexError`. This is the **secondary** safety net. On its own it would have **masked** the market-id corruption by silently skipping a market that really has odds — so Part 1 is the fix that actually restores odds capture, and Part 2 only stops any future empty result from aborting the run.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `populate_template` substitutes a string value with the default `add_quotes=False` (as `update_runner_odds` does for `<MarketID>`) THEN the system replaces the quoted placeholder `"<MarketID>"` with the bare, unquoted value so the re-parsed JSON holds a number instead of a string

1.2 WHEN the substituted market id string ends in `0` (e.g. `"1.263913210"`) THEN the system re-parses it via `json.loads` as the float `1.26391321`, dropping the trailing zero and producing a market id that does not exist on Betfair

1.3 WHEN `listRunnerBook` is called with that corrupted market id THEN the system receives `{"result": []}` (empty, no error) even though the real market has odds, 3 active runners, and matched volume

1.4 WHEN `update_runner_odds` receives that empty `result` THEN the system accesses `json_resp["result"][0]` and raises `IndexError: list index out of range`

1.5 WHEN that `IndexError` is raised inside `update_runner_odds` THEN the system catches it in the method's `try/except`, re-raises it as `MonitorServiceException`, propagates up through `run()`, and aborts the entire run before any remaining targets in `poll_targets` are processed, emitting no warning that names the specific market and runner

### Expected Behavior (Correct)

2.1 WHEN `populate_template` substitutes a string value regardless of `add_quotes` THEN the system SHALL render it as a proper quoted JSON string so that after `json.loads` the value is still the original string (e.g. `"1.263913210"`) and is never reparsed as a number

2.2 WHEN `update_runner_odds` builds the `listRunnerBook` request for a market id ending in `0` THEN the system SHALL send `marketId` as the exact string `"1.263913210"` and SHALL receive the real market's runner book (non-empty `result` with odds)

2.3 WHEN `populate_template` substitutes a list value (e.g. `<ListOfMarketIDs>`, `<list_of_event_ids>`, `<list_of_competition_ids>`, `<list_of_market_types>`) THEN the system SHALL CONTINUE TO render it as a JSON array

2.4 WHEN `populate_template` substitutes the auth placeholders (`<USERID>`, `<PWD>`) with `add_quotes=True`, or substitutes `<RunnerID>` THEN the system SHALL render each as a quoted JSON string that Betfair accepts

2.5 WHEN `listRunnerBook` returns a response whose `result` list is empty for a market/runner THEN the system SHALL detect the empty `result` before indexing it and SHALL NOT raise an `IndexError`

2.6 WHEN an empty `result` is detected for a runner THEN the system SHALL log a warning that names the affected market (e.g. `target[2]`/`target[0]`) and runner (`individual_runner`) and SHALL skip (`continue` to) the next runner instead of crashing

2.7 WHEN one runner within a target is skipped due to an empty `result` THEN the system SHALL CONTINUE processing the remaining runners for that target and the remaining targets in `poll_targets`, so a single empty response does not abort the run

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `populate_template` renders any existing non-`listRunnerBook` request (`CertAuth`, `listEventTypes`, `listCompetitions`, `listEvents`, `marketCatalogue`, `listMarketBook`, `getAccountFunds`) THEN the system SHALL CONTINUE TO produce the same request body it produces today (lists as JSON arrays, auth values as quoted strings)

3.2 WHEN `process_targets` calls `listMarketBook` with the market wrapped in a list THEN the system SHALL CONTINUE TO resolve the market correctly and mark genuinely dead markets `EXPIRED` exactly as before

3.3 WHEN `listRunnerBook` returns a non-empty `result` with a valid runner entry THEN the system SHALL CONTINUE TO read `status` and `ex` odds, write the odds row into `bf.market_table`, and log the per-runner update exactly as before

3.4 WHEN all runners for a target return valid odds THEN the system SHALL CONTINUE TO compute `next_update_time_seconds` from the update-frequency tiers and update `last_updated`/`update_frequency` on `bf.target` for that market

3.5 WHEN `update_runner_odds` encounters a genuine, non-empty-result error (e.g. an API/network/DB failure or a malformed response that is not simply an empty `result`) THEN the system SHALL CONTINUE TO raise `MonitorServiceException` and record the failure to `bf.log_file` as it does today

3.6 WHEN the run loop and single-instance advisory lock behaviour in `run()` execute THEN the system SHALL CONTINUE TO function unchanged (lock acquire/release, stale-target cleanup, adaptive poll/sleep/exit decisions)

## Bug Condition

**F**: `populate_template` + `update_runner_odds` as they exist before the fix.
**F'**: both functions after the fix (Part 1: string substitution always quoted; Part 2: empty-result guard).

The bug has two linked triggers: the request-build corruption (Part 1) and the unguarded empty result it produces (Part 2). `isBugCondition` covers both.

```pascal
FUNCTION isBugCondition(X)
  INPUT: X = (template_name, replace_pairs, add_quotes, json_resp)
         // the populate_template call for one listRunnerBook request and
         // the parsed response it ultimately yields for one (market, runner)
  OUTPUT: boolean

  // Part 1 trigger: a STRING substitution value rendered unquoted, so json.loads
  // reparses it as a number and corrupts it (e.g. a market id ending in '0').
  stringCorruption ←
      EXISTS (key, value) IN X.replace_pairs SUCH THAT
          isString(value)
          AND renderedUnquoted(value, X.add_quotes)      // current add_quotes=False path
          AND reparsedValueDiffersFrom(value)            // "1.263913210" -> 1.26391321

  // Part 2 trigger: listRunnerBook yields an empty result that is then indexed blindly.
  emptyResult ← (X.json_resp.result IS NOT NULL) AND (length(X.json_resp.result) = 0)

  RETURN stringCorruption OR emptyResult
END FUNCTION
```

```pascal
// Property: Fix Checking - Part 1 (restore odds capture)
FOR ALL X WHERE stringCorruption(X) DO
  body ← populate_template'(X.template_name, X.replace_pairs, X.add_quotes)
  ASSERT body.params.marketId = X.replace_pairs["<MarketID>"]     // exact original string, e.g. "1.263913210"
      AND typeOf(body.params.marketId) = String
      AND listRunnerBook(body).result IS NON-EMPTY                 // real market resolves, odds returned
END FOR
```

```pascal
// Property: Fix Checking - Part 2 (empty result never aborts the run)
FOR ALL X WHERE emptyResult(X) DO
  result ← update_runner_odds'( ...X... )
  ASSERT no_exception_raised(result)
      AND warning_logged_naming(market, runner)
      AND runner_was_skipped(continue_to_next)
      AND remaining_runners_and_targets_still_processed()
END FOR
```

```pascal
// Property: Preservation Checking - all non-buggy inputs behave as before
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
  // list substitutions           -> still rendered as JSON arrays
  // auth (add_quotes=True)        -> still quoted strings
  // non-empty valid runner book   -> odds read, written to bf.market_table, last_updated updated
  // genuine error (not empty)      -> still raises MonitorServiceException
END FOR
```
