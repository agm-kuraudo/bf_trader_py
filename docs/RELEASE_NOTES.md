# Betfair Trader 0.7 — Release Notes

**Released:** ~20 October 2026
**Epic:** [SP-367 Betfair Data Analysis & Strategy Discovery](https://amainit.atlassian.net/browse/SP-367)
**Scope:** 10 tickets — epic SP-367 + 5 children (4 stories, 1 task), plus 4 riders (1 story, 1 task, 2 bugs). All Done. ~31 pts.

## Headline

0.7 is the release that turned 0.6's growing dataset into an **evidence-backed
answer to one question: is there anything tradeable in the captured Betfair
odds?** The answer is **no — not with the two obvious odds-only strategies, on
the data as captured.** That is a successful outcome in the terms the release
set itself: "no clear edge found, here's the evidence" de-risks everything that
comes after, and the work produced reusable, trustworthy tooling plus a clear,
prioritised map of what would have to change to ask better questions.

Crucially, the analysis surfaced *why* the obvious edges don't appear and what
the capture pipeline would need to expose to ask sharper questions — captured as
a dependency-linked set of follow-ups that now feed 0.8 planning.

## Themes

### 1. Built the research tooling (the foundation)

- **Clean research dataset loader** ([SP-368](https://amainit.atlassian.net/browse/SP-368), 8 pts) — replaced the brittle single-market `ast.literal_eval` read path with a tested, reusable loader that turns `bf.market_table` into a tidy per-match, per-runner time-series across the whole season: **25,657 records, 0 unparseable, 41 matches**, implied-probability book computable for 99.7% of instants. Pure-core / thin-DB-wrapper split, property-tested, re-runs in seconds against a fresh parquet. Every 0.7 verdict is built on this. See `docs/research_dataset_schema.md`.

### 2. Characterised the market (what the data actually does)

- **Exploratory odds & volume analysis** ([SP-369](https://amainit.atlassian.net/browse/SP-369), 5 pts) — headline finding: **favourites drift *out* (lengthen) pre-match, +2.73% on average**, with the field roughly symmetric around zero. Pre-match book sits just above 1.0 (tight ~0.6% overround); in-play is volatile and event-driven (book 0.31–1.80). Also flagged the two structural gaps that shaped everything after: **no matched-volume** (null on every row) and **runners captured at distinct timestamps**. See `docs/SP-369_odds_behaviour_analysis.md`.

### 3. Tested two odds-only strategies — both ruled out with evidence

- **Green-out / cross-outcome arbitrage scan** ([SP-370](https://amainit.atlassian.net/browse/SP-370), 5 pts) — **NOT VIABLE.** A priced back-all-three green-out does occur pre-match but is **rare (3.3%) and tiny (median 0.13% after commission)**. The eye-catching in-play "green-outs" (up to 207% "profit") are **forward-fill artifacts** of the timestamp mismatch, not real opportunities. See `docs/SP-370_green_out_scan.md`.
- **Pre-match drift trading** ([SP-371](https://amainit.atlassian.net/browse/SP-371), 8 pts) — **NOT VIABLE.** Despite SP-369's real +2.73% drift, every framing (momentum + mean-reversion, every entry/exit window) **loses ~1–2% per unit**, because the drift is smaller than the **back/lay spread** crossed on a round trip (median pre-match spread 1.33%). A real signal that isn't a real edge after costs. See `docs/SP-371_drift_trading.md`.

### 4. Synthesised the verdict and set the direction

- **0.7 synthesis** ([SP-372](https://amainit.atlassian.net/browse/SP-372), 2 pts) — pulled the three findings into one tradeability verdict and a prioritised 0.8 recommendation: **fix capture first (coherent snapshots + volume), then make a strategic market decision — don't backtest, because 0.7 found nothing worth backtesting.** See `docs/SP-372_0.7_synthesis.md`.

### 5. Admin / reliability riders (kept small, done alongside)

- **Automated Postgres backups on the Pi** ([SP-363](https://amainit.atlassian.net/browse/SP-363), 3 pts) — scheduled `pg_dump` + off-Pi retention, replacing the single manual dump that was the only thing protecting the perishable season data. See `docs/SP-363_postgres_backup_runbook.md`.
- **Refreshed stale Rundeck job definitions** ([SP-345](https://amainit.atlassian.net/browse/SP-345), 1 pt).
- **Quality check alerts on zero-settled-match days** ([SP-344](https://amainit.atlassian.net/browse/SP-344), bug) — stopped the capture monitoring crying wolf (the known follow-up carried forward from 0.6).
- **Monitor service IndexError on empty `listRunnerBook`** ([SP-403](https://amainit.atlassian.net/browse/SP-403), bug) — crash on an empty runner-book result, plus investigation of why a runner returns no odds.

## The cross-cutting lesson (worth more than the two verdicts)

**The capture shape limits the questions, not just the answers.** All three
analyses kept hitting the same two walls — no volume/available-size (so a priced
edge can't be confirmed as a *matchable* edge) and runners captured at distinct
timestamps (forcing a forward-fill that is benign pre-match but fabricates
impossible in-play books). These aren't analysis bugs — they're capture
limitations, and fixing them is the single highest-leverage thing for any future
Betfair strategy work. That insight is the backbone of the 0.8 plan.

## Retrospective read

**What went well**

- The release answered its question honestly. "No edge, here's the evidence" is
  a real result, not a failure — and it was reached with property-tested tooling
  rather than hunch.
- The pure-core / thin-wrapper pattern from 0.6 paid off again: one loader
  (SP-368) underpins three independent analyses, each re-runnable in seconds.
- Scope discipline held — the strategy tests stayed strictly odds-only with no
  external-data dependency, and the gaps they found were captured as linked
  follow-ups rather than scope-creeping into 0.7.

**What this sets up**

- A clear, evidence-backed direction for 0.8: fix capture (coherent snapshots +
  volume), start capturing a structurally more promising market (Over/Under
  goals), and investigate historic data as a fast path to analysis.
- A reusable, trustworthy analysis stack that 0.8+ builds on, not from scratch.

**Carried forward / known follow-ups**

- SP-396 — coherent per-poll market snapshots (the lynchpin for cross-outcome work).
- SP-384 — capture matched-volume / available-size.
- SP-397 / SP-398 / SP-399 — re-assessments of the ruled-out analyses, blocked by SP-396/SP-384.
- SP-400 / SP-401 / SP-402 — new direction (goals capture, historic-data spike + pipeline).

---

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
