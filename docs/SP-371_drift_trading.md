# SP-371 — Pre-match drift trading analysis (close out before kick-off)

> **Status:** complete (option C — best-price "quick" version; size-aware fills
> descoped to a follow-up). A price-only simulation of pre-match drift trading
> for epic **SP-367** (0.7 "is there anything tradeable?"). Kept in-repo for
> direct reference and Jira attachment.
>
> **Dataset:** the SP-368 Research_Dataset snapshot, **25,657 records, 41
> matches** (`data/research_dataset.parquet`). Simulation + verdict by
> `scripts/scan_drift_trading.py` (pure core: `logic/drift_trading.py`).

## Verdict

**NOT VIABLE.** Pre-match drift trading — enter a back/lay position in the
pre-match window, close it out before kick-off, bank the price move — loses money
in **every** configuration tested: both framings (momentum and mean-reversion)
and every entry/exit timing window, at roughly **−1% to −2% per unit backed**
after commission. And that is on **optimistic best-price fills** — real fills
would be no better, usually worse. The drift *signal* SP-369 found is real, but
it is **smaller than the back/lay spread you must cross to trade it**, so trading
it is a net loss.

---

## 1. The trade & the two framings

To be flat before kick-off you do both legs pre-match: a back and a lay on the
same selection at two different times. A fully-hedged round trip (back £1 at
`b`, lay the hedge at `l`) locks in `b/l − 1` per unit backed before commission —
a profit exactly when you **backed higher than you laid**. Commission (5%
default) is charged on net winnings, so it scales a positive profit and leaves a
loss untaxed.

- **Momentum** — bet the drift continues. For a selection drifting *out* (price
  rising): **lay at entry, back at exit** (end up backing high, laying low).
- **Mean-reversion** — the mirror: **back at entry, lay at exit** (profit if the
  price falls back).

Both are simulated from the selection's own captured `best_back` / `best_lay` at
the entry and exit instants (no forward-fill — each leg is a real observed price
for that selection, so the SP-370 stale-leg issue does not arise here).

## 2. Results

Swept both framings across seven entry→exit windows (hours before kick-off).
Mean P&L per unit backed, after 5% commission:

| Window (entry→exit) | Momentum | Mean-reversion |
|---|---|---|
| 72h → 1h | −1.99% | −0.91% |
| 48h → 1h | −1.62% | −1.95% |
| 24h → 1h | −2.01% | −1.22% |
| 12h → 1h | −1.79% | −1.42% |
| 6h → 1h | −1.61% | −1.62% |
| 24h → 6h | −2.08% | −1.27% |
| 6h → 0.5h | −1.69% | −1.48% |

Hit rates (fraction of trades profitable) run ~19–44% — i.e. most trades lose.
The best configuration of all 14 is mean-reversion 72h→1h at **−0.91%** — still a
loss. Nothing clears break-even.

![Expected return by timing & framing](../charts/drift_trading_sensitivity.png)

Every point, both framings, all windows, sits below the break-even line.

## 3. Why it loses — the spread

This reconciles with SP-369's finding that favourites drift *out* ~2.73%: the
drift is real, but **the back/lay spread eats it**. A round trip crosses the
spread (you back at the lower back price and lay at the higher lay price), and in
this data the **median pre-match back/lay spread is 1.33%** (mean 1.54%, 90th
percentile 2.5%). A ~2% *median* directional drift — with huge dispersion, so
any individual trade is far from guaranteed — simply isn't reliably larger than
the ~1.3%+ spread you pay to get in and out. The net of "sometimes-right
direction minus always-paid spread" is a consistent small loss. This is the
textbook outcome: a price *tendency* that isn't big or consistent enough to beat
transaction costs.

## 4. Honest limitations (why this is option C)

SP-371's full scope asks to use captured available-to-back/lay **sizes** so fills
reflect real liquidity. The SP-368 dataset has **best prices only** — no size, no
matched-volume (SP-384). So this sim assumes a **full match at the best price**
at both legs. That is **optimistic**: real fills walk the ladder and get a worse
average price or only partial matches. Since the best-price sim *already loses*,
the limitation only strengthens the NOT-VIABLE verdict — real trading would lose
more. The size-aware version is descoped to a follow-up pending **SP-384**
(volume/size capture) and **SP-396** (coherent snapshots).

Other scope notes:
- Strictly pre-match, flat before kick-off — no match results used (drift
  *sense 1*; the held-to-settlement *sense 2* is 0.8).
- Entry/exit timing sensitivity is covered by the seven-window sweep (§2); the
  verdict is robust across all of them.

## 5. Conclusion & handover

- **Pre-match drift trading: NOT VIABLE** on this data. Don't pursue it as a 0.7
  strategy. The drift signal is real but sub-spread; trading it is a net loss
  even before realistic fills.
- Together with **SP-370** (green-out: NOT VIABLE), **both** 0.7 odds-only
  strategy hypotheses are now answered in the negative — which is a perfectly
  good 0.7 outcome: the data has been honestly assessed and neither crude
  strategy survives costs. This is the evidence SP-372 (the 0.7 synthesis) pulls
  together.
- A size-aware re-run would only confirm the verdict here, so it is low priority;
  it is folded into the SP-384/SP-396-dependent follow-up rather than raised as
  urgent.

### How to reproduce

```bash
python scripts/scan_drift_trading.py --parquet data/research_dataset.parquet
# prints per-config mean/median/hit-rate + overall verdict;
# writes charts/drift_trading_sensitivity.png ; commission overridable: --commission 0.02
```

Methodology note: P&L is a fully-hedged back/lay round trip on best prices with
commission on net winnings; momentum and mean-reversion are mirror framings; the
sweep covers seven entry/exit windows. Each leg uses the selection's own observed
price (no forward-fill). No match results or settlement data are used — strictly
odds-only, per the SP-367 scoping.
