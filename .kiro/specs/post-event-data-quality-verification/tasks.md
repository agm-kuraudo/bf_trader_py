# Implementation Plan: Post-Event Data-Quality Verification (SP-332)

## Overview

This plan implements the automated post-event data-quality verification for captured
Betfair odds (Jira **SP-332**, `bf_trader_py`, label `betfair`), following the SP-328
conventions: pure decision logic in `logic/`, thin I/O wrappers in `scripts/`, Hypothesis
property tests + `pytest` unit/integration tests, and the `.env`/`DotenvLoader` + `psycopg2`
connection approach used by `scripts/check_freshness.py` and `scripts/verify_db.py`.

Work proceeds bottom-up: the pure `logic/quality_checks.py` decision functions first (each
close to its property test), then the durable-record tables + `scripts/check_quality.py`
daily job, then the pure `logic/quality_report.py` formatters + `scripts/report_quality.py`
on-demand report, then wiring/integration, the Rundeck daily job, a data-calibration
validation task against real Pi season data, and the Confluence documentation note. Each
step builds on the previous so nothing is left orphaned.

**Scope note:** This feature only **detects and reports** the in-play under-sampling defect
(the observed ~900s cadence vs the intended 5s). Fixing the polling is **SP-343**, a separate
ticket, and is explicitly out of scope here. All Coverage/Useful thresholds are anchored to
the intended 5s cadence per the design's Threshold Calibration; the calibration task
validates those fixed thresholds against real data and produces a sample report — it does
**not** re-derive thresholds to fit the current (defective) data.

The implementation language is **Python** (matching the design and the existing codebase).

## Tasks

- [x] 1. Scaffold the pure quality-checks module and thresholds
  - Create `logic/quality_checks.py` (pure: no `os`, no `psycopg2`, no file/network access), mirroring the style of `logic/deploy_checks.py`
  - Define the `QualityThresholds` frozen dataclass and the `default_max_gaps()` helper as the single documented source of the five calibrated thresholds: `look_back_hours=24`, `coverage_shortfall_ratio=0.40`, `inplay_interval_s=5`, `inplay_duration_s=105*60`, per-tier `max_gap_s`, `null_price_ratio=0.50`, `min_samples_per_market=200`, `prematch_window_s=3*3600`, `settlement_grace_s=0`
  - Define the `DimensionOutcome` and `MatchQualityResult` dataclasses (four dimensions + overall) used by the aggregation
  - Define the `Cadence_Tier` interval map constant (`IN_PLAY`=5, `LESS_THAN_3H`=300, `LESS_THAN_6H`=900, `LESS_THAN_12H`=3600, `MORE_THAN_12H`=14400)
  - _Requirements: 3.7, 5.5, 9.1, 10.3_

- [x] 2. Implement the Odds_Value parser/serializer and price helpers
  - [x] 2.1 Implement `parse_odds`, `serialize_odds`, `back_prices`, `lay_prices`, `has_any_price`
    - `parse_odds` uses `ast.literal_eval` (the stored form is a Python dict `repr`, not JSON, as `analyse_service.py` already does); returns `None` on any failure (non-dict, missing either ladder, ladder not a list of `{price, size}`) leaving no partial result
    - A successful parse guarantees `availableToBack` and `availableToLay` are lists; preserve the optional `tradedVolume` key for round-trip
    - `serialize_odds` is the round-trip partner: `str(dict)` form; `has_any_price` is true when at least one back OR lay price exists
    - _Requirements: 4.1, 4.2, 9.5, 9.6_

  - [x] 2.2 Write property test for the Odds_Value parser round-trip
    - **Property 1: Odds_Value parser round-trip** — for any valid Odds_Value `v`, `serialize_odds(parse_odds(v))` equals `v`, preserving all three keys including `tradedVolume`
    - **Validates: Requirements 9.6**

  - [x] 2.3 Write property test for parser rejection with no partial result
    - **Property 2: Parser rejects malformed input with no partial result** — for any input that is not a valid Odds_Value, `parse_odds` returns `None` and produces no partially parsed value
    - **Validates: Requirements 4.2, 9.5**

- [x] 3. Implement the Present dimension and selection helpers
  - [x] 3.1 Implement `present_result`, `is_verifiable`, `default_look_back_window`
    - `present_result`: fail when `market_id` absent (2.4) or `row_count == 0` (2.2), recording affected identifiers; pass when `row_count >= 1` (2.3)
    - `is_verifiable`: true iff status in `{CLOSED, EXPIRED}` AND `start_time` within window (1.1, 1.3)
    - `default_look_back_window(now)`: previous calendar day 00:00:00–23:59:59 local (1.2)
    - _Requirements: 1.1, 1.2, 1.3, 2.2, 2.3, 2.4_

  - [ ]* 3.2 Write property test for the Present outcome
    - **Property 3: Present outcome tracks market_id presence and row count** — passes iff `market_id` present AND `n >= 1`; fails (recording identifiers) when absent or `n == 0`
    - **Validates: Requirements 2.2, 2.3, 2.4**

  - [ ]* 3.3 Write property test for selection admitting exactly settled, in-window targets
    - **Property 8: Selection admits exactly settled, in-window targets** — `is_verifiable` returns true iff status is `CLOSED`/`EXPIRED` AND `start_time` in window; `IDENTIFIED`/`OPEN` and out-of-window excluded
    - **Validates: Requirements 1.1, 1.3**

- [x] 4. Implement the Coverage dimension
  - [x] 4.1 Implement `expected_sample_count` and `coverage_result`
    - `expected_sample_count` sums, per Cadence_Tier active across `[prematch_start, inplay_end]`, the expected sampling intervals, using the **intended** in-play interval (`inplay_interval_s=5`) so matches captured at the observed ~900s cadence fall short and are correctly flagged (the SP-343 defect, detect-only)
    - `coverage_result` computes shortfall vs `coverage_shortfall_ratio`, detects the largest contiguous no-row gap per applicable tier against `max_gap_s`, handles the in-play-empty special case, and excludes benign gaps wholly outside `prematch_window_s`; returns `passed`, `actual`, `expected`, `shortfall`, `largest_gap`, `inplay_empty`, `reasons`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 4.2 Write property test for Coverage
    - **Property 4: Coverage passes only within tolerance and gap limits** — passes iff shortfall ratio within `coverage_shortfall_ratio` AND no in-window gap exceeds the applicable `max_gap_s` AND in-play not empty while pre-match count passes; on failure records actual/expected/shortfall and largest-gap bounds
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**

- [x] 5. Implement the Consistency dimension
  - [x] 5.1 Implement `consistency_result`
    - Runs all sub-checks: every Odds_Value parses (4.1/4.2), both-empty-price proportion vs `null_price_ratio` (4.3), distinct `runner_id` count equals declared `runner_ids` count (4.4), per-runner timestamps non-decreasing in storage order (4.5), no duplicate `(market_id, runner_id, timestamp)` (4.6); declared `runner_ids` absent/unparseable always fails (4.7)
    - Returns per-sub-check evidence + overall `passed`
    - _Requirements: 4.1, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 5.2 Write property test for Consistency
    - **Property 5: Consistency passes only when every sub-check holds** — passes iff all sub-checks hold; absent/unparseable declared `runner_ids` always fails
    - **Validates: Requirements 4.1, 4.3, 4.4, 4.5, 4.6, 4.7**

- [x] 6. Implement the Useful dimension
  - [x] 6.1 Implement `useful_result`
    - Single boolean result: pass requires `row_count >= min_samples_per_market` AND at least one row within `prematch_window_s` before `start_time` AND at least one row at/after settlement (`settlement_grace_s`); records earliest/latest timestamps and which lifecycle portion is absent on failure
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 6.2 Write property test for Useful
    - **Property 6: Useful requires both resolution and lifecycle span** — passes iff `row_count >= min_samples_per_market` AND a pre-match-window row AND an at/after-settlement row are present; missing portion recorded in the failure reason
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**

- [x] 7. Implement per-match aggregation
  - [x] 7.1 Implement `aggregate_match`
    - Produces one `MatchQualityResult`: each dimension recorded as PASS/FAIL/NOT_EVALUATED; `overall = FAIL` if any dimension fails or is unevaluated, PASS only when all four pass; failed/unevaluated dimensions carry reason + evidence
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 7.2 Write property test for aggregation
    - **Property 7: Aggregate outcome is pass only when all dimensions pass** — for any four outcomes drawn from `{PASS, FAIL, NOT_EVALUATED}`, `overall = PASS` iff all four are PASS; every non-passing dimension contributes its reason and evidence
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**

  - [x] 7.3 Write unit tests for the pure quality-checks logic
    - Present: 0-row and null-`market_id` fail; 1-row passes (2.2–2.4)
    - Coverage: synthetic 5s-cadence match passes; ~96-row ~900s-cadence match fails on shortfall (SP-343 case); 3-row stub fails; `1.243049256`-style (63 pre-match, 0 in-play) fails the in-play-empty case (3.6); benign ~98,000s overnight gap outside the pre-match window does not fail
    - Consistency: well-formed passes; injected unparseable odds, duplicate key, out-of-order timestamp, wrong runner count, absent `runner_ids` each fail (4.1–4.7)
    - Useful: full-lifecycle >=200-row match passes; ~96-row ~900s match fails resolution; 3-row stub and pre-match-only capture fail (5.1–5.4)
    - Aggregation: a `NOT_EVALUATED` dimension forces `overall = FAIL` (6.5)
    - Build `QualityThresholds` from the single documented source, not hard-coded numbers (so a revision updates tests through one edit)
    - _Requirements: 2.2, 2.3, 2.4, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.3, 4.4, 4.5, 4.6, 4.7, 5.1, 5.2, 5.3, 5.4, 6.5_

- [x] 8. Checkpoint - pure logic complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement the durable results tables and the daily check wrapper
  - [x] 9.1 Define the two results-table DDLs and the schema-readiness helper in `scripts/check_quality.py`
    - Create `scripts/check_quality.py` scaffolding mirroring `verify_db.py`/`check_freshness.py`: `REQUIRED_DB_KEYS`, `CONNECT_TIMEOUT_S=10`, `CONNECT_ATTEMPTS=3`, `MAX_RUN_DURATION_S=3600`, `LOCK_FILE`, `RESULTS_SCHEMA="bf"`, `RUN_TABLE`, `MATCH_RESULT_TABLE`, `RETENTION_DAYS=90`, and `_read_db_config`
    - Add `CREATE TABLE IF NOT EXISTS` DDL for `bf.quality_run` and `bf.quality_match_result` exactly per the design (text `COLLATE pg_catalog."default"`, `timestamp with time zone`, `jsonb` evidence, `TABLESPACE pg_default`, `ALTER TABLE IF EXISTS ... OWNER to postgres`)
    - Reuse `logic.deploy_checks.missing_tables` + `validate_env` to create only absent tables (the `verify_db.py` pattern), leaving existing rows untouched
    - _Requirements: 7.4, 9.1, 9.2, 9.3, 10.1_

  - [x] 9.2 Implement `check_quality` orchestration (I/O only, no quality decisions)
    - Connect via `.env`/`DotenvLoader` + `psycopg2` with `CONNECT_TIMEOUT_S`, retrying up to `CONNECT_ATTEMPTS` (7.5); ensure results tables exist; acquire the single-run `LOCK_FILE` guard and skip if held (8.3)
    - `SELECT` settled targets (`status IN ('CLOSED','EXPIRED')` within the window) and their `bf.market_table` rows ordered by `ctid` (storage order for the 4.5 ordering check); associate each target with rows sharing its `market_id`, isolating no-row targets (1.6) and per-match not-evaluated failures (2.5, 6.5) without aborting the run
    - Call the pure `logic/quality_checks.py` functions per match, then `INSERT` one `bf.quality_run` row + one `bf.quality_match_result` row per match (mapping each `DimensionOutcome` into the `*_outcome` columns and the `evidence` `jsonb`); record run `status` (`COMPLETED`/`UNREACHABLE`/`TIMEOUT`/`SKIPPED`) and `overall_alert`
    - Prune rows older than `RETENTION_DAYS` each run (>= the 30-day minimum, 7.4)
    - _Requirements: 1.1, 1.4, 1.5, 1.6, 2.5, 6.5, 7.1, 7.4, 7.5, 8.3, 8.4, 9.2, 9.3_

  - [x] 9.3 Implement `main()` one-line stdout summary and exit codes
    - Print a single readable run-summary line for the Rundeck output (surface (b)); non-zero exit on any FAIL / unreachable / zero-matches within 5s of the run row being written (7.2, 7.3, 7.5, 7.7); zero exit when all pass (7.6); set `overall_alert` accordingly
    - _Requirements: 7.2, 7.3, 7.6, 7.7_

  - [ ]* 9.4 Write unit tests for `check_quality` orchestration
    - Test the exit-code decisions and run-summary given synthetic per-match results (all-pass -> 0; any FAIL -> non-zero; zero-matches -> non-zero alert); test the results-table mapping (`DimensionOutcome` -> columns/`evidence`); DB-dependent assertions skip cleanly when no store is present (repo convention)
    - _Requirements: 7.2, 7.3, 7.6, 7.7_

- [x] 10. Implement the pure report formatters
  - [x] 10.1 Implement `logic/quality_report.py` formatters (pure, no I/O)
    - Create `logic/quality_report.py` with `format_report_text`, `format_report_markdown`, `format_report_csv`, each taking already-fetched `run_row` + `match_rows` (plain data) and returning a string; no queries/file access/clock; contain no quality decisions
    - `format_report_text`: run header (run_id, window, verified/passed/failed counts, status, alert flag) + aligned per-match table with every FAIL match shown
    - `format_report_markdown`: heading + summary + Markdown table (one row per match) suitable for Confluence/ticket paste
    - `format_report_csv`: stdlib `csv`; header + exactly one data row per match in the documented column order
    - _Requirements: 7.1, 9.1, 10.1_

  - [ ]* 10.2 Write property test for the CSV formatter row count
    - **Property 9: CSV report has exactly one data row per match** — `format_report_csv` output has data-row count (after the header) equal to the number of input match rows
    - **Validates: Requirements 10.1**

  - [ ]* 10.3 Write unit tests for the report formatters
    - Over a small known `run_row` + `match_rows` fixture (a couple of PASS matches and at least one FAIL): text output contains the run header fields and every FAIL match's `target_id`/`market_id`; markdown output is a heading/summary + table with one row per match including every match; CSV has the header + one data row per match in the documented column order
    - _Requirements: 7.1, 10.1_

- [x] 11. Implement the on-demand report command
  - [x] 11.1 Implement `scripts/report_quality.py` (read-only I/O + `argparse`)
    - Create `scripts/report_quality.py` mirroring the `check_quality.py` connection approach (`.env`/`DotenvLoader`, `psycopg2` + `CONNECT_TIMEOUT_S`) but strictly read-only: never creates tables, never writes rows, performs no quality evaluation
    - Implement `select_run` (default latest via `ORDER BY run_started DESC LIMIT 1`, or `--run-id`, or `--date`/`--from`+`--to` range) and `select_match_rows` (optionally `--failures-only` on `overall_outcome = 'FAIL'`), both read-only `SELECT`s on `bf.quality_run`/`bf.quality_match_result`
    - Implement `report_quality(...)` delegating formatting to `logic/quality_report.py` for `--format text|markdown|csv` (default `text`); run-selection options mutually exclusive
    - `main()`: parse options, print the formatted result to stdout, exit 0 on success (non-zero only on connection/argument error)
    - _Requirements: 9.3, 10.1, 10.2_

  - [ ]* 11.2 Write integration smoke test for `report_quality.py`
    - Run `scripts/report_quality.py` against `my_postgres` for the latest run (default `text` path); assert non-empty output and zero exit, verifying the read-only `SELECT`s and wiring to the pure formatters; skip cleanly when no store is reachable (repo convention)
    - _Requirements: 10.1_

- [x] 12. Integration test for the daily check against `my_postgres`
  - [x] 12.1 Write the integration test for `check_quality.py`
    - Execute `scripts/check_quality.py` against `my_postgres` on `my_trading_network` (same `.env`/`DotenvLoader` path as `check_freshness.py`); assert a pass/fail Quality_Result is produced and the durable record is written: a new `bf.quality_run` row for the run and one `bf.quality_match_result` row per verified match with `*_outcome` and `evidence` populated
    - Assert the results tables are created if absent (the `verify_db.py` readiness pattern) without disturbing existing rows; assert the unreachable-after-3-attempts path returns non-zero exit with no result rows written (bad-host temp `.env`, per the `check_freshness` integration test pattern); skip cleanly when no store is reachable
    - _Requirements: 9.4, 7.4, 7.5_

- [x] 13. Checkpoint - full check + report pipeline complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 14. Add the Rundeck daily job definition
  - Add the Rundeck job definition (YAML/XML under the repo's job-definition location, alongside the existing freshness job) for a once-per-calendar-day run at a fixed configured time, invoking `scripts/check_quality.py` on the Pi; distinct schedule/trigger/execution instance from the Freshness_Check job, and independent of the capture cadence
    - _Requirements: 8.1, 8.2, 8.5_

- [ ] 15. Validate calibrated thresholds against real season data
  - Add a one-off validation script/module (e.g. `scripts/calibrate_quality.py`) that connects read-only to the Pi's `my_postgres` (via `.env`/`DotenvLoader`, over SSH per the pi-access workflow) and runs the `check_quality` pipeline over the accruing season data, then emits a `report_quality`-formatted sample report
    - Confirm the fixed, design-anchored thresholds correctly distinguish good from deficient captures (0-row Present failures, 3-row stubs, and ~69–99-row ~900s-cadence matches expected to fail Coverage/Useful per SP-343) — this **validates** the thresholds and produces a sample report; it does **not** re-derive them to fit the current defective data
    - Capture the sample report output for use in the Confluence note (task 16)
    - _Requirements: 3.7, 5.5, 10.1_

- [ ] 16. Author the Confluence documentation note (Side Projects space)
  - Create the SP-332 Confluence note in the Side Projects space documenting: where the Quality_Report lives (`bf.quality_run`/`bf.quality_match_result`) and how to read it via `scripts/report_quality.py` (text/markdown/csv, from Windows or the Pi) and the underlying `SELECT`s, interpreting each field (10.1); how to interpret a Quality_Alert with at least one operator action, noting Coverage/Useful failures on current data are the expected SP-343 under-sampling signal (10.2); and each of the five thresholds (coverage tolerance, max acceptable gap, null-price threshold, minimum samples per market, lifecycle-span criteria) with its configured value and its location in `QualityThresholds` in `logic/quality_checks.py` (10.3)
  - Embed the sample report captured in task 15
    - _Requirements: 10.1, 10.2, 10.3_

- [ ] 17. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- The pure-logic property tests (2.2, 2.3, 4.2, 5.2, 6.2, 7.2), the pure quality-checks unit tests (7.3), and the Req 9.4 integration test (12.1) are required. The remaining `*` tasks — the Present/selection properties (3.2, 3.3), the CSV formatter property (10.2), the report formatter unit tests (10.3), the `report_quality` smoke test (11.2), and the `check_quality` orchestration unit tests (9.4) — stay optional and can be skipped for a faster MVP.
- Each task references specific granular requirements clauses for traceability.
- Checkpoints (tasks 8, 13, 17) ensure incremental validation.
- Property tests validate the nine universal Correctness Properties from the design; each is its own sub-task annotated with its property number and the requirements it validates, placed next to the implementation it checks.
- Unit tests validate specific calibration-derived examples and edge cases.
- All Coverage/Useful thresholds are anchored to the intended 5s cadence; matches captured at the observed ~900s cadence are expected to fail (the SP-343 defect, detect-only — no polling fix here).
- The Rundeck job (14), calibration validation (15), and Confluence note (16) are top-level tasks without decimal notation and are therefore not included in the parallel-execution dependency graph below.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1"] },
    { "id": 1, "tasks": ["2.2", "2.3", "3.1"] },
    { "id": 2, "tasks": ["3.2", "3.3", "4.1"] },
    { "id": 3, "tasks": ["4.2", "5.1"] },
    { "id": 4, "tasks": ["5.2", "6.1"] },
    { "id": 5, "tasks": ["6.2", "7.1"] },
    { "id": 6, "tasks": ["7.2", "7.3", "9.1", "10.1"] },
    { "id": 7, "tasks": ["9.2", "10.2", "10.3", "11.1"] },
    { "id": 8, "tasks": ["9.3", "11.2"] },
    { "id": 9, "tasks": ["9.4", "12.1"] }
  ]
}
```
