# Implementation Plan: Pi Postgres Backup Automation

> Tracked by **SP-363** — _Automate Postgres backups on the Pi (scheduled pg_dump + off-Pi retention)_.
> Derived from the approved `design.md` and `requirements.md` for this feature.
> Requirement IDs below use the `requirements.md` numbering (R1–R6, by acceptance criterion e.g. `1.5`).
> Property references (P1–P7) point at the `design.md` Correctness Properties section.

## Overview

This feature is mostly operational/infra work on the Raspberry Pi plus a Windows-side pull, not a large change to the `bf_trader_py` Python app. The one genuinely unit-testable piece is the keep-last-3 **retention selection** logic, which is implemented in Python (pytest + hypothesis, ruff) so its behaviour can be proven with property-based and unit tests. Everything else — the Rundeck schedule firing, the Samba pull on Windows, and the one-time restore rehearsal — is delivered as scripts/job definitions plus explicit manual verification tasks with observable success criteria, consistent with the design's Testing Strategy.

The implementation language for the testable retention logic is **Python**, matching the project. The Windows pull is delivered as PowerShell (Windows Task Scheduler), and the Pi-side orchestration is a shell script runnable by the native Rundeck job. The canonical operational documentation is the in-repo runbook `docs/SP-363_postgres_backup_runbook.md`.

Build order: first the pure, testable retention core and its tests, then the Pi-side backup+retention script that uses it, then the Rundeck job definition, then the Windows pull task, then the runbook, then the deploy+verify-on-Pi step and the one-time restore rehearsal, and finally the single end-of-feature verification gate.

## Tasks

- [x] 1. Implement the pure keep-last-3 retention selection core (Python, testable)
  - [x] 1.1 Create the retention selection module
    - Add `scripts/backup_retention.py` with a pure function that, given a list of filenames (and their mtimes) on the Backup_Share, returns the set to **delete** to leave the newest 3.
    - Match only the `bf_trader_<UTC-timestamp>.dump` naming pattern; parse the embedded `YYYYMMDDThhmmssZ` UTC timestamp; order descending by parsed UTC timestamp.
    - Fall back to file mtime when the embedded timestamp cannot be parsed; break remaining ties by descending lexicographic filename order so selection is deterministic.
    - Return empty (zero deletions) when 3 or fewer matching artifacts exist; never select non-matching files.
    - Keep this module pure (no filesystem deletes, no process calls) so it is unit-testable in isolation.
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 3.6; 1.2 (sortable-name assumption the ordering relies on)_

  - [ ]* 1.2 Property test: retention bound (keep newest 3)
    - **Property 3: Retention bound**
    - For any set of matching artifacts, the retained set is at most 3 and is exactly the 3 most recent by embedded UTC timestamp.
    - **Validates: Requirements 3.1**

  - [ ]* 1.3 Property test: pattern-match-only selection
    - For any mix of matching and non-matching filenames, no non-matching file is ever selected for deletion.
    - **Property 3: Retention bound** (selection scope)
    - **Validates: Requirements 3.5**

  - [ ]* 1.4 Property test: zero deletions when 3 or fewer artifacts
    - For any input of 3 or fewer matching artifacts, the delete set is empty.
    - **Property 3: Retention bound** (lower-bound safety)
    - **Validates: Requirements 3.6**

  - [ ]* 1.5 Unit tests: mtime fallback and deterministic tie-break
    - Unparseable-timestamp artifacts order by mtime; equal-timestamp-and-mtime artifacts break ties by descending lexicographic filename.
    - _Requirements: 3.3, 3.4_

- [x] 2. Implement the Pi-side backup + retention orchestration
  - [x] 2.1 Add the delete-executor around the retention core
    - Extend `scripts/backup_retention.py` with a thin executor that takes the delete set from task 1.1 and deletes those files, preserving the retained set and surfacing an error indication (non-zero exit / stderr) that identifies any artifact that failed to delete, without aborting the preserved set.
    - _Requirements: 3.7_

  - [ ]* 2.2 Unit tests: delete-failure handling preserves the retained set
    - Simulate a delete failure (e.g. unwritable/locked file) and assert the retained set is preserved and the failing artifact is identified in the error output.
    - _Requirements: 3.7_

  - [x] 2.3 Create the Pi backup script invoked by Rundeck
    - Add `scripts/pi_postgres_backup.sh`: pre-flight check that `/home/mypi/bf_backups` is writable and the SD-card root has at least the configured minimum free space, aborting before any write if not; run `docker exec my_postgres pg_dump -Fc bf_trader` writing `bf_trader_<UTC-timestamp>.dump` (fixed-width, lexicographically sortable UTC name) under `/home/mypi/bf_backups`; never write onto `/mnt/data`.
    - Validate the dump (zero exit code AND non-zero file size); on failure, remove any zero-byte output, exit non-zero, and do NOT invoke retention.
    - Only on a Valid_Dump, invoke the retention executor (task 2.1) via the venv Python.
    - Emit log output and timing to stdout/stderr so Rundeck records outcome, log, and duration.
    - _Requirements: 1.1, 1.2, 2.1, 1.5, 4.1, 3.2_

  - [ ]* 2.4 Unit/integration test: fail-closed invocation gating
    - Drive the script's validation/gating logic (mockable surface) to assert retention is invoked only after a Valid_Dump and skipped on empty/non-zero-exit dumps and on failed pre-flight.
    - **Property 4: Fail-closed pruning**
    - **Validates: Requirements 3.2, 1.4, 1.5, 4.1**

- [x] 3. Define the native Rundeck Backup_Job (weekly schedule)
  - [x] 3.1 Author the Rundeck job definition
    - Create the job definition file (committed under `scripts/` or a `rundeck/` job-export, following existing SP-328 job conventions) that runs `scripts/pi_postgres_backup.sh` on a fixed weekly schedule (day-of-week, time-of-day, Pi local timezone) and surfaces success/failure, log, and duration in Rundeck run history.
    - Ensure a non-zero script exit marks the run failed; a missed/failed run is identifiable as a non-success result.
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.6, 4.5, 4.4_

- [x] 4. Implement the Windows-side pull task (off-Pi retention)
  - [x] 4.1 Create the PowerShell pull script
    - Add `scripts/pull_pi_backups.ps1`: connect to the `[bf_backups]` Samba share as `mypi` within a configured connection timeout; enumerate Dump_Artifacts; copy each artifact not already retained locally to the operator-chosen durable Windows path.
    - After each copy, verify the local byte size equals the source byte size; on mismatch or interrupted copy, treat the artifact as not retained, keep the Pi source authoritative, do not present a partial copy as retained, and surface the verification/transfer failure (non-zero exit / logged error).
    - Skip artifacts already retained with a verified-equal size so repeated runs produce no duplicates; copy every not-yet-retained artifact including those from preceding missed cycles. Never delete, overwrite, or modify artifacts on the Pi. Pull-only — never triggered from the Pi.
    - Apply an independent off-Pi retention policy that keeps at least the 3 most recent local copies.
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 4.2, 4.3, 4.6_

  - [x] 4.2 Create the Windows Task Scheduler registration
    - Add a companion script/snippet (e.g. `scripts/register_pull_pi_backups_task.ps1` or documented `schtasks` command) that registers the pull script to run at a cadence at least as frequent as the weekly backup.
    - _Requirements: 2.2_

- [x] 5. Author the canonical in-repo runbook
  - [x] 5.1 Write `docs/SP-363_postgres_backup_runbook.md`
    - Follow the existing `docs/SP-NNN_short_name.md` pattern. Document: the weekly schedule (day-of-week, time-of-day, timezone); the dump location `/home/mypi/bf_backups`; the keep-last-3 retention policy (exactly 3 most recent retained, older removed, noting the 30-day age alternative was considered and not chosen); the Windows pull procedure as ordered steps with an observable success end state; the restore-into-Local_Dev_TimescaleDB procedure as ordered steps with an observable success end state; the Continuous_Aggregate_Caveat; that the restore uses `pg_restore --clean --if-exists`, replaces the Local_Dev_TimescaleDB contents, and is run only when overwriting local dev data is acceptable.
    - Leave placeholders for the Restore_Rehearsal outcome (pass/fail + date) and the verification evidence, to be filled in by task 7.
    - Cite SP-338 only as the origin of the Backup_Share and the original Continuous_Aggregate_Caveat knowledge, with no other scope attributed to SP-338.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 6.9, 6.10_

- [ ] 6. Deploy and verify the backup job on the Pi (Definition of Done for deployable work)
  - [~] 6.1 Deploy the backup script and Rundeck job to the Pi and verify a real run
    - Sync `scripts/pi_postgres_backup.sh` and `scripts/backup_retention.py` to the Pi and import/enable the Rundeck Backup_Job (via the project deploy mechanism / Rundeck import).
    - Trigger the job on demand and confirm: a new non-empty, correctly named `bf_trader_<UTC-timestamp>.dump` appears under `/home/mypi/bf_backups` and nothing is written to `/mnt/data`; the Rundeck run shows success with log and duration.
    - Seed the share with 4+ dummy pattern-matching dumps, run retention, and confirm exactly the newest 3 remain and only pattern-matching files were touched.
    - Simulate a failing dump (e.g. stop `my_postgres`) and confirm the run is marked failed and no existing dump is pruned (fail-closed observed on real infra).
    - Record the observed outcomes (this is the deploy+verify gate; the job must actually run on the Pi, not just be merged).
    - **Properties 1, 2, 3, 4** (observed end-to-end on the Pi)
    - _Requirements: 1.1, 1.3, 2.1, 3.1, 3.2, 4.1_

  - [~] 6.2 Verify the Windows pull end-to-end against the live share
    - Run `scripts/pull_pi_backups.ps1` against the Pi share: confirm new dumps are copied, sizes match the source, and a second run copies nothing new (idempotence). Confirm no Pi-side artifact is modified.
    - Record the observed outcomes.
    - **Properties 5, 6**
    - _Requirements: 2.2, 2.3, 2.5, 2.7_

- [ ] 7. Perform the one-time restore rehearsal and record evidence
  - [~] 7.1 Restore a scheduled dump into the Local_Dev_TimescaleDB and verify
    - Only after explicitly confirming it is acceptable to overwrite the current Local_Dev_TimescaleDB data, restore exactly one scheduled Dump_Artifact into the running Local_Dev_TimescaleDB with `pg_restore --clean --if-exists`, applying the Continuous_Aggregate_Caveat from the runbook. Do not touch the Pi's production `my_postgres`.
    - On restore error, record a did-not-complete failure outcome in the runbook; on success, record per-restored-object counts, per-table row counts, and MAX timestamp checks for time-series tables, plus the rehearsal pass/fail outcome and date, into `docs/SP-363_postgres_backup_runbook.md` (filling the task 5.1 placeholders).
    - **Property 7: Restorability**
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.6, 6.8_

- [x] 8. Final verification gate (single end-of-feature gate)
  - [x] 8.1 Run the repo's lint, format, and full test gate
    - Run `ruff check .` then `ruff format --check .`, fixing anything flagged (`ruff check --fix .`, `ruff format .`).
    - Then run the full suite with the venv Python (`& "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m pytest`) and ensure it is green, including the new retention property/unit tests.
    - This is the single end-of-feature verification gate (do not run it per sub-task).
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- The only unit-testable logic is the retention selection core (tasks 1.x, 2.1–2.2), so property/unit tests concentrate there; P1, P2, P5, P6, P7 are operational properties verified by the manual/observable tasks (6.x, 7.1), consistent with the design's Testing Strategy.
- Each task references specific requirement acceptance-criteria IDs (R1–R6 numbering) for traceability.
- Task 6 is the deploy+verify-on-Pi step required by the project's Definition of Done for deployable work: the backup script/job must actually run on the Pi, not merely be merged.
- Task 7 (restore rehearsal) is the key acceptance control and must be run only when overwriting the local dev DB is acceptable.
- Task 8 is the single final gate (ruff + full pytest), per the repo's testing steering — not a per-task action.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "2.1", "3.1", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "4.2"] },
    { "id": 3, "tasks": ["2.4", "6.1"] },
    { "id": 4, "tasks": ["6.2"] },
    { "id": 5, "tasks": ["7.1"] },
    { "id": 6, "tasks": ["8.1"] }
  ]
}
```
