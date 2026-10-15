# Requirements Document

## Introduction

This feature (Jira SP-368, part of the Betfair Trader 0.7 "analyse the captured
data" release) builds a clean, reusable research dataset loader from the Betfair
odds captured during the season. Today the only analysis path is
`analyse_service.py` (`MarketAnalyser`), which reads a single hardcoded
`market_id`, parses each stored odds blob ad hoc with `ast.literal_eval` and only
reads one depth level (`availableToBack[1]["price"]`), and plots a single market.
Every downstream 0.7 analysis (exploratory analysis SP-369, green-out scan
SP-370, pre-match drift SP-371) needs a reliable, reusable way to turn
`bf.market_table` into clean per-match, per-runner time-series spanning **many**
matches.

The deliverable is a documented, tested loader that produces a tidy multi-match
dataset: one row per captured observation (match x runner x timestamp) with
structured odds columns (best back, best lay, available depth where present),
runner identity and outcome role (Home/Draw/Away), market/event metadata, and the
matched-volume signal (`totalMatched`) over time. Following the SP-328 / SP-332
house pattern, the parsing and shaping is pure, property-testable logic in
`logic/`, with a thin database-reading wrapper in `scripts/`. The existing
`MarketAnalyser` read path is refactored onto the new loader so there is a single
odds-reading implementation.

Scope is **odds-only**: no match results/settlement data is read or required
(that is deferred to 0.8). The loader shapes what is already captured; it does
not change the capture pipeline, the database schema, or the stored odds format.
Each record also carries a pre-match vs in-play Match_Phase marker, but that
marker is derived purely from the Capture_Timestamp relative to the Match
`start_time` — it uses no match results or settlement data and so stays within
the odds-only scope.

## Glossary

- **Dataset_Loader**: The component this feature delivers. It reads captured odds
  from the data store and produces the Research_Dataset. Comprises a pure
  transform layer (`logic/`) and a thin database-reading wrapper (`scripts/`).
- **Odds_Transform**: The pure, I/O-free logic layer of the Dataset_Loader. Given
  raw captured rows (as plain Python values), it parses and shapes them into
  Dataset_Records without touching the database or filesystem. Lives in `logic/`
  and is property-testable, following the SP-328 pattern.
- **Dataset_Reader**: The thin I/O wrapper of the Dataset_Loader. It reads rows
  from the data store, delegates all shaping to the Odds_Transform, and writes the
  Research_Dataset artifact. Lives in `scripts/`.
- **Research_Dataset**: The tidy output: a table (pandas DataFrame, persistable to
  Parquet) of Dataset_Records spanning all captured matches, with a documented
  Dataset_Schema.
- **Dataset_Record**: One row of the Research_Dataset: a single captured
  observation of one Runner in one Match at one Capture_Timestamp, carrying the
  structured odds, volume, identity and metadata columns of the Dataset_Schema.
- **Dataset_Schema**: The documented set of columns, their names, types and
  meaning, that every Dataset_Record conforms to.
- **Odds_Value**: The raw stored odds for one Market_Row: a Python dict `repr`
  string (parsed with `ast.literal_eval`, not JSON) in `bf.market_table.odds`,
  containing `availableToBack` and `availableToLay` price/size ladders and an
  optional `tradedVolume` key.
- **Price_Ladder**: An `availableToBack` or `availableToLay` list of
  `{price, size}` entries within an Odds_Value, ordered best-first by Betfair.
  May be empty.
- **Best_Back**: The best (highest) available-to-back price for a Runner at a
  Capture_Timestamp — the first entry of the `availableToBack` Price_Ladder.
- **Best_Lay**: The best (lowest) available-to-lay price for a Runner at a
  Capture_Timestamp — the first entry of the `availableToLay` Price_Ladder.
- **Depth**: The additional Price_Ladder entries beyond the best price, where
  present in the Odds_Value.
- **Matched_Volume**: The cumulative amount matched on a Runner's market at a
  Capture_Timestamp, taken from the `totalMatched`/`tradedVolume` figure in the
  captured data where present.
- **Market_Row**: One row of `bf.market_table`: `(timestamp, market_id,
  runner_id, odds)`.
- **Target_Row**: One row of `bf.target`: carries `target_id`, `event_id`,
  `market_id`, `runner_ids`, `start_time`, `status`, `last_updated`, `notes`.
- **Runner_Identity**: A `(runner_id, runner_name)` pair for one selection in a
  Market, parsed from the pipe-delimited `"id-name|id-name"` `runner_ids` string
  on the Target_Row.
- **Outcome_Role**: The role a Runner plays in a MATCH_ODDS market — one of
  `HOME`, `DRAW`, or `AWAY` — derived from the Runner_Identity set of the Match.
- **Match**: A single captured market identified by `market_id`, with its
  associated Target_Row metadata and set of Runner_Identity values.
- **Capture_Timestamp**: The `timestamp` of a Market_Row, the instant the odds
  observation was captured.
- **Match_Phase**: Whether a Dataset_Record was captured before kick-off or
  after. One of `PRE_MATCH` (Capture_Timestamp strictly before the Match
  `start_time`) or `IN_PLAY` (Capture_Timestamp at or after `start_time`). Null
  where `start_time` is unknown.
- **Data_Store**: The project PostgreSQL database (schema `bf`) holding
  `bf.market_table` and `bf.target`, reached with the project `.env` connection
  details via the existing `DBOutputConnection` / `psycopg2` approach.

## Requirements

### Requirement 1: Parse a stored Odds_Value into structured prices and volume

**User Story:** As an analyst, I want each stored Odds_Value parsed into structured best-back, best-lay, depth and volume values, so that analysis reads reliable numeric columns instead of re-parsing raw blobs ad hoc.

#### Acceptance Criteria

1. WHEN an Odds_Value string decodes to a mapping containing both an `availableToBack` Price_Ladder and an `availableToLay` Price_Ladder, THE Odds_Transform SHALL parse it into a structure exposing the `availableToBack` Price_Ladder, the `availableToLay` Price_Ladder, and the Matched_Volume.
2. WHERE a parsed Odds_Value contains a Matched_Volume figure, THE Odds_Transform SHALL expose it; WHERE no Matched_Volume figure is present, THE Odds_Transform SHALL expose the Matched_Volume as a null value.
3. WHEN a parsed Odds_Value has a non-empty `availableToBack` Price_Ladder, THE Odds_Transform SHALL expose the highest back price among its entries as the Best_Back.
4. WHEN a parsed Odds_Value has a non-empty `availableToLay` Price_Ladder, THE Odds_Transform SHALL expose the lowest lay price among its entries as the Best_Lay.
5. WHEN a parsed Odds_Value Price_Ladder has entries beyond the best price, THE Odds_Transform SHALL expose all non-best entries as the Depth for that side, preserving each entry's price and size and ordering them best-to-worst.
6. WHERE a parsed Odds_Value Price_Ladder is empty, THE Odds_Transform SHALL expose the corresponding Best price as a null value and the Depth as an empty sequence.
7. IF an Odds_Value string cannot be decoded into a mapping containing both an `availableToBack` Price_Ladder and an `availableToLay` Price_Ladder, THEN THE Odds_Transform SHALL return a null parse result without raising an exception.
8. IF a Price_Ladder entry has a non-numeric, negative, or otherwise out-of-range price or size, THEN THE Odds_Transform SHALL return a null parse result without raising an exception.
9. THE Odds_Transform SHALL serialize a parsed Odds_Value back to its stored string form.
10. WHEN a valid Odds_Value string is parsed and then serialized, THE Odds_Transform SHALL produce a string that parses to a structure with prices, sizes, entry order, and Matched_Volume value equal to those of the original (round-trip property).

### Requirement 2: Parse Runner_Identity and derive Outcome_Role

**User Story:** As an analyst, I want each Runner attributed to a named selection and to a Home/Draw/Away role, so that every Dataset_Record is traceable to a match outcome.

#### Acceptance Criteria

1. WHEN a Target_Row `runner_ids` string in the form `"id-name|id-name"` is provided, THE Odds_Transform SHALL parse it into a sequence of Runner_Identity pairs, one per pipe-delimited entry, preserving the left-to-right order in which the entries appear in the string.
2. WHEN a `runner_ids` entry contains more than one hyphen, THE Odds_Transform SHALL treat the text before the first hyphen as the `runner_id` and the entire remaining text after the first hyphen (including any further hyphens) as the `runner_name`.
3. IF a `runner_ids` entry has no hyphen, an empty `runner_id` segment, or an empty `runner_name` segment after splitting, THEN THE Odds_Transform SHALL exclude that entry from the Runner_Identity sequence and retain all other parseable entries.
4. IF a `runner_ids` string is absent, is empty, or contains no parseable entry, THEN THE Odds_Transform SHALL return an empty Runner_Identity sequence without raising an exception.
5. WHEN a Match has a Runner_Identity whose `runner_name` is equal to the configured draw selection name under a case-sensitive, exact string comparison after trimming leading and trailing whitespace from both values, THE Odds_Transform SHALL assign that Runner the Outcome_Role `DRAW`.
6. WHEN a Match has exactly three Runner_Identity values with exactly one assigned the `DRAW` Outcome_Role, THE Odds_Transform SHALL assign the `HOME` Outcome_Role to the first non-draw Runner in `runner_ids` order and the `AWAY` Outcome_Role to the second non-draw Runner in `runner_ids` order.
7. IF a Match does not have exactly three Runner_Identity values, or does not have exactly one Runner assigned the `DRAW` Outcome_Role, THEN THE Odds_Transform SHALL assign a null Outcome_Role to every Runner in that Match whose role is not otherwise determined and SHALL record the Match as having an undetermined Outcome_Role assignment, without raising an exception.

### Requirement 3: Produce tidy per-match, per-runner, per-timestamp records

**User Story:** As an analyst, I want one tidy row per observation across all captured matches, so that I can analyse odds and volume over time uniformly without bespoke per-market code.

#### Acceptance Criteria

1. WHEN a set of Market_Rows and their associated Target_Row metadata are provided, THE Odds_Transform SHALL produce exactly one Dataset_Record per distinct `(market_id, runner_id, Capture_Timestamp)` combination present in the Market_Rows that is not excluded under criterion 5 or 8.
2. THE Odds_Transform SHALL populate each Dataset_Record with the `market_id`, the `runner_id`, the Capture_Timestamp, the Best_Back, the Best_Lay, the Matched_Volume, the Runner_Identity `runner_name`, the Outcome_Role, and the Match_Phase, setting the Best_Back, Best_Lay, Matched_Volume, `runner_name`, Outcome_Role, or Match_Phase to a null value where the corresponding value is absent or undetermined for that observation.
3. THE Odds_Transform SHALL populate each Dataset_Record with the Match-level metadata `event_id`, `start_time`, and `target_id` from the associated Target_Row, setting any of these fields to a null value where it is absent on the associated Target_Row.
4. WHERE Depth is present in a Market_Row Odds_Value, THE Odds_Transform SHALL retain that Depth for each side in the resulting Dataset_Record alongside the Best_Back and Best_Lay.
5. IF a Market_Row Odds_Value cannot be parsed, THEN THE Odds_Transform SHALL exclude that Market_Row from the Research_Dataset, record the count of Market_Rows excluded as unparseable, and continue producing Dataset_Records for the remaining parseable Market_Rows.
6. WHEN producing Dataset_Records for a Match, THE Odds_Transform SHALL order the records of each Runner by ascending Capture_Timestamp, and SHALL break ties between records sharing an identical Capture_Timestamp by ascending `runner_id` so that the output ordering is deterministic for identical inputs.
7. THE Odds_Transform SHALL produce the Research_Dataset using only the inputs provided to it, performing no database, filesystem, or network access.
8. IF a Market_Row references a `runner_id` that has no matching Runner_Identity in the associated Match, THEN THE Odds_Transform SHALL still produce the Dataset_Record for that observation with a null `runner_name` and a null Outcome_Role, and SHALL record the count of such records with an unresolved Runner_Identity.

### Requirement 4: Read captured odds across many matches from the Data_Store

**User Story:** As an analyst, I want the loader to read every captured match from the Data_Store, so that the Research_Dataset spans the whole season rather than a single hardcoded market.

#### Acceptance Criteria

1. WHEN the Dataset_Reader runs, THE Dataset_Reader SHALL read Market_Rows for all captured `market_id` values present in `bf.market_table`.
2. WHEN the Dataset_Reader runs, THE Dataset_Reader SHALL read the associated Target_Row metadata for each `market_id` from `bf.target`.
3. WHERE a `market_id` filter is supplied, THE Dataset_Reader SHALL restrict the read to the supplied `market_id` values.
4. WHERE a Capture_Timestamp range is supplied, THE Dataset_Reader SHALL restrict the read to Market_Rows whose `timestamp` is greater than or equal to the range start and less than or equal to the range end, treating a range with only a start value as having no upper bound and a range with only an end value as having no lower bound.
5. THE Dataset_Reader SHALL delegate all parsing and shaping of the read rows to the Odds_Transform.
6. IF the Data_Store is unreachable, THEN THE Dataset_Reader SHALL report an operator-facing error identifying the Data_Store as unreachable and SHALL produce no Research_Dataset artifact.
7. WHEN the Dataset_Reader reads a value from a `market_id` or `runner_ids` supplied as a filter argument, THE Dataset_Reader SHALL pass that value to the data store as a bound query parameter rather than by string interpolation.
8. IF a supplied `market_id` filter or Capture_Timestamp range matches zero Market_Rows, THEN THE Dataset_Reader SHALL produce an empty Research_Dataset artifact containing zero Market_Rows and SHALL report an operator-facing notice indicating that no rows matched the supplied filter.
9. IF a `market_id` has one or more Market_Rows but no associated Target_Row in `bf.target`, THEN THE Dataset_Reader SHALL exclude that `market_id` from the Research_Dataset and SHALL report an operator-facing warning identifying the `market_id` as missing Target_Row metadata.

### Requirement 5: Output a documented Research_Dataset artifact

**User Story:** As an analyst, I want the Research_Dataset written to a documented tabular artifact, so that downstream analysis can load it directly and rely on a stable schema.

#### Acceptance Criteria

1. WHEN the Dataset_Reader completes a run, THE Dataset_Reader SHALL produce the Research_Dataset as a pandas DataFrame whose columns match the Dataset_Schema in name, type, and order.
2. WHEN a run produces zero Dataset_Records, THE Dataset_Reader SHALL produce an empty Research_Dataset DataFrame that still carries every Dataset_Schema column with its declared type.
3. WHERE an output path is supplied, THE Dataset_Reader SHALL persist the Research_Dataset to that path in Parquet format such that reloading it yields a DataFrame equal to the produced Research_Dataset in columns, types, and row count.
4. IF persisting the Research_Dataset to a supplied output path fails, THEN THE Dataset_Reader SHALL report an operator-facing error, leave any existing file at that path unchanged, and retain the in-memory Research_Dataset.
5. THE Dataset_Loader SHALL publish the Dataset_Schema as a documented artifact listing, for each column of criterion 6, its name, type, and meaning.
6. THE Research_Dataset DataFrame SHALL contain exactly the columns `market_id`, `runner_id`, `runner_name`, `outcome_role`, `timestamp`, `best_back`, `best_lay`, `matched_volume`, `match_phase`, `event_id`, `start_time`, and `target_id`, with no additional columns.
7. WHEN the Dataset_Reader completes a run, THE Dataset_Reader SHALL report through an operator-facing channel the count of Dataset_Records produced and the count of Market_Rows excluded as unparseable, reporting zero where none apply.

### Requirement 6: Supersede the existing MarketAnalyser read path

**User Story:** As a maintainer, I want the one-off ad hoc odds parsing in `MarketAnalyser` replaced by the new loader, so that there is a single odds-reading implementation.

#### Acceptance Criteria

1. THE MarketAnalyser SHALL obtain its per-runner odds-over-time data from the Dataset_Loader rather than from its own `ast.literal_eval` parsing of the `odds` column.
2. WHEN the MarketAnalyser produces its single-market odds-over-time data for a given `market_id`, THE MarketAnalyser SHALL expose, for each Runner of that `market_id`, a Best_Back series and a Best_Lay series whose values, their associated Capture_Timestamps, and their ascending-Capture_Timestamp ordering are each equal to those the Dataset_Loader produces for that same `market_id` and Runner.
3. IF the Dataset_Loader produces no Dataset_Record for the requested `market_id`, THEN THE MarketAnalyser SHALL produce an empty odds-over-time result for that `market_id` and report that no captured odds were found for it, without raising an exception.
4. THE repository SHALL contain exactly one odds-parsing code path that reads an Odds_Value, and that path SHALL be the Odds_Transform; no other code path SHALL read an Odds_Value via `ast.literal_eval` of the `odds` column.

### Requirement 7: Tested, documented, lint-clean delivery

**User Story:** As a maintainer, I want the loader covered by tests and documentation that follow the project's house conventions, so that it is trustworthy and consistent with the rest of the codebase.

#### Acceptance Criteria

1. THE Odds_Transform SHALL have property-based tests that, over at least 100 generated valid Odds_Value inputs, assert that parsing an Odds_Value and then serializing it produces a value equal to the original input.
2. THE Odds_Transform SHALL have tests that assert each of the following observable outcomes: Runner_Identity is correctly parsed from a Market_Row, the expected Outcome_Role is assigned, a Dataset_Record is produced for each parseable Market_Row, and each unparseable Market_Row is omitted from the produced Dataset_Records without raising an exception.
3. THE delivered Python modules SHALL pass `ruff check` and `ruff format --check` with zero reported issues.
4. THE delivered test suite SHALL complete under `pytest -m ""` with zero failures and zero errors.
5. THE Odds_Transform and Dataset_Reader public functions SHALL each carry a docstring that states the function's purpose, describes each input parameter, and describes the return value.

### Requirement 8: Classify each record as pre-match or in-play

**User Story:** As an analyst, I want each Dataset_Record tagged as pre-match or in-play, so that I can restrict strategy analysis to the window a strategy applies to.

#### Acceptance Criteria

1. WHEN a Dataset_Record has a known Capture_Timestamp and a known Match `start_time`, and the Capture_Timestamp is strictly earlier than the `start_time`, THE Odds_Transform SHALL assign that Dataset_Record the Match_Phase `PRE_MATCH`.
2. WHEN a Dataset_Record has a known Capture_Timestamp and a known Match `start_time`, and the Capture_Timestamp is equal to or later than the `start_time`, THE Odds_Transform SHALL assign that Dataset_Record the Match_Phase `IN_PLAY`.
3. IF the Match `start_time` is absent or unknown for a Dataset_Record, THEN THE Odds_Transform SHALL assign that Dataset_Record a null Match_Phase without raising an exception.
4. THE Odds_Transform SHALL derive the Match_Phase solely from the Capture_Timestamp and the Match `start_time`, using no match results or settlement data.
