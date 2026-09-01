# Implementation Plan: Season Background Data Capture (SP-328)

## Overview

This plan restores reliable background Betfair capture on the always-on Raspberry Pi 500 using a **rebuild-not-repair** strategy. Deploy source of truth is `master` (Vault already removed in favour of `DotenvLoader`). Work is ordered so the data store (Req 1) and the build/deploy pipeline (Req 7) are proven before capture is switched on, Vault is retired (Req 8) only after a Vault-free run is confirmed, then visibility/freshness (Req 4/5), then documentation (Req 6).

The plan reuses existing components (`target_service`, `monitor_service`, `DotenvLoader`, `DBOutputConnection`, `DefaultStrategy` tiers, `my_postgres`, native Rundeck) and adds only thin new artefacts: `docker-compose.yml`, `scripts/deploy.sh`, `scripts/verify_db.py`, `scripts/check_freshness.py`, and the extracted pure functions.

**Task markers:**
- `*` postfix — optional test sub-task (can be skipped for a faster MVP).
- **[OPERATIONAL]** — a manual step performed on the Pi or in a web UI (SSH key restore, Rundeck UI changes, container removal, Confluence). These CANNOT be completed by a code agent and must be done by the operator.

## Tasks

- [x] 1. Extract and formalise pure logic functions for testability
  - [x] 1.1 Create `validate_env` and `missing_tables` pure functions
    - Add `validate_env(values: dict, required: list) -> list[str]` returning missing/empty (whitespace-only) required keys
    - Add `missing_tables(present: set, required: set) -> set` returning `required - present`
    - Place in a small pure-logic module (e.g. `logic/deploy_checks.py`) with no I/O imports
    - _Requirements: 1.3, 1.4, 1.5, 7.8_

  - [x] 1.2 Formalise `select_tier` in the logic layer
    - Move/formalise `select_tier(tiers, time_until_start) -> int` from tests into `logic` (e.g. `logic/simpleStategy.py` or `logic/cadence.py`)
    - Ensure it is total (returns a defined interval for any time-to-event, including negative/in-play) and monotonic
    - Back it with `DefaultStrategy.UPDATE_FREQUENCY_TIERS`
    - _Requirements: 2.2_

  - [x] 1.3 Create `freshness` pure function
    - Add `freshness(now, last_record_ts, threshold_s) -> {elapsed_s, stalled}` in the pure-logic module
    - `elapsed_s == now - last_record_ts` (>= 0 given `last_record_ts <= now`); `stalled == (elapsed_s > threshold_s)`; absent `last_record_ts` => `stalled == True`
    - _Requirements: 5.2, 5.3, 5.5_

  - [x] 1.4 Write property test for `freshness`
    - **Property 1: Freshness stall decision is exact and elapsed time is non-negative**
    - Tag: `# Feature: season-background-data-capture, Property 1: ...`
    - Hypothesis, min 100 iterations; generate datetimes with `last_ts <= now`, positive thresholds, plus the `None` last-record case
    - **Validates: Requirements 5.2, 5.3, 5.5**

  - [x] 1.5 Write property test for `validate_env`
    - **Property 2: Environment validation returns exactly the missing or empty keys**
    - Tag: `# Feature: season-background-data-capture, Property 2: ...`
    - Generate random key sets; include empty/whitespace values and extra keys
    - **Validates: Requirements 1.3, 7.8**

  - [x] 1.6 Write property test for `missing_tables`
    - **Property 3: Missing-tables is exact set difference**
    - Tag: `# Feature: season-background-data-capture, Property 3: ...`
    - Generate random string sets for present/required
    - **Validates: Requirements 1.4, 1.5**

  - [x] 1.7 Write property test for `select_tier`
    - **Property 4: Cadence tier selection is total and monotonic**
    - Tag: `# Feature: season-background-data-capture, Property 4: ...`
    - Generate random time-to-event values including negative (in-play) and boundary values
    - **Validates: Requirements 2.2**

- [x] 2. Checkpoint - Ensure all pure-logic tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Restore Pi -> GitHub code sync (foundational for deploy)
  - [x] 3.1 **[OPERATIONAL]** Restore Pi -> GitHub SSH access and move the Pi onto `master`
    - Regenerate/repair the SSH deploy key on the Pi and register the public key with the `agm-kuraudo/bf_trader_py` GitHub repo
    - Verify `ssh -T git@github.com` authenticates from the Pi
    - Switch the Pi's local clone from `bf_trader_4` to `master` and `git pull` current code (Vault-free, dotenv loader present)
    - _Requirements: 7.1_

- [x] 4. Confirm the data store is running, reachable, and schema-ready (Req 1)
  - [x] 4.1 Implement `scripts/verify_db.py`
    - Read DB connection details from `.env` via `DotenvLoader`
    - Open a connection with a **10-second** timeout; treat as unreachable on timeout
    - Use `validate_env` to detect missing/empty required DB keys and surface which are missing
    - Confirm the four required tables exist (`bf.target`, `bf.market_table`, `bf.log_file`, `bf.betfair_object_ids`) using `missing_tables`
    - Create ONLY absent tables from `build/sql/create_database.sql` DDL, leaving existing tables and data unchanged
    - Return contract: `{ reachable, missing_config, missing_tables, created_tables, error }`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7_

  - [x] 4.2 Write integration tests for `verify_db` (require `my_postgres`)
    - Connect within the 10-second timeout; confirm the four required tables
    - Assert only absent tables are created; run twice to prove existing tables/data are untouched
    - Assert unreachable-store and missing-config paths surface the correct errors
    - _Requirements: 1.2, 1.4, 1.5, 1.7_

- [x] 5. Restore the repeatable build/deploy pipeline (Req 7)
  - [x] 5.1 Add `docker-compose.yml` at repo root
    - Single `bf_capture` service built from `build/betfair_app.dockerfile`
    - Attach to the external `my_trading_network`; read `.env`; fixed container name
    - Store network/env/name config in version control (no Vault service/host/network)
    - Support `docker compose up -d --build` and `docker compose run --rm bf_capture python monitor_service.py`
    - _Requirements: 7.2, 7.3, 8.4_

  - [x] 5.2 Implement `scripts/deploy.sh` deploy orchestration
    - Ordered steps: code sync -> validate `.env` (via `validate_env`) -> `docker compose up -d --build` -> post-deploy verification
    - On code-sync failure: abort, leave running container unchanged, distinct non-zero exit (E6)
    - On missing/empty `.env` value: abort BEFORE container recreation, name the missing value (E1)
    - On build/recreate failure: retain last known-good container, distinct non-zero exit (E7)
    - Authored for Linux/ARM (the Pi is the sole capture host); note in-file that no `.ps1` sibling is provided because the work PC is not the capture host
    - _Requirements: 7.1, 7.2, 7.4, 7.5, 7.7, 7.8, 7.9_

  - [x] 5.3 Write property test for deploy atomicity
    - **Property 5: Deploy is atomic — a failed step leaves the running container unchanged**
    - Tag: `# Feature: season-background-data-capture, Property 5: ...`
    - Inject staged failures at each step against a recorded container id/state; assert unchanged identity/running state and a non-zero result identifying the failed step
    - **Validates: Requirements 7.7, 7.8, 7.9**

  - [x] 5.4 Write unit test for deploy abort ordering
    - Assert deploy aborts BEFORE recreation when `.env` validation fails
    - _Requirements: 7.8_

- [ ] 6. Point the retained Rundeck capture job at the rebuilt container (Req 3)
  - [ ] 6.1 **[OPERATIONAL]** Update the Rundeck capture job to run the compose-managed container
    - Replace the `docker start bf_monitor_service` step with invoking `scripts/deploy.sh` / `docker compose run` against the rebuilt current-code container
    - Keep the existing schedule/cadence unchanged; verify the job fires and starts capture within 60s of the scheduled time
    - _Requirements: 3.1, 3.2, 3.5_

- [ ] 7. Run the first Vault-free capture deploy and verify persistence (Req 7.6)
  - [ ] 7.1 **[OPERATIONAL]** Populate `.env` on the Pi from the current template
    - Set Betfair credentials and `DB_HOST=my_postgres`, `DB_PORT`, `DB_NAME=bf_trader`, `DB_USER`, `DB_PWD`
    - _Requirements: 7.5_

  - [ ] 7.2 Add post-deploy verification to `deploy.sh`
    - After `up -d --build`, confirm within **300 seconds** that a Monitor cycle runs current code and persists an odds row to `bf_trader`
    - Absence of the Vault startup failure confirms current code is running (satisfies 7.4)
    - _Requirements: 7.4, 7.6_

  - [ ] 7.3 Write integration test for post-deploy Monitor cycle (require `my_postgres`)
    - After `docker compose up -d --build`, assert a Monitor cycle persists an odds row to `bf_trader` within 300 seconds and no Vault startup failure occurs
    - _Requirements: 7.4, 7.6_

  - [ ] 7.4 Write unit test for per-target persist-and-continue (E3)
    - Mock `DBOutputConnection` to fail one target; assert the run records the failure and continues with remaining targets without terminating
    - _Requirements: 2.5, 2.6_

  - [ ] 7.5 Write unit test for single-instance lock (E4)
    - Acquire the lock, attempt a second invocation, assert it is skipped and recorded
    - _Requirements: 3.3_

- [ ] 8. Checkpoint - Confirm capture is running current code and persisting odds
  - Ensure all tests pass and the post-deploy verification succeeds, ask the user if questions arise.

- [ ] 9. Retire Vault — ONLY after a Vault-free run is confirmed (Req 8)
  - [ ] 9.1 **[OPERATIONAL]** Remove the `my_keyvault` container
    - Precondition: task 7/8 confirmed current code running with no Vault dependency (E9 guard)
    - Stop and remove `my_keyvault` so no container of that name remains on the host
    - Do NOT touch the ~608 exited containers, dangling image `d91da2e69804`, or `rundeck-image-01` (owned by SP-294)
    - _Requirements: 8.1, 8.2, 8.5_

  - [ ] 9.2 **[OPERATIONAL]** Disable and delete the "Unlock the Vault" Rundeck job
    - Remove the job so it no longer appears in the schedule and no longer invokes `start_up_vault.sh`
    - _Requirements: 8.3_

  - [ ] 9.3 Write property test for Vault-absence across artifacts
    - **Property 6: No design or configuration artifact references Vault**
    - Tag: `# Feature: season-background-data-capture, Property 6: ...`
    - Scan the in-scope artifact set (`design.md`, `docker-compose.yml`, `.env` template, deploy/verify scripts, Rundeck job definitions) for `my_vault` / `my_keyvault` / Vault wiring; assert none present
    - **Validates: Requirements 8.4**

- [ ] 10. Add recurring data-freshness verification (Req 5)
  - [ ] 10.1 Implement `scripts/check_freshness.py`
    - Query `MAX("timestamp")` from `bf.market_table` via `DBOutputConnection`
    - Report the most recent record timestamp and elapsed seconds using the `freshness` pure function
    - Raise a stall alert when elapsed exceeds the 15-minute threshold, when the store is unreachable (retain last successful check timestamp), or when no records exist; identify the affected data source
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ] 10.2 Write integration tests for `check_freshness` (require `my_postgres`)
    - Against a store with recent records, without recent records, with no records, and an unreachable store
    - Assert correct reporting and alert raising for each case
    - _Requirements: 5.2, 5.3, 5.4, 5.5_

  - [ ] 10.3 **[OPERATIONAL]** Add a Rundeck freshness-check job
    - Schedule `check_freshness.py` at a fixed interval no greater than 15 minutes
    - _Requirements: 5.1_

- [ ] 11. Wire visibility and failure detection (Req 4)
  - [ ] 11.1 Ensure capture writes run-log entries with start/end timestamps and outcome
    - Record run start timestamp, completion timestamp, and outcome (success/failure) to the chosen log location (files/journal per the design decision) and `bf.log_file`
    - On failure, record the failure outcome and reason within 60 seconds
    - _Requirements: 4.1, 4.2, 3.4_

  - [ ] 11.2 **[OPERATIONAL]** Confirm operator-detectable failure/missed-run indication
    - Verify failed or missed runs are detectable via Rundeck run logs / the chosen notification mechanism without inspecting internal state, retained for at least 30 days from a single documented location
    - _Requirements: 4.3, 4.4, 4.6_

- [ ] 12. Documentation (Req 6)
  - [ ] 12.1 **[OPERATIONAL]** Write and link the Confluence check/restart note
    - In the Side Projects space, document a status-check procedure distinguishing "running" from "not running"
    - Document an ordered restart procedure followed by a post-restart verification that re-runs the status check and confirms "running"
    - Direct the operator to the restart procedure when the status check returns "not running"
    - Link the note from the SP-328 ticket before it is transitioned to Done
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [ ] 13. Final checkpoint - Ensure all tests pass and capture is verified stable
  - Ensure all tests pass, confirm freshness checks and run logs are visible, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP.
- Tasks marked **[OPERATIONAL]** are manual steps on the Pi or in a web UI (SSH key restore, Rundeck UI changes, container removal, Confluence). A code agent cannot complete them; the operator must.
- Strategy is rebuild-not-repair; deploy source of truth is `master` with Vault already removed from the codebase.
- Ordering enforces the requirement dependencies: data store (Req 1) and pipeline (Req 7) are proven before capture is switched on; Vault retirement (Req 8) happens only after a Vault-free run is confirmed (E9 guard); then visibility/freshness (Req 4/5); then documentation (Req 6).
- Property tests use Hypothesis (already in the repo), minimum 100 iterations, tagged per the design's tag format.
- Integration and post-deploy tests target Linux/ARM (the Pi is the sole capture host); pure-logic tests are cross-platform.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "3.1"] },
    { "id": 1, "tasks": ["1.4", "1.5", "1.6", "1.7", "4.1", "5.1"] },
    { "id": 2, "tasks": ["4.2", "5.2", "5.3", "5.4", "6.1", "10.1"] },
    { "id": 3, "tasks": ["7.1", "7.2", "10.2", "10.3", "11.1"] },
    { "id": 4, "tasks": ["7.3", "7.4", "7.5", "11.2"] },
    { "id": 5, "tasks": ["9.1", "9.2", "9.3"] },
    { "id": 6, "tasks": ["12.1"] }
  ]
}
```
