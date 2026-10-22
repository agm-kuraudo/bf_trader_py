# monitor-empty-runnerbook-crash Bugfix Design

## Overview

The Betfair monitor service crashed on every 15-minute run from ~09:00 on 2026-10-20 with `IndexError: list index out of range` in `MonitorService.update_runner_odds`, halting all odds capture (SP-403). The surface symptom is blind indexing of an empty `listRunnerBook` result (`json_resp["result"][0]`), but the empty result is not a benign Betfair state — it is produced by a data-corruption bug in how the request body is built.

`RequestBody.populate_template` (`api/request_body.py`) renders a request by `json.dumps(template)` → `str.replace('"<KEY>"', value)` → `json.loads`. On its default `add_quotes=False` branch, a **string** substitution value is spliced in **bare** (unquoted), so `"marketId": "<MarketID>"` becomes `"marketId": 1.263913210` and `json.loads` reparses it as the **float** `1.26391321` — silently dropping the trailing zero. `update_runner_odds` calls `populate_template` with the default `add_quotes=False`, so a corrupted (non-existent) market id reaches `listRunnerBook`, which returns `{"result": []}`, and the blind `result[0]` index raises `IndexError` that aborts the whole run.

The fix has two coordinated parts:

- **Part 1 (primary):** correct `populate_template` so that a string value is always rendered as a proper quoted JSON string — regardless of `add_quotes` — so a market id (or any other string substitution) can never again be reparsed as a number. This is what actually restores odds capture.
- **Part 2 (defense-in-depth):** add an empty-result guard to `update_runner_odds` so that if `listRunnerBook` ever genuinely returns an empty `result`, the run logs a warning naming the market and runner and `continue`s to the next runner instead of crashing.

Part 2 alone would have **masked** the Part 1 corruption (it would silently skip a market that really has odds), so Part 1 is the fix that restores capture and Part 2 only prevents any future empty result from aborting a run.

## Glossary

- **Bug_Condition (C)**: Either (a) `populate_template` renders a string substitution value unquoted so `json.loads` reparses it as a number (corrupting it, e.g. a market id ending in `0`), or (b) `listRunnerBook` yields an empty `result` list that `update_runner_odds` indexes blindly.
- **Property (P)**: After the fix, a string substitution value survives as the exact original string in the rendered body, and an empty `result` is detected and skipped (warned + `continue`) instead of raising `IndexError`.
- **Preservation**: List substitutions still render as JSON arrays; auth values (`add_quotes=True`) still render as quoted strings; a non-empty valid runner book is still read, written to `bf.market_table`, and its target's `last_updated` still updated; genuine (non-empty-result) errors still raise `MonitorServiceException`.
- **populate_template**: The method in `api/request_body.py` that substitutes `<KEY>` placeholders in a JSON template and returns the parsed dict.
- **update_runner_odds**: The method in `monitor_service.py` that, per open target and per runner, calls `listRunnerBook`, reads `status`/`ex` odds, writes the odds row, and updates the target's update-frequency tier.
- **add_quotes**: The optional `populate_template` flag (default `False`) that currently controls whether a value is wrapped in quotes. After the fix it becomes redundant for correctness (retained only for backward-compatible call signatures).
- **MarketID corruption**: A market id string ending in `0` (e.g. `"1.263913210"`) that, when rendered unquoted, round-trips through `json.loads` as the float `1.26391321` → a different, non-existent market.

## Bug Details

### Bug Condition

The bug manifests on two linked triggers. First, `populate_template` substitutes a **string** value on the default `add_quotes=False` path by splicing the bare characters of the value into the JSON string in place of the **quoted** placeholder, so the re-parsed JSON holds a **number** instead of a string — and for a market id ending in `0` the reparsed float drops the trailing zero, yielding a non-existent market. Second, the resulting `listRunnerBook` call returns `{"result": []}`, which `update_runner_odds` indexes blindly (`result[0]`), raising `IndexError` that is re-raised as `MonitorServiceException` and aborts the entire run.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input = (template_name, replace_pairs, add_quotes, json_resp)
         // one listRunnerBook populate_template call and the response it yields
         // for one (market, runner)
  OUTPUT: boolean

  // Part 1 trigger: a STRING substitution value rendered unquoted, so json.loads
  // reparses it as a number and corrupts it (e.g. a market id ending in '0').
  stringCorruption :=
      EXISTS (key, value) IN input.replace_pairs SUCH THAT
          isString(value)
          AND renderedUnquoted(value, input.add_quotes)   // current add_quotes=False path
          AND reparsedValueDiffersFrom(value)              // "1.263913210" -> 1.26391321

  // Part 2 trigger: listRunnerBook yields an empty result that is indexed blindly.
  emptyResult := (input.json_resp.result IS NOT NULL)
                 AND (length(input.json_resp.result) = 0)

  RETURN stringCorruption OR emptyResult
END FUNCTION
```

### Examples

- **Market id ending in `0` (the live outage):** `populate_template("listRunnerBook", {"<MarketID>": "1.263913210", "<RunnerID>": "60298"})` with the default `add_quotes=False`. Expected: body `params.marketId == "1.263913210"` (string). Actual: `params.marketId == 1.26391321` (float, trailing zero dropped) → `listRunnerBook` returns `{"result": []}` → `IndexError`.
- **Market id not ending in `0` (survives by luck):** `"1.263914615"` rendered unquoted reparses to a float that still happens to resolve, so the same run succeeded for that market — the bug is intermittent by market id.
- **`<RunnerID>` substitution:** `"60298"` currently reparses to the bare int `60298`; Betfair accepts `selectionId` as both int and string (verified live), so no crash — but it is the same unquoted-string defect.
- **Empty result with a real market (edge case for Part 2):** even a correctly-rendered request could, in principle, return `{"result": []}` for a genuinely bookless market. Expected after fix: warn naming market + runner and `continue`, never `IndexError`.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- List substitutions (`<ListOfMarketIDs>`, `<list_of_event_ids>`, `<list_of_competition_ids>`, `<list_of_market_types>`) must still render as JSON arrays, so `listCompetitions` / `listEvents` / `marketCatalogue` / `listMarketBook` resolve exactly as before.
- Auth placeholders (`CertAuth` `<USERID>` / `<PWD>`, called with `add_quotes=True`) must still render as quoted JSON strings.
- A non-empty, valid `listRunnerBook` result must still have its `status` and `ex` odds read, the odds row written to `bf.market_table`, and the per-runner update logged.
- When all runners for a target return valid odds, the update-frequency tier (`next_update_time_seconds`) and the `last_updated` / `update_frequency` update on `bf.target` must be computed and written exactly as before.
- A genuine (non-empty-result) error must still raise `MonitorServiceException` and record the failure to `bf.log_file`.
- `process_targets`' `listMarketBook` path (market wrapped in a list) must still mark genuinely dead markets `EXPIRED`.

**Scope:**
All inputs that do NOT satisfy the bug condition must be completely unaffected by this fix. This includes:
- Any list-valued substitution (rendered as a JSON array).
- Auth substitutions with `add_quotes=True` (rendered as quoted strings — unchanged observable output).
- Any `listRunnerBook` response whose `result` is non-empty and well-formed.
- The `run()` loop, single-instance advisory lock, stale-target cleanup, and adaptive poll/sleep/exit behaviour.

**Note:** The expected *correct* behavior for buggy inputs is defined in the Correctness Properties section below (Properties 1 and 2). This section lists only what must NOT change.

## Hypothesized Root Cause

The root cause is **confirmed** by live, read-only investigation on the Pi and against the Betfair API (hand-built probes inside the deployed container): the crashing market `1.263913210` has full odds, and the code sends `listRunnerBook` a corrupted float market id. The mechanics break down as:

1. **Unquoted string substitution in `populate_template` (primary).** On the default `add_quotes=False` branch, a string `value` is spliced in as `template_str.replace(f'"{key}"', value)` — replacing the **quoted** placeholder `"<MarketID>"` with the **bare** value, producing `"marketId": 1.263913210` with no surrounding quotes. `json.loads` then parses that token as a JSON **number**.
   - A market id ending in `0` loses its trailing digit in the float round-trip (`"1.263913210"` → `1.26391321`), becoming a different, non-existent market.
   - Market ids not ending in `0` happen to survive the round-trip, which is why the bug is intermittent by market id.

2. **`add_quotes` only patches the symptom at some call sites.** The auth caller passes `add_quotes=True`, which wraps string values in quotes, so auth never corrupted. The production `listRunnerBook` caller (`update_runner_odds`) uses the default `add_quotes=False`, so its string market id is corrupted. The correctness of a string substitution should not depend on a caller remembering to pass `add_quotes=True`.

3. **Blind indexing in `update_runner_odds` (amplifier).** The corrupted market id yields `{"result": []}`; `update_runner_odds` accesses `json_resp["result"][0]` with no emptiness check, raising `IndexError`. The method's `try/except` re-raises it as `MonitorServiceException`, which propagates through `run()` and aborts the entire run before remaining targets are processed — turning one bad market id into a total capture outage, with no warning naming the specific market/runner.

4. **Test blind spot.** The existing `tests/template_tests.py` calls `listRunnerBook` with `add_quotes=True`, so it never exercised the real (`add_quotes=False`) production path — which is why the corruption was never caught.

## Correctness Properties

Property 1: Bug Condition - String substitutions round-trip as exact strings

_For any_ `populate_template` call whose `replace_pairs` contains a string value (in particular the `listRunnerBook` `<MarketID>` passed by `update_runner_odds` with the default `add_quotes=False`), the fixed `populate_template` SHALL render that value as a proper quoted JSON string so that, after `json.loads`, the value is still the exact original string (e.g. `params.marketId == "1.263913210"`, a `str`, trailing zero intact) and is never reparsed as a number — so the subsequent `listRunnerBook` call resolves the real market and returns a non-empty `result`.

**Validates: Requirements 2.1, 2.2**

Property 2: Fix - Empty result is skipped, never aborts the run

_For any_ `listRunnerBook` response whose `result` list is empty (`isBugCondition`'s `emptyResult` branch), the fixed `update_runner_odds` SHALL detect the empty `result` before indexing it, log a warning naming the affected market (`target[2]`) and runner (`individual_runner`), `continue` to the next runner, and SHALL NOT raise `IndexError` — so the remaining runners for that target and the remaining targets in the run are still processed.

**Validates: Requirements 2.5, 2.6, 2.7**

Property 3: Preservation - List and auth callers unchanged

_For any_ input where the bug condition does NOT hold — a list-valued substitution (`<ListOfMarketIDs>`, `<list_of_event_ids>`, `<list_of_competition_ids>`, `<list_of_market_types>`) or an auth substitution with `add_quotes=True` (`<USERID>` / `<PWD>`) — the fixed `populate_template` SHALL produce the same request body as the original function, preserving JSON-array rendering for lists and quoted-string rendering for auth values.

**Validates: Requirements 3.1, 3.2**

Property 4: Preservation - Valid odds path and genuine-error path unchanged

_For any_ input where the bug condition does NOT hold — a non-empty, well-formed `listRunnerBook` result, or a genuine (non-empty-result) API/network/DB failure — the fixed `update_runner_odds` SHALL produce the same result as the original function: reading `status`/`ex`, writing the odds row to `bf.market_table`, updating `last_updated`/`update_frequency` on `bf.target` per the update-frequency tiers for a valid result; and raising `MonitorServiceException` and recording the failure to `bf.log_file` for a genuine error.

**Validates: Requirements 3.3, 3.4, 3.5**

## Fix Implementation

### Part 1 — fix `populate_template` (primary)

**File**: `api/request_body.py`

**Function**: `RequestBody.populate_template`

**Current loop (buggy):**
```python
for key, value in replace_pairs.items():
    if isinstance(value, list):
        value = json.dumps(value)
    if add_quotes:
        template_str = template_str.replace(
            f'"{key}"', json.dumps(value) if not isinstance(value, str) else f'"{value}"'
        )
    else:
        template_str = template_str.replace(
            f'"{key}"', value if isinstance(value, str) else json.dumps(value)
        )
```

**Corrected contract (what the new loop must guarantee, independent of `add_quotes`):**
- **String value** → always inserted as a **quoted JSON string**, so `"1.263913210"` stays the string `"1.263913210"` (never a number). This is the actual behaviour change.
- **List value** → JSON array (unchanged).
- **Other scalars** (int / float / bool) → `json.dumps`-ed as today (unchanged).

**Recommended implementation — `json.dumps` uniformly for the replacement token:**
```python
for key, value in replace_pairs.items():
    # json.dumps produces a correctly-quoted+escaped JSON token for every type:
    #   str  -> "..."  (quoted JSON string; the fix for <MarketID>/<RunnerID>)
    #   list -> [...]  (JSON array; unchanged behaviour)
    #   int/float/bool -> bare number/bool (unchanged behaviour)
    template_str = template_str.replace(f'"{key}"', json.dumps(value))
```

`json.dumps(value)` is the single clean rule that satisfies the whole contract:
- `json.dumps("1.263913210")` → `"1.263913210"` (quoted) — the market id can never again reparse as a float.
- `json.dumps(["1.238784666"])` → `["1.238784666"]` — lists stay JSON arrays, so the explicit `isinstance(value, list)` pre-dump is no longer needed.
- `json.dumps(60298)` / `json.dumps(True)` → `60298` / `true` — other scalars unchanged.

**On `add_quotes`:** this uniform rule makes `add_quotes` **redundant for correctness** — both the old `add_quotes=True` branch (auth) and the old `add_quotes=False` branch now produce identical, correct output for every value type. To avoid breaking existing callers' signatures (`CertAuth` passes `add_quotes=True`; the existing `listRunnerBook` test passes `add_quotes=True`), **keep the `add_quotes=False` parameter in the signature** but stop branching on it (or treat it as a no-op kept only for backward compatibility). Callers that still pass `add_quotes=True` keep working and produce the same bytes as before.

**Latent correctness improvement:** `json.dumps` also correctly **escapes special characters** in string values (quotes, backslashes, control characters). The old `f'"{value}"'` path did not, so any auth value containing a `"` or `\` would previously have produced malformed JSON. Switching to `json.dumps` fixes that latent bug for auth and any future string substitution for free.

**Call-site contract preserved (from bugfix.md):**

| Caller | Value / `add_quotes` | Rendering after fix |
|---|---|---|
| `CertAuth` `<USERID>`, `<PWD>` | str, `add_quotes=True` | quoted string (unchanged bytes) |
| `listCompetitions` / `listEvents` / `marketCatalogue` / `listMarketBook` | list, `add_quotes=False` | JSON array (unchanged) |
| `listRunnerBook` `<MarketID>` (str), `<RunnerID>` (str) | `add_quotes=False` | `<MarketID>` stays a **quoted string** (the bug fixed); `<RunnerID>` now a **quoted string** instead of a bare int |

`<RunnerID>` changing from a bare int (`60298`) to a quoted string (`"60298"`) is the only observable change to a working caller. This is **safe**: a live probe confirmed Betfair accepts `selectionId` as a string.

### Part 2 — empty-result guard in `update_runner_odds` (defense-in-depth)

**File**: `monitor_service.py`

**Function**: `MonitorService.update_runner_odds`

**Current (buggy) inner body, per runner:**
```python
json_resp = resp.json()
status = json_resp["result"][0]["status"]          # <-- IndexError when result == []
...
odds = json_resp["result"][0]["runners"][0]["ex"]
```

**Change:** after `json_resp = resp.json()`, read the `result` once and guard it before indexing:
```python
json_resp = resp.json()
result = json_resp.get("result")
if not result:
    Log.log_warning(
        f"listRunnerBook returned an empty result for market {target[2]} "
        f"runner {individual_runner}; skipping this runner"
    )
    continue
status = result[0]["status"]
...
odds = result[0]["runners"][0]["ex"]
```

**Constraints the change must respect:**
1. The guard runs **inside** the per-runner `for individual_runner in target[3]` loop, so `continue` skips only the current runner and the loop proceeds to the next runner / next target.
2. The surrounding `try/except` → `MonitorServiceException` genuine-error path is **preserved** — an empty `result` is handled by `continue` (not an exception), while any real API/network/DB error still falls through to the existing `except` and re-raises as `MonitorServiceException` with the failure recorded to `bf.log_file`.
3. The valid-odds write to `bf.market_table` and the tier-based `last_updated` / `update_frequency` update on `bf.target` are reached unchanged for every runner that returns a non-empty result.

**Design note (why Part 2 is only defense-in-depth):** Part 2 on its own would have **masked** the Part 1 corruption — it would have silently skipped market `1.263913210` (which really has odds) as though it were legitimately bookless, so odds capture would still have been lost for that market with no crash to flag it. Part 1 is therefore the fix that restores capture; Part 2 only stops any *future* genuinely-empty result from aborting the whole run.

## Testing Strategy

### Validation Approach

The strategy follows the two-phase bug-condition methodology. Phase one surfaces counterexamples that demonstrate both triggers on the **unfixed** code — the market-id corruption (Part 1) and the `IndexError` crash it causes (Part 2). Phase two verifies the fix restores correct behaviour and preserves every non-buggy path across all callers. Tests live in the existing suites following repo conventions:

- **`populate_template` unit + regression tests** extend `tests/template_tests.py`.
- **Monitor guard tests** go in `tests/unit_tests_monitor_resilience.py` (`MonitorService()` with `svc.db_connection = MagicMock()`, patch `Call.call` / `BFDriver`, `Log.LOG_FILE = False`).
- **Property-based tests** use Hypothesis in a `tests/test_property_*` module (e.g. `tests/test_property_template_substitution.py`).

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix, confirming the root-cause analysis. If a test unexpectedly passes on unfixed code, re-hypothesise.

**Test Plan**: Call `populate_template` on the **real production path** (`add_quotes=False`, exactly as `update_runner_odds` calls it) and inspect the rendered body; separately drive `update_runner_odds` with a mocked `listRunnerBook` response of `{"result": []}` and assert the crash. Run both against the UNFIXED code to observe the failures.

**Test Cases**:
1. **MarketID corruption (trailing zero)**: render `listRunnerBook` with `{"<MarketID>": "1.263913210", "<RunnerID>": "60298"}` and `add_quotes=False`; assert `params.marketId == "1.263913210"` as an exact string. **Will fail on unfixed code** — it renders the float `1.26391321`. This is the critical regression the old test missed (the old test wrongly used `add_quotes=True`).
2. **RunnerID type**: assert `params.selectionId` is the exact string `"60298"` on the `add_quotes=False` path. Will differ on unfixed code (bare int `60298`).
3. **Empty-result crash**: patch `Call.call` so `resp.json()` returns `{"result": []}`; call `update_runner_odds` with one target/one runner and assert it raises `MonitorServiceException` (from the underlying `IndexError`). **Will fail-as-crash on unfixed code** — this is the behaviour Part 2 removes.

**Expected Counterexamples**:
- `params.marketId` reparsed as float `1.26391321` instead of string `"1.263913210"`.
- `update_runner_odds` raising `MonitorServiceException` on an empty `result`.
- Possible causes (all confirmed): unquoted string substitution in `populate_template`; blind `result[0]` indexing in `update_runner_odds`.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE stringCorruption(input) DO               // Part 1
  body := populate_template_fixed(input.template_name, input.replace_pairs, input.add_quotes)
  ASSERT body.params.marketId = input.replace_pairs["<MarketID>"]   // exact original string
     AND typeOf(body.params.marketId) = String
END FOR

FOR ALL input WHERE emptyResult(input) DO                    // Part 2
  result := update_runner_odds_fixed( ...input... )
  ASSERT no_exception_raised(result)
     AND warning_logged_naming(market, runner)
     AND runner_was_skipped(continue_to_next)
     AND remaining_runners_and_targets_still_processed()
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed functions produce the same result as the original functions.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT populate_template_original(input)   = populate_template_fixed(input)    // lists + auth
  ASSERT update_runner_odds_original(input)  = update_runner_odds_fixed(input)   // valid odds + genuine error
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because it generates many inputs across the domain, catches edge cases manual unit tests miss, and gives strong guarantees that behaviour is unchanged for all non-buggy inputs.

**Test Plan**: Observe behaviour on the UNFIXED code for list substitutions, auth substitutions, a non-empty valid runner book, and a genuine error, then write tests that assert the fixed code produces the same observable output.

**Test Cases**:
1. **List substitution preservation**: `listMarketBook` / `listEvents` / `listCompetitions` / `marketCatalogue` still render their list placeholders as JSON arrays (identical body to unfixed code).
2. **Auth preservation**: `CertAuth` with `add_quotes=True` still renders `<USERID>` / `<PWD>` as quoted strings (identical body).
3. **Valid-odds preservation**: a non-empty `listRunnerBook` result still has `status` / `ex` read, the odds row written to `bf.market_table`, and `last_updated` / `update_frequency` updated per the tiers.
4. **Genuine-error preservation**: a mocked API/DB failure (not an empty `result`) still raises `MonitorServiceException` and records to `bf.log_file`.

### Unit Tests

- `populate_template` renders `<MarketID>` as the exact string `"1.263913210"` on the `add_quotes=False` production path (regression for the trailing-zero corruption; extends `tests/template_tests.py`).
- `populate_template` renders `<RunnerID>` as the string `"60298"` on the `add_quotes=False` path.
- `populate_template` renders list placeholders as JSON arrays and auth values as quoted strings (preservation), and correctly escapes special characters in a string value.
- `update_runner_odds` skips a runner on `{"result": []}` with a warning naming market + runner and does not raise (guard, `tests/unit_tests_monitor_resilience.py`).
- `update_runner_odds` still reads odds, writes `bf.market_table`, and updates `last_updated` for a non-empty result; still raises `MonitorServiceException` on a genuine error.

### Property-Based Tests

- Generate market ids with trailing zeros and arbitrary decimal strings; assert the rendered `listRunnerBook` `marketId` round-trips to the **exact** input string on the `add_quotes=False` path (Property 1).
- Generate arbitrary string substitution values (including special characters); assert the rendered value always equals the input string after `json.loads` (Property 1, escaping).
- Generate list-valued substitutions; assert they always render as JSON arrays regardless of `add_quotes` (Property 3 preservation).
- Generate a mix of empty and non-empty `listRunnerBook` responses; assert `update_runner_odds` skips-and-continues on empty and processes on non-empty, never raising on an empty result (Properties 2 and 4).

### Integration Tests

- Full `update_runner_odds` flow over a target with multiple runners where one runner's `listRunnerBook` returns an empty `result`: the run processes the other runners and the remaining targets, logging one warning (no run abort).
- End-to-end request build + (mocked) `listRunnerBook` round-trip for a market id ending in `0`: the rendered body carries the exact string market id and resolves to a non-empty result.
- Context preservation: `process_targets`' `listMarketBook` list path still marks genuinely dead markets `EXPIRED` unchanged.
