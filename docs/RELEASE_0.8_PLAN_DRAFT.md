# Betfair Trader 0.8 — Release Plan (draft)

> **Status:** planning draft for refinement. Builds on `docs/0.8_direction_notes.md`
> (the strategy thinking) and `docs/SP-372_0.7_synthesis.md` (the 0.7 verdict).
> Candidate tickets already exist in Jira (SP project, label `betfair`,
> `backlog-unplanned`, **no fixVersion yet**). The actual 0.8 fixVersion is set
> at refinement — assigning it is the one deliberate, final step. Keys are real.

## Theme

**Fix the capture foundations and open the two fastest paths to a real edge —
without trying to find the edge yet.**

0.7 proved EPL/UCL MATCH_ODDS is efficient: no tradeable odds-only edge, and the
analyses kept hitting the same two capture limits (no volume, incoherent
timestamps). 0.8's job is to **remove those limits and plant the seeds** for the
next edge, not to backtest — because 0.7 found nothing worth backtesting.

## The constraint that shapes the release

Analysis needs **accumulated** data; a new market has none on day one. So
capture and analysis of a *new* market cannot share a release — they're
separated by accumulation time. This forces a two-track structure (from the
direction notes):

- **Track A (own capture, long lead):** start capturing promising new markets
  now; analyse them a release or two later once data has built up.
- **Track B (historic data, short lead):** Betfair publishes historical exchange
  data — a possible fast path to analysing a market in weeks instead of a year.
  Spike it first; build the pipeline only if the spike says GO.

0.8 is deliberately a **foundation + investigation** release. The meaty analysis
is a 0.9 candidate, gated on data (accumulated or historic) being ready.

---

## Candidate pool (all SP, label `betfair`, unresolved)

Grouped by how they serve the plan. Points marked *(TBD)* are unestimated in
Jira and need a WORD estimate at refinement.

### Foundation — capture fixes (highest priority; unblock everything)

| Key | Summary | Type | Est |
|---|---|---|---|
| **SP-396** | Capture all runners' odds in a coherent per-poll market snapshot | Story | TBD |
| **SP-384** | Capture matched-volume in Betfair odds snapshots | Story | TBD |

These are the two walls every 0.7 analysis hit. SP-396 is the **lynchpin** for
any cross-outcome work (pre-match stale-leg noise; in-play books are
uncomputable without it). SP-384 is what lets a priced edge be confirmed as a
*matchable* edge. They share the same market-book call and are **best delivered
together**. They also unblock the three deferred re-assessments (SP-397/398/399).
Neither can be backfilled — every day un-fixed is a permanent gap in clean data.

### Track A — own capture of a better market (plant the seed)

| Key | Summary | Type | Est |
|---|---|---|---|
| **SP-400** | Start capturing Over/Under goals markets (EPL/UCL) | Story | TBD |

Capture-only this release (analysis waits for accumulation). Over/Under goals is
the single most promising *untested* market: one-dimensional maths and a
genuinely **modelable in-play time-decay** signal. Low incremental cost — same
fixtures, extra market types on the same calls. Best landed **on top of** SP-396
/ SP-384 so goals data is clean and carries volume from day one.

### Track B — historic data (the fast path to answers)

| Key | Summary | Type | Est |
|---|---|---|---|
| **SP-401** | Spike: investigate Betfair historic data (tiers, shape, cost, licence) | Story (spike) | TBD (time-box) |
| SP-402 | Build historic-data processing pipeline (stream replay → dataset) | Story | TBD |

SP-401 is the **highest-value new investigation** — it decides whether we can
analyse a market in weeks (historic) or must wait a year (self-capture).
Time-boxed; the finding is the deliverable. **SP-402 is gated on the spike** and
is a substantial feature in its own right → **0.9 candidate, not 0.8.**

### Improvement / admin / chore riders (the 1–2 we pull in)

| Key | Summary | Type | Est |
|---|---|---|---|
| **SP-395** | Notify me on Rundeck job failures (Betfair) via Telegram | Task | 3 |
| SP-294 | Cleanup items for Betfair/Pi (container/image prune, `.dockerignore`, log rotation, disk alert) | Story | 3 |
| SP-362 | Review and revive the BF Trader web app (`web_site.py`) | Task | TBD |

### Deferred analysis (0.9 candidates — listed for context, NOT in 0.8)

| Key | Summary | Blocked by |
|---|---|---|
| SP-397 | Re-assess pre-match green-out with volume + coherent snapshots | SP-396, SP-384 |
| SP-398 | Assess in-play green-out / arbitrage | SP-396, SP-384 |
| SP-399 | Re-run pre-match drift with size-aware fills | SP-384 |

These need both the capture fix **and** accumulated clean data, so even once
SP-396/384 ship they have nothing coherent to run on until data builds up.
Natural 0.9 work alongside the first goals-market analysis.

---

## Recommended 0.8 scope

Picked to (a) focus on the key areas that move the aim forward — fix the
foundations and open both fast paths — and (b) include **two** improvement/admin
riders so they don't get abandoned.

| Key | Summary | Track | Type | Est | Rationale |
|---|---|---|---|---|---|
| **SP-396** | Coherent per-poll snapshot | Foundation | Story | 5 | Lynchpin; unblocks cross-outcome work. Pair with SP-384. |
| **SP-384** | Capture matched-volume | Foundation | Story | 3 | Confirms priced edges are matchable. Pair with SP-396. |
| **SP-400** | Capture Over/Under goals | A | Story | 5 | Seed the most promising market now; capture-only. |
| **SP-401** | Historic-data spike | B | Spike | — (time-box ~1d) | Highest-value investigation; decides Track B. |
| **SP-395** | Telegram on Rundeck failures | Admin | Task | 3 | Rider #1 — proactive failure alerts; builds on SP-357. |
| **SP-294** | Pi/Docker cleanup + log rotation + disk alert | Admin | Story | 3 | Rider #2 — overdue teardown; prevents a repeat of the SP-336 disk-full outage. |

**Total: 19 pts** across 5 pointed tickets + 1 time-boxed spike (6 tickets).

### WORD estimate notes (the four that were TBD)

- **SP-396 = 5.** Replaces the per-runner `listRunnerBook` loop in
  `monitor_service.update_runner_odds` (one call + one DB row per runner, each at
  its own `current_timestamp`) with a single `listMarketBook`-style call writing
  all runners under one shared timestamp. Medium complexity — live capture hot
  path, must not regress the in-play 5s cadence (SP-343) or the SP-403
  empty-result guard — plus unattended-Pi risk on perishable data. Comparable to
  SP-302 (monitor rework, 5).
- **SP-384 = 3.** Mostly additive: the same market-book call already carries
  `totalMatched` (the `Market`/odds objects already model it); persist it into
  the blob in the shape the SP-368 loader reads. Shares SP-396's plumbing, so
  smaller. *If done standalone (own deploy/verify), closer to 5.*
- **SP-400 = 5.** New market type (Over/Under 2.5 ± 1.5/3.5) on the
  target-discovery + `simpleStategy.py` config path; first non-MATCH_ODDS
  capture. "Done before" = once. Same 5 as SP-328 (stand up capture).
- **SP-401 = no points.** Spike — time-boxed, finding is the deliverable.
  Follows the 0.6 convention (SP-329 spike carried no points).

**Why these two riders.** SP-395 (failure alerts) and SP-294 (Pi cleanup + the
log-rotation / disk-alert prevention scope folded in from SP-336) both directly
protect the capture you're relying on to accumulate — and SP-294 in particular
has been carried since before 0.6. Pulling them in now is the "don't abandon the
chores" slot. SP-362 (web app revive) is the weaker admin candidate — lower
urgency, no capture-protection value — so hold it for a later release unless
capacity is spare.

**Explicitly NOT in 0.8**
- **SP-402** (historic pipeline) — gated on the SP-401 spike; 0.9 if GO.
- **SP-397 / 398 / 399** (re-assessments) — need accumulated clean data; 0.9+.
- Goals / in-play time-decay analysis — needs accumulated goals data; 0.9+.
- Any live orders / paper trading / running bot — still further down the roadmap.

## How 0.8 compares to 0.6 and 0.7

Pointed story points only (the convention here: epics, spikes, most tasks and
all bugs carry no points — so "pts" measures sized stories/tasks, and ticket
*count* is tracked separately).

| Release | Pointed story points | Tickets (total) | Pointed items | Unpointed (epic/spike/task/bug) | Biggest single ticket |
|---|---|---|---|---|---|
| **0.6** Season Data Capture | **31** | 16 | 6 | 10 (1 epic, 1 spike, 2 tasks, 6 bugs) | SP-332 data-quality (8) |
| **0.7** Analysis & Strategy Discovery | **32** | 10 | 7 | 3 (1 epic, 2 bugs) | SP-368 loader, SP-371 drift (8) |
| **0.8** Capture Foundations + Investigation (proposed) | **19** | 6 | 5 | 1 (1 spike) | SP-396, SP-400 (5) |

**Read:**

- **0.8 is deliberately lighter — ~19 pts vs ~31–32** for the last two releases,
  about **60%** of their size. That's intentional, not under-scoped: it's a
  *foundation + investigation* release with no heavy analysis piece. 0.6 and 0.7
  each carried an 8-pt centrepiece (SP-332, SP-368/SP-371); 0.8's largest items
  are 5s. The meaty analysis that would push it back to ~30 is deferred to 0.9
  (gated on data / the spike), by design.
- **Fewer, tighter tickets (6 vs 10–16).** 0.6 was bloated with bugs (6) surfaced
  by standing capture up; 0.7 trimmed to 10. 0.8 is leaner still — no epic this
  round (the candidates hang off existing backlog items, not a new epic), and
  only one unpointed item (the spike).
- **Capacity headroom.** If velocity has been ~31–32 pts/release, 19 leaves room.
  Options at refinement: pull a deferred 0.9 re-assessment forward *once SP-396/384
  land and data has built up* (unlikely within the same release — accumulation),
  or add a third admin rider (SP-362 web-app revive). I'd lean toward **keeping
  0.8 lean**: its value is unblocking, and a tight release that lands the capture
  fixes cleanly is worth more than a padded one.

## Sizing / trim note

All four previously-TBD tickets now estimated (SP-396=5, SP-384=3, SP-400=5,
SP-401 spike=unpointed); the two riders are 3+3. **Total 19 pts.** If scope needs
to shrink, the trim order is: drop SP-294 → drop SP-395 → split SP-400 out. Keep
**SP-396 + SP-384 + SP-401** as the irreducible core.

## Dependencies / sequencing

```
SP-396  ─┐
         ├─ (land together; same market-book call) ──► SP-400 (goals, clean from day 1)
SP-384  ─┘                                        └──► unblocks SP-397/398/399 (0.9)

SP-401 (spike) ──► GO/NO-GO ──► SP-402 (0.9 if GO)

SP-395, SP-294  — independent admin, any time in the release
```

Do SP-396 + SP-384 first, then SP-400 on top of them. SP-401 and the two admin
riders run in parallel with no ordering constraint.

## Open decisions for refinement

1. **Estimate the TBD tickets** (SP-396, SP-384, SP-400, SP-401) with WORD; any
   13+ gets broken down.
2. **Confirm the two riders** — SP-395 + SP-294 recommended; swap/drop at
   capacity.
3. **Set the fixVersion.** Create / confirm **"Betfair Trader 0.8"** and assign
   it to the agreed tickets. Until then they stay `backlog-unplanned` with no
   fixVersion (per the "no new tickets into an in-progress release" rule — 0.8
   isn't started yet, so this is normal pre-planning). SP-402 / SP-397 / SP-398 /
   SP-399 stay unversioned (0.9 candidates).
4. **Over/Under lines** — confirm SP-400 captures 2.5 primarily (± 1.5 / 3.5).
