# Historic data research tools (SP-401 / SP-491)

Tools for testing trading ideas on Betfair's historic exchange data, built for the free **Basic** tier but
tier-agnostic where the file format allows. Nothing here places bets or calls the Betfair API. Background, costs and
the findings so far are in `docs/SP-401-historic-data.md`.

## What is where

| Piece | Role |
|---|---|
| `logic/historic_data.py` | Pure core: parse a market's stream messages into a compact record, commission, flat-stake checks, the exit-rule simulator (profit target, stop loss, time exit), the fast grid evaluator, calibration, post-jump events. No I/O. |
| `scripts/historic_extract.py` | I/O wrapper: reads every `.bz2` under `data/historic/<TIER>` in parallel and writes one pickle cache. |
| `scripts/historic_checks.py` | CLI: `flat`, `longshot`, `calibration`, `ltd`, `goal`. |
| `tests/test_historic_data.py` | Unit and property tests for the core, including both guards below. |

## Workflow

1. **Download** the data from the Betfair historic data site (log in with a Betfair account). The Basic tier is free,
   but each month still has to be "purchased" once. Use the filters (Sport = Soccer, Country = GB, market types
   `MATCH_ODDS` and `OVER_UNDER_25`) to keep the download small.
2. **Extract** the tar with 7-Zip ("No pathnames") into `data/historic/BASIC/<year>/<Mon>/<day>/<eventId>/<marketId>.bz2`.
   The `.bz2` files are read directly; there is no need to decompress them. `data/` is gitignored.
3. **Build the cache** (about 5 minutes for 110,000 files):

   ```powershell
   & ".venv\Scripts\python.exe" scripts/historic_extract.py --root data/historic --tier BASIC --out data/historic/cache_basic.pkl
   ```

4. **Run checks** (each takes seconds to a few minutes):

   ```powershell
   & ".venv\Scripts\python.exe" scripts/historic_checks.py flat
   & ".venv\Scripts\python.exe" scripts/historic_checks.py calibration
   & ".venv\Scripts\python.exe" scripts/historic_checks.py ltd --max-entry 4.0
   ```

The cache is a pickle. Only load caches you generated yourself.

## Reading the results: every number is an upper bound

* Basic only has **last traded price** (`ltp`): no bid/ask, no queue position, no bet delay. In-play `ltp` updates about
  once a minute and only when a bet matches. Real spreads are not in these numbers. As a rule of thumb each 1% of
  price lost per leg costs about 1 per 50 staked on a round trip.
* **Minutes are wall-clock** since in-play start and include about 15 minutes of half-time.
* **Commission** is charged per market on net winnings (5% base rate, `marketBaseRate` in the files). A net loss in a market
  pays nothing; losses do not offset wins in other markets.
* The grid picks its best cell on the earlier half of the months and reports it on the later half. With 144 cells,
  some will always look good in-sample; trust only the out-of-sample figure and the share of positive cells.

## Two guards (both came from real mistakes; do not remove them)

1. **No look-ahead in trade selection** (`assert_selection_ignores_future`, run automatically by `ltd`).
   A first version dropped markets with fewer than 15 in-play price updates. That count depends on the outcome
   (a market whose Over wins early stops trading), so it silently removed the games that hurt a layer and produced a
   fake +6.85 per 50 trade. Only filter on what is known at entry. The guard rebuilds each market with its in-play
   series emptied and fails if the include/exclude decision changes.
2. **Stale last-traded price after an outcome-deciding event** (`asof_calibration`, run automatically by `ltd`).
   After the goal that settles Over 2.5, nobody trades at about 1.01, so `ltp` stays at the pre-goal price and a simulated
   exit on it is not available in reality. On Basic data the Over 2.5 implied probability fell from 53.7% at kick-off to
   42.6% by minute 100 while Over won 53.4% of games. `ltd` refuses to print an exit grid for a runner whose as-of
   price is more than 3 points away from the realised share (override with `--force` only to demonstrate the problem).
   The draw is calibrated within 1 point, so draw exit rules are on firmer ground.

## Not included

* The Flumine full-order-book simulation was a throwaway trial in a separate virtual environment (it needs the Pro tier
  for honest fills). See the SP-401 document for what was learned.
* Advanced and Pro files carry `batb`/`batl` and `atb`/`atl` ladders; `parse_market` currently reads `ltp` only. Extending
  it to replay the ladder is the first step if a paid month is ever bought.
