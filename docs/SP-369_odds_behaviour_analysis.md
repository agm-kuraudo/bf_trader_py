# SP-369 — Exploratory analysis of odds & volume behaviour

> **Status:** complete. Price-only characterisation of the captured Betfair
> MATCH_ODDS data, produced for epic **SP-367** (0.7 "is there anything
> tradeable?"). This note is the SP-369 deliverable; it is kept in-repo so it
> can be referenced directly and attached to the Jira ticket.
>
> **Dataset:** built on the Pi from `bf.market_table` via
> `scripts/build_research_dataset.py` (SP-368 loader). **25,657 records, 0
> unparseable, 41 matches.** Analysis + charts produced by
> `scripts/explore_odds_behaviour.py` (pure core: `logic/odds_behaviour.py`).
>
> **Scope reminder:** this ticket *characterises* the raw material. It does not
> decide strategy viability — the green-out viability maths is **SP-370** and the
> pre-match drift P&L simulation is **SP-371**. Where this note flags a
> candidate pattern, that is a pointer for those tickets, not a verdict.

## TL;DR

- **The dataset is clean and usable for price-only analysis.** 25,657 rows
  parsed with zero failures; the implied-probability book is computable for
  25,575 / 25,657 instants (99.7%).
- **Favourites tend to drift *out* (lengthen) before kick-off**, not shorten.
  Mean relative pre-match drift of the per-match favourite is **+2.73%**. The
  field as a whole is roughly symmetric around zero (median −0.77%, 50.5%
  shortening). This is the most interesting pre-match signal and is the direct
  input to **SP-371**.
- **The pre-match book sits just above 1.0** (median overround ≈ **+0.5%**),
  i.e. a tight single-exchange market. It dips **below 1.0 in 449 pre-match
  instants** (book < 0.98), which is the candidate region **SP-370** must test
  properly (with the lay side + commission, not best-back-only).
- **In-play is volatile and dominated by event-driven dislocations** — the book
  swings from 0.31 to 1.80 — consistent with the plan's decision to keep 0.7
  strategies in the pre-match window only.
- **No matched-volume data exists** (`matched_volume` null for all 25,657 rows).
  Volume characterisation is not possible on the current capture. This is a
  known, accepted gap tracked by **SP-384** (deferred out of 0.7); the SP-368
  loader is correct — capture simply does not persist a volume figure yet.

---

## 1. Dataset shape & coverage

| Measure | Value |
|---|---|
| Records | 25,657 |
| Unparseable (excluded) | 0 |
| Matches (`market_id`) | 41 |
| Pre-match rows | 6,480 |
| In-play rows | 19,177 |
| `best_back` populated | 25,217 / 25,657 (98.3%) |
| `matched_volume` populated | **0 / 25,657** |
| Complete implied-prob book instants | 25,575 / 25,657 (99.7%) |

Two structural facts about the capture drove the analysis design and are worth
recording:

1. **Outcomes are captured at *distinct* timestamps.** For a representative
   match, all 2,757 capture instants carried exactly **one** outcome — the
   Home/Draw/Away rows are each written at their own poll instant, never
   simultaneously. A naive exact-timestamp join therefore yields an empty book.
   The book is instead reconstructed by **as-of (forward-fill) alignment**:
   within each match, each outcome's most-recent implied probability is carried
   forward onto the match's union of timestamps, then summed. (See
   `implied_probability_book` in `logic/odds_behaviour.py`.)
2. **In-play dominates the row count** (19,177 vs 6,480 pre-match) because
   in-play polling is far more frequent, but — see §4 — in-play is not where the
   0.7 strategies operate.

---

## 2. Pre-match drift (the headline signal)

**Question:** do odds drift as kick-off approaches — how much, how consistently?

Measured over `match_phase == PRE_MATCH` only, per `(market_id, runner_id)`, as
the relative move from the first to the last pre-match best-back price
(`last / first − 1`).

| Measure | Value |
|---|---|
| Selections measured | 105 (across 41 matches; 35 fully-resolved favourites) |
| Mean relative drift (all selections) | **+0.04%** |
| Median relative drift (all selections) | −0.77% |
| Fraction that shortened (`rel_drift < 0`) | 50.5% |
| **Mean relative drift of the favourite** | **+2.73%** |

**Reading:** the field as a whole is close to a coin-flip around zero (median
slightly negative, ~half shorten). But the **favourite** — the shortest-priced
selection in each match — drifts *out* by **+2.73%** on average. In plain terms:
favourites tend to get a little *longer* (less likely) as kick-off approaches,
rather than being backed in.

![Pre-match relative drift distribution](../charts/drift_distribution.png)

The histogram shows the field (blue) roughly symmetric around zero, while the
favourites (orange) sit visibly right of centre.

A clean illustrative match (`1.262646901`): the favourite (AWAY, ~2.22) drifted
**out to ~2.45** over three days, while HOME came in from ~3.35 to ~3.05 —
exactly the favourite-lengthening pattern the aggregate captures.

![Pre-match best-back drift for one match](../charts/1-262646901_drift_pre_match.png)

> **Candidate pattern for SP-371.** A systematic favourite drift-out is a
> directional signal a pre-match trade could exploit (lay the favourite early,
> back it later — or the reverse if testing mean-reversion). SP-371 must confirm
> whether it survives realistic fills (using captured available sizes) and
> commission. **+2.73% is a gross price move, not a net return** — do not read it
> as profit.

---

## 3. The implied-probability book (sets up SP-370)

For each instant the book is `(1/home) + (1/draw) + (1/away)` on **best-back
prices**. Above 1.0 is the market's margin (overround); below 1.0 is where a
back-all-three (Dutching) green-out could, in principle, lock in a profit.

| Measure | Value |
|---|---|
| Complete book instants | 25,575 |
| Book min / median / max | **0.314 / 1.006 / 1.801** |
| Instants with book < 1.0 | 4,239 |
| Instants with book < 0.98 | 449 |
| Pre-match: n / median / min | 6,398 / 1.0054 / **0.9661** |
| In-play: n / median / min | 19,177 / 1.0064 / **0.3140** |

**Reading:** the median book is **≈ 1.006** — a very tight ~0.6% overround,
as expected for a liquid single exchange. It does dip below 1.0: 4,239 instants
overall, 449 below 0.98. **Pre-match** the deepest dip is a modest **0.966**;
**in-play** the book reaches a wild **0.314**, which is almost certainly a
transient dislocation (a goal or red card moving one leg while a forward-filled
leg is momentarily stale), not a tradeable arb.

![Implied-probability book over time for one match](../charts/1-262646901_book_over_time.png)

The chart shows the three outcomes stable with the book hugging just above 1.0
through the pre-match window, then erupting into volatility at kick-off.

> **Candidate region for SP-370 — with strong caveats.**
> - This book is **best-back only**. A genuine green-out needs the correct
>   side of the ladder (you back at available-to-back, you cannot also lay at
>   the same price) and must net off **Betfair commission**. A best-back book of
>   0.97 will very likely **not** be a real locked-in profit once the correct
>   prices, available sizes, and commission are applied.
> - The forward-fill means a "book < 1.0" instant may pair a fresh price on one
>   leg with a **stale** price on another. SP-370 should treat the sub-1.0
>   instants as *candidates to investigate*, re-deriving each from the actual
>   contemporaneous ladders, and should probably **focus on pre-match** (min
>   0.966) and discard the extreme in-play dislocations as artifacts.

---

## 4. In-play behaviour

In-play rows outnumber pre-match ~3:1, but in-play is **volatile and
event-driven**: the book spans 0.314–1.80 and the per-match charts show the
probabilities whipping around at and after kick-off. This is consistent with the
release plan's reasoning for keeping 0.7 strategies in the **pre-match window**
only — once the game is live, prices are driven by in-game events (goals, cards,
time decay) that cannot be predicted from pre-match price action. In-play is
characterised here for completeness but is **not** a 0.7 trading surface.

---

## 5. Volume behaviour — not possible (SP-384)

`matched_volume` is **null for all 25,657 rows**. The SP-368 loader derives it
from a scalar `totalMatched` or a `tradedVolume` ladder in the captured odds
blob; neither is present, because the capture pipeline does not persist a volume
figure. The loader is behaving correctly (it nulls an absent field).

Consequently:
- The `volume_over_time.png` chart is intentionally **not produced**.
- The liquidity/size-aware elements of SP-370 and SP-371 fall back to
  **price-only** for 0.7.

This is a known, accepted gap, deferred out of 0.7 and tracked by **SP-384**
("Capture matched-volume in Betfair odds snapshots"). The exploration script
emits an explicit note to this effect on every run so the absence is never
mistaken for a bug.

---

## 6. Conclusions & handover

1. **The data is good enough for price-only 0.7 analysis** — clean parse, a
   computable book for 99.7% of instants, and a clear pre-match signal.
2. **Favourite drift-out (+2.73%) is the standout pattern** → hand to **SP-371**
   to test as a tradeable, costed pre-match strategy.
3. **Sub-1.0 book instants exist but are almost certainly not free money** →
   hand to **SP-370** to re-derive from contemporaneous ladders with the correct
   side + commission; expect most/all to evaporate (prove it either way).
4. **Stay pre-match for 0.7** — in-play is too noisy and event-driven to trade
   on price action alone.
5. **Volume is a gap, not a finding** → **SP-384** when 0.7's successors need it.

### How to reproduce

```bash
# On the Pi (data is there):
cd /usr/local/bf_trader_py
git checkout SP-369-exploratory-odds-analysis && git pull
.venv/bin/python -m pip install -r build/requirements.txt           # ensure deps
.venv/bin/python scripts/build_research_dataset.py --out research_dataset.parquet
.venv/bin/python scripts/explore_odds_behaviour.py --parquet research_dataset.parquet
# charts land in charts/ ; headline numbers print to stdout
```

Methodology note: the overround uses best-back prices only and the book is
reconstructed by as-of forward-fill across independently-timed outcome captures
(§1). Drift is the first→last pre-match best-back move per selection. No match
results or settlement data are used anywhere — the analysis is strictly
odds-only, per the SP-367 scoping.
