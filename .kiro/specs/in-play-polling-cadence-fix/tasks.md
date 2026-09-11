# Implementation Plan

Bugfix: **In-Play Polling Cadence Fix** — Jira **SP-343**.

This plan follows the bug-condition methodology: explore the bug first (Task 1, expected
to FAIL on unfixed code), implement the fix F' (Tasks 2–5), then verify fix-checking and
preservation (Tasks 6–8), and finally deploy + verify on the Pi (Task 9).

## Overview

Fix the in-play / lead-window polling cadence so a monitor run stays alive and samples at
the ~5s IN_PLAY interval (instead of collapsing to the coarse ~900s Rundeck interval or
exiting prematurely). The fix extracts a pure `decide_next_action` decision function, adds
a single-instance advisory lock, and rewrites the `run()` loop, then validates via
fix-checking and preservation property tests before deploying to the Pi.

## Task Dependency Graph

```
1 (Bug Condition exploration test — FAILS on unfixed code)
        │
        ▼
2 (decide_next_action pure fn) ──► 3 (config constants)
        │                               │
        └───────────────┬───────────────┘
                        ▼
4 (advisory-lock helpers)  ──────►  5 (rewrite run() loop, wires 2+3+4)
                                        │
        ┌───────────────┬───────────────┼───────────────┐
        ▼               ▼               ▼               ▼
6 (Fix-Checking     7 (Preservation   8 (single-instance
   property test +     property test +    lock integration
   re-run Task 1)      logging test)      test)
        │               │               │
        └───────────────┴───────────────┘
                        ▼
9 (Deploy to Pi + live in-play verification — top-level, gates Done)
```

- Tasks 2 and 3 can proceed in parallel; task 4 is independent of 2/3.
- Task 5 depends on 2, 3, and 4.
- Tasks 6, 7, 8 depend on 5 and can run in parallel.
- Task 9 depends on 6, 7, 8.

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2", "3", "4"] },
    { "id": 2, "tasks": ["5"] },
    { "id": 3, "tasks": ["6", "7", "8"] }
  ]
}
```

## Notes

- Task 1 (bug-condition exploration test) is expected to FAIL on the unfixed code — that
  failure is the bug confirmation, not a regression to fix.
- Tasks 9 (deploy/verify on the Pi) and 10 (checkpoint) are top-level tasks and are
  excluded from the parallel wave graph above.
- The fix MUST NOT modify `select_tier` return values, the tier config values, or the
  SP-332 quality-checks logic (`logic/quality_checks.py` stays detect-only and unchanged).
- The ~5s in-play / lead-window cadence is loop-driven only — the stored coarse
  `update_frequency` is not changed.

## Tasks

- [x] 1. Write bug-condition exploration property test
  - **Property 1: Bug Condition** - In-play / lead-window run collapses to the Rundeck interval
  - **CRITICAL**: This test MUST FAIL on the current (unfixed) code — the failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails** at this stage
  - **NOTE**: This test encodes the expected behaviour and will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples showing that when an OPEN target is in-play OR within the 20-min lead window but nothing is due at that exact instant, the current run-loop exits (or the effective sample interval collapses to ~900s / the Rundeck re-trigger interval) instead of sleeping ~5s and continuing
  - **Scoped PBT Approach**: Generate `MonitorRunState` inputs where at least one OPEN target satisfies `isBugCondition` — `t.status == "OPEN" AND t.start_time <= now() + LEAD_WINDOW_SECONDS` — with nothing due at the current instant (`filtered_targets == []`); include a pre-match lead-window case (`start_time` ~8 min ahead, coarse stored `update_frequency=300`)
  - Because the pure `decide_next_action` does NOT exist yet on unfixed code, target either (a) a faithful extraction of the CURRENT inline loop decision logic from `monitor_service.py::run()`, or (b) drive `run()` with mocked `DBOutputConnection`/`BFDriver` and assert how many odds updates occur before the run exits
  - Assert the intended fixed behaviour: under a bug-condition input the run continues sampling at ~5s (does not exit / does not collapse to ~900s)
  - Run the test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug and confirms the root-cause hypothesis: empty-`filtered_targets` break, 900s cap, `range(15*60)` budget, pre-kickoff drop-out)
  - Document counterexamples found (e.g. "run performs 1 update then exits; effective interval ≈ 900s not ~5s"; "lead-window target 8 min pre-kickoff → nothing due for ~300s → run exits before kickoff")
  - Mark task complete when the test is written, run, and the failure is documented
  - Files: `tests/test_property_monitor_config.py` (new bug-condition property; may add a small faithful extraction helper under `tests/`)
  - _Requirements: 1.1, 1.2, 1.3, 1.5_

- [x] 2. Extract the pure loop-decision function `decide_next_action`
  - Add pure, DB-free, API-free `decide_next_action(state, elapsed_seconds) -> (action, sleep_seconds)` where `action ∈ {"poll", "sleep", "exit"}`, co-located with `select_tier`
  - Inputs on `state`: `has_due_target`, `has_active_or_imminent`, `nearest_update_seconds`, `in_play_interval`
  - Logic per design Change 1: `elapsed >= HARD_CAP_SECONDS → ("exit", 0)`; else `has_due_target → ("poll", 0)`; else `has_active_or_imminent → ("sleep", max(0.1, in_play_interval - 1))`; else `("exit", 0)`
  - Drive the game-on sleep from the IN_PLAY interval, NOT the coarse `nearest_update_seconds`
  - Add module constants `HARD_CAP_SECONDS = 6 * 3600`, `LEAD_WINDOW_SECONDS = 20 * 60`, and read the IN_PLAY interval from the tiers config (default 5)
  - Do NOT modify `select_tier` or the tier config values
  - Files: `logic/simpleStategy.py`
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 3. Add config constants for the hard cap and lead window
  - Add `MONITOR_HARD_CAP_SECONDS` (default `6 * 3600`) and `MONITOR_LEAD_WINDOW_SECONDS` (default `20 * 60`) to `DefaultStrategy`
  - Surface both via `config/strategy.yaml`, loaded with the existing optional-key `.get()` pattern in `FromFileStrategy` (`yaml_content.get("MONITOR_HARD_CAP_SECONDS", DefaultStrategy.MONITOR_HARD_CAP_SECONDS)`, same for the lead window)
  - Document in code/config that `MONITOR_LEAD_WINDOW_SECONDS` MUST exceed the ~15-min Rundeck re-trigger interval plus margin, so a run is always alive at 5s before any kickoff
  - Files: `logic/simpleStategy.py`, `config/strategy.yaml`
  - _Requirements: 2.5, 2.7_

- [x] 4. Add Postgres session-scoped advisory-lock helpers
  - Add `try_acquire_run_lock() -> bool` and `release_run_lock() -> None` on `DBOutputConnection`
  - Use `pg_try_advisory_lock(%s)` / `pg_advisory_unlock(%s)` with a documented fixed bigint key (e.g. `RUN_ADVISORY_LOCK_KEY = 4310343`)
  - **CRITICAL**: Use the SESSION-scoped functions, NOT `pg_advisory_xact_lock` — the connection runs `autocommit = True`, so a transaction-scoped lock would release immediately and give no protection. `pg_try_advisory_lock` returns immediately (non-blocking) so a second run gets `False`
  - Session-close / connection-drop auto-releases the lock — this is what makes it self-healing (avoids the poisoned-lock problem `scripts/fix_lock.py` patched)
  - Files: `output/dboutput.py`
  - _Requirements: 1.4, 2.6_

- [x] 5. Rewrite `monitor_service.py::run()` to use the decision function and lock
  - **Acquire the single-instance guard** immediately after `open_connection(...)`: if `try_acquire_run_lock()` is `False`, log "Another run active, exiting as no-op", close the connection, and return (exit 0 no-op)
  - Replace `for _i in range(15 * 60):` and the two premature breaks (empty-`filtered_targets` break and `nearest_update_seconds > MONITOR_MAX_WAIT_SECONDS` break) with a `while True:` loop bounded only by the 6-hour cap (checked inside `decide_next_action`)
  - Compute `active_or_imminent` = targets with status `OPEN` and `start_time <= now() + LEAD_WINDOW_SECONDS` (status at tuple index `[1]`, start_time at `[6]`, per `process_targets`)
  - **Reconcile due-ness (BUG A')**: treat an active-or-imminent target as due when `in_play_interval` (~5s) has elapsed since its `last_updated`; poll the union of `filtered_targets` and these in-play-due targets, so the 5s cadence is driven even though the stored `update_frequency` stays coarse
  - Build `LoopState(has_due_target, has_active_or_imminent, nearest_update_seconds, in_play_interval)` and delegate to `decide_next_action(state, time.monotonic() - run_start)`
  - `"exit" → break`; `"poll" → update_runner_odds(poll_targets); reload_from_db = True`; `"sleep" → time.sleep(sleep_seconds)` (no reload)
  - Release the lock on normal exit before `close()`; on crash rely on session-close auto-release (do not add explicit release in the except path)
  - Preserve `reload_from_db` semantics and durable run start/end/failure logging to `bf.log_file` (including the existing top-level except that writes the failure marker)
  - **Do NOT modify** `select_tier` or the `update_frequency` write in `update_runner_odds` (Change 3a). `MONITOR_MAX_WAIT_SECONDS` may remain a config constant but is no longer consulted as a bail-out
  - Files: `monitor_service.py`
  - _Bug_Condition: isBugCondition(X) — EXISTS OPEN t WHERE t.start_time <= now() + LEAD_WINDOW_SECONDS AND effective_sample_interval >> 5s_
  - _Expected_Behavior: decide_next_action never returns "exit" while an active-or-imminent target remains and elapsed < 6h; polls/sleeps at ~IN_PLAY interval; single-instance guard held by exactly one run_
  - _Preservation: coarse tiers, idle cheap-exit, select_tier, stored update_frequency, durable logging, and SP-332 unchanged_
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 1.4, 3.3, 3.4_

- [x] 6. Fix-Checking property test + confirm Task 1 now passes
  - **Property 1: Expected Behavior** - In-play / lead-window cadence restored and run kept alive
  - Property: FOR ALL active-or-imminent states (an OPEN target in-play OR within the 20-min lead window, including pre-match targets with a coarse stored `update_frequency`) and `elapsed < 6h`, `decide_next_action` never returns `"exit"`, and when nothing is due it returns `"sleep"` with `sleep_seconds ≈ IN_PLAY interval` (~5s), not near the coarse `nearest_update_seconds`
  - **Lead-window boundary unit cases**: target at exactly `start_time == now() + LEAD_WINDOW_SECONDS` (inclusive edge) → `"sleep"` (not `"exit"`); target at `+1s` beyond the window with a far-out nearest update → `"exit"`
  - **6-hour cap property**: for any state, once `elapsed >= HARD_CAP_SECONDS` the action is `"exit"`
  - **IMPORTANT**: Re-run the SAME exploration test from Task 1 — do NOT write a new one
  - **EXPECTED OUTCOME**: Task 1 test now PASSES (confirms the bug is fixed); new fix-checking properties PASS
  - Files: `tests/test_property_monitor_config.py`
  - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.7_

- [x] 7. Preservation property test + logging preservation
  - **Property 2: Preservation** - Idle cheap-exit, coarse tiers, and logging unchanged
  - **IMPORTANT**: Follow observation-first methodology — first observe idle/pre-match behaviour on the code, then encode as properties
  - Property: FOR ALL genuinely-idle states (no OPEN target in-play or within the 20-min lead window — every OPEN `start_time > now() + LEAD_WINDOW_SECONDS`), `decide_next_action` returns `"exit"` (idle cheap-exit preserved)
  - Reuse/extend the existing `select_tier` totality/monotonicity properties to prove tier selection returns identical intervals for all time-to-event inputs (tier selection unchanged, Req 3.3)
  - Extend `unit_tests_monitor_resilience.py` to assert the start marker, success marker, and `"Ending run with failure"` marker are still written under the new loop, and that failure-logging never masks the original exception
  - Do NOT touch SP-332 (`logic/quality_checks.py`) — it stays detect-only and unchanged (Req 3.5)
  - **EXPECTED OUTCOME**: Tests PASS (baseline behaviour preserved, no regressions)
  - Files: `tests/test_property_monitor_config.py`, `tests/unit_tests_monitor_resilience.py`
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 8. Single-instance advisory-lock integration test
  - Open two `DBOutputConnection` sessions following the repo's DB test conventions
  - Assert: first `try_acquire_run_lock()` returns `True`; a second concurrent attempt returns `False`; after `release_run_lock()` (or closing the first session) the lock can be re-acquired — proving the guard is self-releasing and not poison-prone
  - Files: `tests/integration_test_verify_db.py` (following `tests/unit_tests_db.py` conventions)
  - _Requirements: 1.4, 2.6_

- [~] 9. Deploy and verify on the Pi (Definition of Done)
  - Run the full test suite (`pytest`) and confirm all tests pass, including Task 1's now-passing exploration test and the fix/preservation properties
  - Deploy to the Raspberry Pi via `scripts/deploy.sh` (syncs code, validates `.env`, rebuilds/recreates the container with `docker compose up -d --build`, runs post-deploy verification). See `pi-access` steering for how to reach the Pi
  - **Live in-play verification (required before Done)**: against a live in-play match, confirm `bf.market_table` shows in-play rows at ~5s spacing (not ~900s), including rows beginning at ~5s spacing from within the 20-min pre-kickoff lead window (opening minutes captured, not lost to a post-kickoff Rundeck trigger)
  - Confirm the single-instance guard: a second Rundeck-triggered `run --rm` logs "Another run active, exiting as no-op" and exits, so only one run holds the lock at a time
  - **Ticket status**: while complete-but-not-deployed the ticket sits in **Mostly Done**; only after deploy + live verification, add the SP-343 completion comment (noting the deploy was done and the ~5s in-play cadence incl. pre-kickoff lead window + single-instance no-op were verified on the Pi) and move to **Done**
  - Files: `scripts/deploy.sh`
  - _Requirements: 2.1, 2.6, 2.7_

- [~] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass; ask the user if questions arise.
