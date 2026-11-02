# SP-395 — Email notifications on Betfair Rundeck job failures

## What this does

When a scheduled **Betfair** Rundeck job fails, Rundeck sends an **email**
failure notification (via SMTP2GO) so the failure lands in an inbox / on the
phone, instead of being discovered by manually checking the Rundeck UI.

Failure-only — there are no success notifications (out of scope for SP-395). The
Trading Bot jobs are covered separately.

## Why email (and the history behind it)

The original plan was a Telegram alert triggered by a Rundeck **on-failure
local-command notification** that ran a small sender script on the Pi. That
plan did not survive contact with the actual Rundeck install, and the reasons
are worth recording so we do not repeat the dead ends:

- **Rundeck has no "command"/"script" notification plugin.** On this install
  (Rundeck **5.10.0**, Community/free), the only built-in notification types are
  **email**, **webhook**, and **HTTP** — there is no plugin that runs a local
  command or script on failure. Both the UI and a YAML import reject it:
  `Notification Plugin type "command" was not found or could not be loaded`
  (confirmed in `service.log`: `Plugin command for service: Notification was
  not found`). This is a core Rundeck limitation at every version, not a
  version/edition gap — upgrading or going paid would not add it. Running a
  script on a notification requires writing a custom (Groovy) notification
  plugin.
- **A webhook can't call the Telegram Bot API directly.** Rundeck's webhook
  notification POSTs *its own* execution payload to a URL; it cannot shape the
  body into Telegram's `sendMessage` form (token in the path, `chat_id`+`text`
  in the body). Going webhook→Telegram would need a small translator service on
  the Pi — more moving parts than the problem warrants here.
- **Email is a built-in plugin — it just works, once SMTP authenticates.** No
  listener, no plugin install, no daemon. It also fires on the execution result
  (so it catches aborts/kills, not just a non-zero exit), via
  `${notification.eventStatus}` resolving to `FAILURE`/`KILLED`/etc.

### The SMTP saga (why SMTP2GO)

Email needs a working SMTP account, and the first choice fought back:

- **Office 365 (`smtp.office365.com`) is a dead end here.** Basic SMTP AUTH
  returns `535 5.7.3 Authentication unsuccessful` regardless of password,
  because Microsoft disables SMTP AUTH tenant-wide when **Security Defaults** is
  on — and the per-mailbox "Authenticated SMTP" toggle does nothing while
  Security Defaults is enabled. Microsoft is also fully deprecating SMTP basic
  auth (~Sept 2025), so this path is effectively closed. Enabling it would mean
  turning off Security Defaults tenant-wide (a real security downgrade) and may
  still not work.
- **SMTP2GO (free tier) works.** Purpose-built transactional SMTP, allows
  standard username/password auth, 1,000 emails/month free (ample for failure
  alerts). A direct `smtplib` send from the Pi succeeds, which proves the path
  independently of Rundeck.

## Mechanism

1. **Rundeck SMTP config** points Rundeck at SMTP2GO (see below).
2. **Each Betfair job** has an **On Failure → Email** notification that sends to
   a recipient address. Rundeck does the sending; there is no project code in
   the failure path.

## Rundeck SMTP configuration (on the Pi)

Added to `/etc/rundeck/rundeck-config.properties` (a backup of the original is
at `rundeck-config.properties.bak-sp395`), then `sudo systemctl restart
rundeckd`:

```properties
# SP-395: SMTP2GO mail settings for Rundeck failure notifications
grails.mail.host=mail-eu.smtp2go.com
grails.mail.port=587
grails.mail.username=amainit.co.uk
grails.mail.password=<SMTP2GO SMTP-user password>   # from the SMTP2GO dashboard
grails.mail.props.mail.smtp.starttls.enable=true
grails.mail.props.mail.smtp.auth=true
grails.mail.default.from=aiden@amainit.co.uk        # must be a VERIFIED sender in SMTP2GO
```

Notes:

- `mail-eu.smtp2go.com` is the **EU-region** host shown in this account's
  dashboard; `mail.smtp2go.com` also works. Port 587 with STARTTLS.
- `grails.mail.username` is the **SMTP user** from SMTP2GO → *Sending → SMTP
  Users* (not the account login). The **from** address must be verified under
  *Sending → Verified Senders*.
- The password is stored in plaintext in the config file (root/rundeck-readable
  only, mode `640`). Rundeck ships the jasypt encryption plugin if you later
  want to encrypt it.
- **Execution links:** `grails.serverURL` is `http://localhost:4440`, so links
  in the emails point at localhost (not useful off the Pi). The subject line
  carries the actionable information; set `grails.serverURL` to a reachable
  host if you want clickable links.

## Per-job notification (applied in the Rundeck UI)

For each Betfair job (**Monitor Service**, **Target Service**, **Data Quality
Checks**, **Verify Fresh Data**, **Postgres Backup**): Edit → **Notifications** →
under **If execution fails** add an **Email** notification. Suggested subject:

```
[Betfair] ${job.name} ${notification.eventStatus}
```

`${notification.eventStatus}` resolves to `FAILURE`, `KILLED`, etc. This can
also be set as a global template in `rundeck-config.properties`
(`rundeck.mail.failure.template.subject=...`) so it is consistent without
per-job entry.

> Rundeck stores job definitions in its own H2 DB; the tracked YAMLs under
> `build/run_deck_job_definition/` are **reference only** and do not drive the
> live jobs. The email notification is configured per job in the UI.

## Verifying (Definition of Done)

1. SMTP path proven from the Pi (done): a direct `smtplib` STARTTLS send via
   `mail-eu.smtp2go.com` as `amainit.co.uk` returned "message accepted by
   server" and the test email arrived.
2. Rundeck restarted cleanly with the mail config (done): no SMTP errors on
   startup; service responds on `localhost:4440`.
3. **Live failure test (the DoD):** add the On Failure email notification to a
   low-impact job (or a throwaway copy), point it at a command that exits
   non-zero, run it, and confirm the failure email arrives **and** the job is
   still recorded FAILED. Revert afterwards.

While complete-but-not-fully-verified, the ticket sits in **Mostly Done**; it
moves to **Done** only after the live failure email is confirmed on the Pi.

## The retained Telegram module (future use)

`logic/telegram_notify.py` (+ `tests/test_telegram_notify.py`) is kept
deliberately, even though email is the SP-395 mechanism. It is a small,
generic Betfair-side Telegram sender (dedicated "Betfair Issues" bot; token +
chat id read from `.env` via `DotenvLoader` under
`TELEGRAM_BETFAIR_ISSUES_BOT_TOKEN` / `TELEGRAM_BETFAIR_ISSUES_CHAT_ID`), with a
property-tested pure formatter. It is **not** wired to Rundeck (Rundeck can't
call it on failure), but it is ready for other notification needs — e.g. the Pi
disk alert (`scripts/pi/disk_alert.sh`) already anticipates reusing this layer
instead of its interim `logger`-only alert. The Rundeck launcher that the
command-notification approach needed (`scripts/telegram_notify.sh`) has been
removed, since that trigger mechanism does not exist on this install.
```
