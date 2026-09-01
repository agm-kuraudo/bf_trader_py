#!/bin/bash
#
# SP-328 season-background-data-capture: repeatable rebuild-not-repair deploy.
#
# Authored for Linux/ARM — the Raspberry Pi 500 is the sole capture host
# (Req 2.4). There is deliberately NO .ps1 sibling: the Windows work PC powers
# off daily and is explicitly not the capture host, so a PowerShell deploy
# script would have no host to run against (documented design decision).
#
# Ordered steps (each failure returns a DISTINCT non-zero exit code and leaves
# the last known-good running container UNCHANGED):
#   1. code sync        (git pull)                  -> exit 11 on failure (E6, Req 7.7)
#   2. validate .env    (required keys non-empty)   -> exit 12 on failure (E1, Req 7.8)
#   3. build + recreate (docker compose up --build) -> exit 13 on failure (E7, Req 7.9)
#   4. post-deploy verify (Monitor cycle persists)  -> exit 14 on failure (Req 7.6)
#
# The container is only ever replaced by step 3, so a failure in step 1 or 2
# aborts BEFORE any container recreation and the previously running container
# is untouched.

set -euo pipefail

# --- Distinct exit codes per failure mode -----------------------------------
readonly EXIT_SYNC_FAILED=11
readonly EXIT_ENV_INVALID=12
readonly EXIT_BUILD_FAILED=13
readonly EXIT_VERIFY_FAILED=14

# --- Paths -------------------------------------------------------------------
# Resolve the repo root as the parent of this script's directory, so the
# script works regardless of the caller's working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_DIR}/.env"

# Prefer the project virtualenv python, fall back to python3.
if [ -x "${REPO_DIR}/.venv/bin/python" ]; then
    PYTHON="${REPO_DIR}/.venv/bin/python"
else
    PYTHON="python3"
fi

# Required .env keys (single source of truth for the list lives here; the
# emptiness check itself is delegated to logic.deploy_checks.validate_env).
readonly REQUIRED_KEYS=(
    BF_AppKey
    BF_USERID
    BF_PWD
    BF_CRT_FILE
    BF_KEY_FILE
    DB_HOST
    DB_PORT
    DB_NAME
    DB_USER
    DB_PWD
)

log() {
    echo "[deploy] $*"
}

err() {
    echo "[deploy][ERROR] $*" >&2
}

# --- Step 1: code sync -------------------------------------------------------
sync_code() {
    log "Step 1/4: syncing current code (git pull) in ${REPO_DIR}"
    if ! git -C "${REPO_DIR}" pull --ff-only; then
        err "code sync failed — leaving the running container unchanged (Req 7.7)."
        exit "${EXIT_SYNC_FAILED}"
    fi
    log "code sync OK."
}

# --- Step 2: validate .env ---------------------------------------------------
# Reuses logic.deploy_checks.validate_env for single-source-of-truth emptiness
# logic. Runs BEFORE any container recreation, so a missing key aborts without
# touching the running container (Req 7.8 / E1).
validate_env_file() {
    log "Step 2/4: validating required .env keys"

    if [ ! -f "${ENV_FILE}" ]; then
        err ".env not found at ${ENV_FILE} — aborting before container recreation (Req 7.8)."
        exit "${EXIT_ENV_INVALID}"
    fi

    local missing
    if ! missing="$(
        REQUIRED_KEYS_STR="${REQUIRED_KEYS[*]}" ENV_FILE="${ENV_FILE}" "${PYTHON}" - <<'PY'
import os
import sys

from dotenv import dotenv_values

from logic.deploy_checks import validate_env

required = os.environ["REQUIRED_KEYS_STR"].split()
values = dotenv_values(os.environ["ENV_FILE"])
missing = validate_env(values, required)
if missing:
    print(" ".join(missing))
    sys.exit(1)
sys.exit(0)
PY
    )"; then
        err "required .env value(s) missing or empty: ${missing}"
        err "aborting BEFORE container recreation (Req 7.8 / E1)."
        exit "${EXIT_ENV_INVALID}"
    fi

    log ".env validation OK."
}

# --- Step 3: build + recreate ------------------------------------------------
# docker compose up -d --build rebuilds the image from current code and
# recreates the container. If it fails, compose leaves the previously running
# container in place; we surface a distinct exit (Req 7.9 / E7).
build_and_recreate() {
    log "Step 3/4: building image and recreating container (docker compose up -d --build)"
    if ! docker compose --project-directory "${REPO_DIR}" up -d --build; then
        err "image build or container recreation failed — last known-good container retained (Req 7.9)."
        exit "${EXIT_BUILD_FAILED}"
    fi
    log "build + recreate OK."
}

# --- Step 4: post-deploy verification ----------------------------------------
# TODO(Task 7.2): Full 300s verification that a Monitor cycle runs current code
# and persists an odds row to bf_trader is COMPLETED UNDER TASK 7.2. This
# function is scaffolded here so step ordering and exit semantics are in place;
# Task 7.2 fills in the concrete freshness/persistence assertion.
verify_post_deploy() {
    log "Step 4/4: post-deploy verification"
    log "NOTE: full 300s Monitor-cycle persistence check is completed under Task 7.2."
    # Placeholder: intentionally a no-op success so the pipeline structure is
    # exercised. Task 7.2 will replace the body with a real check (e.g. run a
    # one-shot capture cycle and confirm a fresh row lands in bf.market_table
    # within 300s), and will `exit "${EXIT_VERIFY_FAILED}"` on failure.
    return 0
}

main() {
    log "starting deploy (rebuild-not-repair) for SP-328"
    sync_code
    validate_env_file
    build_and_recreate
    verify_post_deploy
    log "deploy complete."
}

main "$@"
