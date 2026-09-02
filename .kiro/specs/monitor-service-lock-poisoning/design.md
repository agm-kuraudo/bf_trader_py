# Monitor Service Lock Poisoning Bugfix Design

> Jira: **SP-330** — "Monitor Service single-instance lock is poisoned permanently by any crashed run" (Side Projects / SP, type: Bug, label: `betfair`). All work references SP-330.

## Overview

`MonitorService.run()` (in `monitor_service.py`) currently opens a DB connection and then, before doing any capture, runs a count-based single-instance "lock": it reads lifetime totals of two audit markers from `bf.log_file` and, if `SUM('Starting run') != SUM('Ending run successfully')`, assumes another instance is live, sleeps 60s, retries up to 5 times, then raises `MonitorServiceException("Monitor Service: Failed to acquire lock")`.

Because the check reads *lifetime* log-line counts and has no concept of process liveness, a single crashed run that wrote `Starting run` but never wrote `Ending run successfully` poisons the counts permanently. From then on every invocation waits ~5 minutes and aborts without capturing (the SP-328 15-month gap; Pi evidence 2026-09-01: 231 starts vs 230 ends).

**Fix approach (agreed with the user):** *remove the count-based lock entirely* rather than harden it. The lock only guarded against two overlapping `run()` invocations; capture is append-only (`INSERT ... VALUES (current_timestamp, ...)` into `bf.market_table`, no unique constraint, no read-modify-write) and self-gates via `get_filtered_targets()` on `last_updated + update_frequency < now`, so an overlap's worst case is a rare benign duplicate row. Deleting the gating step makes the poisoning failure mode impossible by construction and self-heals trivially (no lock to poison). The `Starting run` / `Ending run successfully` markers are retained purely as audit/observability log lines — only the *gating that reads them* is removed.

This is a **pure deletion plus test changes**: no DB migration, no new config, no new dependency.

## Glossary

- **Bug_Condition (C)**: No Monitor instance is actually alive now, yet `bf.log_file` carries residue (unequal lifetime `Starting run` / `Ending run successfully` counts) from a prior crashed run. The old count-based lock reads this residue as "already running" and blocks the run.
- **Property (P)**: When the bug condition holds, the fixed `run()` proceeds normally — writes `Starting run`, runs the capture cycle — and never sleeps on a retry loop or raises `Failed to acquire lock`.
- **Preservation**: For all non-buggy inputs, capture behaviour is unchanged, and the audit markers and failure-logging behaviour are unchanged.
- **run()**: The method in `monitor_service.py` that opens the DB connection, (currently) runs the lock, writes `Starting run`, cleans up stale targets, authenticates, runs the 15-minute capture loop, and writes `Ending run successfully` on success.
- **F (original)**: `run()` with the `for i in range(5):` count-lock block present.
- **F' (fixed)**: `run()` with that block removed — flows directly from `open_connection(...)` to `db_write_log("Monitor Service: INFO: Starting run")`.
- **bf.log_file**: Durable run log table; holds the `Starting run` / `Ending run successfully` / failure marker rows written by `db_write_log`.

## Bug Details

### Bug Condition

The bug manifests when a Monitor run begins while no other instance is actually alive, but a prior crashed run left an orphaned `Starting run` (unequal lifetime counts) in `bf.log_file`. The count-based lock is either unable to distinguish "historical residue" from "a live process", inferring liveness purely from lifetime log-line equality, and so gates a run that should proceed.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X of type RunStartState  // observable state when run() begins
  OUTPUT: boolean

  RETURN (NOT X.another_instance_alive)
         AND X.has_orphaned_lock_residue   // SUM('Starting run') != SUM('Ending run successfully') in bf.log_file
END FUNCTION
```

### Examples

- **Pi counterexample (real):** `bf.log_file` = 231 `Starting run` vs 230 `Ending run successfully`, no live Monitor process. Expected: run proceeds and captures. Actual (F): sleeps ~5 minutes, raises `Failed to acquire lock`, captures zero odds; `bf.market_table` MAX timestamp frozen.
- **Fresh orphan:** one crashed run leaves counts 1 vs 0, no live instance. Expected: next run proceeds. Actual (F): blocked, aborts.
- **Balanced counts, no live instance:** counts equal (e.g. 231 vs 231). Expected and actual (F): run proceeds. (Not a bug input — but F' behaves identically here.)
- **Edge — genuinely concurrent second run (`another_instance_alive = true`):** not the bug condition. See Preservation Requirements for the intended behavioural change here.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The audit markers are still written: `Monitor Service: INFO: Starting run` at the start and `Monitor Service: INFO: Ending run successfully` on successful completion (retained for auditing/observability; only the gating that read them is removed). — Req 3.1
- Capture logic is unchanged: append-only `INSERT INTO bf.market_table("timestamp", market_id, runner_id, odds) VALUES (current_timestamp, %s, %s, %s);`, target filtering in `get_filtered_targets()`, and `bf.target` updates (`last_updated=NOW()`, `update_frequency`, status transitions) all behave exactly as before. — Req 3.2
- The stale-target cleanup (`UPDATE bf.target SET status='EXPIRED' ... start_time < NOW() - INTERVAL '{stale_hours} hours'`) that immediately follows `Starting run` is unchanged. — Req 3.2
- Failure logging is unchanged: a run that fails after starting still records `Monitor Service: ERROR : Ending run with failure : ...` to `bf.log_file` and re-raises `MonitorServiceException`, and failure-logging never masks the original exception (SP-328 Task 11.1). — Req 3.3

**Scope:**
All inputs that do NOT involve the removed lock are completely unaffected. This includes odds capture, target status handling, initial-odds fetch for newly-opened targets, and failure recording.

**Intentional behavioural change (not a regression) — Req 3.4:** for `X.another_instance_alive = true`, F skipped and recorded the second invocation (old SP-328 Req 3.3); F' does **not** skip — the second invocation simply runs. This is a conscious trade: capture is append-only with no unique constraint / no read-modify-write and self-gates on `last_updated + update_frequency`, so the worst case is one extra benign duplicate odds row a few ms apart. SP-328 Req 3.3 is deliberately *retired* for this service.

## Hypothesized Root Cause

This is a confirmed, well-understood defect (not a speculative one). The root cause is the design of the lock itself:

1. **Liveness inferred from lifetime history (primary cause):** the `for i in range(5):` block reads `SUM(CASE WHEN message='Monitor Service: INFO: Starting run' ...)` vs `SUM(... 'Ending run successfully' ...)` across *all of* `bf.log_file` and treats inequality as "another instance is running". There is no PID, no heartbeat, no timestamp window — a crashed run's orphaned `Starting run` is indistinguishable from a live run.
2. **Permanent poisoning:** because the imbalance persists in the durable log forever, every subsequent run reads it and blocks. No self-healing path exists short of manual reconciliation.
3. **Disproportionate failure mode:** the lock exists only to avoid a harmless duplicate append-only row, yet its failure silently kills all perishable capture. Removing it is proportionate.

## Correctness Properties

Property 1: Bug Condition - Orphaned lock residue no longer blocks a run

_For any_ input where the bug condition holds (isBugCondition returns true — no live instance, but unequal lifetime `Starting run` / `Ending run successfully` residue in `bf.log_file`), the fixed `run()` SHALL proceed: it writes `Monitor Service: INFO: Starting run`, does NOT sleep on a retry loop, and does NOT raise `MonitorServiceException("Monitor Service: Failed to acquire lock")`.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Non-lock behaviour unchanged

_For any_ input where the bug condition does NOT hold (isBugCondition returns false), the fixed `run()` SHALL produce the same capture behaviour as the original function, preserving the audit markers (`Starting run` / `Ending run successfully`), append-only odds capture and target updates, and the failure-logging-and-re-raise path. (One intended exception, Req 3.4: a genuinely concurrent second invocation is no longer skipped/recorded — it runs — which is a deliberate trade, not a regression.)

**Validates: Requirements 3.1, 3.2, 3.3**

## Fix Implementation

### Changes Required

This is a **pure deletion** in one function plus corresponding test changes. No DB migration, no new config, no new dependency.

**File**: `monitor_service.py`

**Function**: `MonitorService.run()`

**Specific Changes**:

1. **Remove the count-based lock block.** Delete the entire lock section that currently sits between `self.db_connection.open_connection(db_details_string)` and `self.db_connection.db_write_log("Monitor Service: INFO: Starting run")`:

   ```python
   # Logic to make sure only 1 instance can run at a time
   for i in range(5):
       start_count, finish_count = self.db_connection.db_read(
           "SELECT SUM(CASE WHEN message = 'Monitor Service: INFO: Starting run' THEN 1 ELSE 0 END) AS starting_run_count,  SUM(CASE WHEN message = 'Monitor Service: INFO: Ending run successfully' THEN 1 ELSE 0 END) AS ending_run_count FROM bf.log_file;"
       )[0]

       if start_count != finish_count:
           Log.log_warning(
               "Monitor Service: Appears to be already running. Will retry every 60 seconds for 5 minutes"
           )
           time.sleep(60)
       else:
           break

       if i == 4:
           raise MonitorServiceException("Monitor Service: Failed to acquire lock")
   ```

   After removal, `run()` flows directly:

   ```python
   self.db_connection = DBOutputConnection()
   self.db_connection.open_connection(db_details_string)

   self.db_connection.db_write_log("Monitor Service: INFO: Starting run")

   # Clean up stale targets whose start_time has passed by more than the configured threshold
   stale_hours = DefaultStrategy.STALE_TARGET_HOURS
   ...
   ```

2. **Keep the `import time`.** `time.sleep(...)` is still used later in the capture loop (`time.sleep(max(0.1, nearest_update_seconds - 1))`), so the top-of-file `import time` stays. Only the lock's `time.sleep(60)` call is removed.

3. **Leave audit markers untouched.** `db_write_log("Monitor Service: INFO: Starting run")` and `db_write_log("Monitor Service: INFO: Ending run successfully")` (plus the `Log.log_info` for the latter) remain exactly as-is.

4. **Leave the failure `except` block untouched.** The top-level `except Exception as e:` block that logs `Monitor Service: ERROR : Ending run with failure : ...` and re-raises (best-effort, never masking) is unchanged.

5. **No other edits.** Stale-target cleanup, authentication, the capture loop, target filtering, and odds capture are all unchanged. `MonitorServiceException` remains (still raised elsewhere); only the `Failed to acquire lock` raise site is removed.

## Testing Strategy

### Validation Approach

Two phases: first confirm the bug on the *unfixed* code (a run with orphaned residue is blocked), then verify the fix proceeds for buggy inputs and preserves behaviour for non-buggy inputs. All tests are unit tests with no live network or DB — consistent with the existing `tests/unit_tests_monitor_resilience.py`: a `MagicMock` `DBOutputConnection` patched into `run()`, `BFDriver`/auth patched, and `monitor_service.time.sleep` patched so no real waiting occurs.

**Test file:** `tests/unit_tests_monitor_resilience.py`.

**Runner (from repo README + `.kiro/steering/environment.md`):** pytest via the project virtual environment on Windows:
```
& "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m pytest tests/unit_tests_monitor_resilience.py -v
```
Follows Python standards: local `.venv`, pinned deps in `requirements.txt`; cross-platform (on Linux/macOS use `.venv/bin/python -m pytest ...`). No new dependency is introduced.

### Existing Test Changes

- **Remove `TestSingleInstanceLock` entirely.** Both of its tests assert the removed behaviour:
  - `test_second_invocation_is_skipped_and_recorded` — asserts unequal counts `(231, 230)` cause a sleep-retry and `Failed to acquire lock`, and that `Starting run` was NOT written. This is now the *opposite* of desired behaviour → delete.
  - `test_balanced_lock_allows_the_run_to_proceed_past_the_lock` — the balanced-vs-unbalanced distinction no longer exists once the lock is gone → delete.
- **Replace with a new class** (see below) asserting the fix property and the audit-marker preservation.
- **`TestPersistAndContinue` — unchanged.** It does not touch the lock.
- **`TestRunFailureLogging` — unchanged and still valid.** Its mocks seed `db_read.return_value = [(5, 5)]` purely to pass the old gating; once the lock is gone that seed is irrelevant but harmless (the query is simply never made). Both tests continue to pass: failure record still written, original exception still propagates.

### Exploratory Bug Condition Checking

**Goal**: Surface the counterexample on the UNFIXED code before deleting the lock — confirm the root cause.

**Test Plan**: Drive `run()` with a mocked DB whose `db_read` returns unequal counts `[(231, 230)]` and no live instance, patch `monitor_service.time.sleep`. On unfixed code this raises `Failed to acquire lock` and never writes `Starting run`.

**Test Cases**:
1. **Orphaned residue blocks run (unfixed)**: `db_read` → `[(231, 230)]`; assert `MonitorServiceException("... Failed to acquire lock")` raised and `Starting run` NOT written *(fails-to-be-blocked once the fix lands — this exact assertion is the one being deleted)*.

**Expected Counterexamples**:
- Unfixed `run()` sleeps on the retry loop and raises `Failed to acquire lock` despite no live instance.
- Confirms root cause: gating derived from lifetime log-count equality.

### Fix Checking

**Goal**: For all inputs where the bug condition holds, the fixed `run()` proceeds (Property 1).

**Pseudocode:**
```
FOR ALL X WHERE isBugCondition(X) DO
  result := run_fixed(X)
  ASSERT started_run(result)                       // "Starting run" written
    AND NOT slept_on_retry_loop(result)            // no lock retry sleep
    AND NOT raised_failed_to_acquire(result)
END FOR
```

**Test (new `TestNoSingleInstanceLock`)**:
1. **Orphaned residue proceeds**: mock DB with `db_read` returning unequal residue (e.g. `[(231, 230)]`); patch `DBOutputConnection` → mock, patch `BFDriver` so `get_token()` returns `False` (auth fails fast *after* `Starting run`, so we reach and assert the marker without running the full capture cycle); patch `monitor_service.time.sleep`. Assert:
   - `Starting run` WAS written (`db_write_log` called with `"Monitor Service: INFO: Starting run"`).
   - `Failed to acquire lock` was NOT raised (the run fails later on auth, not on the lock — assert the raised message does not contain `Failed to acquire lock`).
   - No retry-loop sleep attributable to the lock: because auth fails fast before the capture loop, `time.sleep` is not called (the lock's `sleep(60)` is gone and the capture-loop sleep is never reached).

### Preservation Checking

**Goal**: For all non-buggy inputs, F' matches F (Property 2), with the single intended exception in Req 3.4.

**Pseudocode:**
```
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT capture_behaviour(run_fixed(X)) = capture_behaviour(run_original(X))
END FOR
```

**Testing Approach**: Property-based testing is well suited to preservation checking (generates many inputs, catches edge cases, gives strong "unchanged for all non-buggy inputs" guarantees). For this small deletion, the practical high-value coverage is example-based unit tests over the observable seams (audit markers, failure logging), since the capture internals are already covered by existing tests and are literally untouched by the diff.

**Test Cases**:
1. **Audit markers on success**: a run that reaches success writes both `Starting run` and `Ending run successfully`. (Covered directly, or via existing behaviour — the markers and their call sites are unchanged.)
2. **Failure logging preserved**: `TestRunFailureLogging.test_failure_writes_failure_record_and_reraises` — still writes `Starting run` and `ERROR : Ending run with failure`, and re-raises. Unchanged, must still pass.
3. **Failure logging never masks original error**: `TestRunFailureLogging.test_failure_logging_never_masks_original_error` — original `boom` still propagates. Unchanged, must still pass.
4. **Persist-and-continue preserved**: `TestPersistAndContinue` — unchanged, must still pass.

### Unit Tests

- New `TestNoSingleInstanceLock`: orphaned residue proceeds past where the lock used to be and writes `Starting run` without raising `Failed to acquire lock`.
- Retained `TestRunFailureLogging` and `TestPersistAndContinue` verify preservation of failure logging and per-target continue.

### Property-Based Tests

- Optional: a hypothesis-generated property over residue counts `(start, finish)` with `start != finish` and no live instance, asserting `run()` always writes `Starting run` and never raises `Failed to acquire lock`. Low incremental value given the deletion is unconditional, but documents Property 1 as a generalisation of the single example.

### Integration Tests

- Not required for this change (no live DB/network in the unit suite; capture flow is unchanged). Operational verification is the real-world signal: after deploy on the Pi, confirm a run proceeds and `bf.market_table` MAX timestamp advances again despite the historical count imbalance — this is the ultimate SP-330 acceptance check and is tracked as deployment verification rather than an automated integration test.
