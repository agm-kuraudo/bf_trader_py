# Requirements Document

## Introduction

This feature improves the Betfair capture pipeline so that a single poll of a
market records all of that market's runners (Home / Draw / Away for MATCH_ODDS)
as one coherent snapshot sharing one capture timestamp, and persists a
matched-volume figure for each runner in that same snapshot. It combines two
agreed-together Jira tickets that modify the same seam — the capture pipeline's
odds snapshot writer (`monitor_service.update_runner_odds`) and the
`simpleStategy.py` capture configuration — and feed the same downstream
consumer, the SP-368 research dataset loader (`scripts/build_research_dataset`
and the pure `logic/research_dataset.py` Odds_Transform):

- **SP-396 — Coherent per-poll market snapshot.** Today each runner is written
  as its own row at its own poll instant (one `listRunnerBook` call per runner,
  each INSERT using its own `current_timestamp`), so the three outcomes of a
  match are almost never captured at the same timestamp. This forces every
  cross-outcome calculation to reconstruct a market snapshot by as-of
  forward-fill, which is benign pre-match but fabricates impossible sub-1.0
  books in-play (observed in-play books down to 0.314). The capture pipeline
  must instead record all of a market's runners together from one Betfair
  market-book call, sharing one capture timestamp.

- **SP-384 — Matched-volume capture.** The SP-368 loader derives
  `matched_volume` from a scalar `totalMatched` if present, else the summed
  size of a `tradedVolume` ladder, else null. Both are currently absent from
  the stored odds blobs in `bf.market_table.odds` (the writer stores only the
  `ex` object from `listRunnerBook`, which carries only the back/lay ladders),
  so every row's `matched_volume` is null. The capture pipeline must persist a
  matched-volume figure into the stored odds blob in the exact shape the SP-368
  Odds_Transform already reads, so no loader change is needed.

Hard constraints agreed for both: backfill is not possible (this improves
coverage going forward only); the change must be deployed and verified on the
Raspberry Pi (the Betfair capture deployment target, via `scripts/deploy.sh`);
and the SP-368 loader / Odds_Transform format MUST NOT change — the writer must
persist data in the shape the loader already reads.

Reference material in this repo: `docs/SP-370_green_out_scan.md` (coherent
snapshot and forward-fill context, and the matched-volume gap), the SP-368
Odds_Transform (`logic/research_dataset.py`, `_matched_volume` /
`structured_odds`) and reader (`scripts/build_research_dataset.py`), and the
existing `listMarketBook` request template (`api/request_body.py`), which
already requests `EX_BEST_OFFERS`, `EX_ALL_OFFERS`, and `EX_TRADED` across all
runners in one call.

## Glossary

- **Capture_Pipeline**: The background odds-capture service, principally
  `MonitorService.update_runner_odds` in `monitor_service.py`, together with the
  `simpleStategy.py` capture configuration that drives it.
- **Snapshot_Writer**: The part of the Capture_Pipeline that reads a market's
  prices from the Betfair API and writes odds rows into
  `bf.market_table`.
- **Market_Book_Call**: A single Betfair `listMarketBook` request for one
  market that returns every runner of that market in one response (contrast the
  current per-runner `listRunnerBook` calls).
- **Market_Snapshot**: The set of odds rows written by the Capture_Pipeline for
  all runners of one market from one poll, sharing one Capture_Timestamp.
- **Capture_Timestamp**: The `timestamp` column value of a `bf.market_table`
  row, identifying when an observation was captured.
- **Runner**: One selection of a market (for MATCH_ODDS: the Home outcome, the
  Away outcome, or The Draw).
- **Odds_Blob**: The string value stored in the `bf.market_table.odds` column
  for one Runner at one Capture_Timestamp — a `str(dict)` rendering of the
  Runner's exchange prices.
- **Matched_Volume**: A numeric figure of money matched, persisted into the
  Odds_Blob either as a scalar `totalMatched` key or as a `tradedVolume` ladder
  of `{price, size}` entries, in the shape the Odds_Transform already reads.
- **Odds_Transform**: The pure SP-368 transform (`logic/research_dataset.py`)
  that parses each Odds_Blob; its `_matched_volume` reads a scalar
  `totalMatched`, else sums a `tradedVolume` ladder's sizes, else yields null.
- **Research_Dataset**: The SP-368 dataset produced by
  `scripts/build_research_dataset.py` from captured odds, exposing per-runner
  observations with `best_back`, `best_lay`, `matched_volume`, `outcome_role`,
  and `timestamp` columns.
- **Dataset_Loader**: The SP-368 reader (`scripts/build_research_dataset.py`)
  that builds the Research_Dataset by delegating all shaping to the
  Odds_Transform.
- **Pi**: The Raspberry Pi that is the deployment target for the Betfair capture
  stack.
- **Deploy_Process**: Running `scripts/deploy.sh` on the Pi, which syncs code,
  validates `.env`, and rebuilds and recreates the capture container.

## Requirements

### Requirement 1: Capture all runners in one coherent timestamped snapshot

**User Story:** As a trading researcher, I want every runner of a market captured together at one shared timestamp, so that I can compute a true cross-outcome market book without forward-fill reconstruction.

#### Acceptance Criteria

1. WHEN the Capture_Pipeline polls a market for odds, THE Snapshot_Writer SHALL retrieve prices for all runners of that market from one Market_Book_Call.
2. WHEN the Snapshot_Writer writes a Market_Snapshot for a market, THE Snapshot_Writer SHALL compute one shared Capture_Timestamp once per Market_Book_Call and assign it to every Runner row of that Market_Snapshot.
3. WHEN the Snapshot_Writer writes a Market_Snapshot, THE Snapshot_Writer SHALL write exactly one bf.market_table row per Runner returned by the Market_Book_Call, omitting no returned Runner and writing no duplicate Runner row.
4. WHEN the Market_Book_Call returns N runners (N >= 1) for a market, THEN that Market_Snapshot SHALL consist of exactly N Runner rows whose Capture_Timestamp values are all equal to one identical value.
5. IF the Market_Book_Call returns an empty result for a market (a null or absent response, or a response whose runner collection is empty), THEN THE Snapshot_Writer SHALL write no bf.market_table row for that market and SHALL continue polling the remaining markets.
6. IF the Market_Book_Call for a market fails or raises an error, THEN THE Snapshot_Writer SHALL record a log entry identifying the failure and the affected market, SHALL write no bf.market_table row for that market, and SHALL continue polling the remaining markets.
7. IF the Snapshot_Writer fails partway through writing a Market_Snapshot, THEN that market's poll SHALL leave either all N Runner rows at one shared Capture_Timestamp or no Runner rows for that poll, never an incomplete subset.

### Requirement 2: Persist matched volume in the same snapshot

**User Story:** As a trading researcher, I want each captured Runner row to carry a matched-volume figure, so that the Research_Dataset exposes liquidity alongside price without changing the loader.

#### Acceptance Criteria

1. WHEN the Snapshot_Writer writes a Runner row AND the Market_Book_Call response carries a Matched_Volume figure for that Runner, THE Snapshot_Writer SHALL persist that Matched_Volume figure into the Runner's Odds_Blob.
2. WHERE the Market_Book_Call response carries a scalar matched-total value for a Runner, THE Snapshot_Writer SHALL persist that value into the Odds_Blob under the totalMatched key that the Odds_Transform reads.
3. WHERE the Market_Book_Call response carries both a scalar matched-total value and a traded-volume ladder for a Runner, THE Snapshot_Writer SHALL persist the scalar value under the totalMatched key and SHALL NOT be required to also persist the tradedVolume ladder.
4. WHERE the Market_Book_Call response carries a traded-volume ladder for a Runner AND carries no scalar matched-total value, THE Snapshot_Writer SHALL persist that ladder into the Odds_Blob under the tradedVolume key that the Odds_Transform reads, as a list of entries each carrying a price and a size.
5. WHEN the Market_Book_Call response carries a scalar matched-total value of zero for a Runner, THE Snapshot_Writer SHALL persist that zero value under the totalMatched key rather than omitting the matched-volume key.
6. IF the Market_Book_Call response carries no matched-volume figure for a Runner (neither a scalar matched-total nor a traded-volume ladder), THEN THE Snapshot_Writer SHALL write that Runner row with its back and lay ladders and SHALL omit both the totalMatched and tradedVolume keys from the Odds_Blob.
7. THE Snapshot_Writer SHALL persist the Matched_Volume into the same Odds_Blob that carries the Runner's back and lay ladders, within the same Runner row.
8. THE Snapshot_Writer SHALL persist the Odds_Blob in the string shape that the existing Odds_Transform structured_odds function parses without any change to the Odds_Transform.

### Requirement 3: Research dataset surfaces coherent multi-outcome snapshots with volume

**User Story:** As a trading researcher, I want the Research_Dataset to expose coherent per-market, per-timestamp snapshots with non-null matched volume, so that the book is a true snapshot and liquidity is available for analysis.

#### Acceptance Criteria

1. WHEN the Dataset_Loader builds the Research_Dataset from a market captured by the updated Capture_Pipeline whose Market_Snapshot recorded all three MATCH_ODDS outcomes (HOME, DRAW, AWAY) at one shared Capture_Timestamp, THE Research_Dataset SHALL expose, for that Capture_Timestamp, exactly one Runner observation for each of the three outcome_role values (HOME, DRAW, AWAY).
2. WHEN the Dataset_Loader builds the Research_Dataset from markets captured by the updated Capture_Pipeline, THE Research_Dataset SHALL expose a matched_volume equal to the Matched_Volume figure carried by the Runner's Odds_Blob for every Runner observation whose Odds_Blob carried a Matched_Volume figure.
3. WHEN the Dataset_Loader builds the Research_Dataset, IF a Runner observation's Odds_Blob carried no Matched_Volume figure, THEN THE Research_Dataset SHALL expose a null matched_volume for that observation.
4. THE updated Capture_Pipeline SHALL produce Odds_Blobs that the Dataset_Loader processes without any change to scripts/build_research_dataset.py or logic/research_dataset.py.
5. WHEN a market captured by the updated Capture_Pipeline is loaded, THE Research_Dataset SHALL derive each of a shared Capture_Timestamp's three outcome observations from the Runner row written at that same Capture_Timestamp, without carrying an earlier Runner's price forward onto a later Runner's Capture_Timestamp.

### Requirement 4: Deploy and verify on the Pi

**User Story:** As the operator of the capture stack, I want the change deployed and verified on the Pi, so that live captures gain coherent snapshots and matched volume on the real deployment target.

#### Acceptance Criteria

1. WHEN the updated Capture_Pipeline is deployed to the Pi, THE Deploy_Process SHALL rebuild and recreate the capture container from the updated code via scripts/deploy.sh, and the recreated container SHALL be in a running state after the Deploy_Process completes.
2. IF the Deploy_Process fails to rebuild or recreate the capture container, THEN THE Deploy_Process SHALL report a failure indication identifying the failed step and SHALL leave the previously running capture container unchanged.
3. WHEN a capture cycle runs on the Pi within 10 minutes after the Deploy_Process completes, THE Capture_Pipeline SHALL write a Market_Snapshot whose Runner rows for a currently-captured market all share one identical Capture_Timestamp value.
4. WHEN a capture cycle runs on the Pi within 10 minutes after the Deploy_Process completes, THE Capture_Pipeline SHALL write at least one Runner row whose Odds_Blob carries a Matched_Volume figure for a currently-captured market.
5. WHEN scripts/build_research_dataset.py runs against a market captured on the Pi after the Deploy_Process completes, THE Research_Dataset SHALL expose at least one instant for that market in which all three MATCH_ODDS outcomes (HOME, DRAW, AWAY) are present at one shared Capture_Timestamp.
6. WHEN scripts/build_research_dataset.py runs against a market captured on the Pi after the Deploy_Process completes, THE Research_Dataset SHALL expose a non-null matched_volume for at least one Runner observation of that market.
7. WHEN odds rows are written on the Pi after the Deploy_Process completes, THE Capture_Pipeline SHALL leave every bf.market_table odds row captured before the Deploy_Process completed unchanged in its stored Odds_Blob and Capture_Timestamp.
