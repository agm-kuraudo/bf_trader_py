# SP-395 — Telegram notifications on Betfair Rundeck job failures

## What this does

When a scheduled **Betfair** Rundeck job fails (non-zero exit, kill, timeout, or
dispatch error), Rundeck fires an **on-failure notification** that runs a small
local command on the Pi. That command sends a Telegram message to a dedicated
**"Betfair Issues"** bot so the failure lands on the phone, instead of being
discovered by manually checking the Rundeck UI.

Failure-only — there are no success notifications (out of scope for SP-395). The
Trading Bot jobs are covered separately.

## Why this shape (and not the alternatives)

- **Rundeck on-failure notification, not a wrapper inside the job command.**
  Rundeck's notification fires on the *execution* result, so it catches failures
  a wrapper inside the command would miss: the job being killed or timed out, a
  node/dispatch failure, or the container failing to start before our code runs.
  It is declarative (a block in the job definition), so applying it to new jobs
  is a copy-paste, and it is a side effect of the FAILED result — it cannot
  change the job's own outcome.
- **A dedicated "Betfair Issues" bot, not SP-357's report bot.** SP-357 (in the
  *trading* project) is specifically a daily-report delivery layer with its own
  bot. Betfair failure alerts are a different purpose and a different codebase /
  deployment, so they get their own bot token and chat id. No cross-project
  import.
- **A local-command notification, not a webhook.** The sender runs fine on the
  Pi (the Rundeck host), so there is no need for an inbound webhook receiver.
  The token stays local in the repo `.env`; nothing is exposed.
- **Sender runs on the host via the repo venv, not inside the container.**
  Rundeck runs notification commands directly on the host, and the failure path
  should not depend on the `bf_capture` container (which may be exactly what
  just broke). The host `.venv` already has `requests` and `python-dotenv`; the
  sender also falls back to the stdlib `urllib` if `requests` is unavailable.

## Pieces

| Piece | Path | Role |
|---|---|---|
| Python sender | `logic/telegram_notify.py` | Formats the message and posts to the Bot API `sendMessage`. Reads the bot token + chat id from `.env` via `DotenvLoader`. Pure formatter is property-tested. |
| Launcher | `scripts/telegram_notify.sh` | What Rundeck runs. Resolves the repo root + venv python (same logic as `deploy.sh`), calls `python -m logic.telegram_notify`, **always exits 0**. |
| Job definitions | `build/run_deck_job_definition/*.yaml` | Tracked reference copies of the Betfair Rundeck jobs, each with the `notification.onfailure` block added. |
| `.env` keys | `.env.example` | `TELEGRAM_BETFAIR_BOT_TOKEN`, `TELEGRAM_BETFAIR_CHAT_ID`. |
| Tests | `tests/test_telegram_notify.py` | Formatter properties + config/delivery/CLI behaviour (no network). |

> **Note on the job YAMLs:** the files under `build/run_deck_job_definition/`
> are tracked **for reference only** — editing them here does **not** change the
> live Rundeck jobs. Rundeck stores job definitions in its own H2 DB; the live
> jobs must be updated in the Rundeck UI/API (see "Applying to live Rundeck").

## Configuration

Two new secrets in the repo-root `.env` (never committed):

```
TELEGRAM_BETFAIR_ISSUES_BOT_TOKEN=...   # from BotFather, for the Betfair Issues bot
TELEGRAM_BETFAIR_ISSUES_CHAT_ID=...     # the chat to deliver to
```

### Creating the bot and getting the chat id

1. In Telegram, message **@BotFather**, `/newbot`, name it e.g. *Betfair
   Issues*, and copy the **token** it returns → `TELEGRAM_BETFAIR_BOT_TOKEN`.
2. Start a chat with the new bot (send it any message) so it is allowed to
   message you.
3. Get the chat id: open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read
   `result[].message.chat.id`, or use a chat-id helper bot. Put it in
   `TELEGRAM_BETFAIR_CHAT_ID`.
4. Deploy the updated `.env` to the Pi (`/usr/local/bf_trader_py/.env`). The
   token/chat id are read from that file at notification time.

## The launcher must be executable

Rundeck runs the on-failure command **directly** (not via `bash ...`), so
`scripts/telegram_notify.sh` must have the executable bit set or Rundeck cannot
launch it (it fails with a permission error and no alert is sent). The file is
tracked in git with mode `755`, so a normal `git pull`/`deploy.sh` on the Pi
preserves it. If you ever see a permission error (e.g. after copying the file by
other means, or on a filesystem that drops the bit), restore it with:

```bash
chmod +x /usr/local/bf_trader_py/scripts/telegram_notify.sh
```

Running it as `bash scripts/telegram_notify.sh ...` also works for a manual
smoke test and sidesteps the bit, but the live Rundeck notification needs the
executable bit because it invokes the path directly.

## The notification block (per job)

Each Betfair job definition carries:

```yaml
  notification:
    onfailure:
      plugin:
        type: command
        configuration:
          command: /usr/local/bf_trader_py/scripts/telegram_notify.sh "${job.name}"
            "${job.execid}" "${job.url}"
```

Rundeck expands `${job.name}`, `${job.execid}`, and `${job.url}` into the
positional args the launcher passes to the Python sender. The message looks
like:

```
⚠ Betfair job FAILED: Monitor Service
Execution: #12345
http://localhost:4440/.../execution/show/12345
```

## Applying to new jobs

Any new Betfair job adopts failure notifications by adding the same
`notification.onfailure` block above to its definition. Nothing else is needed —
the launcher and sender are generic (the job name comes from `${job.name}`).
Keep the tracked copy under `build/run_deck_job_definition/` in sync.

## Applying to live Rundeck

The tracked YAMLs are reference only. To make it live on the Pi, for each
Betfair job: in the Rundeck UI, open the job → **Edit** → **Notifications** →
add an **On Failure** notification of type **"Run script/local command"** with
the command above (or import the updated YAML via the job's **Upload
Definition**). Rundeck is at `localhost:4440` on the Pi, projects `Betfair` and
`VPA`.

## Verifying (Definition of Done)

The DoD requires a **deliberately-failed Betfair job to produce a Telegram
alert, verified on the Pi**. Suggested check:

1. Ensure `.env` on the Pi has the two new keys populated and the latest code is
   deployed (`scripts/deploy.sh`).
2. Smoke-test the launcher directly on the Pi (no Rundeck needed):
   ```bash
   /usr/local/bf_trader_py/scripts/telegram_notify.sh "Smoke Test" "0" "http://localhost:4440"
   ```
   A message should arrive; the script exits 0 regardless.
3. Trigger a real failure path: temporarily point a low-impact job (e.g. a
   throwaway test job, or a copy of **Verify Fresh Data**) at a command that
   exits non-zero, run it, and confirm the Telegram alert arrives **and** the
   job is still recorded FAILED (the notification did not change the outcome).
   Revert the job afterwards.

While the code is complete but not yet deployed/verified on the Pi, the ticket
sits in **Mostly Done**; it moves to **Done** only after the live failure test
passes on the Pi (per the deployable-work Definition of Done).

## Guarantees

- **Never breaks the job.** `scripts/telegram_notify.sh` and
  `logic/telegram_notify.py::main` always exit 0. A missing token/chat id, a
  missing interpreter, or a Bot API error is logged to stderr and swallowed.
- **No secret leakage.** The token is read via `DotenvLoader` and never logged;
  configuration errors report the **key name**, not the value.
- **Reusable.** The sender is a generic Betfair-side Telegram layer. The Pi disk
  alert (`scripts/pi/disk_alert.sh`) already anticipates reusing this
  (SP-357/SP-395 delivery layer) instead of its interim `logger`-only alert.
```
