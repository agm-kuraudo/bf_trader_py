#!/usr/bin/env bash
#
# SP-395 — Telegram failure notification launcher for Betfair Rundeck jobs.
#
# Rundeck invokes this as an on-failure *local-command* notification, passing
# the job name, execution id, and execution URL as positional arguments:
#
#     scripts/telegram_notify.sh "<job name>" "<exec id>" "<exec url>"
#
# In the Rundeck job YAML the command is wired with Rundeck's notification
# context variables, e.g.:
#
#     notification:
#       onfailure:
#         plugin:
#           type: command
#           configuration:
#             command: /usr/local/bf_trader_py/scripts/telegram_notify.sh "${job.name}" "${job.execid}" "${job.url}"
#
# It runs on the Pi HOST (Rundeck runs notification commands directly, not in
# the bf_capture container), using the repo virtualenv python. The Python module
# (logic/telegram_notify.py) reads the dedicated Betfair Issues bot token and
# chat id from the repo-root .env via DotenvLoader.
#
# CRITICAL (SP-395 DoD): a notification must NEVER change the outcome of the job
# that triggered it. This wrapper therefore always exits 0 — a missing config, a
# missing interpreter, or a delivery failure is logged to stderr but never
# propagated. Rundeck has already recorded the job as FAILED by the time this
# runs; our job is only to tell the operator, not to re-fail anything.

set -uo pipefail

# Resolve the repo root the same way scripts/deploy.sh does, so the launcher
# works regardless of the caller's working directory.
#   1. $REPO_DIR env override
#   2. parent of this script's directory (when invoked by path)
#   3. compiled-in default deploy location on the Pi
DEFAULT_REPO_DIR="/usr/local/bf_trader_py"

if [ -n "${REPO_DIR:-}" ]; then
    REPO_DIR="$(cd "${REPO_DIR}" 2>/dev/null && pwd)" || REPO_DIR=""
else
    SCRIPT_PATH="${BASH_SOURCE[0]:-}"
    if [ -n "${SCRIPT_PATH}" ]; then
        SCRIPT_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
        REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
    else
        REPO_DIR=""
    fi
fi

if [ -z "${REPO_DIR}" ] || [ ! -d "${REPO_DIR}/.git" ]; then
    REPO_DIR="${DEFAULT_REPO_DIR}"
fi

# Prefer the project virtualenv python, fall back to python3 (same as deploy.sh).
if [ -x "${REPO_DIR}/.venv/bin/python" ]; then
    PYTHON="${REPO_DIR}/.venv/bin/python"
else
    PYTHON="python3"
fi

JOB_NAME="${1:-}"
EXEC_ID="${2:-}"
EXEC_URL="${3:-}"

# Run the sender from the repo root so `python -m logic.telegram_notify`
# resolves the package imports (api.auth.dotenv_loader, etc.). The module itself
# always exits 0; the `|| true` is belt-and-braces so an interpreter-launch
# failure still cannot fail this wrapper.
(
    cd "${REPO_DIR}" || exit 0
    "${PYTHON}" -m logic.telegram_notify "${JOB_NAME}" "${EXEC_ID}" "${EXEC_URL}"
) || true

exit 0
