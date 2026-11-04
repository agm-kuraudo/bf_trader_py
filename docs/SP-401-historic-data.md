# SP-401: Betfair historic data - tiers, shape, cost, licence

Status: **FINAL finding** (spike, time-boxed; closed 2026-11-04; licence terms not read, see end). Evidence labelled **measured** (from the sample
files in `data/historic/`, gitignored) or **inferred / unverified**.

## Tiers and cost

| Tier | Granularity | Fields (measured) | Soccer price |
|---|---|---|---|
| Basic (free) | change-only `ltp` | `ltp` only; no ladder, no volume | free |
| Advanced | ~1-2 s | `batb`/`batl` (top of book), `ltp`, `tv`, `trd` | £69/mo, £699/yr |
| Pro | API ticks (~50 ms) | full `atb`/`atl` ladder, `trd`, `ltp`, `tv`, BSP fields | £230/mo, £2,299/yr |

Prices were supplied by the operator from the historic data site (not independently checked).
For scale: the live API key is about £500/yr, so Advanced for a year is roughly 1.4x that and
Pro roughly 4.6x.

Unverified: whether a single month can be bought for one sport (the site says each period can be
"purchased" once; per-month purchase is implied by the monthly price but not tested).

## Shape (measured)

- One `.bz2` per market, laid out `TIER/year/Mon/day/eventId/marketId.bz2`. Readable directly with
  Python `bz2`; no tar extraction needed once downloaded.
- One JSON message per line (`op: mcm`, `pt` epoch ms). First message carries the full
  `marketDefinition`; later messages carry `rc` runner-change deltas.
- Sizes overwrite, not add; size 0 removes a level. Ladder must be rebuilt by replay
  (`data/historic/_replay.py` is a throwaway prototype that does this).
- Distance from `bf.market_table`: our rows are snapshots of top-3 back/lay plus an (empty)
  `tradedVolume`. Pro replays to a superset of that; Advanced gives top of book only; Basic gives
  last price only.

## Coverage (measured on the samples)

- Basic: Oct 2026, 633 events x (MATCH_ODDS + OVER_UNDER_25), GB football, all leagues. 29 of the
  81 events in `bf.target` overlap.
- Advanced and Pro samples: Nov 2015 only, same two market types, GB football.
- Files carry `eventName`/`marketType`/`countryCode`; no competition id. Premier League needs a
  team-name match after download.
- Over/Under 2.5 is present in all three tiers.

## Fields that matter

- **Volume (the SP-384 gap):** only Advanced and Pro. Basic has none.
- **Results (new):** the final `marketDefinition` carries runner status `WINNER`/`LOSER` and
  `settledTime`, including in Basic (checked on Sunderland v Brighton: Brighton won; Under 2.5).
  Our capture has no result, so Basic is a free results feed.
- **In-play:** Pro and Advanced contain in-play. Match Odds 27574353 (Everton v Sunderland, 2015):
  Pro 2,054 in-play messages, median gap 551 ms; Advanced 1,053, median 1,988 ms. This is relevant
  because recent bugs have left gaps in our own in-play capture (operator report; not measured
  here).

## Licence

Betfair's developer page presents the data for analysis and back-testing (building a simulation
model, exporting to CSV for databases/spreadsheets). **I have not read the actual terms of use**
(they sit behind the login/purchase flow). Open: whether research outputs derived from the data may
be used to inform live betting (expected yes for personal use), and any redistribution limits.
Action: read the terms shown at purchase before buying any paid tier.

## Recommendation (final)

**NO-GO on SP-402 (the stream-replay pipeline) and on any paid tier, for now.**

Why:
1. **The questions the data could answer came back negative.** On 52,407 Match Odds markets (2026,
   free Basic data), flat-stake backing/laying the favourite, draw, outsider, home and away, and the
   "lay the favourite early, back it at the off" trade, all show gross results indistinguishable from
   zero. After commission (5% base rate; still negative at 2%) they lose. This is the picture of an
   efficient market, consistent with the 0.7 finding (SP-372).
2. **The remaining untested ideas need data we do not have for free.** The one worth testing
   properly (price movement with a real spread, in-play time decay of Over/Under) needs bid/ask or
   ladders: Advanced (GBP 699/yr, or GBP 69 for one month) or Pro (GBP 2,299/yr, or GBP 230 a month).
   There is no specific theory yet that justifies that outlay.
3. **The pipeline would serve a thesis that is not supported.** SP-402 is substantial work to replay
   streams into a research dataset; with efficient pricing on the top-level questions, there is no
   evidence yet that the result would be tradable.

What is worth keeping, all free:
- **Basic as a results source.** Final `marketDefinition` carries WINNER/LOSER and `settledTime`
  (measured), which the capture lacks. Small, optional follow-up if results are ever needed.
- **The research tools are now in the repo** (SP-491): `logic/historic_data.py`, `scripts/historic_extract.py`,
  `scripts/historic_checks.py`, tests in `tests/test_historic_data.py`, and the workflow in
  `docs/historic_data_tools.md`. The early throwaway scripts (`_survey.py`, `_replay.py`, `_flumine_sim.py`)
  stay in the gitignored `data/historic/`.
- **Flumine** (open source) as the reference simulator if a paid month is ever justified.

If this is revisited: first write down a specific theory that needs bid/ask, buy at most one month
of Advanced, and compare it with our captured ladders for the same fixtures.

## Purpose and cost framing

The aim is to test trading theories on historic data, which the operator judges at least as good as
our own DB capture (and it fills our in-play gaps). The cost is not extortionate if one month is
enough: Advanced Soccer is £69 for a month, Pro £230. Any outlay still has to be justified against a
specific theory, so buy only after a theory is written down that needs volume or in-play detail.

## Existing tools and ideas worth reusing

Sources: [Betfair developer page](https://developer.betfair.com/historical-data-services-api/),
[Caan Berry review of MarketFeeder Pro](https://caanberry.com/market-feeder-pro/),
[Betfair Automation Hub: Flumine simulations](https://betfair-datascientists.github.io/tutorials/flumineSimulations/).
Content paraphrased from those pages.

- **Time Machine (MarketFeeder Pro).** Betfair's page lists it as its example app. Per the review it
  replays recorded markets (pre-off, in-play, settlement) faster than real time, with the ability to
  step back and forth, place simulated bets and watch events such as goals. It is a paid add-on
  (about £6/month for the feature, plus a small fee per market, kept permanently). Used to
  back-test trigger-based strategies before running them live. Windows only.
- **Market Replay (Betting Assistant).** I could not find a reliable description, so nothing is
  claimed here. Needs a look at the vendor site directly.
- **Flumine simulation mode (open source, Python).** Replays Pro stream files and simulates limit
  orders, including queue position and lapse on in-play. Betfair's tutorial uses it to enter at
  different times before the off and compare fills. It states its limits: no market impact from
  simulated bets, no cross-matching, tested on Pro only. This is the closest fit for our stack.

Ideas to take:
1. **Sweep the entry time** (e.g. 600 s ... 0 s before the off) and compare fills and profit by
   time and price band. Cheap to run once a replay exists.
2. **Simulate order fills, not just prices.** Back-testing on prices alone overstates results;
   queue position and lapse-at-in-play matter.
3. **Speed-up replay with rewind** is for visual review; a deterministic replay is what we need for
   automated tests, so skip the UI.
4. **State the market-impact limitation** in any result; replays cannot show how our own bets would
   move the market, so keep stakes realistic.

### Flumine trial (measured, free, no purchase)

Flumine is open-source Python, not a purchase. Installed 3.4.0 (with betfairlightweight 2.24.0) in an
isolated throwaway venv under `data/historic/` (gitignored); nothing added to project dependencies.
Script: `data/historic/_flumine_sim.py`.

- **API drift:** Betfair's tutorial uses the older `market_filter=` form. Flumine 3.x takes explicit
  `streams=[BetfairHistoricalStream(file_path=...)]` per file. The `.bz2` files load directly.
- **Gotcha:** `listener_kwargs={"inplay": True}` restricts processing to in-play updates only. Omit it
  to see the pre-match period.
- **Fill modelling works.** On 68 Pro Match Odds markets (Nov 2015), backing the favourite 10 minutes
  before the off with a 10 stake: taking the price already on offer matched on 68/68 orders, though
  not always in full (553.86 of 680 matched). Resting an offer one step better matched on only 22/68
  (32%), 216.37 of 680. That is the trade-off a price-only back-test cannot show.
- **Profit figures not interpreted.** Small sample, one fortnight of 2015, a single naive rule, no
  commission. They are shown only to prove the plumbing.
- **Fit for us:** needs Pro-level files for honest fills. The free Basic tier cannot drive it (no ladder).
  So Flumine is a tool for a future, paid-data decision, not something to adopt now.

Decision for now: no purchase; keep Flumine as the reference tool if a paid month is ever justified.

## Free worked example: does pre-match drift predict winners? (Basic, 2026)

Throwaway script `data/historic/_drift_check2.py` (gitignored). Data: 110,502 Basic files, Jan to Oct 2026,
GB football, all leagues. For each Match Odds runner, take the last `ltp` known at least 24 h before
kick-off and compare it with the last `ltp` before kick-off. Then check win rate and ROI of backing at the
close (1 unit, **no commission**), grouped by move size. Tests 0.8 idea 2 (drift as a winner signal).
53,454 runner rows used (about 17,800 markets). It supersedes an earlier October-only run (about 420 markets,
inconclusive).

| Group (all runners) | n | Win rate | Implied at close | ROI at close (95% +/-) |
|---|---|---|---|---|
| Steamed >10% | 6,685 | 38.6% | 38.7% | -1.9% (3.8%) |
| Stable | 35,619 | 37.0% | 37.0% | -1.1% (1.6%) |
| Drifted >10% | 11,150 | 22.8% | 23.3% | -8.9% (4.0%) |

By price band (close price), drifted runners: favourites (<2.5) -5.2% (+/-5.5%); mid (2.5 to 6)
+2.4% (+/-4.4%); outsiders (6+) -29.7% (+/-9.4%). Excluding the Draw: steamed -3.7%, stable -2.5%,
drifted -8.5%.

**Reading: no edge from drift. The market is efficient on this data.**
- Win rate matches the closing implied probability in every group (e.g. mid-priced drifters 26.5% vs
  25.9%). The closing price already contains the drift.
- The weak-looking "drifted" result (-8.9%) comes mostly from outsiders, who are over-backed (the
  familiar favourite-longshot bias), not from drift itself: within the favourite and mid bands the
  drift effect is within the noise.
- By month, drifted ROI swings between +7.7% and -22.3% with wide intervals (+/-12% to 21%), so
  month-to-month variation is noise.
- All figures are before commission and spread, so any real strategy would do worse.
- The one pocket that stands out is outsiders that drifted (win 7.5% vs 9.0% implied, ROI -29.7%).
  Taking the other side means laying long shots, which ties up large liability for small gains and
  is exactly where `ltp`-only data cannot show whether a price was actually available. Not
  pursued; noted for completeness.

Selection caveat: 107,046 runner rows had no `ltp` 24 h before kick-off or no close price and were
dropped (about two thirds of runners), so the sample is skewed towards fixtures traded a day or more
ahead (likely higher-profile matches). A further 1,745 markets had no single winner.

Data caveats: Basic has no bid/ask or volume, so even a real signal could not be tested against the
spread. Coverage is 2026 only. The second checking pass (data counted as 110,502 files at run time) was
read while extraction was finishing; rerun once to confirm if more files have landed.

What it shows about the data: Basic is enough for free, results-based checks at scale (tens of
thousands of markets), and this one found nothing to trade. That is a useful answer: it removes
idea 2 from the 0.9 backlog unless a better-defined drift signal is proposed.

## Cheap strategy checks (Basic, 2026, flat GBP 50)

Reproduce with `scripts/historic_extract.py` (one parallel pass into a cache of 55,245 Match Odds markets, no
unreadable files) then `scripts/historic_checks.py flat` and `longshot` (see `docs/historic_data_tools.md`).
Flat GBP 50 per bet; commission 5% (the file's `marketBaseRate`) on net winnings per market; no
premium charge or discount. For lays, GBP 50 is the stake won if the selection loses; risk is the
liability. **Prices are last-traded (`ltp`), not bid/ask, so every figure is an upper bound.** A sanity
check shows why: backing all three outcomes at the close sums to about -GBP 6.8k gross on 52,407
markets (home +21.8k, away -33.6k, draw +5.0k), i.e. roughly zero, so `ltp` carries no spread or
overround. Real tradable prices would be worse.

Prices at the close (52,407 usable markets):

| Strategy | Gross GBP | Net GBP (after commission) | Net GBP per bet (95%) |
|---|---|---|---|
| Back favourite | -2,586 | -63,007 | -1.20 +/- 0.41 |
| Lay favourite | +2,586 | -57,984 | -1.11 +/- 0.41 |
| Back draw | +4,992 | -93,867 | -1.79 +/- 0.84 |
| Lay draw | -4,992 | -103,580 | -1.98 +/- 0.86 |
| Back outsider | +37,731 | -68,634 | -1.31 +/- 1.97 |
| Back home | +21,788 | -53,069 | -1.01 +/- 1.80 |
| Back away | -33,572 | -121,828 | -2.32 +/- 0.85 |

- **Gross is close to zero; commission is what sinks them.** Back favourite is -0.05 per bet gross and
  -1.20 net. Strategies that win often in small amounts (lay the draw wins about 73% of the time) pay
  the most commission.
- **Back favourite and lay the draw lost money in every one of the ten months** (net). The monthly
  figures are in the script output.
- Using prices at T-24h (13,310 markets with prices on all three runners): nothing significant
  except small negatives (lay favourite -1.41 +/- 0.83; back away -3.36 +/- 1.49). Back draw (+2.12 +/-
  6.86) and back outsider (+3.38 +/- 9.83) are positive but far inside the noise.

### The lay-the-favourite-early, back-it-at-the-off trade

The earlier "drift" test asked whether drift predicts winners, which is not the idea. The idea is a
trade: lay the favourite when the game is far off, back it at kick-off, and lock in
`stake x (1 - P_early / P_close)` regardless of the result, on the thesis that the favourite's price
lengthens as the underdog gains support. Favourite = lowest price at the early time.

| Early time | Markets | Favourite price rose | Mean gross per trade (95%) | 1% trimmed mean | Net per trade (after commission) | Total net GBP |
|---|---|---|---|---|---|---|
| T-72h | 4,587 | 47.8% | +0.11 +/- 0.31 | +0.23 | -0.02 | -91 |
| T-24h | 13,310 | 46.6% | +0.01 +/- 0.15 | +0.03 | -0.10 | -1,370 |
| T-6h | 35,898 | 46.0% | -0.07 +/- 0.08 | -0.05 | -0.16 | -5,673 |
| T-1h | 47,621 | 46.5% | -0.36 +/- 0.84 | +0.06 | -0.43 | -20,444 |

- **No evidence of the sentiment drift.** The favourite's price rose in fewer than half the markets
  (46 to 48%), gross profit per GBP 50 trade is within a few pence of zero, and the median trade is
  exactly zero. After commission it is negative at every horizon.
- By favourite price band at T-24h (the only band cut with enough data): under 1.5 +0.15 +/- 0.26;
  1.5 to 2.0 -0.09 +/- 0.22; 2.0 to 3.0 +0.05 +/- 0.24 per trade. Nothing significant.
- The reverse (back early, lay at the close) is the negative of the gross column, so it is also about
  zero before commission.
- **Data limits:** Basic `ltp` is the last traded price and updates only when a bet matches, so
  "price at T-72h" is often stale and the plain mean is distorted by outliers (the first run showed
  an absurd +413% mean move, which is why medians and trimmed means are reported). Only 4,587 markets
  have a price on all three runners at T-72h, so that row leans towards high-profile fixtures. With
  true bid/ask the trade would also pay the spread on both legs, which `ltp` cannot show.
- To test the thesis properly needs real bid/ask over time (Advanced or Pro), or our own captured
  ladders from `bf.market_table` for the captured fixtures.

### Commission sensitivity

Betfair charges commission on net winnings per market (a net loss in a market pays none; losses in
one market do not offset wins in another) per its
[charges page](https://www.betfair.com/aboutUs/Betfair.Charges/). The model applies it that way.
Net result per GBP 50 bet, back favourite / lay draw / lay-early trade at T-24h: at 0% commission
-0.05 / -0.10 / +0.01; at 2% -0.51 / -0.85 / -0.04; at 5% -1.20 / -1.97 / -0.10. At 0% all are
indistinguishable from zero. The 5% is the market base rate (a maximum); a lower personal rate
helps, but not enough to make any of these positive.

## In-play exit-rule checks (SP-491, Basic 2026, GBP 50 stake)

Reproduce with `scripts/historic_extract.py` (in-play `ltp` series for 52,601 Match Odds and 52,603 Over/Under 2.5
markets) then `scripts/historic_checks.py ltd`, `goal` and `calibration` (see `docs/historic_data_tools.md`; the
two guards described below are built into `ltd`). The `ltd` command refuses to print an exit grid for the Over/Under
runner because its calibration fails, so the Over/Under exit tables from the first run are not reproducible by
design. In-play `ltp` updates about once a minute and only when a bet matches. **Every figure is an upper bound: last-traded prices, no spread, no queue, no bet
delay, no match clock (minutes are wall-clock since in-play start, including about 15 minutes of half-time).**
Commission 5% on net winnings per market.

### Two mistakes found and fixed (read this before trusting any simulation like it)

1. **Look-ahead filter.** A first run required at least 15 in-play updates per market. That count depends on
   the outcome (a market whose Over wins early stops trading), so it silently dropped the games that hurt
   layers. It produced a fake +GBP 6.85 per trade for laying Over 2.5. Fixed by filtering only on
   information known at entry. Check: calibrate the entry price against realised outcomes on the full
   population first (pre-kick-off Over 2.5 was well calibrated: 53.3% implied, 53.2% realised).
2. **Stale last-traded price after an outcome-deciding event.** On Over 2.5 the as-of implied probability
   falls from 53.7% at kick-off to 42.6% at wall-clock minute 100 while Over actually wins 53.4% of games.
   When Over is settled by a goal nobody trades at about 1.01, so the last price stays at the pre-goal
   value, and a simulated "exit" on it is not tradable. **Every in-play Over/Under exit result is therefore
   invalid.** The draw is calibrated within 1 point at every time (24.6% to 25.6% implied against 24.8%
   realised), so the draw results are on firmer ground. Rule: always run an as-of calibration like this
   before believing an `ltp`-priced exit.

### Results

| Test | Result |
|---|---|
| **Lay the draw (LTD), hold to settlement** | -2.18 +/- 0.85 per trade net; gross -0.29. Commission pays 1.88 of it. |
| **LTD with a stop loss, any cell** | Negative in every cell with a stop (-0.26 to -1.98 net). Stops reduce the result. |
| **LTD, take profit, no stop, time exit (all draw prices)** | +0.1 to +0.5 net per trade; out of sample (Jul to Oct) +0.11 +/- 0.41: nothing. |
| **LTD, draw price at most 4.0 at kick-off, take profit, no stop** | +0.7 to +1.4 net per trade (gross +1.8 to +1.9). Best cell picked on Jan to Jun (target 10, no stop, exit at wall-clock minute 75) scores +0.99 +/- 0.39 on Jul to Oct (n=11,695). 131 of 144 cells positive in training. |
| **Over/Under 2.5 exit rules** | Invalid (stale-price artefact above). Hold to settlement -0.92 +/- 0.42 (commission only). |
| **Post-jump draw trade** (draw lengthens at least 20% in a sample, back it 1 to 5 minutes later, lay 5 to 10 minutes after that) | Gross +1.4 to +2.7 at no slippage, +0.4 to +1.7 at 1% per leg, about zero at 2%, negative at 3%. Positive in every month at 1%. The opposite jump also shows the draw price falling afterwards, so it looks like a general pattern in last-traded draw prices rather than a goal effect. |
| **Laying long shots** (price 6 or more, fixed liability GBP 50) | -0.03 +/- 0.26 per bet (win 9.8% vs 10.2% implied). Dead at every threshold from 6 to 30. |

The repo tool (`scripts/historic_checks.py ltd --max-entry 4.0`) reproduces every grid cell exactly. Its default
walk-forward splits the months present in half (here the first five of ten, selecting target 5, no stop, minute 90,
training mean +1.68) and scores +0.89 +/- 0.44 on the later months (n=13,098), consistent with the +0.99 above.

### Reading

- **Nothing here is an edge.** The strongest candidate is "lay the draw when its price is at most 4.0, book
  profit at a target, no stop loss, otherwise close at about minute 60 of play". After commission and with
  no slippage it makes about GBP 1 per GBP 50 trade (2%).
- **Part of that is not an edge.** Holding to settlement gives gross -0.29 but pays GBP 1.88 commission; exiting
  early gives gross +1.8 to +1.9 and pays about 0.5. So about GBP 1.3 of the net improvement is simply smaller
  winning amounts attracting less commission, and the rest is the take-profit option.
- **The take-profit gain is suspicious.** A fixed-time exit with no target is neutral (gross +0.26 at kick-off,
  0.00 at minute 60, -0.10 at minute 75 for draw prices up to 4.0; negative for all draws). The gain
  appears only when exits trigger on spikes in the last-traded series, which is where noise in `ltp`
  and wide real spreads would hurt most.
- **Break-even slippage is roughly 0.5% to 1% per leg (estimate: each 1% costs about GBP 1 per GBP 50 trade).** A spread of one or two ticks on a price near 3 to 4
  costs about 1.5% to 3% across the two legs, which removes the profit.
- **What would be needed to validate:** real bid/ask history for these matches (Advanced for GBP 69 a month, or
  Pro), then replay with realistic fills. Our own capture cannot do it: the delayed key makes in-play prices
  about 15 minutes old.

## Not answered by this spike

- **Licence terms** were not read (they sit behind the purchase flow). Betfair's developer page
  presents the data for analysis and back-testing. Read the terms before any paid purchase or
  redistribution of results.
- Whether recent months of Advanced/Pro match the 2015 sample shape (only 2015 was sampled).
- Whether single-month purchases work as assumed (untested; nothing was purchased).
