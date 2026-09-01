# Requirements Document

## Introduction

This feature stands up reliable **background Betfair data capture** for the newly started football season, tracked under Jira ticket **SP-328 "Stand up background data capture for the new season"** (Side Projects / SP, label: `betfair`, fixVersion *Betfair Trader 0.6*).

The season is already running, and captured odds data is **perishable**: any period without capture is a permanent, unrecoverable gap. The priority is therefore to get capture running early and reliably in the background — on the always-on Raspberry Pi 500 — while other VPA work proceeds in parallel.

The capture system is the existing `bf_trader_py` project: three Python services (`target_service`, `monitor_service`, `analyse_service`) that identify football `MATCH_ODDS` targets, track their odds over time, and store results in PostgreSQL. Scheduling is currently handled by Rundeck installed natively on the Pi. Docker itself works fine on the Pi — Postgres, pgAdmin, Vault and the capture app all run in Docker on the `my_trading_network` user-defined network; it was only the attempt to *containerise Rundeck* that failed (no ARM image / poor performance), so Rundeck runs natively instead.

**Current deployment state (verified on the Pi via the SP-329 spike, 2026-09-01):** Rundeck is running natively and its scheduled jobs are firing, but capture has been broken for well over a year — the current named capture container (`bf_monitor_service`) last ran on 2026-03-25 (exit 1), and older `docker run`-style capture containers stop at 2025-05-09. The root cause is a **Vault connectivity failure at startup**: the deployed (~15-month-old) image still depends on HashiCorp Vault and tries to reach it at hostname `my_vault`, which does not resolve. The Vault container is actually named `my_keyvault` and sits on the default `bridge` network rather than `my_trading_network`, so it is unreachable by name from the capture container. The failure is raised in `BFDriver.__init__` → `Vault()`, **before** any Betfair or Postgres work, so nothing is ever written to the Data_Store — the DB connection itself is fine. Vault has since been **removed from the codebase** in favour of a `.env` loader (`api.auth.dotenv_loader.DotenvLoader`), but the running artefact is a baked Docker image that has never been rebuilt, so the retired Vault code still executes. SP-329 (Spike: Map and document the existing Betfair capture deployment on the Pi) mapped this and recommends a **rebuild/redeploy with current code** rather than repairing the Vault wiring. SP-328 stands up reliable, verified capture on that basis.

The broader epic goal is to **simplify**. For this ticket the scheduling mechanism is settled: SP-329 confirmed the existing native Rundeck already works and only the capture job's dependencies were broken, so SP-328 **retains the existing native Rundeck** (see Requirement 3) rather than re-evaluating schedulers. **SP-294 (Remove Rundeck — replace with simple manual run scripts)** may replace or descope Rundeck later on its own timeline; that is independent of SP-328 and the two do not need to be reconciled here. In keeping with the user's standing notes, the simplest reliable option wins: the goal is dependable perishable-data capture, not a polished platform.

**Platform target:** Linux on the Raspberry Pi 500 (ARM). The user's work PC is Windows but powers off around 5PM daily and is explicitly **not** the capture host, because football markets are typically evening/weekend.

## Glossary

- **Rundeck**: Scheduling/automation tool currently used to run the capture services on a schedule. Installed **natively** on the Raspberry Pi 500 (not in Docker) — not because Docker is unworkable on the Pi (it runs the datastore, pgAdmin, Vault and the capture app fine), but because the attempt to *containerise Rundeck itself* failed (no ARM image / poor performance). Provides a web UI for run logs and failure visibility.
- **Vault**: Legacy HashiCorp Vault secrets store. The ~15-month-old deployed image reaches for it at hostname `my_vault`, but the running container is named `my_keyvault` and sits on the `bridge` network rather than `my_trading_network`, so it is unreachable by name — the direct cause of the capture gap. Vault has been removed from the current codebase in favour of a `.env` loader, so it is to be retired (container plus its "Unlock the Vault" Rundeck job) under this ticket.
- **Baked Image / Build-Deploy Pipeline**: The capture container runs a Docker image (`agm-karaudo/betfair_app_01`) baked ~15 months ago and never rebuilt. There is no working path from committed code to running code: the Pi cannot `git pull` (broken SSH deploy key), nothing rebuilds the image, and the schedule only `docker start`s the existing container. Restoring a repeatable code-sync → rebuild → recreate flow is in scope for this ticket.
- **MATCH_ODDS**: The Betfair market type for football match-result betting. The default scope of what capture tracks.
- **Target_Service**: The Python service that identifies football `MATCH_ODDS` betting targets based on strategy.
- **Monitor_Service**: The Python service that tracks odds over time for identified targets and lands the data in PostgreSQL.
- **Analyse_Service**: The Python service that analyses gathered odds data. Not required to run on the capture cadence.
- **Perishable Data**: Odds data that can only be captured live as markets move. Any gap in capture is permanent and unrecoverable — historical odds cannot be back-filled.
- **Always-On Host**: The Raspberry Pi 500, which runs continuously and is therefore the required host for background capture (as opposed to the work PC, which powers off).
- **Capture**: The end-to-end flow of identifying targets, monitoring their odds over time, and persisting the results to PostgreSQL.
- **Data Store**: The PostgreSQL instance where captured odds are persisted. Confirmed by SP-329 as the container `my_postgres` (image `postgres:16.1`) at 172.19.0.3 on `my_trading_network`, database `bf_trader`. It is reachable and not the cause of the gap — capture fails before any DB write (see Current Deployment State).

## Requirements

### Requirement 1: Data Storage Prerequisite (Foundational)

**User Story:** As the operator of the trading system, I want the PostgreSQL data store confirmed installed, running, reachable, and schema-ready on the Raspberry Pi, so that captured odds have somewhere to land before capture is switched on.

*Note: This requirement is foundational and blocks all others. Capture cannot be considered stood up until the Data Store is confirmed working. SP-329 confirmed PostgreSQL runs as the container `my_postgres` at 172.19.0.3 on `my_trading_network` (DB `bf_trader`) and is reachable — it is not the cause of the gap (capture fails at Vault startup before any DB write). What remains to confirm is its schema-readiness for the current code once that code is actually redeployed.*

#### Acceptance Criteria

1. THE Data_Store SHALL be verified as installed and running on the Always-On Host before capture is enabled.
2. WHEN the Data_Store is checked, THE System SHALL confirm the Data_Store is reachable using the connection details (host, port, database name, user, and password) read from the project `.env` configuration, treating the Data_Store as unreachable if a connection is not established within 10 seconds.
3. IF one or more of the required connection details (host, port, database name, user, or password) are missing from the project `.env` configuration, THEN THE System SHALL treat capture as not stood up and SHALL surface to the operator an error indicating which connection details are missing.
4. THE System SHALL confirm that the database tables required for odds capture, as enumerated in the season-background-data-capture design, exist in the Data_Store.
5. IF a required capture table is absent, THEN THE System SHALL create only the absent required table before capture is enabled, leaving existing tables and their data unchanged.
6. THE season-background-data-capture design SHALL record that the Data_Store runs as the existing Docker container `my_postgres` on `my_trading_network` (confirmed working by SP-329), and SHALL NOT propose migrating it off Docker on the basis of any assumed Docker limitation on the Pi (no such limitation exists; only containerising Rundeck failed).
7. IF the Data_Store cannot be confirmed running and reachable within the 10-second connection timeout, THEN THE System SHALL leave capture disabled and SHALL surface to the operator an error indicating the Data_Store is unreachable.

### Requirement 2: Minimum Viable Capture Defined and Running

**User Story:** As the operator of the trading system, I want a defined minimum viable capture running on the always-on Raspberry Pi, so that live odds accrue during the season without depending on the work PC.

#### Acceptance Criteria

1. THE season-background-data-capture design SHALL document the captured scope as the markets, events, and odds selections handled by the existing football `MATCH_ODDS` `Target_Service` to `Monitor_Service` flow, and SHALL use that flow as the default with no additional markets or event types unless explicitly listed.
2. THE season-background-data-capture design SHALL document the capture cadence as a set of named update-frequency tiers selected by time-to-event, where each tier maps to a fixed interval between successive odds updates for a target, such that two testers reading the design select the same interval for the same time-to-event.
3. THE Capture SHALL run on the Always-On Host, defined as a host that remains powered on and available 24 hours per day, 7 days per week, excluding only unplanned outages.
4. THE Capture SHALL run to completion without any dependency on the work PC, which is unavailable outside its operating window (powered off at approximately 17:00 local time daily), such that a capture run started while the work PC is powered off still persists odds.
5. WHEN a Capture run reaches a target due for update, THE Monitor_Service SHALL persist the captured odds for that target's runners to the Data_Store.
6. IF persisting captured odds to the Data_Store fails for a target, THEN THE Monitor_Service SHALL record the failure and continue processing the remaining targets without terminating the run.
7. WHILE the football season is active, THE Capture SHALL run on the defined cadence tiers with no scheduled downtime, such that no gap in persisted odds is attributable to a planned pause of the Capture.
8. WHEN the Always-On Host restarts, THE Capture SHALL resume on the defined cadence without manual intervention.

### Requirement 3: Scheduling Mechanism (Retain Existing Native Rundeck)

**User Story:** As the operator of the trading system, I want capture to keep running on the existing native Rundeck scheduler on the Pi, so that recurring capture is restored with the least change, since Rundeck already works and only the capture job's dependencies were broken.

*Note: SP-329 confirmed Rundeck runs natively on the Pi (`rundeckd`, enabled at boot) and its jobs fire on schedule — the scheduler was never the fault; the capture job failed on the Vault dependency (see Requirement 7/8). This ticket therefore retains Rundeck rather than re-evaluating schedulers. SP-294 (Remove Rundeck — replace with simple manual run scripts) may later replace or descope Rundeck independently; that decision does not affect SP-328 and the two need not be reconciled here.*

#### Acceptance Criteria

1. THE season-background-data-capture design SHALL retain the existing native Rundeck installation on the Always-On Host as the scheduling mechanism, without migrating to an alternative scheduler.
2. WHEN the retained Rundeck scheduler reaches a scheduled trigger point on the defined cadence, THE scheduling mechanism SHALL start the Capture within 60 seconds of that scheduled time.
3. IF a scheduled trigger fires while a previous Capture invocation is still running, THEN THE scheduling mechanism SHALL skip the new invocation and record that the run was skipped, so that concurrent Capture executions do not overlap.
4. IF a Capture invocation exits with a non-zero status, THEN THE scheduling mechanism SHALL record the failure with a timestamp and the exit status, and SHALL leave the defined cadence unchanged so that the next scheduled trigger still fires.
5. THE season-background-data-capture design SHALL update the retained Rundeck capture job so that it deploys/starts the rebuilt current-code container (per Requirement 7) rather than `docker start`ing the stale baked container.

### Requirement 4: Visibility and Failure Detection

**User Story:** As the operator of the trading system, I want to see run logs and know when a scheduled run has failed, so that I retain the visibility Rundeck's web UI previously provided and can act on silent stalls.

#### Acceptance Criteria

1. WHEN a scheduled Capture run starts, THE scheduling mechanism SHALL record a run log entry containing the run start timestamp, the run completion timestamp, and the run outcome (success or failure), retained for at least 30 days and locatable by the operator from a single documented location.
2. WHEN a scheduled Capture run fails, THE System SHALL record the failure outcome and failure reason in the run log within 60 seconds of the failure occurring.
3. IF a scheduled Capture run fails or does not complete within its expected run window, THEN THE System SHALL produce an operator-detectable failure indication that identifies the affected run without requiring the operator to inspect internal system state.
4. IF a scheduled Capture run does not start at its scheduled time, THEN THE System SHALL make the missed run detectable to the operator through the same failure indication mechanism rather than stalling silently.
5. THE season-background-data-capture design SHALL record the chosen log-visibility and failure-detection approach as an explicit, dated decision selected from at least the following options: log files plus notification, logs only, or system journal.
6. THE chosen visibility approach SHALL enable the operator to locate and read run logs and identify every failed or missed run over the retention period, matching at minimum the log-access and failure-identification capability that Rundeck's web UI provided.

### Requirement 5: Recurring Data Verification

**User Story:** As the operator of the trading system, I want a recurring way to confirm data is actually landing, so that silent stalls in capture are detectable through evidence of data freshness.

#### Acceptance Criteria

1. WHILE the capture process is enabled, THE System SHALL check that captured odds are landing in the Data_Store at a fixed interval no greater than 15 minutes.
2. WHEN a capture freshness check runs, THE System SHALL report the timestamp of the most recently stored odds record and the elapsed time in seconds since that record was stored.
3. IF the elapsed time since the most recently stored odds record exceeds the expected capture cadence of 15 minutes, THEN THE System SHALL raise a stall alert to the operator that identifies the affected data source and the elapsed time since the last stored record.
4. IF a capture freshness check cannot query the Data_Store, THEN THE System SHALL raise an alert to the operator indicating the Data_Store is unreachable and SHALL retain the timestamp of the last successful check.
5. IF the Data_Store contains no odds records at the time of a capture freshness check, THEN THE System SHALL raise a stall alert to the operator indicating that no captured odds are present.

### Requirement 6: Documentation

**User Story:** As the operator of the trading system, I want a short Confluence note on checking and restarting capture, so that the ticket definition of done and the workflow documentation requirement are met.

#### Acceptance Criteria

1. WHEN the operator creates the Confluence note in the Side Projects space, THE Confluence note SHALL document a status-check procedure that produces output distinguishing a "running" result (Capture process is active) from a "not running" result (Capture process is absent or stopped).
2. THE Confluence note SHALL document an ordered restart procedure listing each start step in sequence, followed by a post-restart verification step that re-runs the status check from criterion 1 and confirms a "running" result.
3. IF the status check from criterion 1 produces a "not running" result, THEN THE Confluence note SHALL direct the operator to execute the ordered restart procedure from criterion 2.
4. WHEN the Confluence note is published, THE operator SHALL link the note from the SP-328 ticket before the ticket is transitioned to Done.

### Requirement 7: Build and Deploy Pipeline Restored

**User Story:** As the operator of the trading system, I want a working, repeatable path from committed code to running code on the Pi, so that current code (with Vault already removed) actually executes and future fixes can be deployed without hand-surgery.

*Note: SP-329 found three independent broken links between "code committed" and "code running": the Pi cannot `git pull` (broken SSH deploy key), nothing rebuilds the ~15-month-old baked image `agm-karaudo/betfair_app_01`, and the Rundeck job only `docker start`s the existing container rather than recreating it from a new image. Any one alone means latest code never runs. This scope is kept within SP-328 by operator choice, though it could reasonably be a separate ticket.*

#### Acceptance Criteria
1. THE System SHALL provide a code-sync mechanism that retrieves the current repository code onto the Always-On Host, and the design SHALL record the chosen sync method.
2. THE design SHALL define a repeatable deploy that syncs current code, rebuilds the capture image, and recreates the capture container on `my_trading_network`, replacing the `docker start`-only pattern.
3. THE design SHALL evaluate `docker-compose` (`up -d --build`) as the recommended mechanism so that container network, environment, and name configuration are stored in version control.
4. WHEN the deploy is executed, THE System SHALL build the running capture container from current repository code using the `.env` loader and no Vault dependency, verifiable by the absence of the Vault startup failure.
5. WHEN the deploy is executed, THE System SHALL populate `.env` on the Always-On Host from the current template with the Betfair credentials and the DB values `DB_HOST=my_postgres`, `DB_PORT`, `DB_NAME=bf_trader`, `DB_USER`, and `DB_PWD`.
6. WHEN a full deploy has completed, THE System SHALL verify within 300 seconds that a Monitor Service cycle runs current code and persists odds to the `bf_trader` database.
7. IF the code-sync step fails to retrieve the current repository code, THEN THE System SHALL abort the deploy, leave the previously running container unchanged, and return an error indicating the sync failure.
8. IF any required `.env` value (Betfair credentials, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, or `DB_PWD`) is missing or empty after population, THEN THE System SHALL abort the deploy before container recreation and return an error indicating which required value is missing.
9. IF the image build or container recreation fails, THEN THE System SHALL retain the last known-good running container and return an error indicating the build or recreation failure.

### Requirement 8: Vault Retirement

**User Story:** As the operator of the trading system, I want the legacy Vault dependency removed from the deployment, so that the direct cause of the capture gap is eliminated at source rather than repaired.

*Note: Vault has already been removed from the codebase in favour of the `.env` loader. This requirement covers removing the now-unused Vault infrastructure on the Pi. It deliberately excludes the wider container/image teardown (~608 exited containers, dangling image `d91da2e69804`, abandoned `rundeck-image-01`), which belongs to SP-294.*

#### Acceptance Criteria

1. WHEN current code has been confirmed running without any Vault dependency per Requirement 7, THE operator SHALL stop and remove the `my_keyvault` Vault container from the Always-On Host such that no container named `my_keyvault` remains in the host's container list.
2. IF current code has not been confirmed running without any Vault dependency per Requirement 7, THEN THE operator SHALL NOT remove the `my_keyvault` Vault container.
3. WHEN removing Vault infrastructure, THE operator SHALL disable and delete the "Unlock the Vault" Rundeck job so that it no longer appears in the Rundeck job schedule and no longer invokes `start_up_vault.sh`.
4. THE season-background-data-capture design SHALL NOT define, reference, or provision any Vault hostname or Vault network wiring (including `my_vault` on `my_trading_network` or `my_keyvault` on the `bridge` network), such that no design artifact contains a Vault service, host, or network entry.
5. THE season-background-data-capture design SHALL limit Vault retirement to the `my_keyvault` container and the "Unlock the Vault" Rundeck job, and SHALL NOT remove the ~608 exited containers, the dangling image `d91da2e69804`, or the `rundeck-image-01` container, which are owned by SP-294.

## Open Questions

These uncertainties are recorded deliberately and are to be resolved during design (some require the operator to verify against the live Pi). They are not decided here.

1. ~~**PostgreSQL install form and reachability**~~ — **RESOLVED by SP-329.** PostgreSQL runs as the container `my_postgres` (`postgres:16.1`) at 172.19.0.3 on `my_trading_network`, database `bf_trader`, and is reachable. It is not the cause of the gap.
2. ~~**Native vs container decision for PostgreSQL**~~ — **CLOSED.** Premised on a non-existent Docker limitation. Docker works fine on the Pi; PostgreSQL stays as the existing `my_postgres` container. No migration decision required.
3. **Log-visibility and failure-notification approach** — The specific mechanism (log files plus notification vs logs only vs system journal) has not been finalised by the operator.
4. ~~**Final scheduler choice**~~ — **RESOLVED: retain the existing native Rundeck.** SP-329 confirmed Rundeck already works and only the capture job's dependencies were broken, so SP-328 keeps Rundeck (see Requirement 3). SP-294 may replace or descope Rundeck later on its own timeline, independently of SP-328.
5. ~~**Repair vs rebuild of the existing deployment**~~ — **RESOLVED by SP-329: rebuild.** Redeploy with current code (which has the `.env` loader and no Vault dependency) rather than repairing the Vault wiring, since that mechanism has been designed out of the codebase.
6. ~~**Root cause of the data gap**~~ — **RESOLVED by SP-329.** Not "Rundeck fires but nothing persists" — capture fails at startup with a Vault connectivity error (app expects host `my_vault`; the container is `my_keyvault` on the `bridge` network, not `my_trading_network`), before any DB write. The gap is well over a year (current container last ran 2026-03-25; older `docker run` debris stops 2025-05-09), not ~6 months.

## Dependencies

- **SP-329 (Spike: Map and document the existing Betfair capture deployment on the Pi)** — COMPLETE. Its findings are folded into this document (see the [SP-329 note](https://amainit.atlassian.net/wiki/spaces/JE/pages/460718082)): root cause is a Vault connectivity failure, the recommendation is rebuild-not-repair, and the build/deploy pipeline gap is now captured as Requirement 7 and Vault retirement as Requirement 8.
- **SP-294 (Remove Rundeck — replace with simple manual run scripts)** — Independent of SP-328. SP-328 retains the existing native Rundeck (see Requirement 3); SP-294 may replace or descope Rundeck later on its own timeline, and the two do not need to be reconciled here. SP-294 also owns the wider Docker debris teardown (~608 exited containers, dangling image `d91da2e69804`, abandoned `rundeck-image-01`), which is explicitly out of scope for SP-328 (see Requirement 8).
- Related simplification epic tickets for context: SP-222 (Move Rundeck to a proper database), SP-295 (Simplify docker-compose), SP-319 (Review trading automation delivery).
