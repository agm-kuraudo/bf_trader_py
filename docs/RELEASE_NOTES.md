# Betfair Trader 0.6 — Release Notes

**Released:** 9 October 2026
**Sprint:** Ruby (September 2026)
**Epic:** [SP-334 Season Data Capture](https://amainit.atlassian.net/browse/SP-334)
**Scope:** 16 tickets — 6 stories, 3 tasks, 1 spike, 6 bugs. All Done.

## Headline

0.6 is the release that got **live season data capture working again** after a
dormant period of over a year. The last real capture cycle was March 2025; by
the start of this season the pipeline was silently broken. This release
diagnosed why, rebuilt the path from committed code to running code, and
stood capture back up — then added the reliability, monitoring and
data-quality checks needed to trust the data it produces.

Because season data is perishable (any period without capture is a permanent
gap), this was the highest-priority Betfair work despite Betfair strategy
being otherwise deprioritised.

## Themes

### 1. Got capture running again (the core win)

- **Diagnosed the dormancy** ([SP-329](https://amainit.atlassian.net/browse/SP-329), spike) — mapped the undocumented Pi deployment: containers, where the running code lived, boot/autostart config, Rundeck jobs, and the Postgres location. Produced the first real documentation of the setup and the repair-vs-rebuild recommendation that SP-328 acted on.
- **Rebuilt and redeployed capture** ([SP-328](https://amainit.atlassian.net/browse/SP-328)) — root cause of the gap was a startup failure (the app expected a Vault host that no longer existed), not a persistence problem. Restored the broken code-sync path to the Pi, replaced the ~15-month-old baked image with a repeatable `docker compose up -d --build` deploy, and confirmed data landing on a recurring basis. Reconciled the stale pre-spike spec against reality rather than implementing against wrong assumptions.
- **Live test run** ([SP-262](https://amainit.atlassian.net/browse/SP-262)) — watched a real match end to end, confirming the market odds, application behaviour and data flow all held up over a full game.

### 2. Simplified the secret/config story

- **Replaced Vault with a `.env` file** ([SP-292](https://amainit.atlassian.net/browse/SP-292)) — removed HashiCorp Vault entirely (client code, build/config/startup scripts, the "unlock the Vault" job) in favour of `python-dotenv`. Removed a whole class of startup fragility: no more starting, unsealing and authenticating a Vault container just to run the app. This directly fixed the root cause of the capture outage.

### 3. Made capture reliable, not just working

- **Immediate first odds fetch + configurable cadence** ([SP-302](https://amainit.atlassian.net/browse/SP-302)) — newly identified targets now fetch odds immediately on going OPEN instead of waiting out a 4-hour timer (which had been leaving `bf.market_table` empty). Update-frequency tiers (in-play, <3h, <6h, <12h, >12h), stale-target cleanup and loop thresholds are now driven from `config/strategy.yaml`, with backwards-compatible defaults.
- **Monitor service robustness bugs:**
  - Crash on stale/expired market targets ([SP-301](https://amainit.atlassian.net/browse/SP-301))
  - Single-instance lock permanently poisoned by any crashed run ([SP-330](https://amainit.atlassian.net/browse/SP-330))
  - In-play polling not tightening to the 5s cadence — effective ~900s gaps ([SP-343](https://amainit.atlassian.net/browse/SP-343))

### 4. Can now trust the data, not just collect it

- **Post-event data-quality verification** ([SP-332](https://amainit.atlassian.net/browse/SP-332), 8 pts) — the standout piece. Freshness checks answer "is data arriving now?"; this answers the separate, harder question "once matches finish, is the captured data present, consistent and actually useful for analysis?" Verifies presence, coverage against expected cadence tiers, consistency (well-formed, non-null, monotonic, no dupes) and sufficiency per market — with a daily post-event report and alerting. This is what ultimately decides whether the perishable data we're accruing is worth analysing downstream (VPA / ML).

### 5. Shored up the infrastructure underneath

- **Added external storage to the Pi** ([SP-338](https://amainit.atlassian.net/browse/SP-338)) — the SP-336 outage was the 29G SD-card root filesystem hitting 100% and crash-looping Postgres. Attached and persisted a 64GB USB drive and relocated high-growth/high-value data onto it, removing a recurring single point of failure and extending SD-card life. Documented recovery for a missing drive at boot.
- **Reconciled DB scripts with the live schema + TimescaleDB** ([SP-352](https://amainit.atlassian.net/browse/SP-352)) — caught schema drift (two live `quality_*` tables missing from the creation scripts and verifier) before a destructive rebuild could silently drop them, and switched `my_postgres` to a TimescaleDB image. Unblocks the persistent market-data store work (SP-349).

### 6. Housekeeping

- Fixed stale unit tests referencing outdated monitor/db APIs ([SP-331](https://amainit.atlassian.net/browse/SP-331))
- `DB_HOST` mismatch between local dev and Docker ([SP-299](https://amainit.atlassian.net/browse/SP-299))
- `call_auth` using class instead of instance attributes ([SP-300](https://amainit.atlassian.net/browse/SP-300))
- `bf_web_server.service` failing on boot via a stale venv path ([SP-361](https://amainit.atlassian.net/browse/SP-361))

## Retrospective read

**What went well**

- The spike-first approach paid off: SP-329 turned a guesswork situation into a facts-based rebuild, and caught that the stale spec would have led the implementation astray.
- Scope discipline held — SP-292 explicitly refused to fix unrelated issues it uncovered and instead raised linked tickets, which is likely where several of this release's bugs came from.
- The release tells a coherent story: get it running → make it reliable → make it trustworthy → make the infra durable. Not just a bag of fixes.

**What this sets up**

- A trustworthy, growing season dataset — the actual point of the whole epic — ready for VPA / ML analysis downstream.
- TimescaleDB in place and schema reconciled, unblocking the persistent market-data store (SP-349).

**Carried forward / known follow-ups**

- SP-344 — quality check alerts non-zero on days with zero settled matches (still Backlog).
- SP-294 / SP-293 — Pi teardown (exited containers, dangling images, Rundeck debris) deliberately kept out of this release.
- SP-349 — persistent market-data store, now unblocked by SP-352.
