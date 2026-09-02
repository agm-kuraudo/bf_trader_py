# Bugfix Requirements Document

## Introduction

Tracked under Jira ticket **SP-330 "Monitor Service single-instance lock is poisoned permanently by any crashed run"** (Side Projects / SP, type: Bug, label: `betfair`). All work for this bugfix must reference SP-330.

`MonitorService.run()` (in `monitor_service.py`) enforces a single-instance lock by comparing **lifetime totals** of two log messages across the entire `bf.log_file` table:

```python
start_count, finish_count = self.db_connection.db_read(
    "SELECT SUM(CASE WHEN message = 'Monitor Service: INFO: Starting run' THEN 1 ELSE 0 END) ...,"
    "       SUM(CASE WHEN message = 'Monitor Service: INFO: Ending run successfully' THEN 1 ELSE 0 END) ..."
    " FROM bf.log_file;"
)[0]
if start_count != finish_count:
    # assume "already running": sleep 60s, retry up to 5 times, then raise
    ...
```

The lock has no concept of process liveness or staleness. It infers "another instance is running" purely from historical log-line counts. If any run ever writes `Starting run` but dies before writing `Ending run successfully` (for example the Vault startup crash that caused the ~15-month capture gap found during SP-328), the counts become permanently unequal. From that point on **every** future Monitor invocation believes another instance is running, waits five minutes on the retry loop, then aborts with `Failed to acquire lock` without capturing anything.

**Evidence (Pi, 2026-09-01):** `bf.log_file` held 231 `Starting run` rows against 230 `Ending run successfully` rows — one orphaned start from the crashed 2025-05-21 run. The Monitor container showed `Up`, sat ~5 minutes on the retry loop, exited on `Failed to acquire lock`, and wrote zero odds even though five valid upcoming targets existed. `bf.market_table` MAX timestamp was stuck at 2025-05-25.

**Impact:** capture is perishable (SP-328). A single crashed run silently and permanently disables all future capture until an operator manually reconciles the counts. A one-shot workaround (inserting one matching `Ending run successfully` row so starts == ends) has been applied to unblock capture, but this is a manual patch, not a fix — it re-occurs on the next crash.

### Fix direction: remove the lock entirely

After reviewing `monitor_service.py` with the user, the decision is to **remove the single-instance lock entirely** rather than harden it into a liveness/staleness-aware mechanism. The reasoning is grounded in what the lock actually protects and what it actually costs:

- **The lock's only purpose** is to stop two `run()` invocations overlapping. It guards nothing else.
- **Capture is append-only.** `update_runner_odds()` writes `INSERT INTO bf.market_table("timestamp", market_id, runner_id, odds) VALUES (current_timestamp, %s, %s, %s);` — there is no unique constraint and no read-modify-write, so there is no race to corrupt.
- **The capture loop self-gates.** `get_filtered_targets()` only selects a target when `last_updated + update_frequency < now`, and `update_runner_odds()` sets `last_updated=NOW()` immediately after writing odds. So even if two runs overlapped, they converge quickly rather than tightly duplicating work.
- **The worst case from a clash is benign:** a rare duplicate odds row in `bf.market_table` written a few milliseconds apart. No corruption, no data loss. The user has confirmed duplicate rows are acceptable — no downstream consumer requires one-row-per-timestamp/runner.
- **Invocation is externally serialised.** A single-instance scheduler controls invocation, so true concurrency is unlikely in practice and harmless when it does occur.
- **The lock's failure mode is catastrophic and disproportionate.** The count-based lock cannot detect a live process — it infers state from lifetime log-line counts — and a single crashed run permanently blocks *all* future capture (the 15-month gap). Preventing a harmless duplicate row is not worth a mechanism that can silently kill perishable capture forever.

Removing the lock makes the poisoning failure mode impossible by construction: with no gating step, a crashed run leaves nothing behind that can block a future run. Self-healing is achieved trivially by having no lock to heal. The `Starting run` / `Ending run successfully` log markers are retained purely for run auditing/observability — only the *gating* is removed, not the log lines.

## Bug Analysis

### Current Behavior (Defect)

The lock treats any historical imbalance between lifetime `Starting run` and `Ending run successfully` counts in `bf.log_file` as evidence that another instance is currently running, regardless of whether any process is actually live.

1.1 WHEN a Monitor Service run writes `Monitor Service: INFO: Starting run` to `bf.log_file` but terminates before writing `Monitor Service: INFO: Ending run successfully` THEN the system permanently leaves the lifetime start and finish counts unequal, poisoning the lock for all future runs.

1.2 WHEN a new Monitor Service run starts and no other instance is running, but a prior crashed run left the lifetime start/finish counts unequal THEN the system incorrectly concludes another instance is running, sleeps 60 seconds and retries up to five times, then raises `MonitorServiceException("Monitor Service: Failed to acquire lock")` and captures no odds.

1.3 WHEN the lock has been poisoned by a crashed run THEN the system never self-heals and every subsequent invocation aborts on `Failed to acquire lock` until an operator manually reconciles the `bf.log_file` counts.

### Expected Behavior (Correct)

2.1 WHEN a Monitor Service run starts THEN the system SHALL NOT perform any count-based single-instance lock acquisition, so there is no gating step whose failure can block the run.

2.2 WHEN a prior Monitor Service run crashed after writing `Starting run` and left an orphaned start / unequal counts in `bf.log_file` THEN the system SHALL proceed normally on the next invocation — writing `Starting run`, running the capture cycle, and (on success) writing `Ending run successfully` — because a crashed run can no longer block future runs.

2.3 WHEN a Monitor Service run begins THEN the system SHALL proceed directly to capture without any staleness interval, retry loop, or new lock mechanism; self-healing after a crash is inherent in there being no lock to poison.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a Monitor Service run executes THEN the system SHALL CONTINUE TO write the audit log markers `Monitor Service: INFO: Starting run` at the start and `Monitor Service: INFO: Ending run successfully` on successful completion. These markers are retained for run auditing/observability; only the *gating* that read them is removed, not the log lines themselves.

3.2 WHEN a Monitor Service run captures odds THEN the system SHALL CONTINUE TO write to `bf.market_table` (append-only `INSERT ... VALUES (current_timestamp, ...)`) and update `bf.target` (`last_updated=NOW()`, `update_frequency`, status transitions) exactly as before. The change SHALL be confined to removing the lock and SHALL NOT alter capture logic, target filtering, or target status handling.

3.3 WHEN a Monitor Service run fails after starting THEN the system SHALL CONTINUE TO record the failure outcome and reason to `bf.log_file` (`Monitor Service: ERROR : Ending run with failure : ...`) and re-raise `MonitorServiceException`, without failure-logging masking the original exception (preserving SP-328 Task 11.1 behaviour).

3.4 **Intentional scope decision — SP-328 Requirement 3.3 is retired for this service, not regressed.** SP-328 Requirement 3.3 ("skip and record a concurrent invocation", exercised by SP-328 Task 7.5) is deliberately withdrawn for the Monitor Service. Removing the lock means a genuinely concurrent second invocation is no longer skipped or recorded — it simply runs. This is acceptable because capture is append-only with no unique constraint and no read-modify-write, and `get_filtered_targets()` self-gates on `last_updated + update_frequency < now`, so overlapping runs converge and the only possible effect is a rare, benign duplicate odds row. This is a conscious behavioural trade recorded here, not a silent regression.

## Bug Condition and Property Specification

### Definitions

- **F**: the original (unfixed) `MonitorService.run()` lock — compares lifetime `Starting run` / `Ending run successfully` counts across all of `bf.log_file` and gates the run on their equality (retry loop, then `Failed to acquire lock`).
- **F'**: the fixed `run()` — **no lock at all**. The count query and retry/gating block are removed; `run()` proceeds directly from opening the DB connection to writing `Starting run` and running the capture cycle. The `Starting run` / `Ending run successfully` audit markers are retained.
- **X**: the observable state at the moment a Monitor run begins — specifically whether another instance is *actually alive* now, and the residue left in the durable store by prior runs (orphaned starts / unequal counts in `bf.log_file`).

### Bug Condition

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type RunStartState
  OUTPUT: boolean

  // The bug fires when NO instance is actually running now, yet the durable
  // store carries residue from a prior crashed run that the count-based lock
  // reads as "already running".
  RETURN (NOT X.another_instance_alive) AND X.has_orphaned_lock_residue
END FUNCTION
```

Concrete counterexample from the Pi: `bf.log_file` with 231 `Starting run` rows and 230 `Ending run successfully` rows, no live Monitor process — `isBugCondition` is true, and F aborts on `Failed to acquire lock`.

### Property — Fix Checking

```pascal
// Property: orphaned residue from a crashed run does NOT block a new run.
// With no lock, there is no gating to fail, so the run always proceeds.
FOR ALL X WHERE isBugCondition(X) DO
  result <- run'(X)               // F' has no lock acquisition step
  ASSERT started_run(result)       // "Starting run" is written, capture proceeds
    AND NOT raised_failed_to_acquire(result)
END FOR
```

### Property — Preservation Checking

```pascal
// Property: for non-buggy inputs, F' captures exactly as F did, with ONE
// intended exception (below).
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT capture_behaviour(F'(X)) = capture_behaviour(F(X))
END FOR

// In particular, for NOT isBugCondition(X):
//  - no residue, no live instance -> run proceeds and captures (as F did).
//  - INTENDED BEHAVIOURAL CHANGE: X.another_instance_alive = true.
//      F  : skips and records the second invocation (old SP-328 Req 3.3).
//      F' : does NOT skip — the second invocation runs.
//    This is called out honestly as a deliberate change, NOT a silent
//    regression. Its worst-case impact is negligible/benign: at most one extra
//    append-only duplicate odds row in bf.market_table a few ms apart, because
//    capture has no unique constraint / no read-modify-write and
//    get_filtered_targets() self-gates on last_updated + update_frequency.
```

## Existing Tests Impact

`tests/unit_tests_monitor_resilience.py` currently contains `TestSingleInstanceLock`, which asserts the count-based gating behaviour that is being removed:

- `test_second_invocation_is_skipped_and_recorded` — drives `run()` with a mocked DB reporting unequal counts `(231, 230)`, patches `sleep`, and asserts the run raises `Failed to acquire lock`, that it slept (retried), and that `Starting run` was **not** written. This assertion is now the opposite of desired behaviour and MUST be removed/replaced.
- `test_balanced_lock_allows_the_run_to_proceed_past_the_lock` — asserts a balanced count `(231, 231)` proceeds past the lock without sleeping. The "balanced vs unbalanced" distinction no longer exists once the lock is gone.

These tests will be removed/replaced with tests asserting the new behaviour:

- Given orphaned residue (e.g. unequal `Starting run` / `Ending run successfully` counts, no live instance), `run()` proceeds and writes `Monitor Service: INFO: Starting run` — it does **not** sleep on a retry loop and does **not** raise `Failed to acquire lock`.
- The audit markers are still written: `Starting run` at the start and `Ending run successfully` on success (with authentication/capture stubbed as needed to reach the markers).

`TestRunFailureLogging` (SP-328 Task 11.1 behaviour) remains valid and unchanged: a failed run still records `Monitor Service: ERROR : Ending run with failure : ...` and re-raises, and failure-logging still never masks the original exception. Its mocks currently seed a balanced lock count `(5, 5)` purely to get past the old gating; once the lock is removed those seeded counts become irrelevant but harmless.
