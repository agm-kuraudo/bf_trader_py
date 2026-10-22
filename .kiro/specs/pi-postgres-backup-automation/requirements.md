# Requirements Document

> Tracked by **SP-363** — _Automate Postgres backups on the Pi (scheduled pg_dump + off-Pi retention)_.
> Derived from the approved `design.md` for this feature. Builds on **SP-338** (Backup_Share + continuous-aggregate caveat origin) and **SP-328** (native Rundeck / capture infra).

## Introduction

The Betfair capture stack runs Postgres/TimescaleDB (container `my_postgres`, database `bf_trader`) on an always-on Raspberry Pi under Docker. Backups are currently manual-only, so any Pi/SD/USB failure loses everything captured since the last hand-run dump. This feature automates recurring backups and durable off-Pi retention, and proves recoverability once.

A native Rundeck job on the Pi runs `pg_dump -Fc` of `bf_trader` weekly, writing compressed custom-format dumps into the existing off-USB Backup_Share. A Pi-side retention step keeps the last 3 dumps and fails closed (it prunes only after a validated, non-empty dump). A Windows PC pulls the dumps off the Pi over the existing Samba share (pull-only; the Pi never pushes) for durable off-Pi retention. A one-time restore rehearsal into the operator's existing persistent local dev TimescaleDB proves the dumps are recoverable end-to-end and leaves a usable `bf_trader` copy for local dev/data work. The canonical operational documentation is produced as a new in-repo markdown runbook, not in Confluence.

These requirements capture WHAT the feature must do; the technical approach (native Rundeck, Samba, `-Fc`, keep-last-3) is fixed by `design.md` and is not re-opened here.

## Glossary

- **Backup_Job**: The native Rundeck job on the Pi that runs `pg_dump -Fc` of `bf_trader` on a weekly schedule (SP-328 provides the native Rundeck scheduler).
- **Retention_Step**: The Pi-side mechanism, invoked by the Backup_Job, that prunes dumps on the Backup_Share to the newest 3.
- **Backup_Share**: The existing off-USB landing zone `/home/mypi/bf_backups` on the Pi's SD-card root (NOT `/mnt/data`), exposed over Samba as `[bf_backups]` (defined by SP-338).
- **Dump_Artifact**: A single compressed `pg_dump -Fc` output file named `bf_trader_<UTC-timestamp>.dump`, source database `bf_trader` in container `my_postgres`.
- **Valid_Dump**: A Dump_Artifact produced by a zero-exit `pg_dump` whose output file is non-empty (greater than 0 bytes).
- **Windows_Pull_Task**: The scheduled task on the operator's Windows PC that reads the Backup_Share over Samba and copies new dumps to durable Windows storage. Pull-only — the Pi never initiates the transfer.
- **Off_Pi_Copy**: A copy of a Dump_Artifact retained on durable Windows storage by the Windows_Pull_Task.
- **Local_Dev_TimescaleDB**: The operator's existing persistent local dev TimescaleDB Docker container on the dev machine, set up identically to the Pi's `my_postgres` (same image/config).
- **Restore_Rehearsal**: The one-time operator-run restore of a scheduled dump into the Local_Dev_TimescaleDB via `pg_restore --clean --if-exists`, proving recoverability and leaving a usable local dataset.
- **Continuous_Aggregate_Caveat**: The TimescaleDB continuous-aggregate-on-restore handling first recorded under SP-338 and carried forward into this feature's runbook.
- **Repo_Runbook**: The new in-repo markdown runbook `docs/SP-363_postgres_backup_runbook.md` that is this feature's canonical operational documentation (not Confluence).

## Requirements

### Requirement 1: Scheduled weekly backup

**User Story:** As the Pi operator, I want a scheduled job that dumps `bf_trader` weekly, so that recent capture data is backed up automatically without manual runs.

#### Acceptance Criteria

1. WHEN the weekly schedule fires, THE Backup_Job SHALL run `pg_dump -Fc` of database `bf_trader` in container `my_postgres` and write exactly one Dump_Artifact to the Backup_Share.
2. THE Backup_Job SHALL name each Dump_Artifact `bf_trader_<UTC-timestamp>.dump` using a fixed-width, lexicographically sortable UTC representation of the timestamp so that newest-N selection by filename ordering matches between the Pi and the Windows copies.
3. WHEN the Backup_Job completes a run, THE Backup_Job SHALL record the run outcome as success or failure, together with the log output and the run duration, in Rundeck run history.
4. IF the `pg_dump` command exits with a non-zero exit code, THEN THE Backup_Job SHALL mark the run failed in Rundeck run history.
5. IF the `pg_dump` command produces a zero-byte output file, THEN THE Backup_Job SHALL mark the run failed in Rundeck run history and SHALL NOT retain the zero-byte output file as a Dump_Artifact.
6. IF the Backup_Share is unreachable or unwritable at the time the Dump_Artifact is to be written, THEN THE Backup_Job SHALL mark the run failed in Rundeck run history.

### Requirement 2: Durable dump storage and off-Pi retention

**User Story:** As the Pi operator, I want dumps stored off the USB disk and copied to my Windows PC, so that backups survive a USB failure or total loss of the Pi.

#### Acceptance Criteria

1. THE Backup_Job SHALL write every Dump_Artifact under `/home/mypi/bf_backups` on the SD-card root and SHALL NOT write any Dump_Artifact onto `/mnt/data`.
2. WHEN the Windows_Pull_Task runs, THE Windows_Pull_Task SHALL read the `[bf_backups]` Backup_Share and copy each Dump_Artifact that is not already retained locally to durable Windows storage.
3. WHEN the Windows_Pull_Task copies a Dump_Artifact, THE Windows_Pull_Task SHALL verify that the byte size of the local Off_Pi_Copy equals the byte size of the source file on the Backup_Share.
4. IF the byte size of a copied Off_Pi_Copy does not equal the byte size of its source on the Backup_Share, THEN THE Windows_Pull_Task SHALL treat that Dump_Artifact as not retained, retain the source as the authoritative copy, and surface an indication that the copy failed verification.
5. WHEN the Windows_Pull_Task runs against a Dump_Artifact it has already retained with a verified-equal byte size, THE Windows_Pull_Task SHALL skip copying that Dump_Artifact so that repeated runs produce no duplicate Off_Pi_Copy.
6. THE Windows_Pull_Task SHALL apply its own off-Pi retention policy that retains at least the 3 most recent Dump_Artifacts and SHALL apply this policy independently of the Pi-side keep-last-3 policy.
7. THE Pi SHALL NOT initiate any transfer of Dump_Artifacts to the Windows PC.

### Requirement 3: Pi-side retention (keep last 3, fail-closed)

**User Story:** As the Pi operator, I want old dumps pruned to a bounded set, so that the Backup_Share does not grow unbounded while the most recent recoverable dumps are preserved.

#### Acceptance Criteria

1. WHEN the Retention_Step runs after a Valid_Dump, THE Retention_Step SHALL select Dump_Artifacts in descending order by embedded UTC timestamp, retain the 3 most recent, and delete the older ones, leaving at most 3 Dump_Artifacts on the Backup_Share.
2. IF a run does not produce a Valid_Dump, THEN THE Backup_Job SHALL NOT invoke the Retention_Step so that existing Dump_Artifacts are left unchanged.
3. WHEN a Dump_Artifact's embedded UTC timestamp cannot be parsed, THE Retention_Step SHALL order that Dump_Artifact by its file mtime.
4. WHEN two Dump_Artifacts sort equally by embedded UTC timestamp and file mtime, THE Retention_Step SHALL break the tie by descending lexicographic filename order so that the selection is deterministic.
5. THE Retention_Step SHALL select only files matching the `bf_trader_<UTC-timestamp>.dump` naming pattern and SHALL leave unrelated files on the Backup_Share untouched.
6. WHEN 3 or fewer matching Dump_Artifacts exist on the Backup_Share, THE Retention_Step SHALL perform zero deletions.
7. IF deleting an older Dump_Artifact fails, THEN THE Retention_Step SHALL preserve the retained set and surface an error indication identifying the Dump_Artifact that failed to delete.

### Requirement 4: Fault handling and visibility

**User Story:** As the Pi operator, I want failures to be safe and visible, so that a bad run never destroys good backups and I can detect missed runs.

#### Acceptance Criteria

1. IF the Backup_Share is unwritable or the SD-card root has less free space than the configured minimum free-space threshold when a dump is written, THEN THE Backup_Job SHALL abort the run before writing any Dump_Artifact, SHALL mark the run failed in Rundeck run history, SHALL NOT invoke the Retention_Step, and SHALL leave previously retained Dump_Artifacts unchanged.
2. IF the Windows_Pull_Task cannot reach the Samba share within the configured connection timeout during a cycle, THEN THE Windows_Pull_Task SHALL mark that cycle failed and SHALL NOT delete, overwrite, or modify any Dump_Artifact on the Pi.
3. WHEN a Windows_Pull_Task cycle reaches the Backup_Share, THE Windows_Pull_Task SHALL copy every Dump_Artifact present on the Backup_Share that is not yet retained locally, including any Dump_Artifacts produced during one or more preceding missed cycles.
4. WHILE the weekly schedule is not firing, THE Backup_Share SHALL retain its most recent set of retained Dump_Artifacts without deletion, so that the Backup_Share holds at least one Dump_Artifact whenever at least one successful run has previously completed.
5. WHEN a run fails or a scheduled run does not start at its scheduled time, THE Backup_Job SHALL record the outcome as a non-success result in Rundeck run history, including an indication identifying the run as failed or missed.
6. IF a Dump_Artifact copy during a Windows_Pull_Task cycle is interrupted before completion, THEN THE Windows_Pull_Task SHALL mark that cycle failed and SHALL NOT present the partially copied Dump_Artifact as retained locally.

### Requirement 5: Restore rehearsal into the local dev TimescaleDB

**User Story:** As the operator, I want to restore a scheduled dump into my local dev TimescaleDB once, so that I prove the dumps are recoverable end-to-end and gain a usable `bf_trader` copy for local dev and data-analysis work.

#### Acceptance Criteria

1. WHERE the operator has explicitly confirmed overwriting the current Local_Dev_TimescaleDB data before the restore begins, THE Restore_Rehearsal SHALL restore exactly one scheduled Dump_Artifact into the Local_Dev_TimescaleDB using `pg_restore --clean --if-exists`, replacing the Local_Dev_TimescaleDB's current contents with a usable `bf_trader` copy.
2. IF the operator has not confirmed overwriting the current Local_Dev_TimescaleDB data, THEN THE Restore_Rehearsal SHALL NOT begin and SHALL leave the Local_Dev_TimescaleDB contents unchanged.
3. THE Restore_Rehearsal SHALL NOT modify the Pi's production `my_postgres` container.
4. WHEN continuous aggregates are encountered during the Restore_Rehearsal, THE Restore_Rehearsal SHALL apply the Continuous_Aggregate_Caveat handling documented in the Repo_Runbook.
5. IF the Restore_Rehearsal terminates with a `pg_restore` error that prevents a complete restore, THEN THE Restore_Rehearsal SHALL report an error indicating the restore did not complete and SHALL record the failure outcome in the Repo_Runbook.
6. WHEN the Restore_Rehearsal completes successfully, THE operator SHALL record verification evidence comprising per-restored-object counts, per-table row counts, and MAX timestamp checks for time-series tables, together with the rehearsal outcome, in the Repo_Runbook.

### Requirement 6: Canonical in-repo runbook

**User Story:** As a future maintainer, I want one in-repo runbook describing the whole backup/restore procedure, so that the schedule, storage, retention, pull, and restore steps are documented where the code lives.

#### Acceptance Criteria

1. THE feature SHALL produce the Repo_Runbook at `docs/SP-363_postgres_backup_runbook.md` following the existing `docs/SP-NNN_short_name.md` pattern.
2. THE Repo_Runbook SHALL document the weekly schedule including day-of-week, time-of-day, and timezone.
3. THE Repo_Runbook SHALL document the dump location `/home/mypi/bf_backups`.
4. THE Repo_Runbook SHALL document the keep-last-3 retention policy, stating that exactly the 3 most recent Dump_Artifacts are retained and older ones removed, and noting that the 30-day age alternative was considered and not chosen.
5. THE Repo_Runbook SHALL document the Windows pull procedure for off-Pi retention as ordered steps with an observable success end state.
6. THE Repo_Runbook SHALL document the restore-into-Local_Dev_TimescaleDB procedure as ordered steps with an observable success end state.
7. THE Repo_Runbook SHALL document the Continuous_Aggregate_Caveat.
8. THE Repo_Runbook SHALL document the Restore_Rehearsal outcome as pass or fail together with the date.
9. THE Repo_Runbook SHALL record that the restore uses `pg_restore --clean --if-exists`, replaces the Local_Dev_TimescaleDB contents, and is run only when overwriting local dev data is acceptable.
10. IF SP-338 is referenced in the Repo_Runbook, THEN THE Repo_Runbook SHALL cite SP-338 only as the origin of the Backup_Share and the original Continuous_Aggregate_Caveat knowledge, with no other scope attributed to SP-338.
