# Implementation Plan

## Overview

This plan delivers the two-part SP-403 fix using the two-phase bug-condition methodology. **Part 1 (primary)** corrects `RequestBody.populate_template` (`api/request_body.py`) so a string substitution value always renders as a quoted JSON string and can never be reparsed as a number — restoring odds capture for market ids ending in `0`. **Part 2 (defense-in-depth)** adds an empty-result guard to `MonitorService.update_runner_odds` (`monitor_service.py`) so a genuinely empty `listRunnerBook` result warns-and-continues instead of crashing the whole run.

The code changes are small (a one-line replacement loop body plus a 3-line guard), but Part 1 touches a shared helper used by every request caller, so preservation across all callers (lists as JSON arrays, auth as quoted strings) is the main risk the tests must cover.

Phase one writes tests that FAIL on the unfixed code — surfacing both the market-id corruption (Part 1) and the `IndexError` crash (Part 2) — and captures the baseline behaviour to preserve. Phase two applies both fixes and verifies the fix and preservation properties hold.

## Task Dependency Graph

- Task 1 (bug-condition exploration tests for BOTH parts) and Task 2 (preservation tests captured on the unfixed code) are independent and both run against the UNFIXED code. They must come before any fix.
- Task 3 (the two-part fix) depends on Tasks 1 and 2 being written and run first.
  - Task 3.1 (Part 1 — `populate_template`) and Task 3.2 (Part 2 — guard) depend on Tasks 1 and 2.
  - Task 3.3 (verify Property 1 + Property 2 fix tests pass) depends on 3.1 and 3.2.
  - Task 3.4 (verify Property 3 + Property 4 preservation tests still pass) depends on 3.1 and 3.2.
- Task 4 (checkpoint) depends on 3.3 and 3.4.

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1", "2"], "dependsOn": [] },
    { "wave": 2, "tasks": ["3.1", "3.2"], "dependsOn": ["1", "2"] },
    { "wave": 3, "tasks": ["3.3", "3.4"], "dependsOn": ["3.1", "3.2"] },
    { "wave": 4, "tasks": ["4"], "dependsOn": ["3.3", "3.4"] }
  ]
}
```

## Tasks

- [ ] 1. Write bug condition exploration tests for BOTH parts (BEFORE implementing the fix)
  - **Property 1: Bug Condition** - String substitution corruption and the empty-result crash it causes
  - **CRITICAL**: These tests MUST FAIL on unfixed code - failure confirms both triggers of the bug exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: These tests encode the expected behavior - they will validate the fix when they pass after implementation
  - **GOAL**: Surface counterexamples that demonstrate (a) the market-id corruption in `populate_template` and (b) the `IndexError` crash it ultimately produces in `update_runner_odds`
  - **Scoped PBT Approach**: Both triggers are deterministic, so scope the properties to the concrete failing shapes - a `<MarketID>` string ending in `0` (e.g. `"1.263913210"`) rendered on the `add_quotes=False` path, and a `listRunnerBook` response whose `result` is empty/falsy (`{"result": []}`). A Hypothesis-driven variant may vary the trailing-zero decimal strings while holding the `add_quotes=False` path fixed (deferred to Task 2's property module if preferred)
  - **Part 1 test (extends `tests/template_tests.py`)**: call `populate_template("listRunnerBook", {"<MarketID>": "1.263913210", "<RunnerID>": "60298"}, add_quotes=False)` via the REAL production path - `add_quotes=False`, exactly as `update_runner_odds` calls it (the old test wrongly used `add_quotes=True` and so missed the bug)
    - Assert `body["params"]["marketId"] == "1.263913210"` AND `isinstance(body["params"]["marketId"], str)` - the exact string with the trailing zero intact
    - Assert `body["params"]["selectionId"] == "60298"` AND it is a `str`
    - **EXPECTED OUTCOME on unfixed code**: FAILS - `marketId` reparses to the float `1.26391321` (trailing zero dropped) and `selectionId` is the bare int `60298`
  - **Part 2 test (`tests/unit_tests_monitor_resilience.py`)**: follow existing monitor conventions - construct `MonitorService()`, set `svc.db_connection = MagicMock()`, patch `api.call.Call.call` / `monitor_service.BFDriver` so `resp.json()` returns `{"result": []}`, set `Log.LOG_FILE = False`
    - Call `update_runner_odds` with one target / one runner and assert it raises `MonitorServiceException` (wrapping the underlying `IndexError: list index out of range` from blind `json_resp["result"][0]`)
    - The post-fix assertions this test will encode: `update_runner_odds` does NOT raise, a warning naming market (`target[2]`) + runner (`individual_runner`) is logged, and remaining runners/targets still process
  - Run both tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (Part 1 renders the corrupted float; Part 2 raises `MonitorServiceException` - both correct, they prove the bug exists)
  - Document counterexamples found: `marketId` reparsed as float `1.26391321` instead of string `"1.263913210"`; `selectionId` bare int `60298`; `update_runner_odds` raising `MonitorServiceException` on an empty `result`
  - Mark task complete when the tests are written, run, and the failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [ ] 2. Write preservation tests captured on the unfixed code (BEFORE implementing the fix)
  - **Property 2: Preservation** - All non-buggy caller paths and the valid-odds / genuine-error paths unchanged
  - **IMPORTANT**: Follow observation-first methodology - observe behaviour on the UNFIXED code first, then encode it so the tests pass today and must keep passing after the fix
  - **`populate_template` preservation (extends `tests/template_tests.py`)** - observe and assert on the UNFIXED code:
    - List substitutions still render as JSON arrays: `listMarketBook` (`<ListOfMarketIDs>`), `listEvents` (`<list_of_event_ids>` / `<list_of_competition_ids>` / `<list_of_market_types>`), `listCompetitions`, `marketCatalogue` - assert the rendered placeholder is a Python `list` after `json.loads` (Preservation Req 3.1, 3.2)
    - Auth with `add_quotes=True` still renders `<USERID>` / `<PWD>` as quoted strings - assert they come back as `str` (Preservation Req 3.1)
    - Special-character escaping: a string value containing a `"` or `\` round-trips to the exact input string after `json.loads` (captures the latent escaping behaviour the fix also corrects)
  - **`update_runner_odds` preservation (`tests/unit_tests_monitor_resilience.py` / `tests/unit_tests_monitor_service.py`)** - `MonitorService()` with `svc.db_connection = MagicMock()`, patch `Call.call` / `BFDriver`, `Log.LOG_FILE = False`, assert on `db_write` / `db_write_log` / `Log.log_warning`:
    - Valid non-empty `{"result": [{"status": "OPEN", "runners": [{"ex": {...}}]}]}` response reads `status`/`ex`, writes an odds row to `bf.market_table`, and logs the per-runner update (Preservation Req 3.3)
    - After all runners for a target succeed, `last_updated` / `update_frequency` on `bf.target` are updated via the update-frequency tier logic (Preservation Req 3.4)
    - A genuine error (`Call.call` raises an API/network failure, NOT an empty `result`) still raises `MonitorServiceException` and records the failure to `bf.log_file` (Preservation Req 3.5)
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms the baseline behaviour to preserve across every caller of the shared helper)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 3. Two-part fix for the market-id corruption and empty-result crash

  - [ ] 3.1 Implement Part 1 - fix `populate_template` (primary)
    - In `RequestBody.populate_template` (`api/request_body.py`), replace the `add_quotes` branching loop body with a single uniform rule: `template_str = template_str.replace(f'"{key}"', json.dumps(value))` for every `(key, value)` in `replace_pairs`
    - `json.dumps` produces the correct JSON token for each type: `str` -> quoted string (the fix for `<MarketID>` / `<RunnerID>`), `list` -> JSON array (unchanged), `int`/`float`/`bool` -> bare scalar (unchanged); it also correctly escapes special characters in string values (latent escaping fix)
    - Drop the now-redundant `isinstance(value, list)` pre-`json.dumps` step (the uniform `json.dumps(value)` already arrays a list)
    - **Keep the `add_quotes=False` parameter in the signature** for backward compatibility with existing callers (`CertAuth` passes `add_quotes=True`; the existing `listRunnerBook` test passes `add_quotes=True`) but stop branching on it - it becomes a no-op for correctness and both old branches now produce identical correct output
    - Leave the surrounding `try/except` -> `RequestBodyException` wrapper unchanged
    - _Bug_Condition: isBugCondition(input).stringCorruption - a string value rendered unquoted on the add_quotes=False path, reparsed by json.loads as a number (from design)_
    - _Expected_Behavior: a string substitution value always renders as a quoted JSON string so after json.loads it is still the exact original string, never a number (expectedBehavior / Property 1 from design)_
    - _Preservation: list substitutions still render as JSON arrays; auth (add_quotes=True) still renders quoted strings (Property 3 from design)_
    - _Requirements: 2.1, 2.2, 2.4, 3.1, 3.2_

  - [ ] 3.2 Implement Part 2 - empty-result guard in `update_runner_odds` (defense-in-depth)
    - In `MonitorService.update_runner_odds` (`monitor_service.py`), inside the inner `for individual_runner in target[3]` loop, after `json_resp = resp.json()`, read `result = json_resp.get("result")` instead of blindly indexing `json_resp["result"][0]`
    - Add `if not result:` guard immediately after: `Log.log_warning(...)` naming market `target[2]` and runner `individual_runner`, then `continue` to the next runner
    - On the valid path, index the validated `result[0]` for the `status` and `ex` reads (replace `json_resp["result"][0]` with `result[0]`); leave `status`/`ex` reads, `odds_str`, the `bf.market_table` INSERT, and the per-runner log unchanged
    - Leave the post-loop tier-based `next_update_time_seconds` / `last_updated` / `update_frequency` update on `bf.target` untouched
    - Preserve the existing `try/except` -> `db_write_log` + `raise MonitorServiceException(...)` genuine-error path (the empty-result case now `continue`s before any indexing, so it never enters the `except`)
    - _Bug_Condition: isBugCondition(input).emptyResult - json_resp has key "result" and result is empty/falsy, indexed blindly as result[0] (from design)_
    - _Expected_Behavior: detect the empty result before indexing, log a warning naming market + runner, continue to the next runner, no IndexError/MonitorServiceException (expectedBehavior / Property 2 from design)_
    - _Preservation: valid-odds read/write to bf.market_table, tier-based last_updated update, and genuine-error MonitorServiceException raising all unchanged (Property 4 from design)_
    - _Requirements: 2.5, 2.6, 2.7, 3.3, 3.4, 3.5, 3.6_

  - [ ] 3.3 Verify bug condition exploration tests now pass
    - **Property 1: Expected Behavior** - String substitutions round-trip as exact strings; empty result is skipped
    - **IMPORTANT**: Re-run the SAME tests from Task 1 - do NOT write new tests
    - The tests from Task 1 encode the expected behavior; when they pass they confirm the market id renders as the exact string `"1.263913210"` and the empty-result runner is skipped with a warning
    - Run the Part 1 and Part 2 exploration tests from Task 1
    - **EXPECTED OUTCOME**: Tests PASS (Part 1 - `marketId == "1.263913210"` as a `str`, `selectionId == "60298"`; Part 2 - no exception, warning logged naming market + runner, remaining runners/targets still processed)
    - _Requirements: 2.1, 2.2, 2.5, 2.6, 2.7_

  - [ ] 3.4 Verify preservation tests still pass
    - **Property 2: Preservation** - List/auth callers and valid-odds / genuine-error paths unchanged
    - **IMPORTANT**: Re-run the SAME tests from Task 2 - do NOT write new tests
    - Run the preservation tests from Task 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions - lists still render as JSON arrays, auth values still quoted strings, valid odds still read/written with the tier update, genuine errors still raise `MonitorServiceException`)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run the end-of-task verification gate per the repo testing policy, once: `ruff check .` then `ruff format --check .`, then the full `pytest` suite via the venv Python (`& "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m pytest`)
  - Fix anything ruff flags (`ruff check --fix .` / `ruff format .`) - files written by tooling can drift from ruff style or lose a trailing newline
  - Confirm the Task 1 exploration tests now pass and the Task 2 preservation tests still pass, with no regressions elsewhere (Part 1 touches a shared helper used by every request caller, so watch the full suite, not just the new tests)
  - Ensure all tests pass; ask the user if questions arise

## Notes

- **Part 1** edits `RequestBody.populate_template` in `api/request_body.py`; **Part 2** edits `MonitorService.update_runner_odds` in `monitor_service.py`. No other signatures, call sites, or `run()` logic change.
- `<RunnerID>` changing from a bare int (`60298`) to a quoted string (`"60298"`) is the only observable change to a previously-working caller; a live probe confirmed Betfair accepts `selectionId` as a string.
- `populate_template` tests extend `tests/template_tests.py`; monitor guard/preservation tests go in `tests/unit_tests_monitor_resilience.py` (conventions: `MonitorService()` with `svc.db_connection = MagicMock()`, patch `api.call.Call.call` / `monitor_service.BFDriver`, `Log.LOG_FILE = False`).
- Property-based tests use Hypothesis in a `tests/test_property_*` module (e.g. `tests/test_property_template_substitution.py`), consistent with the repo's existing `test_property_*` suite: generate trailing-zero / arbitrary decimal market-id strings and assert the rendered `marketId` round-trips to the exact input on the `add_quotes=False` path; generate arbitrary string values and assert escaping round-trips; generate list substitutions and assert they always render as JSON arrays.
- Run tests with the venv Python per `environment.md`. This is deployable work on the Pi: once merged and verified locally, the fix must be deployed and verified on the Raspberry Pi (`scripts/deploy.sh`) before SP-403 moves to Done; until then it belongs in Mostly Done.
- Out of scope: the separate DNS-lookup / connectivity issue mentioned elsewhere on the ticket, and any EXPIRED-marking / data cleanup.
