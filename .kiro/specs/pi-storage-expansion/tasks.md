# Implementation Plan: Pi Storage Expansion (SP-338)

## Overview

This is an operations / infrastructure change executed on the always-on Raspberry Pi
over SSH (see the `pi-access` steering for how to reach the Pi), using the confirmed
design choices:

- **Relocation boundary — Option A:** relocate the whole Docker data-root
  (`/var/lib/docker` → `/mnt/data/docker`). Because `my_postgres` has no dedicated
  volume today, this transitively relocates the Postgres data too.
- **Mount point:** `/mnt/data` (ext4, mounted by UUID with `nofail`). The live USB is
  `/dev/sda1`, ~57.7G, currently `vfat` (LABEL `KINGSTON`, UUID `4234-7595`), to be
  reformatted to ext4.
- **Log_Share relocation — in scope:** move `/usr/local/my_share` (Samba `[NewShare]`,
  the Rundeck-written daily GBPUSD/SPY files mapped as the Operator's Windows "Y:" drive)
  onto the USB at `/mnt/data/my_share` and repoint the `[NewShare]` `path =`. Host-path
  move + Samba config edit, independent of the Docker data-root move.
- **Backup:** manual `pg_dump` for MVP written to a dedicated SD-card **Backup_Share**
  `/home/mypi/bf_backups` (OFF the USB), exposed over its own Samba share so the Operator
  maps a drive letter and copies dumps off to Windows. No scheduled job required for MVP.

There is **no property-based testing** for this feature. The design justifies the
omission (IaC / side-effect-only operations against external system state), so the
"tests" here are repeatable on-target verification commands and recorded evidence,
not automated test code.

### Safety rules for every task below

- **Read-only diagnostics** (`lsblk`, `df -h /`, `findmnt`, `docker info`, `docker ps`,
  `blkid`, the integrity-check `SELECT` queries) may run freely without confirmation.
- **State-changing / destructive steps** — reformatting the KINGSTON drive, editing
  `/etc/fstab`, stopping Docker/Postgres, running `rsync` of the data-root or the
  Log_Share, editing `/etc/docker/daemon.json`, editing `smb.conf` (the `[NewShare]`
  path and the new Backup_Share), reloading/restarting `smbd`, reclaiming the old
  `/var/lib/docker` or `/usr/local/my_share`, and rebooting — MUST be gated behind
  **explicit Operator confirmation** and are flagged **[DESTRUCTIVE/STATE-CHANGING — confirm first]**.
- The safety ordering from the design MUST be preserved: off-drive `pg_dump` backup
  BEFORE any destructive step → stop writers in order → record integrity baseline
  BEFORE moving → `rsync -aHAX` WITHOUT deleting the original → verify post ≥ baseline
  BEFORE reclaiming the old data → reboot-persistence verification → record evidence.
- **Cross-project awareness (shared infrastructure):** the Pi's Docker daemon,
  Postgres, and Rundeck are shared between this Betfair capture project and a separate
  Trading project. The Docker daemon stop in task 3 brings down **all** containers
  (including the Trading project's), and the Log_Share quiesce in task 9 pauses the
  **Trading project's** Rundeck writer (it owns the GBPUSD/SPY files). Run the
  relocation in a **quiet window** to minimise disruption to both projects, and when
  running the existing verification steps confirm that **both** the shared stack is
  healthy **and** the Log_Share still receives Rundeck (Trading) writes after the move.
  No Trading-specific steps are required beyond this awareness.

## Tasks

- [x] 1. Pre-flight diagnostics and baselines (read-only)
  - [x] 1.1 Capture drive and device layout
    - Run `lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,UUID,PARTUUID` and `sudo blkid` on the Pi; identify the KINGSTON partition (currently auto-mounted at `/media/mypi/KINGSTON`) and its current (removable) filesystem type.
    - Record the device/partition node for use in later steps.
    - _Requirements: 1.1, 1.5_

  - [x] 1.2 Record Root_Filesystem usage baseline
    - Run `df -h /` and record the root used-percentage as the baseline (latest reading ≈75% used — 21G used, 7.0G avail on the 29G root).
    - _Requirements: 5.1_

  - [x] 1.3 Record Data_Integrity_Check baseline
    - Confirm no capture run is active first (`docker ps`).
    - Run the six baseline queries via `docker exec my_postgres psql -U postgres -d bf_trader -tAc "<query>"`:
      - `SELECT count(*), max("timestamp") FROM bf.market_table;`
      - `SELECT count(*), max("timestamp") FROM bf.log_file;`
      - `SELECT count(*), max(last_updated) FROM bf.betfair_object_ids;`
      - `SELECT count(*), max(last_updated) FROM bf.target;`
      - `SELECT count(*), max(run_started) FROM bf.quality_run;`
      - `SELECT count(*) FROM bf.quality_match_result;`
    - Record all counts and MAX timestamps (`market_table` is the primary acceptance signal).
    - _Requirements: 4.2_

- [x] 2. Create the Backup_Share and take an off-USB backup before any destructive step **[STATE-CHANGING — confirm first]**
  - [x] 2.1 Create the Backup_Share directory and Samba share
    - Create the backup dir on the SD-card root: `mkdir -p /home/mypi/bf_backups` — a path NOT on `/mnt/data` (so a USB failure cannot destroy the backups).
    - Add a Samba share to `smb.conf` exposing it, e.g. `[bf_backups]` with `path = /home/mypi/bf_backups`, `read only = no`, `valid users = mypi`.
    - Validate with `sudo testparm -s` and reload: `sudo systemctl reload smbd`.
    - _Requirements: 6.1, 6.2_

  - [x] 2.2 Produce a manual pg_dump into the Backup_Share and copy it off to Windows
    - Run `docker exec my_postgres pg_dump -U postgres -d bf_trader -Fc > "/home/mypi/bf_backups/bf_trader_$(date +%Y%m%dT%H%M%S).dump"`.
    - Verify the dump file is non-empty (`ls -l`) before proceeding.
    - Confirm the Backup_Share is reachable from Windows: map a drive letter to `\\<pi-host>\bf_backups` and copy the dump off the Pi for durable retention.
    - _Requirements: 6.1, 6.2, 6.3, 4.5_

- [x] 3. Stop writers in order **[STATE-CHANGING — confirm first]**
  - [x] 3.1 Quiesce and stop capture, Postgres, and the Docker daemon
    - Confirm no capture run is active (`docker ps`); pause/scale down any Rundeck trigger if needed.
    - Stop Postgres and pgAdmin: `docker stop my_postgres my_pgadmin`.
    - Stop the Docker daemon: `sudo systemctl stop docker docker.socket`.
    - _Requirements: 4.1_

- [x] 4. Provision the External_Drive as ext4 and mount at `/mnt/data`
  - [x] 4.1 Unmount the desktop auto-mount and reformat to ext4 **[DESTRUCTIVE — confirm first]**
    - State explicitly to the Operator that `mkfs.ext4` ERASES everything on the KINGSTON drive; require confirmation.
    - Unmount: `udisksctl unmount -b /dev/<part>` (or `sudo umount /media/mypi/KINGSTON`); disable the desktop auto-mount so it cannot race the fstab mount.
    - Reformat: `sudo mkfs.ext4 -L pidata /dev/<part>`.
    - Capture the new filesystem UUID: `sudo blkid /dev/<part>`.
    - _Requirements: 1.5_

  - [x] 4.2 Create the mount point and add the fstab entry **[STATE-CHANGING — confirm first]**
    - `sudo mkdir -p /mnt/data`.
    - Add to `/etc/fstab` using the ext4 UUID and failure-tolerant options:
      `UUID=<ext4-uuid>  /mnt/data  ext4  defaults,nofail,x-systemd.device-timeout=10s  0  2`.
    - Apply without reboot: `sudo systemctl daemon-reload && sudo mount -a`.
    - _Requirements: 1.1, 1.2, 2.1, 2.2_

  - [x] 4.3 Verify the mount is present and writable
    - Run `findmnt /mnt/data` (expect ext4 mounted) and a write/read probe: `sudo touch /mnt/data/.probe && rm /mnt/data/.probe`.
    - _Requirements: 1.4_

- [x] 5. Relocate the Docker data-root (Option A) **[STATE-CHANGING — confirm first]**
  - [x] 5.1 Copy the Docker data-root preserving ownership/permissions (do NOT delete the original)
    - With the daemon stopped (from task 3.1), run `sudo rsync -aHAX --info=progress2 /var/lib/docker/ /mnt/data/docker/`.
    - Leave `/var/lib/docker` in place; it is the retained original until integrity passes.
    - _Requirements: 3.2, 4.5_

  - [x] 5.2 Re-point the Docker daemon via daemon.json **[STATE-CHANGING — confirm first]**
    - Set `/etc/docker/daemon.json` to `{ "data-root": "/mnt/data/docker" }`.
    - _Requirements: 3.3_

- [x] 6. Restart services and resume capture **[STATE-CHANGING — confirm first]**
  - [x] 6.1 Start the daemon and confirm the new data-root
    - `sudo systemctl start docker`; confirm `docker info --format '{{.DockerRootDir}}'` reports `/mnt/data/docker`.
    - _Requirements: 3.3_

  - [x] 6.2 Start Postgres and resume capture via deploy
    - Start Postgres: `bash scripts/start_up_postgres.sh` (or `docker start my_postgres my_pgadmin`); wait until Postgres answers `SELECT 1`.
    - Run `scripts/deploy.sh` so the capture service rebuilds and resumes writing on current code.
    - _Requirements: 3.2, 3.7_

- [x] 7. Verify data integrity against the baseline (decision gate)
  - [x] 7.1 Re-run the Data_Integrity_Check and assert post ≥ baseline
    - Re-run the six queries from task 1.3.
    - Assert every post row count ≥ baseline count AND every post `MAX(timestamp)` ≥ baseline (≥ because capture may have written new rows after restart).
    - **Pass** → continue to task 8. **Fail** (fewer rows / earlier MAX) → treat relocation as failed and go to task 11 (rollback); do NOT reclaim the original.
    - _Requirements: 4.3, 4.4_

- [x] 8. Checkpoint — integrity confirmed before reclaiming
  - Ensure the integrity check passed and all services are healthy; ask the Operator if questions arise. Do not proceed to reclaim the original data until this is confirmed.

- [x] 9. Relocate the Log_Share onto the External_Drive **[STATE-CHANGING — confirm first]**
  - Depends on the ext4 mount existing (task 4). Independent of the Docker data-root move (host-path + Samba edit), run here to keep the safety ordering: copy without delete → repoint → verify → reclaim.
  - [x] 9.1 Quiesce the Rundeck writer and copy the share to the USB (do NOT delete the original)
    - If a Rundeck job may fire mid-copy, pause/disable the relevant Rundeck schedule (or run during a quiet window) so no file is written during the copy.
    - Copy preserving ownership/permissions: `sudo rsync -aHAX /usr/local/my_share/ /mnt/data/my_share/` (result owned `rundeck:rundeck`). Leave `/usr/local/my_share` in place as the retained original.
    - _Requirements: 3.4, 4.5_

  - [x] 9.2 Repoint the `[NewShare]` Samba share and reload smbd
    - In `smb.conf`, change the `[NewShare]` `path =` from `/usr/local/my_share` to `/mnt/data/my_share`.
    - Validate with `sudo testparm -s`, then `sudo systemctl reload smbd` (use `restart smbd` if reload does not re-read the path).
    - _Requirements: 3.5_

  - [x] 9.3 Verify the Windows "Y:" drive and that Rundeck writes land on the USB
    - From Windows, confirm the mapped "Y:" drive still lists the existing files and that a test file written from Windows appears under `/mnt/data/my_share`.
    - Re-enable the Rundeck schedule (or trigger a run) and confirm the next daily file (`GBPUSD_YYYYMMDD.txt` / `SPY_YYYYMMDD.txt`) is created under `/mnt/data/my_share`, not under `/usr/local/my_share`.
    - _Requirements: 3.5, 3.6_

- [x] 10. Reclaim the original data and reduce root pressure **[DESTRUCTIVE — confirm first]**
  - [x] 10.1 Rename-then-remove the old Docker data-root
    - Only after a passing integrity check: `sudo mv /var/lib/docker /var/lib/docker.old`; verify services remain healthy, then delete `sudo rm -rf /var/lib/docker.old` once stable.
    - _Requirements: 4.5, 5.2_

  - [x] 10.2 Rename-then-remove the old Log_Share location
    - Only after task 9.3 verifications pass: `sudo mv /usr/local/my_share /usr/local/my_share.old`; confirm the `[NewShare]` share and Rundeck writes remain healthy from `/mnt/data/my_share`, then delete `sudo rm -rf /usr/local/my_share.old` once stable.
    - _Requirements: 3.4, 4.5, 5.2_

  - [x] 10.3 Confirm root usage dropped and meets the target
    - Run `df -h /`; confirm the root used-percentage is lower than the task 1.2 baseline AND is at or below 60%.
    - If still above 60%, prune Docker images and re-measure (the Log_Share move is already reclaimed above).
    - _Requirements: 5.2, 5.3_

- [x] 11. Verify boot resilience and reboot persistence **[STATE-CHANGING — confirm first]**
  - [x] 11.1 Reboot and confirm mount + data-root + Log_Share + capture persist
    - Reboot the Pi; after boot confirm `findmnt /mnt/data` is mounted ext4, `docker info` data-root is `/mnt/data/docker`, `my_postgres` is up, the `[NewShare]` share still serves `/mnt/data/my_share`, and a fresh capture cycle completes (`scripts/deploy.sh` runs `scripts/verify_deploy.py`).
    - _Requirements: 1.3, 1.4, 3.6, 8.2_

  - [x] 11.2 Verify drive-absent boot resilience (controlled test)
    - With the drive physically removed (or the fstab UUID temporarily invalidated in a controlled test), confirm the Pi still boots to a login-capable state within the timeout and `df` shows the root filesystem — no emergency-prompt hang. Restore the drive/fstab afterward.
    - _Requirements: 2.1, 2.2_

- [x] 12. Document the rollback / drive-absent recovery path (runbook support)
  - [ ] 12.1 Record the rollback and absent-drive recovery steps for the runbook
    - Docker rollback: stop Docker → revert `/etc/docker/daemon.json` (or `sudo mv /var/lib/docker.old /var/lib/docker` if already renamed) → start Docker → `scripts/deploy.sh` → confirm capture resumes.
    - Log_Share rollback: revert the `[NewShare]` `path =` to `/usr/local/my_share` (retained until verified) and `sudo systemctl reload smbd` to restore the share.
    - Absent-at-later-boot: diagnose with `findmnt /mnt/data`, `lsblk`, `dmesg | tail`; reseat + `sudo mount -a` if merely unmounted; restore from the latest `pg_dump` onto a replacement drive if the drive is dead.
    - _Requirements: 2.3, 3.5, 4.4, 7.4_

- [x] 13. Author the Confluence runbook
  - [x] 13.1 Write the storage-layout and verification runbook page
    - Document which elements reside on `/mnt/data` (Docker data-root incl. Postgres data; the relocated Log_Share at `/mnt/data/my_share`) vs the Root_Filesystem (incl. the Backup_Share `/home/mypi/bf_backups`); record the Mount_Point (`/mnt/data`), the stable identifier (ext4 UUID), and the filesystem type (ext4).
    - Document the verification commands the Operator runs (`findmnt /mnt/data`, `docker info --format '{{.DockerRootDir}}'`, the integrity queries, `df -h /`).
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 13.2 Document the Log_Share-on-USB location and verification
    - Record that `[NewShare]` now points at `/mnt/data/my_share` (the Rundeck GBPUSD/SPY daily files on the USB), how the Operator verifies the mapped Windows "Y:" drive still reads/writes, and how to confirm new Rundeck writes land on the USB. Include the Log_Share rollback (revert `[NewShare]` path + reload smbd).
    - _Requirements: 7.5_

  - [x] 13.3 Document the Backup_Share and manual backup/restore procedure
    - Backup_Share: `/home/mypi/bf_backups` on the SD card (OFF the USB), its Samba share definition, and how the Operator maps a drive letter and copies dumps off to Windows.
    - Backup: the manual `pg_dump -Fc` command writing into `/home/mypi/bf_backups`; state that MVP is manual-only (no scheduled job).
    - Restore: `docker exec -i my_postgres pg_restore -U postgres -d bf_trader --clean --if-exists < <dump>`; note full-disaster rebuild (recreate via `build/postgres_build.sh` then `pg_restore`).
    - _Requirements: 6.4, 6.5, 7.6_

  - [x] 13.4 Add drive-absent recovery section to the runbook
    - Incorporate the recovery steps from task 12.1 into the runbook so the Operator can detect and recover from an absent/unreadable drive at boot.
    - _Requirements: 2.3, 7.4_

- [x] 14. Record deployment-and-verification evidence on SP-338
  - [x] 14.1 Record the evidence and gate the Done transition
    - Write into SP-338 (or the runbook, linked from the ticket) the before/after root used-% , the integrity-check pass (post ≥ baseline), the Log_Share relocation proof (`[NewShare]` → `/mnt/data/my_share`, Rundeck writes land on USB), and the reboot-persistence proof (mount + data-root + capture after reboot).
    - SP-338 MUST remain out of Done until this evidence is recorded; per project process the ticket sits in Mostly Done until deployed-and-verified evidence exists.
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 15. Final checkpoint — relocation complete and evidenced
  - Confirm the mount persists across reboot, the integrity check passed, the Log_Share serves from `/mnt/data/my_share` with Rundeck writing there, the Backup_Share is reachable from Windows, root usage is ≤60%, the runbook is published, and the evidence is recorded on SP-338. Ask the Operator if questions arise.

## Notes

- This feature is operations/infrastructure work run on the Pi via SSH, not application code; most tasks are run-on-Pi procedure and verification steps plus Confluence runbook authoring.
- There are no property-based tests (the design justifies the omission for IaC / side-effect-only operations); verification is on-target command execution with recorded evidence.
- State-changing/destructive steps are flagged and MUST be gated behind explicit Operator confirmation; read-only diagnostics run freely.
- The safety ordering is enforced by task order: backup (2) → stop writers (3) → baseline recorded (1.3) before move (5) → rsync without delete (5.1) → integrity gate (7/8) before reclaim (10) → Log_Share move with copy-without-delete + verify (9) before its reclaim (10.2) → reboot verification (11) → evidence (14).
- The Log_Share relocation (task 9: `/usr/local/my_share` → `/mnt/data/my_share`, repoint `[NewShare]`) is in scope and follows the same safety pattern as the Docker move — copy preserving `rundeck:rundeck` without deleting the original, repoint Samba, verify Windows "Y:" + Rundeck writes, then reclaim.
- The Backup_Share (`/home/mypi/bf_backups`) is created in task 2 and stays OFF the USB; the pg_dump writes there and the Operator copies dumps off to Windows over the mapped drive.
- Each task references specific requirements for traceability.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["3.1"] },
    { "id": 4, "tasks": ["4.1"] },
    { "id": 5, "tasks": ["4.2"] },
    { "id": 6, "tasks": ["4.3"] },
    { "id": 7, "tasks": ["5.1"] },
    { "id": 8, "tasks": ["5.2"] },
    { "id": 9, "tasks": ["6.1"] },
    { "id": 10, "tasks": ["6.2"] },
    { "id": 11, "tasks": ["7.1"] },
    { "id": 12, "tasks": ["9.1"] },
    { "id": 13, "tasks": ["9.2"] },
    { "id": 14, "tasks": ["9.3"] },
    { "id": 15, "tasks": ["10.1", "10.2"] },
    { "id": 16, "tasks": ["10.3"] },
    { "id": 17, "tasks": ["11.1"] },
    { "id": 18, "tasks": ["11.2"] },
    { "id": 19, "tasks": ["12.1", "13.1"] },
    { "id": 20, "tasks": ["13.2"] },
    { "id": 21, "tasks": ["13.3"] },
    { "id": 22, "tasks": ["13.4"] },
    { "id": 23, "tasks": ["14.1"] }
  ]
}
```
