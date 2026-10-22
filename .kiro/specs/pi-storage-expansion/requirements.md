# Requirements Document

## Introduction

The always-on Raspberry Pi hosts the deployed Betfair capture stack (`bf_trader_py`), Postgres, Docker, and scheduled jobs (Rundeck). All of this currently shares a single 29G SD-card root filesystem (`/dev/mmcblk0p2`). The SP-336 outage was caused by that root filesystem reaching 100% capacity, which crash-looped Postgres and took down Betfair capture. The SP-336 cleanup restored headroom to roughly 72% used, but usage has since crept back up to 75% used (21G used, 7.0G available on the 29G root), confirming that the underlying single-point-of-failure remains: operating system, Docker images and layers, container logs, Rundeck logs/data, the Rundeck-written daily data files under `/usr/local/my_share`, and the Postgres data all compete for the same small, write-limited card.

A 64GB USB drive (reporting ~57.7G usable) has already been purchased and inserted into the Pi, where it currently appears auto-mounted at `/media/mypi/KINGSTON`. This feature covers provisioning that drive as persistent, reboot-safe storage; relocating selected high-growth and high-write workloads onto it (including the Rundeck-written daily data share); performing the relocation safely with data integrity verification; reducing root filesystem pressure; providing an off-USB backup share from which the Operator can copy database dumps to a Windows machine; and documenting the resulting layout and recovery procedure in the Confluence runbook.

Because this work runs on the Raspberry Pi (the Betfair capture deployment target), the project process requires that final changes be deployed and verified on the Pi before the associated Jira ticket (SP-338) moves to Done. The requirements below therefore include on-target verification as explicit, measurable acceptance criteria.

This is an operations/infrastructure change rather than application code. The requirements describe the behavior the storage configuration and relocation procedure must exhibit, expressed so each can be verified on the Pi.

## Glossary

- **Pi**: The always-on Raspberry Pi hosting the deployed Betfair capture stack and scheduled jobs.
- **Root_Filesystem**: The SD-card partition `/dev/mmcblk0p2` (29G) currently serving as `/` on the Pi.
- **External_Drive**: The 64GB USB storage device already inserted in the Pi, currently appearing at `/media/mypi/KINGSTON`.
- **Mount_Configuration**: The persistent mount definition recorded in `/etc/fstab` that attaches the External_Drive at a defined mount point on boot.
- **Mount_Point**: The directory path at which the External_Drive is attached (to be finalized in design; e.g. `/mnt/data`).
- **Relocation_Procedure**: The documented, ordered steps that stop the relevant services, move selected data onto the External_Drive, re-point services at the new location, and restart them.
- **Relocated_Element**: A unit of data or storage area moved from the Root_Filesystem onto the External_Drive. Elements in scope: the Docker data root (`/var/lib/docker`), the Postgres data (held in the Docker data root today), and the Log_Share host path (`/usr/local/my_share`).
- **Postgres_Data_Volume**: The persistent Postgres data store backing the `bf_trader` database.
- **Docker_Data_Root**: The Docker storage directory `/var/lib/docker` holding images, layers, and volumes.
- **Capture_Service**: The deployed Betfair capture process(es) that write market data to Postgres.
- **Data_Integrity_Check**: A before/after comparison of row counts and `MAX(timestamp)` on the relevant `bf_trader` tables used to confirm no data was lost during relocation.
- **Runbook**: The Confluence page describing what lives where on the Pi, how to verify storage health, and how to recover.
- **Operator**: The person responsible for running and maintaining the Pi and the Betfair capture stack.
- **Backup_Procedure**: A documented, repeatable method for capturing a recoverable copy of the Relocated_Element data in case the Pi or External_Drive fails.
- **Log_Share**: The Samba share `[NewShare]`, currently backed by the SD-card host path `/usr/local/my_share` (~64M), into which the Rundeck jobs write daily data files (`GBPUSD_YYYYMMDD.txt` and `SPY_YYYYMMDD.txt`, owned by `rundeck:rundeck`). It is mapped by the Operator as a drive letter (the "Y:" drive) on the Operator's Windows machine. It is a recurring write/growth source living on the Root_Filesystem.
- **Backup_Share**: A dedicated SD-card directory (for example `/home/mypi/bf_backups`) exposed as its own Samba share so the Operator can map a drive letter on the Operator's Windows machine and copy Backup_Procedure dumps off the Pi. The Backup_Share is deliberately kept OFF the External_Drive so a USB-drive failure cannot destroy the backups.

## Requirements

### Requirement 1: Provision persistent external storage

**User Story:** As the Operator, I want the External_Drive mounted at a defined, reboot-safe mount point, so that relocated data remains available automatically after the Pi restarts.

#### Acceptance Criteria

1. THE Mount_Configuration SHALL define the External_Drive by a stable identifier (filesystem UUID or PARTUUID) rather than by a transient device node such as `/dev/sda1`.
2. THE Mount_Configuration SHALL attach the External_Drive at a defined Mount_Point recorded in `/etc/fstab`.
3. WHEN the Pi completes a reboot, THE External_Drive SHALL be mounted at the defined Mount_Point without manual intervention.
4. WHEN the Operator runs a mount verification command on the Pi after a reboot, THE External_Drive SHALL report as mounted at the defined Mount_Point with read and write access.
5. THE External_Drive SHALL use a Linux-native filesystem (for example ext4) rather than the as-shipped removable-media filesystem, so that file ownership and permissions required by Postgres and Docker are supported.

### Requirement 2: Boot resilience when the external drive is absent

**User Story:** As the Operator, I want the Pi to boot successfully even when the External_Drive is missing or unreadable at boot, so that a drive failure degrades service rather than preventing the Pi from starting.

#### Acceptance Criteria

1. WHERE the Mount_Configuration includes failure-tolerant mount options (for example `nofail`), THE Pi SHALL complete boot to a usable state when the External_Drive is absent at boot.
2. IF the External_Drive is absent or unreadable at boot, THEN THE Pi SHALL reach a login-capable state within the configured boot timeout rather than halting in an emergency maintenance prompt indefinitely.
3. IF the External_Drive is absent at boot, THEN THE Runbook SHALL describe how the Operator detects the absent drive and how the Operator recovers the affected services.

### Requirement 3: Relocate selected high-growth and high-write elements

**User Story:** As the Operator, I want selected high-growth and high-write elements moved onto the External_Drive, so that heavy writes and growth no longer consume the Root_Filesystem and SD-card endurance is extended.

#### Acceptance Criteria

1. THE design SHALL record which Relocated_Elements are moved onto the External_Drive and the rationale for the chosen boundary (minimum scope: the Postgres_Data_Volume; broader scope: the Docker_Data_Root and the Log_Share).
2. WHEN relocation is complete, THE Postgres_Data_Volume SHALL reside on the External_Drive and the `bf_trader` Postgres instance SHALL read and write its data from the External_Drive location.
3. WHERE the Docker_Data_Root is selected as a Relocated_Element, THE Docker daemon SHALL use the External_Drive location as its data root after relocation.
4. WHEN relocation is complete, THE Log_Share host path SHALL reside on the External_Drive at a defined location under the Mount_Point.
5. WHEN relocation of the Log_Share is complete, THE Samba configuration SHALL point the `[NewShare]` share at the External_Drive location.
6. WHEN relocation of the Log_Share is complete, THE Rundeck jobs SHALL continue to write the daily data files to the Log_Share, and those writes SHALL land on the External_Drive location.
7. WHEN relocation is complete, THE Capture_Service SHALL start and resume writing new market data to Postgres.

### Requirement 4: Safe relocation with data integrity verification

**User Story:** As the Operator, I want relocation performed with services stopped and data verified intact, so that moving the data does not corrupt or lose captured market data.

#### Acceptance Criteria

1. BEFORE moving a Relocated_Element that is in active use, THE Relocation_Procedure SHALL stop the services that write to that element.
2. BEFORE moving the Postgres_Data_Volume, THE Relocation_Procedure SHALL record a Data_Integrity_Check baseline consisting of row counts and `MAX(timestamp)` for the relevant `bf_trader` tables.
3. WHEN relocation of the Postgres_Data_Volume is complete and services are restarted, THE Relocation_Procedure SHALL record a second Data_Integrity_Check and SHALL confirm that post-relocation row counts are greater than or equal to the baseline and `MAX(timestamp)` is greater than or equal to the baseline.
4. IF a Data_Integrity_Check after relocation shows fewer rows or an earlier `MAX(timestamp)` than the recorded baseline, THEN THE Relocation_Procedure SHALL treat the relocation as failed and THE Runbook SHALL describe how the Operator restores the pre-relocation data location.
5. THE Relocation_Procedure SHALL retain the original data at its pre-relocation location until a post-relocation Data_Integrity_Check has confirmed success.

### Requirement 5: Reduce root filesystem pressure

**User Story:** As the Operator, I want the Root_Filesystem usage meaningfully reduced after relocation, so that the Root_Filesystem is no longer at risk of filling and crash-looping Postgres.

#### Acceptance Criteria

1. BEFORE relocation, THE Relocation_Procedure SHALL record the Root_Filesystem used-percentage as a baseline.
2. WHEN relocation is complete, THE Relocation_Procedure SHALL record the Root_Filesystem used-percentage and SHALL confirm it is lower than the recorded baseline.
3. WHEN relocation is complete, THE Root_Filesystem used-percentage SHALL be at or below 60 percent.

### Requirement 6: Backup provision for relocated data

**User Story:** As the Operator, I want a documented backup method for the relocated data that writes to an off-USB Samba share I can reach from my Windows machine, so that captured data can be recovered if the Pi or the External_Drive fails.

#### Acceptance Criteria

1. THE Backup_Procedure SHALL produce a recoverable copy of the Postgres_Data_Volume contents written to the Backup_Share directory, which resides on the Root_Filesystem (off the External_Drive).
2. THE Samba configuration SHALL expose the Backup_Share so that the Operator can map a drive letter to it from the Operator's Windows machine and copy backup files off the Pi.
3. THE Backup_Procedure SHALL use a manual `pg_dump` for the MVP, and THE Runbook SHALL state that the procedure is manual.
4. THE Runbook SHALL document how the Operator restores the Postgres_Data_Volume from a backup produced by the Backup_Procedure.
5. WHERE the Backup_Procedure is scheduled rather than manual, THE Backup_Procedure SHALL record the location and frequency of the backups in the Runbook.

### Requirement 7: Documentation of the resulting layout

**User Story:** As the Operator, I want the storage layout and verification steps documented in the Runbook, so that I can understand what lives where and recover from problems without re-deriving the setup.

#### Acceptance Criteria

1. THE Runbook SHALL document which Relocated_Elements reside on the External_Drive and which remain on the Root_Filesystem after this change.
2. THE Runbook SHALL document the Mount_Point, the stable identifier used in the Mount_Configuration, and the filesystem type of the External_Drive.
3. THE Runbook SHALL document the commands the Operator runs to verify that the External_Drive is mounted and that each Relocated_Element resides on the External_Drive.
4. THE Runbook SHALL document the recovery steps for the case where the External_Drive is absent or unreadable at boot.
5. THE Runbook SHALL document the relocated Log_Share location on the External_Drive, the `[NewShare]` Samba path change, and how the Operator verifies the mapped Windows drive still reads and writes after the move.
6. THE Runbook SHALL document the Backup_Share location, its Samba share definition, and how the Operator maps a drive letter to copy backups off the Pi.

### Requirement 8: On-target deployment and verification

**User Story:** As the Operator, I want the storage change applied and verified on the Pi itself, so that the change is proven on the live Betfair capture target before the work is considered complete.

#### Acceptance Criteria

1. THE Mount_Configuration and Relocation_Procedure SHALL be applied on the Pi, not only described in documentation.
2. WHEN the change is applied, THE Capture_Service SHALL be confirmed running on the Pi and writing new market data to Postgres after a reboot.
3. WHEN on-target verification is complete, THE associated Jira ticket (SP-338) SHALL remain out of Done status until the deployment-and-verification evidence (mount persisted across reboot, Data_Integrity_Check passed, Root_Filesystem reduced) has been recorded in the ticket or Runbook.
