# Betfair Trader 0.7 — Release Plan

> Created in Jira on 2026-10-09. Epic **SP-367** with children SP-368–372, all
> in fixVersion **Betfair Trader 0.7**, Backlog. This doc mirrors Jira so we
> don't need to call it to review scope. Keys are real; update both if scope
> changes.

## Theme

**Analyse the captured Betfair data and find a candidate edge.**

0.6 got live match odds captured, verified and trustworthy. 0.7's job is to
turn that growing dataset into an answer to one question:

> **Is there anything tradeable in this data at all?**

The deliverable is an *answer* (plus the tooling to keep asking it), not a
working bot. A perfectly good 0.7 outcome is "no clear edge found, here's the
evidence" — that still de-risks everything after it.

Explicitly **not** in 0.7: placing live orders, a running bot, the big
code-quality / stack-simplification epics (SP-290 / SP-291, now unscheduled).
Quality work is picked up opportunistically *as* analysis code is written
(tests + docstrings on the new code), not as a headline.

## Where the journey goes (context, not all 0.7)

```
[0.6 DONE]  Capture live Betfair odds, verified & trustworthy
[0.7 HERE]  ANALYSE (odds-only) — shape the data, scan for edges,
            crudely test green-out + pre-match drift strategies
[0.8]       BACKTEST candidate strategies properly on captured history
[0.9]       PAPER-TRADE live, no real money
[1.0]       LIVE small-stakes orders via API (existing SP-265)
```

## Starting point (what already exists in bf_trader_py)

- `analyse_service.py` (`MarketAnalyser`) — a manual, single-market
  visualiser: reads one hardcoded `market_id` from `bf.market_table`, parses
  the odds JSON, plots back/lay odds-over-time per runner to `charts/`.
  Last run was one market in **March 2025** (pre-dormancy). That's the whole
  of the current "analysis".
- **No** cross-match aggregation, **no** volume analysis, **no** match
  outcomes/results, **no** strategy evaluation, **no** backtest.
- `simpleStategy.py` is capture config (which events to track, polling
  cadence) — NOT a betting strategy, despite the name.
- Data-model gotchas to design around:
  - Odds stored as raw JSON blobs in `bf.market_table.odds`, parsed with
    `ast.literal_eval`, only reading one depth level. Brittle.
  - Runners stored in `bf.target.runner_ids` as `"id-name|id-name"` string.
  - No results/settlement data captured anywhere — not needed for 0.7's
    odds-only strategies; relevant only to the 0.8 hold-to-settlement work.

---

## Epic — SP-367 Betfair Data Analysis & Strategy Discovery
*Epic · Labels: betfair, analysis · fixVersion: Betfair Trader 0.7*

Analyse the Betfair odds data captured during the season to determine whether
any tradeable edge exists, and build the minimum tooling needed to evaluate
candidate strategies. Covers shaping the captured odds into a research
dataset, exploratory analysis, and a first-pass evaluation of two odds-only
strategy hypotheses (green-out/arbitrage across outcomes, and pre-match drift
trading across time). Success is a clear, evidence-backed answer on
tradeability — not a deployed bot.

**Epic fully delivered in 0.7:** all five children (SP-368–372) are in this
release, so the epic opens and closes within 0.7 — nothing spills to another
version.

**Key scoping decision — 0.7 is "odds-only":** both strategy ideas are tested
purely on the captured odds time-series, with **no match-results dependency**.
This is deliberate (see the drift note below) and keeps 0.7 free of any
external data source.

---

## Two senses of "drift" (why results are NOT needed in 0.7)

The drift idea splits into two different strategies:

1. **Pre-match drift trading (0.7, SP-371).** Enter on the drift and *close out
   before kick-off* while the market is still open — e.g. back the favourite at
   2.0 three hours out, lay it off at 1.8 near kick-off, bank the difference.
   P&L comes entirely from the price moving; you are flat before the whistle, so
   the match outcome is irrelevant. **No results data needed.** Once the game
   is live, odds are driven by in-game events (goals, red cards, time decay) —
   noise we can't predict from pre-match price action — so we stay in the
   pre-match window.
2. **Drift-as-winner-signal, hold to settlement (0.8+).** Drift *predicts* the
   winner; you hold the bet through the match and only profit if the selection
   wins. This needs match results to measure, so it is **deferred to 0.8** as a
   separate question.

0.7 implements sense 1 only. The results-sourcing spike is therefore deferred
to 0.8, where hold-to-settlement strategies live.

---

## Tickets

Ordered by dependency. SP-368 (dataset) must land first; then the exploratory
and both strategy tests can proceed. Both strategy tests (SP-370 green-out,
SP-371 pre-match drift) are self-contained odds maths with **no external
dependency**.

### SP-368 — Build a clean Betfair research dataset from captured odds
*Story · Est 8 · Labels: betfair, analysis*
*WORD: good knowledge, some risk from brittle JSON, done-before=few,
work=half-day+, new reusable module + docs.*

**Why:** `MarketAnalyser` only reads one market and parses odds ad hoc. All
analysis needs a reliable, reusable way to turn `bf.market_table` into clean
per-match, per-runner time-series across *many* matches.

**Scope:**
- Robust parse of the stored odds JSON into structured columns (best back,
  best lay, and available depth where present) — replace the brittle
  `ast.literal_eval` + `[1]["price"]` approach.
- Join runner identities (parse `bf.target.runner_ids`) and market/event
  metadata so each row is attributable to a match + outcome (Home/Draw/Away).
- Include volume (`totalMatched`) over time — currently captured but unused.
- Output a tidy dataset (dataframe / parquet) spanning all captured matches,
  with a documented schema. Thin DB-reading wrapper + pure transform logic
  (property-testable), following the SP-328 logic/scripts pattern.

**DoD:** A documented, tested loader that produces a multi-match tidy dataset;
old `MarketAnalyser` read path superseded or refactored onto it.

---

### SP-369 — Exploratory analysis of odds & volume behaviour
*Story · Est 5 · Labels: betfair, analysis · Depends on: SP-368*
*WORD: decent knowledge, exploratory, work=3–4h, half-page Confluence writeup.*

**Why:** Before hypothesising strategies, understand the raw material. How do
match-odds actually behave across the dataset?

**Scope (on the SP-368 dataset):**
- Characterise pre-match odds movement: do favourites' odds drift
  (shorten/lengthen) as kick-off approaches? How much, how consistently?
- Characterise in-play movement and volume spikes (where in-play data exists).
- Overlay the three outcomes (Home/Draw/Away) and look at the implied-
  probability book (sum of 1/odds) over time — sets up SP-370.
- Produce summary charts + a short written characterisation. Generalise the
  existing per-runner plotting to work across matches.

**DoD:** A short Confluence note with charts describing how the odds/volume
behave, flagging anything that looks like a candidate pattern.

---

### SP-370 — Green-out / arbitrage scan (back-all-outcomes)
*Story · Est 5 · Labels: betfair, analysis, strategy · Depends on: SP-368*
*WORD: decent knowledge, self-contained maths, no external dependency,
work=3–4h.*

**Why:** Direct test of the first strategy idea — can you back/lay all three
outcomes for a guaranteed profit regardless of result? No results data needed;
it's pure maths on captured odds. Can start as soon as the dataset exists.

**Scope (on the SP-368 dataset):**
- For every captured timestamp of every match, compute the overround /
  implied-probability book: `(1/home) + (1/draw) + (1/away)`.
- Flag any instant where a back-all-three (Dutching) or back/lay combination
  yields a locked-in profit (book < 1 after assumed commission), including
  realistic Betfair commission and available-size constraints.
- Report frequency, size and duration of any such opportunities across the
  season. Honest conclusion on whether cross-outcome arbitrage is real and
  worth pursuing (expectation: rare/tiny on a single exchange — but prove it).

**DoD:** A scan + report quantifying green-out opportunities (count, typical
margin after commission, liquidity). Clear verdict: viable / not viable, with
evidence.

---

### SP-371 — Pre-match drift trading analysis (close out before kick-off)
*Story · Est 8 · Labels: betfair, analysis, strategy*
*Depends on: SP-368. NO results dependency — flat before kick-off.*
*WORD: some knowledge, exploratory, work=5–8h, new Confluence section.*

**Why:** Tests the drift idea in its tradeable, self-contained form (drift
sense 1): back/lay on pre-match odds movement and close the position out
*before kick-off*, banking the price difference regardless of who wins. P&L
comes from the odds moving, so no match results are needed. We stay strictly
in the pre-match window — once the game is live, odds are driven by in-game
events we can't predict from price action.

**Scope (on the SP-368 dataset, pre-match rows only):**
- Define drift measures over the pre-match window (e.g. odds change over the
  final N hours before start_time) for favourites / selections of interest.
- Simulate an entry→pre-kickoff-exit round trip: enter at time T, close out
  at a later pre-kickoff time, compute realised P&L from the back/lay prices
  at those instants. Test both momentum (drift continues) and mean-reversion
  (drift reverses) framings.
- Use the captured available-to-back/lay **sizes**, not just top price, so
  simulated fills reflect liquidity you could actually have got; apply Betfair
  commission.
- Report expected return, hit rate, and sensitivity to entry/exit timing.
  Honest verdict on whether a pre-match drift edge exists after costs.

**DoD:** A written analysis quantifying whether pre-match drift trading is
profitable after commission and realistic fills, with a viable/not-viable
verdict. No external data used.

---

### SP-372 — 0.7 synthesis: tradeability verdict & 0.8 recommendation
*Task · Est 2 · Labels: betfair, analysis · Depends on: SP-369/370/371*
*WORD: writeup, ties findings together.*

**Why:** The point of the release. Pull the SP-369/370/371 findings into one
place and decide the next move.

**Scope:**
- Summarise what the data shows and whether any candidate edge survived.
- Recommend 0.8 direction: which (if any) strategy goes to proper backtesting,
  or whether to change tack (different markets, more data, abandon). Note that
  outcome-based strategies (drift held to settlement) would pull in the
  deferred results-sourcing spike.
- Capture in Confluence as the 0.7 review.

**DoD:** 0.7 review note + a concrete, prioritised recommendation for 0.8.

---

## Admin / tidy-up riders (already existed; kept in 0.7)

- **SP-363 — Automate Postgres backups on the Pi.** Strong case: the perishable
  season data you're about to analyse is currently protected by a single manual
  dump. Low effort, high risk-reduction.
- **SP-344 — Bug: quality check alerts on zero-settled-match days.** Quick fix;
  keeps the capture monitoring you rely on from crying wolf.
- **SP-345 — Refresh stale Rundeck job definitions (1 pt).** Optional; include
  only if touching Rundeck anyway.

## Pushed out of 0.7 (fixVersion cleared, labelled `backlog-unplanned`)

Done on 2026-10-09. These are now unscheduled; filter with
`labels = backlog-unplanned` at refinement time.

- **Epic SP-290** (Code Quality) + stories SP-296/297/298.
- **Epic SP-291** (Stack Simplification) + stories SP-293/294/295.
- **Epic SP-261** (Betfair Live Experiment) + stories SP-264/265.
- **SP-93** (pre-match profit-locking research) — superseded in spirit by the
  SP-370/371 data-driven approach.
- **SP-362** (web app review) — kept its `tech-debt` label.

Each pushed-out epic is fully unscheduled along with its children, so no epic
straddles 0.7 with children in another version.

## Scope summary

Five new tickets under epic SP-367 (results spike deferred to 0.8):

| Key | Summary | Type | Est |
|---|---|---|---|
| SP-368 | Build a clean research dataset | Story | 8 |
| SP-369 | Exploratory analysis of odds & volume | Story | 5 |
| SP-370 | Green-out / arbitrage scan | Story | 5 |
| SP-371 | Pre-match drift trading | Story | 8 |
| SP-372 | 0.7 synthesis / verdict | Task | 2 |

~28 pts + riders (SP-363 backups, SP-344 bug, SP-345 Rundeck). Clean,
odds-only, no external data dependency.

## Decisions locked in

1. **Scope:** ~28 pts + riders accepted as the 0.7 shape (SP-371 pre-match
   drift stays in 0.7, not deferred).
2. **Markets:** analysis targets MATCH_ODDS on EPL/UCL (what capture targets).
3. **Pre-match window:** SP-371 closes out before kick-off; nothing is held
   into the live game in 0.7.
