# SP-400 — Start capturing Over/Under goals markets (EPL/UCL)

**Status:** capture-only change (no analysis — that waits for accumulation, 0.9+).
**fixVersion:** Betfair Trader 0.8. **Epic:** SP-437.

## What this ticket does

Adds **Over/Under total-goals** markets to the Betfair capture targets for the
EPL/UCL fixtures already tracked, so their odds start flowing into the store now
and enough history builds up for the in-play time-decay analysis later
(`docs/0.8_direction_notes.md`, Track A). Primary target is **Over/Under 2.5**;
**1.5** and **3.5** are captured too since we're already polling the same
fixtures (low incremental cost).

## Why it's a small change (where the work actually is)

The capture pipeline has two halves, and only the **target-identification** half
needed changing:

- **Monitor loop** (`monitor_service.py`) — already market-type agnostic. It
  polls whatever `market_id`s sit in `bf.target` via `listMarketBook`, builds a
  coherent per-poll snapshot (SP-396), and writes
  `(timestamp, market_id, runner_id, odds)` rows to `bf.market_table`. An
  Over/Under market is just another `market_id` with its own two runners
  ("Over 2.5 Goals" / "Under 2.5 Goals"), so **no change was needed here**.
- **Research-dataset loader** (`logic/research_dataset.py`, SP-368) — likewise
  per-`market_id` and handles arbitrary runner counts. Market **type** is not a
  column; it lives implicitly in `bf.target.notes` (the full market description
  blob, which carries `marketType` via the `MARKET_DESCRIPTION` projection). So
  **no schema or loader change was needed**.
- **Target identification** (`target_service.py` → `BFDriver.get_target_markets`
  / `get_events`) — this is where the change landed (below).

## Changes made

1. **`logic/simpleStategy.py` — `FromFileStrategy` now loads `MARKET_TYPEs` from
   `config/strategy.yaml`.** It previously read EVENTS/COMPETITIONS/etc. but
   *silently ignored* the `MARKET_TYPEs` key, so capture was pinned to the class
   default `["MATCH_ODDS"]` regardless of the file — a latent bug. Loaded with
   the class default as fallback, so an older yaml without the key keeps the
   previous MATCH_ODDS-only behaviour.

2. **`config/strategy.yaml` — added the validated Over/Under codes** to
   `MARKET_TYPEs`: `MATCH_ODDS`, `OVER_UNDER_25`, `OVER_UNDER_15` (see the
   validation results below).

3. **`BFDriver.get_target_markets` — emit one Target per returned market.** It
   previously kept only `markets[0]` for each event, which was fine when the
   filter was a single market type (one market per event) but would silently
   drop every market after the first once we filter on several types. It now
   iterates all markets `listMarketCatalogue` returns for the event and creates
   a `Target` per market, reading `marketTime`/`runners` from each market's own
   object (not from `self.__market_obj`, whose instance state only reflects the
   last market built).

`MAX_EVENTS` still caps **events** (not markets) — correct; each event now just
spawns more targets (1 MATCH_ODDS + up to 3 Over/Under per fixture).

## Market-type codes — VALIDATED against the live API

Codes confirmed by `scripts/validate_market_types.py` (reusable, read-only — it
calls Betfair `listMarketTypes` for Soccer + EPL/UCL and dumps a real fixture's
markets), run 2026-10-27:

| Code            | Coverage          | Runners                       | Decision           |
|-----------------|-------------------|-------------------------------|--------------------|
| `OVER_UNDER_25` | 39 markets        | Under / Over 2.5 Goals        | **capture** (primary) |
| `OVER_UNDER_15` | 5 markets         | Under / Over 1.5 Goals        | **capture** (thin) |
| `OVER_UNDER_05` | 39 markets        | Under / Over 0.5 Goals        | exists; out of scope |
| `OVER_UNDER_35` | **does not exist**| —                             | NOT captured       |

Important correction to the initial plan: there is **no standalone
`OVER_UNDER_35`** market type for these competitions. The ticket said "consider
3.5", but the 3.5 line only lives inside the aggregate `ALT_TOTAL_GOALS`
("Goal Lines") market and as `FIRST_HALF_GOALS_35`. Listing `OVER_UNDER_35`
would have silently captured nothing (a wrong code returns no market, no error),
so it was dropped. This is exactly why the codes were validated against the live
API rather than assumed.

The runner names on a real `OVER_UNDER_25` market confirm the two-runner shape
the loader expects: "Under 2.5 Goals" / "Over 2.5 Goals".

## Data-quality caveat (delayed App Key)

We're on the free/delayed Betfair App Key, not the £500 live key (deferred until
there's a money-making strategy — see `docs/SP-384_matched_volume_live_key_blocker.md`).
Practical impact on captured Over/Under data: **odds are delayed ~15 minutes and
there's no matched-volume figure**. Not ideal for the eventual in-play
time-decay analysis, but it does **not** block capture — the data still
accumulates and the structure (two runners, time-decaying Over price) is intact.
Worth remembering when the 0.9+ analysis reads this data.

## Definition of Done

- New captures record Over/Under goals markets for tracked fixtures, verified on
  the Pi (rows landing in `bf.market_table` for the OU `market_id`s).
- No analysis in this ticket.

## Deploy (handled manually)

Pull latest on the Pi and run the project's `scripts/deploy.sh` (syncs code,
validates `.env`, rebuilds + recreates the container). Verify an OU market's
rows appear after a capture run before moving the ticket to Done.
