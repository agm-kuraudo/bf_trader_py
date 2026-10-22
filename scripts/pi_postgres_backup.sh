#!/bin/bash
#
# SP-363 pi-postgres-backup-automation: the Pi-side Backup_Job body.
#
# This script runs on the Pi HOST (NOT inside a container) and is invoked by the
# native Rundeck "Postgres Backup" job (build/run_deck_job_definition/Postgres_Backup.yaml)
# at its deployed path /usr/local/bf_trader_py/scripts/pi_postgres_backup.sh.
#
# What it does, in order (fail-closed throughout):
#   1. Pre-flight: the Backup_Share (/home/mypi/bf_backups) must exist and be
#      writable, AND the SD-card root it lives on must have at least the
#      configured minimum free space. If not, ABORT before writing anything and
#      exit non-zero WITHOUT invoking retention (Req 4.1).
#   2. Dump:  docker exec my_postgres pg_dump -Fc bf_trader  ->  a new
#      bf_trader_<UTC-timestamp>.dump under the Backup_Share. The name uses a
#      fixed-width, lexicographically sortable UTC timestamp YYYYMMDDThhmmssZ
#      (Req 1.1, 1.2). Dumps are NEVER written onto /mnt/data (Req 2.1).
#   3. Validate: the dump counts as a Valid_Dump only when pg_dump exited 0 AND
#      the output file is non-zero bytes. On failure, remove any zero-byte
#      output file, exit non-zero, and do NOT invoke retention (Req 1.5, 3.2).
#   4. Retention: ONLY on a Valid_Dump, invoke the keep-last-3 retention executor
#      (scripts/backup_retention.py main()) via the venv Python, passing the
#      Backup_Share path (Req 3.2 fail-closed gating).
#
# All progress, errors and timing go to stdout/stderr so Rundeck records the
# outcome, the full log, and the run duration (Req 1.3). A non-zero exit here
# marks the Rundeck run failed (keepgoing:false in the job definition).
#
# Rundeck visibility: distinct non-zero exit codes make failures diagnosable in
# run history.

set -euo pipefail

# --- Distinct exit codes per failure mode -----------------------------------
readonly EXIT_PREFLIGHT_FAILED=21   # Backup_Share unwritable or low free space (Req 4.1)
readonly EXIT_DUMP_FAILED=22        # pg_dump non-zero exit or zero-byte output (Req 1.5)
readonly EXIT_RETENTION_FAILED=23   # retention executor reported a problem (Req 3.7)

# --- Configurable knobs (override via environment for non-default hosts) -----
#
# These default to sensible Pi paths. On the Pi the venv Python path differs
# from the Windows dev path, so both the interpreter and the retention script
# path are variables here; override them with the matching env var if the repo
# or venv lives elsewhere.

# Repo deployed location on the Pi (matches the SP-328 deploy + the Rundeck job).
REPO_DIR="${REPO_DIR:-/usr/local/bf_trader_py}"

# The off-USB Backup_Share on the SD-card root (SP-338). NEVER /mnt/data.
BACKUP_SHARE="${BACKUP_SHARE:-/home/mypi/bf_backups}"

# Minimum free space (in MiB) required on the filesystem holding the
# Backup_Share before a dump is attempted. Pre-flight aborts below this (Req 4.1).
MIN_FREE_MB="${MIN_FREE_MB:-500}"

# Postgres source (SP-338): custom-format dump of `bf_trader` in container
# `my_postgres`.
PG_CONTAINER="${PG_CONTAINER:-my_postgres}"
PG_DATABASE="${PG_DATABASE:-bf_trader}"

# Retention executor: the venv Python running scripts/backup_retention.py.
# Prefer the project virtualenv python, fall back to python3 (mirrors deploy.sh).
if [ -n "${PYTHON:-}" ]; then
    : # honour an explicit override
elif [ -x "${REPO_DIR}/.venv/bin/python" ]; then
    PYTHON="${REPO_DIR}/.venv/bin/python"
else
    PYTHON="python3"
fi
RETENTION_SCRIPT="${RETENTION_SCRIPT:-${REPO_DIR}/scripts/backup_retention.py}"

# --- Logging helpers (everything to stdout/stderr for Rundeck) ---------------
log() {
    echo "[backup] $*"
}

err() {
    echo "[backup][ERROR] $*" >&2
}

# --- Step 1: pre-flight ------------------------------------------------------
# The Backup_Share must exist and be writable, and its filesystem must have at
# least MIN_FREE_MB free. On ANY failure we abort BEFORE writing a dump and
# WITHOUT invoking retention, leaving previously retained dumps untouched (Req 4.1).
preflight() {
    log "Step 1/3: pre-flight checks on Backup_Share '${BACKUP_SHARE}' (min free ${MIN_FREE_MB} MiB)"

    # The share is created by SP-338; we do NOT mkdir it. A missing share is a
    # misconfiguration we fail closed on rather than silently recreating.
    if [ ! -d "${BACKUP_SHARE}" ]; then
        err "Backup_Share '${BACKUP_SHARE}' does not exist (expected to be created by SP-338). Aborting before any write."
        exit "${EXIT_PREFLIGHT_FAILED}"
    fi

    if [ ! -w "${BACKUP_SHARE}" ]; then
        err "Backup_Share '${BACKUP_SHARE}' is not writable. Aborting before any write (Req 4.1)."
        exit "${EXIT_PREFLIGHT_FAILED}"
    fi

    # Guard against accidentally pointing the share at the USB mount: dumps must
    # live on the SD-card root, never on /mnt/data (Req 2.1).
    case "${BACKUP_SHARE}" in
        /mnt/data | /mnt/data/*)
            err "Backup_Share '${BACKUP_SHARE}' is on /mnt/data; dumps must be written off the USB disk (Req 2.1). Aborting."
            exit "${EXIT_PREFLIGHT_FAILED}"
            ;;
    esac

    # Free space on the filesystem that actually holds the share, in MiB.
    # `df -Pm` gives POSIX, portable 1 MiB-block output; column 4 is available.
    local free_mb
    if ! free_mb="$(df -Pm "${BACKUP_SHARE}" | awk 'NR==2 {print $4}')"; then
        err "Could not determine free space for '${BACKUP_SHARE}'. Aborting before any write."
        exit "${EXIT_PREFLIGHT_FAILED}"
    fi

    if [ -z "${free_mb}" ] || ! [[ "${free_mb}" =~ ^[0-9]+$ ]]; then
        err "Unexpected free-space reading ('${free_mb}') for '${BACKUP_SHARE}'. Aborting before any write."
        exit "${EXIT_PREFLIGHT_FAILED}"
    fi

    if [ "${free_mb}" -lt "${MIN_FREE_MB}" ]; then
        err "Only ${free_mb} MiB free on the filesystem holding '${BACKUP_SHARE}', below the ${MIN_FREE_MB} MiB minimum. Aborting before any write (Req 4.1)."
        exit "${EXIT_PREFLIGHT_FAILED}"
    fi

    log "pre-flight OK: share writable, ${free_mb} MiB free (>= ${MIN_FREE_MB} MiB)."
}

# --- Step 2+3: dump + validate ----------------------------------------------
# Produces a Valid_Dump or fails closed. On any failure the (possibly partial /
# zero-byte) output file is removed and we exit non-zero WITHOUT retention
# (Req 1.5, 3.2). Echoes the produced filename on success via DUMP_PATH.
DUMP_PATH=""
run_dump() {
    local timestamp
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local dump_name="bf_trader_${timestamp}.dump"
    DUMP_PATH="${BACKUP_SHARE}/${dump_name}"

    log "Step 2/3: dumping '${PG_DATABASE}' from container '${PG_CONTAINER}' -> ${DUMP_PATH}"

    # Run pg_dump inside the container and stream the custom-format archive to the
    # host Backup_Share. We capture the dump's exit status explicitly (pipefail is
    # on, but we also disable -e around the redirect so we can clean up on failure).
    local dump_rc=0
    set +e
    docker exec "${PG_CONTAINER}" pg_dump -Fc "${PG_DATABASE}" > "${DUMP_PATH}"
    dump_rc=$?
    set -e

    # Validate: Valid_Dump requires BOTH a zero exit code AND a non-zero file size.
    if [ "${dump_rc}" -ne 0 ]; then
        err "pg_dump exited ${dump_rc}; this is NOT a Valid_Dump."
        remove_failed_output
        err "removed failed output and skipping retention (fail-closed, Req 1.5/3.2)."
        exit "${EXIT_DUMP_FAILED}"
    fi

    if [ ! -s "${DUMP_PATH}" ]; then
        err "dump output '${DUMP_PATH}' is zero bytes; this is NOT a Valid_Dump (Req 1.5)."
        remove_failed_output
        err "removed zero-byte output and skipping retention (fail-closed, Req 1.5/3.2)."
        exit "${EXIT_DUMP_FAILED}"
    fi

    log "Step 3/3: validated Valid_Dump: ${DUMP_PATH} ($(dump_size_bytes) bytes)."
}

# Report the dump file size in bytes (best-effort; blank if it cannot be read).
dump_size_bytes() {
    stat -c %s "${DUMP_PATH}" 2>/dev/null || echo "?"
}

# Remove a failed/zero-byte output file so it is never retained as a Dump_Artifact
# (Req 1.5). Best-effort: a cleanup failure is logged but does not mask the
# original dump failure.
remove_failed_output() {
    if [ -n "${DUMP_PATH}" ] && [ -e "${DUMP_PATH}" ]; then
        if rm -f "${DUMP_PATH}"; then
            log "removed failed output file '${DUMP_PATH}'."
        else
            err "could not remove failed output file '${DUMP_PATH}'; it may linger on the share."
        fi
    fi
}

# --- Retention (only on a Valid_Dump) ----------------------------------------
# Invoke the keep-last-3 retention executor via the venv Python, passing the
# Backup_Share path. Reached ONLY after a Valid_Dump, so a failed/empty dump can
# never trigger pruning (Req 3.2 fail-closed).
run_retention() {
    log "Valid_Dump produced; invoking retention: ${PYTHON} ${RETENTION_SCRIPT} ${BACKUP_SHARE}"

    local retention_rc=0
    set +e
    "${PYTHON}" "${RETENTION_SCRIPT}" "${BACKUP_SHARE}"
    retention_rc=$?
    set -e

    if [ "${retention_rc}" -ne 0 ]; then
        err "retention executor exited ${retention_rc} (a delete failed or the share was unreadable, Req 3.7). The new Valid_Dump is already written and preserved."
        exit "${EXIT_RETENTION_FAILED}"
    fi

    log "retention complete."
}

main() {
    local start_epoch
    start_epoch="$(date -u +%s)"
    log "starting SP-363 Postgres backup at $(date -u +%Y-%m-%dT%H:%M:%SZ) (UTC)"

    preflight
    run_dump
    run_retention

    local end_epoch
    end_epoch="$(date -u +%s)"
    log "backup complete in $((end_epoch - start_epoch))s. Dump_Artifact: ${DUMP_PATH}"
}

main "$@"
