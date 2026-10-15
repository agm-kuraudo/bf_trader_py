# Implementation Plan: Betfair Research Dataset (SP-368)

## Overview

This plan builds the Dataset_Loader for SP-368 (Betfair Trader 0.7) incrementally
and test-first, following the SP-328 / SP-332 house pattern: pure, I/O-free,
property-testable logic in `logic/research_dataset.py`, a thin psycopg2 reader in
`scripts/build_research_dataset.py`, a documented 12-column schema in
`docs/research_dataset_schema.md`, and a refactor of `analyse_service.py` onto the
loader so the repository keeps exactly one odds-parsing path.

Each implementation step pairs with its hypothesis property tests and example
unit tests. Odds_Value generation reuses the existing SP-332 hypothesis
generators in `tests/test_property_quality_checks.py`, and the pure parse/
serialize core reuses `logic/quality_checks.py` (`parse_odds`, `serialize_odds`,
`back_prices`, `lay_prices`). Pure-logic tests need no database; the reader is
tested with a fake cursor that records SQL + bound parameters, plus fixtures.
Property-based tests run a minimum of 100 generated inputs and are tagged
`# Feature: betfair-research-dataset, Property {n}: {property_text}`.

## Tasks

- [x] 1. Scaffold the pure Odds_Transform module and data models
  - Create `logic/research_dataset.py` with a no-I/O module docstring (no `os`, `psycopg2`, filesystem, or network), mirroring `logic/quality_checks.py`
  - Define frozen dataclasses `PriceLevel`, `StructuredOdds`, `RunnerIdentity`, `OutcomeRoleAssignment`, and the record/result containers (`Dataset_Record` shape, `TransformResult`) with docstrings
  - Define `DEFAULT_DRAW_NAME` constant (the configured draw selection name, e.g. `"The Draw"`) and type aliases for `MarketRow` / `TargetRow` inputs
  - Import `parse_odds` / `serialize_odds` from `logic.quality_checks` to establish the single parse path
  - _Requirements: 1.1, 6.4, 7.5_

- [x] 2. Implement Odds_Value structuring and serialization (Req 1)
  - [x] 2.1 Implement `structured_odds` and `serialize_structured`
    - `structured_odds(raw)` delegates the raw decode to `quality_checks.parse_odds`; computes `best_back` = max back price, `best_lay` = min lay price, best-to-worst `back_depth` / `lay_depth` of non-best entries, and `matched_volume` (scalar `totalMatched` when present, else summed `tradedVolume` sizes, else `None`)
    - Return `None` without raising when the value lacks both ladders (Req 1.7) or any ladder entry has a non-numeric, negative, or out-of-range price or size (Req 1.8); empty ladder → null best + empty depth (Req 1.6)
    - `serialize_structured(odds)` round-trips back to the stored `str(dict)` form via `quality_checks.serialize_odds`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 7.5_
  - [x] 2.2 Write property test for Odds_Value round-trip
    - **Property 1: Odds_Value round-trip** — parse then serialize reproduces back/lay prices, sizes, entry order, and matched volume
    - Reuse the SP-332 odds generators from `tests/test_property_quality_checks.py`
    - **Validates: Requirements 1.9, 1.10, 7.1**
  - [x] 2.3 Write property test for best-price correctness
    - **Property 2: Best-price correctness** — `best_back`/`best_lay` equal max/min regardless of ladder order; empty ladder → null best + empty depth
    - Generate shuffled and empty ladders
    - **Validates: Requirements 1.3, 1.4, 1.5, 1.6**
  - [ ]* 2.4 Write unit tests and depth-retention test for `structured_odds`
    - Example cases: valid parse, missing-ladder → `None`, out-of-range/negative/non-numeric price or size → `None` (no raise)
    - Depth-retention: an Odds_Value with ladder entries beyond the best retains non-best entries (price + size, best-to-worst) per side alongside `best_back`/`best_lay`
    - _Requirements: 1.7, 1.8, 3.4_

- [x] 3. Implement Runner_Identity parsing and Outcome_Role assignment (Req 2)
  - [x] 3.1 Implement `parse_runner_ids`
    - Parse a `"id-name|id-name"` string into ordered `RunnerIdentity` pairs, splitting each entry on the FIRST hyphen only, preserving left-to-right order, dropping malformed entries (no hyphen, empty id, or empty name), returning empty for absent/empty/no-parseable input, never raising
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 7.5_
  - [x] 3.2 Implement `assign_outcome_roles`
    - DRAW = runner whose trimmed name case-sensitively equals the trimmed configured draw name; when exactly three runners with exactly one draw, assign HOME/AWAY to first/second non-draw runner in order; otherwise null role for undetermined runners with `undetermined = True`; never raise
    - _Requirements: 2.5, 2.6, 2.7, 7.5_
  - [ ]* 3.3 Write property test for runner_ids parsing
    - **Property 3: Runner_ids parsing** — preserves order, splits on first hyphen, drops malformed entries while retaining parseable ones, never raises
    - Generate mixed well-formed/malformed entries
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**
  - [ ]* 3.4 Write property test for Outcome_Role assignment
    - **Property 4: Outcome_Role assignment** — correct HOME/DRAW/AWAY for the exactly-three-with-one-draw case; null roles + undetermined flag otherwise; never raises
    - Generate valid and degenerate runner sets
    - **Validates: Requirements 2.5, 2.6, 2.7**
  - [ ]* 3.5 Write unit tests for identity and role examples
    - Runner_Identity correctly parsed from a Market_Row's Target; expected Outcome_Role assigned for a canonical MATCH_ODDS trio
    - _Requirements: 2.1, 2.5, 2.6, 7.2_

- [x] 4. Implement Match_Phase classification (Req 8)
  - [x] 4.1 Implement `match_phase`
    - Return `PRE_MATCH` when capture timestamp strictly precedes `start_time`, `IN_PLAY` when at/after, `None` when either is unknown; derive solely from the two timestamps; never raise
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 7.5_
  - [ ]* 4.2 Write property test for Match_Phase classification
    - **Property 5: Match_Phase classification** — PRE_MATCH / IN_PLAY / null boundaries hold
    - Generate ordered, equal, and null timestamp pairs
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4**

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement record assembly in the Odds_Transform (Req 3)
  - [x] 6.1 Implement `build_records` (top-level pure entry point)
    - Produce exactly one Dataset_Record per distinct `(market_id, runner_id, capture_ts)` that parses; populate `market_id`, `runner_id`, `timestamp`, `best_back`, `best_lay`, `matched_volume`, `runner_name`, `outcome_role`, `match_phase`, and Target metadata `event_id` / `start_time` / `target_id`, nulling where absent/undetermined
    - Exclude unparseable Market_Rows while counting them; still emit a record with null identity + null role for a `runner_id` absent from the Target while counting it; order each runner's records by ascending `capture_ts`, ties broken by ascending `runner_id`; use only inputs, no I/O
    - Return a `TransformResult` carrying records plus the unparseable-excluded and unresolved-runner counts
    - _Requirements: 3.1, 3.2, 3.3, 3.5, 3.6, 3.7, 3.8, 7.5_
  - [x] 6.2 Write property test for record determinism and ordering
    - **Property 6: Record determinism and ordering** — identical input yields identical output; per-runner ascending timestamp with ties broken by ascending runner_id
    - Generate multi-runner, multi-timestamp, duplicate-timestamp inputs
    - **Validates: Requirements 3.1, 3.6**
  - [x] 6.3 Write property test for exclusion and unresolved accounting
    - **Property 7: Exclusion and unresolved accounting** — produced records + unparseable count = distinct observations; unresolved count = produced records whose runner_id had no identity
    - Generate mixes of parseable/unparseable rows and resolved/unresolved runners
    - **Validates: Requirements 3.5, 3.8**
  - [ ]* 6.4 Write unit tests for record-assembly examples
    - A Dataset_Record produced for each parseable Market_Row; each unparseable Market_Row omitted without raising; null fields where undetermined
    - _Requirements: 3.2, 3.3, 3.5, 7.2_

- [x] 7. Implement the DataFrame schema layer and schema documentation (Req 5)
  - [x] 7.1 Implement `empty_dataset` helper and DataFrame assembly
    - Add an `empty_dataset()` helper (shared by the zero-record path and schema doc) that builds the typed-but-empty 12-column frame with the declared dtypes and column order
    - Add a function that turns a `TransformResult`'s records into a pandas DataFrame carrying exactly the 12 Dataset_Schema columns in declared order/dtypes, with no additional columns
    - _Requirements: 5.1, 5.2, 5.6, 7.5_
  - [ ]* 7.2 Write property/example test for schema invariance
    - **Property 8: Schema invariance** (example-based) — empty-record and populated runs both carry exactly the 12 columns in declared order and dtypes, no extras
    - **Validates: Requirements 5.2, 5.6**
  - [x] 7.3 Author the Dataset_Schema documentation
    - Create `docs/research_dataset_schema.md` listing all 12 columns (`market_id`, `runner_id`, `runner_name`, `outcome_role`, `timestamp`, `best_back`, `best_lay`, `matched_volume`, `match_phase`, `event_id`, `start_time`, `target_id`) with name, type, meaning, and null conditions
    - _Requirements: 5.5, 5.6_

- [x] 8. Implement the Dataset_Reader DB layer (Req 4)
  - [x] 8.1 Scaffold `scripts/build_research_dataset.py` and implement row reads
    - Mirror `scripts/verify_db.py` / `scripts/check_quality.py`: `sys.path` shim, `DotenvLoader`, `psycopg2.connect` with `connect_timeout`, `output.log.Output`
    - Implement `read_market_rows(cursor, market_ids, ts_start, ts_end)` and `read_targets(cursor, market_ids)` passing all filter values as BOUND query parameters (never interpolated); no filter → all markets; a one-sided timestamp range is open on the other side
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.7, 7.5_
  - [ ]* 8.2 Write reader tests for bound-parameter usage and filters
    - Drive `read_market_rows` / `read_targets` with a fake cursor that records SQL + the parameter sequence; assert filter values arrive as bound parameters, never string-interpolated; assert open-ended range handling
    - _Requirements: 4.3, 4.4, 4.7_

- [x] 9. Implement run orchestration, persistence, and reporting (Req 4, 5)
  - [x] 9.1 Implement `build_dataset` and `main`
    - `build_dataset` connects, reads rows, delegates all shaping to `build_records`, assembles the schema DataFrame, optionally persists Parquet to `--out`, and returns a result carrying the frame plus run-summary counts
    - Error/edge paths: Data_Store unreachable → operator-facing error, no artifact, non-zero exit (Req 4.6); a `market_id` with rows but no Target → exclude + warning (Req 4.9); zero-match filter → empty typed 12-column frame + notice (Req 4.8, 5.2); Parquet persist failure → error, leave existing file unchanged, retain in-memory frame, non-zero exit (Req 5.4); always report produced + unparseable-excluded counts (Req 5.7)
    - `main` parses args (`--market-id`, `--from`, `--to`, `--out`), runs `build_dataset`, prints the summary, returns the exit code
    - _Requirements: 4.5, 4.6, 4.8, 4.9, 5.1, 5.3, 5.4, 5.7, 7.5_
  - [x] 9.2 Write reader tests for orchestration, persistence, and reporting
    - Fake query results assert: unreachable store → error + no artifact + non-zero exit; rows-without-Target → excluded + warning; zero-match → empty typed frame + notice; produced/excluded counts reported
    - Parquet round-trip to a real temp path (reload equals produced frame in columns/dtypes/row count); forced write failure asserts error reported, existing file unchanged, in-memory frame retained, non-zero exit
    - _Requirements: 4.6, 4.8, 4.9, 5.3, 5.4, 5.7_

- [x] 10. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Refactor MarketAnalyser onto the loader (Req 6)
  - [x] 11.1 Replace the inline parse path in `analyse_service.py`
    - Change `MarketAnalyser.get_market_data` / `process_data` to build the single-market odds-over-time frame by calling `build_records` on the rows it reads for its one `market_id`, projecting each runner's `best_back` / `best_lay` series in ascending-timestamp order
    - Delete the direct `ast.literal_eval` usage and the latent index-1 ladder bug so the repository has exactly one odds-parsing path; return an empty result + logged "no captured odds" notice when the loader yields no record (no exception)
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - [x] 11.2 Write MarketAnalyser equivalence and empty-result tests
    - Build Market_Rows for one `market_id`; run both `MarketAnalyser` and `build_records`; assert each runner's `best_back`/`best_lay` series, their timestamps, and ascending ordering are equal (Req 6.2)
    - Assert the empty-result + "no captured odds" notice path when the loader yields no record (Req 6.3)
    - _Requirements: 6.2, 6.3_

- [x] 12. Final gate - Full suite, lint, and format clean
  - Run `ruff check .` and `ruff format --check .` and fix any reported issues
  - Run `pytest -m ""` (full suite, including any slow tests) with zero failures and zero errors
  - _Requirements: 7.3, 7.4_

## Notes

- Tasks 2.2, 2.3, 6.2, 6.3, 9.2, and 11.2 are now required core verification tasks (no `*`): the Research_Dataset underpins the whole 0.7 release (SP-369/370/371), and Req 7.1 explicitly mandates the Odds_Value round-trip property test. The remaining `*` test sub-tasks (2.4, 3.3, 3.4, 3.5, 6.4, 7.2, 8.2) stay optional and can be skipped for a faster MVP; core implementation tasks are never optional.
- Each task references specific requirement/acceptance-criteria numbers for traceability.
- Property-based tests (Properties 1-8 from the design) reuse the SP-332 odds generators and run a minimum of 100 generated inputs; pure-logic tests need no database.
- Parquet is the only output format — no CSV export.
- Checkpoints provide incremental validation points.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "3.2", "4.2", "8.1"] },
    { "id": 3, "tasks": ["3.3", "3.4", "3.5", "6.1", "8.2"] },
    { "id": 4, "tasks": ["6.2", "6.3", "6.4", "7.1", "9.1"] },
    { "id": 5, "tasks": ["7.2", "7.3", "9.2", "11.1"] },
    { "id": 6, "tasks": ["11.2"] }
  ]
}
```
