"""Telegram failure notifications for Betfair Rundeck jobs (SP-395).

This is a small, self-contained delivery layer that sends a Telegram message
via the Bot API ``sendMessage`` method when a Betfair Rundeck job fails. It is
deliberately separate from the trading project's SP-357 report-delivery layer:
the two use *different* bots (a dedicated "Betfair Issues" bot here), and the
Betfair capture lives in its own codebase/deployment, so a shared import across
projects is not worth the coupling.

Design notes:

* **Secrets via DotenvLoader.** The bot token and chat id are read from the
  repo-root ``.env`` through :class:`api.auth.dotenv_loader.DotenvLoader`, the
  same mechanism every other secret in this project uses. The token is never
  logged or echoed.
* **Failure-isolated.** Sending a notification must never change the outcome of
  the job that triggered it (SP-395 Definition of Done). The CLI entry point
  therefore always exits 0, and :func:`send_telegram_message` returns a bool
  rather than raising on a delivery error. Only a *configuration* problem
  (missing token/chat id) raises, and the CLI swallows that too.
* **Minimal, degradable dependencies.** Prefers ``requests`` (already installed
  on the Pi host venv) but falls back to the stdlib ``urllib`` so the
  notification path keeps working even in a stripped-down environment. The
  failure path should not depend on much, because the thing that just broke may
  be exactly what a heavier dependency would need.

The message formatting (:func:`format_failure_message`) is a pure function with
no I/O so it can be property-tested deterministically, matching the pattern used
by ``logic/deploy_checks.py``.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader

# .env keys for the dedicated Betfair Issues bot. Kept here as the single source
# of truth so the CLI, tests, and docs agree.
TOKEN_KEY = "TELEGRAM_BETFAIR_ISSUES_BOT_TOKEN"
CHAT_ID_KEY = "TELEGRAM_BETFAIR_ISSUES_CHAT_ID"

# How long to wait on the Bot API before giving up. The notification is
# best-effort, so this is kept short to avoid holding anything up.
DEFAULT_TIMEOUT_S = 10


def format_failure_message(
    job_name: str,
    execution_id: str | None = None,
    execution_url: str | None = None,
) -> str:
    """Build the mobile-readable failure message for a Rundeck job.

    Pure function (no I/O) so it can be property-tested. The message leads with
    a clear failure marker and the job name, then adds the Rundeck execution id
    and a link when they are available, so the notification is actionable from a
    phone without opening the Rundeck UI first.

    Args:
        job_name: The Rundeck job name (e.g. ``"Monitor Service"``). When blank
            or ``None``, a neutral ``"(unknown job)"`` placeholder is used so the
            message is still coherent.
        execution_id: The Rundeck execution id, if known. Omitted from the
            message when blank/``None``.
        execution_url: A link to the Rundeck execution, if known. Omitted from
            the message when blank/``None``.

    Returns:
        A short plain-text message suitable for Telegram ``sendMessage``.
    """
    name = (job_name or "").strip() or "(unknown job)"
    lines = [f"\N{WARNING SIGN} Betfair job FAILED: {name}"]

    exec_id = (execution_id or "").strip()
    if exec_id:
        lines.append(f"Execution: #{exec_id}")

    url = (execution_url or "").strip()
    if url:
        lines.append(url)

    return "\n".join(lines)


def _post_message(token: str, chat_id: str, text: str, timeout_s: int) -> bool:
    """POST a message to the Telegram Bot API. Returns True on HTTP 2xx.

    Prefers ``requests`` when importable; otherwise uses the stdlib ``urllib``.
    Any transport/HTTP error is caught and reported as a ``False`` return (the
    caller logs a redacted message) so delivery problems never propagate.
    """
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}

    try:
        import requests  # local import: optional dependency

        resp = requests.post(api_url, json=payload, timeout=timeout_s)
        return 200 <= resp.status_code < 300
    except ImportError:
        # Fall back to the standard library.
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return False
    except Exception:
        # requests present but the call failed (timeout, connection, etc.).
        return False


def send_telegram_message(
    text: str,
    loader: DotenvLoader | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> bool:
    """Send ``text`` to the Betfair Issues Telegram chat.

    Reads the bot token and chat id from ``.env`` via :class:`DotenvLoader`.

    Args:
        text: The message body to deliver.
        loader: An optional pre-built :class:`DotenvLoader`. When ``None``, a
            loader is created against the repo-root ``.env``. Injectable for
            tests.
        timeout_s: Bot API request timeout in seconds.

    Returns:
        ``True`` when the Bot API accepted the message (HTTP 2xx), ``False`` on
        any delivery error.

    Raises:
        ConfigurationException: If the token or chat id is missing/empty in
            ``.env``. This is a *configuration* error, distinct from a delivery
            failure; the CLI entry point catches it so the job is still never
            affected.
    """
    if loader is None:
        loader = DotenvLoader()

    token = loader.get_secret(TOKEN_KEY)
    chat_id = loader.get_secret(CHAT_ID_KEY)

    return _post_message(token, chat_id, text, timeout_s)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point invoked by ``scripts/telegram_notify.sh``.

    Usage::

        python -m logic.telegram_notify "<job name>" ["<exec id>"] ["<exec url>"]

    Always returns 0. A notification must never change the outcome of the job
    that triggered it (SP-395 DoD), so neither a configuration problem nor a
    delivery failure is allowed to surface as a non-zero exit. Problems are
    reported to stderr with the token redacted.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    job_name = args[0] if len(args) > 0 else ""
    execution_id = args[1] if len(args) > 1 else ""
    execution_url = args[2] if len(args) > 2 else ""

    message = format_failure_message(job_name, execution_id, execution_url)

    try:
        delivered = send_telegram_message(message)
    except ConfigurationException as exc:
        # Missing/empty token or chat id. Report the KEY name (from the
        # exception), never the value.
        print(f"[telegram_notify] configuration error: {exc}", file=sys.stderr)
        return 0
    except Exception as exc:  # pragma: no cover - defensive catch-all
        print(f"[telegram_notify] unexpected error: {type(exc).__name__}", file=sys.stderr)
        return 0

    if not delivered:
        print("[telegram_notify] delivery failed (Bot API did not accept the message)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
