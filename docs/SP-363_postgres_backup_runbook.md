# SP-363 — Pi Postgres backup & restore runbook

> **Status:** canonical operational runbook for the automated Postgres backups
> on the Raspberry Pi. This is the in-repo home (not Confluence) for the whole
> backup/restore procedure: the weekly schedule, the dump location, the
> keep-last-3 retention policy, the Windows off-Pi pull, and the
> restore-into-local-dev-TimescaleDB procedure with the TimescaleDB
> continuous-aggregate caveat.
>
> **Scope:** the Betfair capture stack runs Postgres/TimescaleDB (container
> `my_postgres`, database `bf_trader`) on an always-on Raspberry Pi under Docker.
> This runbook documents the automated `pg_dump -Fc` backups into the off-USB
> Backup_Share, their off-Pi retention on the Windows PC, and the one-time
> restore rehearsal that proves recoverability.
>
> **Origin note (SP-338):** the Backup_Share (`/home/mypi/bf_backups`, Samba
> `[bf_backups]`) and the original TimescaleDB continuous-aggregate-on-restore
> caveat knowledge both originate from **SP-338**. SP-338 is cited in this
> runbook *only* as the origin of those two things; no other scope is attributed
> to it. Everything else here is SP-363.

## TL;DR

- A native Rundeck job on the Pi runs `pg_dump -Fc` of `bf_trader` **weekly**
  and writes a timestamped dump into `/srv/bf_backups` (SD-card root, OFF
  the USB disk).
- The Pi keeps **only the 3 most recent** dumps; older ones are pruned. Pruning
  runs **only after a validated, non-empty dump** (fail-closed).
- A Windows PC **pulls** the dumps off the Pi over Samba (pull-only — the Pi
  never pushes) for durable off-Pi retention via `scripts/pull_pi_backups.ps1`.
- Recoverability is proven **once** by restoring a scheduled dump into the
  operator's local dev TimescaleDB with `pg_restore --clean --if-exists`. That
  restore **replaces** the local dev DB contents, so it is run only when
  overwriting local dev data is acceptable.

---

## 1. Weekly backup schedule

| Property | Value |
|---|---|
| **Day of week** | **Sunday** |
| **Time of day** | **03:00** |
| **Timezone** | **Pi local time** (Europe/London) |
| Cadence | Weekly |
| Runs | Native Rundeck Backup_Job on the Pi (not cron; cron is a fallback only) |

The canonical weekly slot is **Sunday 03:00 Pi local time** — a low-traffic
early-morning window. The dump filename embeds a UTC timestamp (see §2), so the
`03:00` local wall-clock fire time will appear in the filename as the
corresponding UTC time (e.g. `03:00Z` in winter, `02:00Z` during BST).

> **Must match the Rundeck job.** This day/time is the canonical value for the
> schedule. It MUST be kept in sync with the actual Rundeck Backup_Job
> definition (SP-363 task 3.1). If the Rundeck job is configured with a
> different day/time, update **this table** so the runbook and the job agree —
> the Rundeck job is the thing that actually fires, and this runbook documents
> what it is set to.

The Rundeck run history records each run's outcome (success/failure), log
output, and duration. A failed `pg_dump` (non-zero exit), a zero-byte output
file, or an unwritable Backup_Share marks the run **failed** and does **not**
prune existing dumps.

---

## 2. Dump location

All dumps are written under:

```
/srv/bf_backups
```

This is the **Backup_Share** — on the Pi's **SD-card root**, deliberately **NOT**
on the USB-mounted `/mnt/data`, so that a USB-disk failure cannot destroy the
backups. It is exposed over Samba as `[bf_backups]` (`valid users = mypi`).

> **Relocated under SP-363.** SP-338 originally created the share at
> `/home/mypi/bf_backups`, but the native Rundeck service runs as the `rundeck`
> user, which cannot traverse `/home/mypi` (mode `700`). The share was therefore
> moved to `/srv/bf_backups`, owned `rundeck:mypi` (setgid, group-writable), and
> the Samba `[bf_backups]` share re-pointed there. It is still on the SD-card
> root (off the USB), still served to `mypi` over Samba, so the Windows pull is
> unchanged.

Each dump (a **Dump_Artifact**) is a single compressed Postgres custom-format
(`-Fc`) archive, named:

```
bf_trader_<UTC-timestamp>.dump      e.g.  bf_trader_20260115T030000Z.dump
```

The timestamp is a fixed-width, lexicographically sortable UTC form
`YYYYMMDDThhmmssZ`. This makes "newest N" selection by filename ordering
consistent between the Pi and the Windows copies.

---

## 3. Retention policy — keep last 3 (fail-closed)

The Pi keeps **exactly the 3 most recent Dump_Artifacts** on the Backup_Share.
After each successful backup run, the Retention_Step selects dumps newest-first
(by embedded UTC timestamp, falling back to file mtime, with a deterministic
lexicographic tie-break), **retains the 3 most recent, and deletes the older
ones**. At most 3 dumps remain after a successful run. Only files matching the
`bf_trader_<UTC-timestamp>.dump` pattern are ever touched — unrelated files on
the share are left alone.

Retention is **fail-closed**: it runs **only after a validated, non-empty dump**.
A failed or zero-byte run never triggers pruning, so a bad run can never delete
good dumps.

> **Why keep-last-3 and not a 30-day age rule?** A 30-day age-based retention
> policy was **considered and NOT chosen**. Count-based "keep last 3" always
> retains the most recent successful dumps with a bounded, predictable footprint
> regardless of how reliably the job fires. The 30-day age rule was rejected as
> the primary policy because **a stalled scheduler could age out every dump and
> leave the share empty** — with keep-last-3, the 3 most recent dumps are never
> aged out, so a stall cannot silently empty the share.

The Windows pull (§4) keeps its **own, independently-retained** copies off-Pi,
so longer history can be kept off the Pi even though the Pi itself holds only 3.

---

## 4. Windows pull procedure (off-Pi retention)

The Windows PC pulls dumps off the Pi over the Samba share for durable off-Pi
retention. This is **pull-only**: the Pi never initiates the transfer. The pull
is performed by `scripts/pull_pi_backups.ps1` (registered to run on Windows Task
Scheduler at a cadence at least as frequent as the weekly backup).

**Ordered steps:**

1. Ensure the Pi is online and the `[bf_backups]` Samba share is reachable.
2. Run the pull script from the Windows PC:
   ```powershell
   powershell -File scripts\pull_pi_backups.ps1
   ```
   (Normally this runs automatically via the registered Task Scheduler task; the
   manual invocation above is for an on-demand pull.)
3. The script authenticates to `[bf_backups]` as `mypi` and **enumerates** the
   Dump_Artifacts on the share.
4. For each Dump_Artifact **not already retained locally** with a verified-equal
   size, the script **copies** it to the operator-chosen durable Windows path.
5. After each copy, the script **verifies** the local copy's byte size equals the
   source size on the share. On mismatch or an interrupted copy, the artifact is
   treated as **not retained** (the Pi source stays authoritative) and the
   failure is surfaced.
6. Already-retained dumps (verified-equal size) are **skipped**, so repeated runs
   produce no duplicates. Every not-yet-retained dump is copied, including any
   produced during preceding missed cycles.
7. The script applies its own independent off-Pi retention (keeping at least the
   3 most recent local copies).

**Observable success end state:** every Dump_Artifact currently on the Pi share
exists as a byte-size-verified local copy in the durable Windows path; a second
run of the script copies **nothing new** (idempotent); and no artifact on the Pi
has been modified, deleted, or overwritten.

---

## 5. Restore into the local dev TimescaleDB

This procedure restores a scheduled dump into the operator's **existing
persistent local dev TimescaleDB** — a real Docker container on the dev machine
set up identically to the Pi's `my_postgres` (same image/config). It both proves
the dumps are recoverable end-to-end and leaves a usable `bf_trader` copy on the
dev machine for local development and data-analysis work.

> **This OVERWRITES the local dev DB.** The restore uses
> `pg_restore --clean --if-exists`, which **replaces** the Local_Dev_TimescaleDB's
> current objects and data with the dump's contents. Run this procedure **only
> when overwriting whatever is currently in the local dev DB is acceptable.** It
> never touches the Pi's production `my_postgres`.

**Ordered steps:**

1. **Confirm it is acceptable to overwrite** the current local dev DB contents.
   If overwriting is not acceptable, STOP — do not begin the restore.
2. Ensure the local dev TimescaleDB container is **running**.
3. Select exactly **one** scheduled Dump_Artifact from the durable off-Pi store
   (a `bf_trader_<UTC-timestamp>.dump` pulled by §4).
4. Restore it with `pg_restore --clean --if-exists` into the local dev DB, e.g.:
   ```bash
   pg_restore --clean --if-exists --no-owner --disable-triggers \
     -U postgres -d bf_trader \
     bf_trader_<UTC-timestamp>.dump
   ```
   (`--clean --if-exists` drops existing objects before recreating them, so it
   replaces the current local dev contents.)
5. Apply the **Continuous_Aggregate_Caveat** handling (§6) for any TimescaleDB
   continuous aggregates encountered during the restore.
6. **Verify** the restored data: capture per-restored-object counts, per-table
   row counts, and `MAX(timestamp)` checks for the time-series tables.
7. Record the rehearsal outcome and the verification evidence in §7 / §8 below.

**Observable success end state:** `pg_restore` completes without a
restore-preventing error; the local dev `bf_trader` database contains the
expected objects and tables; the per-table row counts and `MAX(timestamp)`
values match expectations for the chosen dump; and the local dev DB now holds a
usable `bf_trader` copy for dev/data work.

If `pg_restore` terminates with an error that prevents a complete restore,
record a **did-not-complete failure** outcome in §7 with the error detail.

---

## 6. TimescaleDB continuous-aggregate caveat

> **Continuous_Aggregate_Caveat.** TimescaleDB **continuous aggregates** need
> special care on restore — they do not always restore cleanly from a plain
> `pg_dump`/`pg_restore` the way ordinary tables do, because a continuous
> aggregate is a materialized view backed by TimescaleDB-internal objects and
> refresh state rather than a simple table. A naive restore can leave the
> continuous aggregates missing, empty, or not refreshing.

This caveat's knowledge **originates from SP-338** (that is the only scope SP-338
is cited for here). It is carried forward into this SP-363 runbook as the
canonical place the restore-time handling is documented.

**Handling during a restore (§5 step 5):**

- Expect continuous aggregates to require attention beyond the base-table
  restore; do not assume they are correct just because `pg_restore` exited 0.
- After the restore, confirm each continuous aggregate exists and, if required,
  **refresh** it (e.g. `CALL refresh_continuous_aggregate(...)`) so its
  materialized data is rebuilt from the restored hypertable data.
- Include the continuous aggregates in the verification evidence (§5 step 6):
  check their row counts and `MAX(timestamp)` alongside the base time-series
  tables so the rehearsal confirms they came back correctly.
- The **restore rehearsal (§7) is the control** that surfaces any
  continuous-aggregate issue before a real disaster. Any extra steps needed to
  get the continuous aggregates healthy are recorded as part of the rehearsal
  outcome.

---

## 7. Restore rehearsal outcome

- **Outcome:** **PASS** — the dump restored end-to-end into the local dev
  TimescaleDB and reproduced the source faithfully.
- **Date:** 2026-10-22
- **Dump restored:** `bf_trader_20261022T160005Z.dump` (1,239,866 bytes,
  `PostgreSQL custom database dump - v1.15-0`), produced by the first real
  Rundeck run on the Pi and pulled off-Pi to the Windows durable store.
- **Restore command used:**
  `pg_restore --clean --if-exists --no-owner --disable-triggers -U postgres -d bf_trader /tmp/restore.dump`
  (run inside the local dev `my_postgres` container after `docker cp` of the dump).
- **Continuous-aggregate handling applied:** the dump emitted a circular
  foreign-key warning on the `_timescaledb_catalog` tables at dump time (the
  Continuous_Aggregate_Caveat, §6). `--disable-triggers` was used on restore to
  load those catalog tables without the circular-FK checks blocking the load.
  No continuous aggregates actually exist in `bf_trader` yet (see §8), so no
  `refresh_continuous_aggregate` step was needed — the catalog tables restored
  clean and empty, matching the source.
- **Notes:** the restore targeted the operator's persistent local dev
  TimescaleDB (identical image `timescale/timescaledb:latest-pg16` to the Pi),
  overwriting its prior (empty) `bf` tables, per explicit operator confirmation.
  The Pi's production `my_postgres` was not touched.

---

## 8. Verification evidence

Evidence captured from the restored local dev `bf_trader` on 2026-10-22,
compared against the live source on the Pi.

- **Per-restored-object counts (restored local dev, `bf` schema):** 6 tables;
  0 indexes; 0 constraints; 0 sequences. This matches the source Pi DB, which
  also has no indexes/constraints/sequences on `bf` (the `bf` tables are plain
  tables — a schema observation, not a restore defect).
- **Per-table row counts (restored vs source Pi):**

  | Table | Restored (local dev) | Source (Pi, live) |
  |---|---|---|
  | `market_table` | 35,287 | 35,287 |
  | `log_file` | 4,408 | 4,414 |
  | `target` | 54 | 54 |
  | `betfair_object_ids` | 3 | — |
  | `quality_run` | 38 | — |
  | `quality_match_result` | 49 | — |

  `market_table` and `target` match exactly. `log_file` differs by 6 rows
  because the live Pi kept logging after the 16:00 dump was taken; the restored
  copy correctly matches the dump's point-in-time, not the later live state.
- **MAX(timestamp) per time-series table (restored local dev):**
  - `bf.market_table.timestamp` = `2026-10-22 14:15:35.447103+00`
  - `bf.log_file.timestamp` = `2026-10-22 15:45:02.106376+00`
  - `bf.target.start_time` = `2026-10-24 16:30:00+00` (forward-dated scheduled targets)
  All consistent with a dump taken at `2026-10-22T16:00:05Z`.
- **Continuous-aggregate row counts / MAX(timestamp):** none present —
  `timescaledb_information.hypertables` and
  `timescaledb_information.continuous_aggregates` both return 0 rows in the
  restored DB **and** in the source Pi DB. The TimescaleDB extension is
  installed and its `_timescaledb_catalog` tables restored (empty), but no
  hypertables or continuous aggregates have been created in `bf_trader` yet, so
  there was nothing to rematerialize. The Continuous_Aggregate_Caveat handling
  (`--disable-triggers`) is retained in the documented procedure so it is
  already in place for when continuous aggregates are added.
