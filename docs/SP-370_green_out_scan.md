# SP-370 — Green-out / arbitrage scan (back-all-outcomes)

> **Status:** complete. A price-only scan of the captured Betfair MATCH_ODDS
> data for a back-all-three (Dutching) green-out, for epic **SP-367** (0.7
> "is there anything tradeable?"). Kept in-repo for direct reference and Jira
> attachment, like the SP-369 note.
>
> **Dataset:** the SP-368 Research_Dataset snapshot, **25,657 records, 41
> matches** (`data/research_dataset.parquet`). Scan + verdict produced by
> `scripts/scan_green_out.py` (pure core: `logic/green_out.py`).

## Verdict

**NOT VIABLE.** Cross-outcome (back-all-three) arbitrage is not a tradeable edge
on this data. A *priced* green-out (book < 1.0 on best-back prices) does occur,
but pre-match it is **rare (3.3% of instants) and tiny (median 0.13% after
commission)** — below any sensible viability bar — and that is *before*
accounting for the two things that would erode it further: unknown (likely thin)
liquidity, and the stale-leg artifact in the book reconstruction. The large
in-play "green-outs" are overwhelmingly reconstruction artifacts, not real
opportunities. This matches the SP-367 plan's prior: "rare/tiny on a single
exchange — but prove it." We've proven it.

---

## 1. The test & the maths

The question: at any captured instant, can you back all three outcomes
(Home/Draw/Away) at once for a profit locked in regardless of result?

For best-back prices `b_home, b_draw, b_away` the back-all-three book is
`B = 1/b_home + 1/b_draw + 1/b_away` (the same implied-probability book SP-369
builds; this scan reuses that code so the two cannot drift). Staking each
outcome in proportion to its implied probability makes every outcome return the
same gross `1/B` per unit staked, so the profit before commission is `1/B − 1`
on *every* outcome — the "locked-in" property. Betfair commission `c` is charged
on net winnings, so it only scales a positive profit:

```
profit_after_commission = (1/B − 1) × (1 − c)   when B < 1,  else 0
```

**A priced green-out exists exactly when `B < 1`.** Commission changes the
*size* of the edge, never its existence — so the opportunity *count* is
commission-independent (reported here at the default **5%**).

---

## 2. Results

Scanned **25,575** complete-book instants (82 of 25,657 dropped as incomplete —
an outcome not yet first-seen in the match). Headline, split by phase:

| Phase | Instants | Green-outs (B<1) | Rate | Median margin (post-comm) | Max margin | Min book |
|---|---|---|---|---|---|---|
| **Pre-match** | 6,398 | 209 | **3.27%** | **0.13%** | 3.34% | 0.966 |
| **In-play** | 19,177 | 4,030 | 21.0% | 0.60% | **207.5%** | **0.314** |
| All | 25,575 | 4,239 | 16.6% | 0.57% | 207.5% | 0.314 |

Green-outs touch 31 of 41 matches; the longest consecutive green-out run within
a match is 276 instants (in-play, and — see below — mostly artifact).

![Distribution of the back-all-three book](../charts/green_out_book_distribution.png)

Both distributions cluster tightly just above `book = 1.0` (the normal
overround); only a modest tail crosses below 1.0. The piles at the chart's
clipped edges (0.90 / 1.15) are the extreme in-play excursions.

### Why the in-play numbers are not real opportunities

The 207.5% "profit" is the tell. A 207% — or even a 43% (the in-play 99th
percentile) — locked-in return is physically impossible on a liquid single
exchange. These come from the book's **as-of forward-fill** reconstruction:
the three outcomes are captured at *distinct* timestamps (never simultaneously —
see SP-369), so each instant uses each outcome's most-recent price. During a
fast in-play move (a goal, a red card) one leg updates while another is
momentarily stale, and the stale+fresh combination reads as an impossible
sub-1.0 book. In-play min book of **0.314** is exactly this. So the verdict is
based on **pre-match only**, where prices move slowly enough that the forward-
fill is benign.

### The pre-match reality

Pre-match, the deepest book is **0.966** and the median green-out margin is
**0.13%** after commission — a tiny edge, occurring ~3% of the time. Even taking
these flags at face value, a 0.13% median return is not a strategy: it would
need three simultaneous fills, at prices that don't move before you're on, with
enough size to matter — and we have **no liquidity data at all** to confirm any
of that (see §3).

---

## 3. Honest limitations

This scan answers *"does a priced back-all-three arb exist after commission?"* —
not *"could you actually have traded it?"* Two gaps, both inherent to the current
dataset and surfaced on every run:

1. **No liquidity / size data.** The SP-368 dataset carries best-back/best-lay
   prices only — no available-size and no matched-volume (`matched_volume` is
   null for the whole season; this is **SP-384**, deferred out of 0.7). A
   flagged green-out is a *priced* opportunity; how much could be matched on all
   three legs is unknown. Real green-outs are typically killed by size: the
   price is there for £5, not £500.
2. **Forward-fill staleness.** As above — a sub-1.0 book can pair a fresh leg
   with a stale one. Benign pre-match, dominant in-play.

A proper confirmation of even the pre-match flags would need per-leg
contemporaneous ladders with sizes — i.e. richer capture (SP-384) — which is out
of 0.7 scope.

---

## 4. Conclusion & handover

- **Green-out / cross-outcome arbitrage: NOT VIABLE on this data.** Don't pursue
  it as a 0.7 strategy. The evidence is a 3.3% pre-match occurrence at a 0.13%
  median margin, with no liquidity behind it and a known artifact inflating the
  in-play figures.
- This is the expected result for a single liquid exchange and **de-risks the
  idea cleanly** — one of the two 0.7 strategy hypotheses is now answered.
- The remaining 0.7 strategy question is **SP-371** (pre-match drift trading),
  where SP-369 found the more promising signal (favourites drift out +2.73%).
- If cross-outcome ideas are ever revisited, they would need **SP-384**
  (volume/size capture) first — without liquidity you cannot tell a real
  green-out from a priced mirage.

### How to reproduce

```bash
# Locally (parquet already pulled to data/) or on the Pi:
python scripts/scan_green_out.py --parquet data/research_dataset.parquet
# prints the per-phase breakdown + verdict; writes charts/green_out_book_distribution.png
# commission is overridable: --commission 0.02
```

Methodology note: the book uses best-back prices only, reconstructed by as-of
forward-fill across independently-timed outcome captures. The verdict is
pre-match-based (in-play books are artifact-prone), requires *plausible*
green-outs (book ≥ 0.90, excluding impossible collapses) above a 1% rate **and**
a median post-commission margin above 0.5%, and uses no match results or
settlement data — strictly odds-only, per the SP-367 scoping.
