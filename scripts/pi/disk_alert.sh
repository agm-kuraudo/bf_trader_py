#!/usr/bin/env bash
# SP-294 — local-only disk-usage alert for the Raspberry Pi.
#
# Interim stopgap ahead of the Telegram-based alert (follow-up ticket, reusing
# the SP-357/SP-395 delivery layer). This script has NO network dependency and
# NO secrets: on a threshold breach it writes a WARNING to the system log via
# `logger` (tag: disk-alert -> journald/syslog) and refreshes a marker file so
# the condition is visible with `journalctl -t disk-alert` or by checking the
# marker. When the Telegram layer lands it can simply watch this same logger
# output instead of reinventing the check.
#
# Motivated by SP-336: a runaway 1.2G pgadmin json.log filled the disk and
# crash-looped Postgres. Docker log-rotation (daemon.json max-size/max-file)
# now caps that at the source; this alert is the catch-all for any other
# disk-fill cause.
#
# Exit status is always 0 (even on breach) so a cron wrapper never treats a
# full disk as a script failure — the alert IS the signal.

set -uo pipefail

# Percentage used at/above which we alert. Override via env for testing.
THRESHOLD="${DISK_ALERT_THRESHOLD:-85}"

# Mounts to watch. The Docker data-root lives on /mnt/data (external disk);
# / is the OS/root filesystem.
MOUNTS=("/" "/mnt/data")

MARKER="/tmp/disk_alert.breached"
TAG="disk-alert"

breached=0

for mnt in "${MOUNTS[@]}"; do
  # Skip mounts that are not present on this host.
  if ! df -P "$mnt" >/dev/null 2>&1; then
    continue
  fi
  # Used percentage as an integer (strip the trailing % from df -P output).
  use=$(df -P "$mnt" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
  avail=$(df -Ph "$mnt" | awk 'NR==2 {print $4}')
  if [ -n "$use" ] && [ "$use" -ge "$THRESHOLD" ]; then
    breached=1
    logger -t "$TAG" -p user.warning \
      "DISK ALERT: ${mnt} at ${use}% used (>= ${THRESHOLD}% threshold), ${avail} free"
  else
    logger -t "$TAG" -p user.info \
      "ok: ${mnt} at ${use}% used, ${avail} free"
  fi
done

if [ "$breached" -eq 1 ]; then
  # Refresh a marker so the breach is trivially visible without scanning logs.
  date -u +"%Y-%m-%dT%H:%M:%SZ breach" > "$MARKER" 2>/dev/null || true
else
  # Clear a stale marker once everything is back under threshold.
  rm -f "$MARKER" 2>/dev/null || true
fi

exit 0
