# Requirements Document

## Introduction

This feature adds **automated post-event data-quality verification** of captured Betfair odds for completed football matches, tracked under Jira ticket **SP-332** (Side Projects / `bf_trader_py`, label `betfair`).

SP-328 restored capture and added a **freshness** check that answers the *liveness* question — "is data arriving right now?". This feature answers a separate, complementary question: once a match has finished, **is the captured data present, consistent, and useful** for later analysis? These two questions are distinct. The freshness check can be green while the accruing data is quietly worthless — for example, odds rows landing but every price null because the market was suspended, in-play never sampled because the cadence tier never tightened to the `IN_PLAY` 5-second interval, or mid-match gaps from skipped monitor runs. Quality is not liveness, and it needs its own verification.

The captured data is **perishable**: it can only ever be gathered live and never back-filled. Any downstream VPA / ML analysis work is only worthwhile if the perishable data being accrued is actually complete and well-formed. (No follow-up analysis task exists yet; that gap is expected to be planned as part of a future milestone.) This feature produces a per-match, post-event quality signal so the operator knows the data is worth analysing and is alerted when a completed match's captured data is deficient.

The verification runs **post-event** — a quality judgement is only meaningful once a match is `CLOSED`/`EXPIRED` (settled). It is expected to run as a **daily** scheduled Rundeck job that looks back at the previous day's completed matches, distinct from the continuous freshness check that runs every few minutes.

The feature follows the SP-328 conventions already established in `bf_trader_py`: pure-logic quality checks live in `logic/` (property-testable, no I/O), a thin I/O wrapper script lives under `scripts/`, integration tests run against the `my_postgres` Data_Store, and operator visibility reuses the SP-328 **logs-only + durable-record + Rundeck-run-status** alerting pattern (mirroring `scripts/check_freshness.py`). Precise numeric thresholds (how many samples count as "enough", acceptable gap sizes) are intentionally left to the design phase, to be informed by the real season data now accruing.

**Platform target:** Linux on the always-on Raspberry Pi 500 (ARM), the sole capture host, running the `my_postgres` container on `my_trading_network`.

## Glossary

- **Data_Store**: The PostgreSQL instance where captured odds are persisted — the `my_postgres` container (`postgres:16.1`) on `my_trading_network`, database `bf_trader`, schema `bf`.
- **Market_Table**: The `bf.market_table` table holding captured odds, one row per market/runner/timestamp observation. Columns (all stored as `text`): `timestamp`, `market_id`, `runner_id`, `odds`.
- **Target_Table**: The `bf.target` table describing tracked football `MATCH_ODDS` targets. Relevant columns include `target_id`, `market_id`, `runner_ids`, `start_time`, `status`, and `update_frequency`.
- **Target**: A tracked football `MATCH_ODDS` betting market, represented by a row in the Target_Table.
- **Target_Status**: The lifecycle state of a Target in the Target_Table, one of `IDENTIFIED`, `OPEN`, `CLOSED`, or `EXPIRED`.
- **Kicked_Off_Target**: A Target that reached kick-off, defined as a Target whose Target_Status has progressed to `CLOSED` or `EXPIRED` (having previously been `OPEN`).
- **Completed_Match**: The match associated with a Kicked_Off_Target that has settled, identified for verification by its Target row reaching Target_Status `CLOSED` or `EXPIRED` within the look-back window.
- **Odds_Value**: The serialized odds string stored in the `odds` column of the Market_Table, containing `availableToBack` and `availableToLay` price/size ladders for a runner at a point in time.
- **Cadence_Tier**: A named update-frequency tier selected by time-to-event, mapping to a fixed polling interval in seconds. The defined tiers are `IN_PLAY` (5s), `LESS_THAN_3H` (300s), `LESS_THAN_6H` (900s), `LESS_THAN_12H` (3600s), and `MORE_THAN_12H` (14400s).
- **Expected_Sample_Count**: The number of Market_Table rows a Target should have accrued over an interval, derived from the Cadence_Tier interval(s) applicable across that interval's duration.
- **Quality_Check**: The end-to-end post-event verification that evaluates a Completed_Match against the Present, Coverage, Consistency, and Useful quality dimensions and produces a per-match pass/fail result.
- **Quality_Dimension**: One of the four verified aspects of captured data: Present, Coverage, Consistency, or Useful.
- **Quality_Report**: The daily post-event report summarizing Quality_Check results over recently Completed_Matches.
- **Quality_Alert**: An operator-facing alert raised when one or more Completed_Matches fail the Quality_Check, surfaced via the Rundeck run status and a durable record, following the SP-328 freshness alerting pattern.
- **Look_Back_Window**: The bounded, configurable period of recently Completed_Matches a single Quality_Check run evaluates (for example, the previous calendar day).
- **Freshness_Check**: The existing SP-328 continuous liveness check (`scripts/check_freshness.py`) that reports whether odds are landing now. Distinct from, and complementary to, this feature.

## Requirements

### Requirement 1: Identify Completed Matches to Verify

**User Story:** As the operator, I want the verification to select exactly the recently completed matches, so that quality is judged only on matches for which a quality judgement is meaningful.

#### Acceptance Criteria

1. WHEN a Quality_Check run starts, THE System SHALL select for verification every Target whose Target_Status is `CLOSED` or `EXPIRED` and whose `start_time` is greater than or equal to the start of the Look_Back_Window and less than or equal to the Quality_Check run start time.
2. THE System SHALL treat the Look_Back_Window as a configurable duration between 1 hour and 168 hours (7 days), with a default equal to the previous calendar day (00:00:00 to 23:59:59 local time of the day preceding the Quality_Check run start date).
3. WHERE a Target whose `start_time` falls within the Look_Back_Window has a Target_Status of `IDENTIFIED` or `OPEN`, THE System SHALL exclude that Target from the Quality_Check.
4. IF no Target qualifies for verification within the Look_Back_Window, THEN THE System SHALL report a count of zero verified Completed_Matches and SHALL complete the Quality_Check run without raising a Quality_Alert.
5. WHEN the System selects a Completed_Match, THE System SHALL associate that Target with every Market_Table row sharing its `market_id`.
6. IF a selected Target has no Market_Table row sharing its `market_id`, THEN THE System SHALL exclude that Target from evaluation and SHALL record an indication that captured odds were unavailable for that Target, retaining all other selected Completed_Matches for evaluation.

### Requirement 2: Present — Every Kicked-Off Target Has Captured Data

**User Story:** As the operator, I want confirmation that every match that kicked off actually produced captured odds rows, so that matches captured with zero data are surfaced rather than silently lost.

#### Acceptance Criteria

1. WHEN the System evaluates a Kicked_Off_Target for the Present dimension, THE System SHALL count the Market_Table rows whose `market_id` exactly equals the Target's `market_id` and SHALL treat the resulting count as an integer greater than or equal to 0.
2. IF a Kicked_Off_Target has a Market_Table row count equal to 0, THEN THE System SHALL mark that Completed_Match as failing the Present dimension and SHALL record the affected `target_id` and `market_id` in the evaluation result.
3. WHEN a Kicked_Off_Target has a Market_Table row count greater than or equal to 1, THE System SHALL mark that Completed_Match as passing the Present dimension.
4. IF a Kicked_Off_Target has a null or missing `market_id`, THEN THE System SHALL mark that Completed_Match as failing the Present dimension and SHALL record the affected `target_id` with an indication that the `market_id` is absent.
5. IF the Market_Table row count operation for a Kicked_Off_Target cannot be completed due to a data source being unavailable, THEN THE System SHALL mark that Completed_Match as not evaluated for the Present dimension, SHALL record the affected `target_id` and `market_id`, and SHALL preserve any prior evaluation result for that Target without overwriting it.

### Requirement 3: Coverage — Captured Counts Match Expected Cadence

**User Story:** As the operator, I want the captured row counts to roughly match the expected sampling cadence across a match's lifecycle, so that large sampling gaps — especially missing dense in-play data — are detected.

#### Acceptance Criteria

1. WHEN the System processes a Completed_Match, THE System SHALL compute an Expected_Sample_Count by summing, for each Cadence_Tier applicable across the match lifecycle, the count of sampling intervals expected within that tier's active duration, spanning from the pre-match period start through the in-play period end.
2. WHEN the System evaluates the Coverage dimension for a Completed_Match, THE System SHALL compute the actual count of Market_Table rows associated with that Completed_Match and SHALL compute the shortfall as the amount by which the actual count is less than the Expected_Sample_Count.
3. IF the shortfall for a Completed_Match exceeds the coverage tolerance defined in the post-event-data-quality-verification design, THEN THE System SHALL mark that Completed_Match as failing the Coverage dimension and SHALL record the actual count, the Expected_Sample_Count, and the shortfall.
4. IF the shortfall for a Completed_Match is within the coverage tolerance defined in the post-event-data-quality-verification design, THEN THE System SHALL mark that Completed_Match as passing the count-based Coverage check and SHALL record the actual count and the Expected_Sample_Count.
5. WHEN the System detects, within a Completed_Match's captured Market_Table rows, a contiguous time interval containing no captured Market_Table row whose duration exceeds the maximum acceptable gap defined in the post-event-data-quality-verification design for the applicable Cadence_Tier, THE System SHALL mark that Completed_Match as failing the Coverage dimension and SHALL record the start timestamp and end timestamp of the largest such gap.
6. WHEN the System evaluates the Coverage dimension for the in-play period of a Completed_Match, THE System SHALL evaluate the in-play period against the `IN_PLAY` Cadence_Tier interval separately from the pre-match period, and IF the in-play period has no associated Market_Table rows while the pre-match period satisfies its count-based Coverage check, THEN THE System SHALL mark that Completed_Match as failing the Coverage dimension.
7. THE post-event-data-quality-verification design SHALL define the coverage tolerance and the maximum acceptable gap per Cadence_Tier, informed by real captured season data, such that two independent testers evaluating the same Completed_Match against the same design reach identical Coverage pass/fail results.

### Requirement 4: Consistency — Captured Odds Are Well-Formed

**User Story:** As the operator, I want the captured odds values to be well-formed and internally consistent, so that malformed, null, or duplicated data is detected before it reaches analysis.

#### Acceptance Criteria

1. WHEN the System evaluates the Consistency dimension for a Completed_Match, THE System SHALL confirm that each associated Odds_Value is parseable into its `availableToBack` and `availableToLay` price ladders.
2. IF an associated Odds_Value cannot be parsed into its `availableToBack` and `availableToLay` price ladders, THEN THE System SHALL mark the Completed_Match as failing the Consistency dimension and SHALL record the count of unparseable Odds_Values.
3. IF the proportion of associated Market_Table rows whose Odds_Value has neither an `availableToBack` price nor an `availableToLay` price exceeds the null-price threshold defined in the post-event-data-quality-verification design, THEN THE System SHALL mark the Completed_Match as failing the Consistency dimension and SHALL record the count of rows lacking both a back and a lay price.
4. WHEN the System evaluates the Consistency dimension, THE System SHALL confirm that the number of distinct `runner_id` values across the Completed_Match's associated Market_Table rows equals the number of runner identifiers listed in the Target's Target_Table `runner_ids` column, and SHALL mark the Completed_Match as failing the Consistency dimension when the two counts differ.
5. WHEN the System evaluates the Consistency dimension, THE System SHALL confirm that, for each `runner_id` of the Completed_Match, the associated Market_Table `timestamp` values are in non-decreasing order in row-storage order, and SHALL mark the Completed_Match as failing the Consistency dimension when a later-stored row carries an earlier `timestamp` than any row stored before it for that `runner_id`.
6. IF two or more associated Market_Table rows share the same `market_id`, `runner_id`, and `timestamp`, THEN THE System SHALL mark the Completed_Match as failing the Consistency dimension and SHALL record the count of duplicate rows.
7. IF the Target's Target_Table `runner_ids` column is absent or cannot be parsed into a list of runner identifiers, THEN THE System SHALL mark the Completed_Match as failing the Consistency dimension and SHALL record that the declared runner count is unavailable for the affected `target_id` and `market_id`.

### Requirement 5: Useful — Sufficient Resolution for Analysis

**User Story:** As the operator, I want each completed match's data to have enough resolution and lifecycle span to support downstream analysis, so that thin or partial captures are flagged as not analysis-ready.

#### Acceptance Criteria

1. IF the count of associated Market_Table rows for a Completed_Match is below the minimum-samples-per-market threshold defined in the post-event-data-quality-verification design, THEN THE System SHALL mark the Completed_Match as failing the Useful dimension and SHALL record the actual row count, the required minimum threshold, and a failure reason indicating insufficient resolution.
2. WHEN the System evaluates the Useful dimension for a Completed_Match, THE System SHALL determine whether the associated Market_Table rows include at least one row timestamped within the defined pre-match window before market start and at least one row timestamped at or after settlement, and SHALL record the earliest and latest row timestamps used in this determination.
3. IF a Completed_Match's associated Market_Table rows are missing the pre-match-window portion, the settlement portion, or both, THEN THE System SHALL mark the Completed_Match as failing the Useful dimension and SHALL record which portion of the lifecycle is absent.
4. WHEN the System completes the Useful dimension evaluation for a Completed_Match, THE System SHALL record a single boolean Useful result of pass or fail, where pass requires both the minimum-samples-per-market threshold to be met and the required lifecycle span from pre-match window through settlement to be present.
5. THE post-event-data-quality-verification design SHALL define the minimum-samples-per-market threshold as a fixed non-negative integer, the pre-match window as a fixed duration before market start, and the settlement boundary, informed by real captured season data, such that two independent testers evaluating the same Completed_Match against the same design values reach the identical pass/fail Useful result.

### Requirement 6: Per-Match Quality Result Aggregation

**User Story:** As the operator, I want each completed match rolled up into a single clear quality result, so that I can tell at a glance which matches passed and which failed and why.

#### Acceptance Criteria

1. WHEN the System has evaluated all four Quality_Dimensions (Present, Coverage, Consistency, Useful) for a Completed_Match, THE System SHALL produce exactly one per-match result recording a discrete pass or fail outcome for each of the four dimensions.
2. IF one or more of the four Quality_Dimensions for a Completed_Match has a fail outcome, THEN THE System SHALL set the Completed_Match's overall Quality_Check outcome to failed.
3. WHEN all four Quality_Dimensions for a Completed_Match have a pass outcome, THE System SHALL set the Completed_Match's overall Quality_Check outcome to passed.
4. WHERE one or more of the four Quality_Dimensions for a Completed_Match has a fail outcome, THE per-match result SHALL include, for each failed dimension, a failure reason and the recorded evidence used to determine that outcome.
5. IF the System cannot evaluate one or more of the four Quality_Dimensions for a Completed_Match, THEN THE System SHALL set the overall Quality_Check outcome to failed and record, for each unevaluated dimension, an indication that the dimension could not be evaluated.

### Requirement 7: Daily Post-Event Reporting and Alerting

**User Story:** As the operator, I want a daily post-event report over recently completed matches with an alert when any match's data is deficient, so that I retain SP-328-style logs-only visibility and am notified of quality problems without inspecting the database by hand.

#### Acceptance Criteria

1. WHEN a Quality_Check run completes, THE System SHALL produce a Quality_Report that lists every verified Completed_Match by `target_id` and `market_id`, its overall Quality_Check outcome as one of PASS or FAIL, and, for each Quality_Dimension evaluated, a per-dimension outcome of PASS or FAIL.
2. IF one or more verified Completed_Matches fail the Quality_Check, THEN THE System SHALL raise a Quality_Alert that identifies each failed Completed_Match by `target_id` and `market_id` and names each failed Quality_Dimension.
3. WHEN a Quality_Check run raises a Quality_Alert, THE System SHALL exit with a non-zero status within 5 seconds of the Quality_Report being written, so that the Rundeck run status surfaces the failure, following the SP-328 freshness alerting pattern.
4. WHEN a Quality_Check run completes, THE System SHALL write the Quality_Report to a durable record locatable by the operator from a single documented location and retained for at least 30 calendar days from the time it is written.
5. IF the System cannot query the Data_Store during a Quality_Check run after 3 connection attempts, THEN THE System SHALL raise a Quality_Alert indicating the Data_Store is unreachable, SHALL produce no Quality_Report for that run, and SHALL exit with a non-zero status.
6. WHEN all verified Completed_Matches pass the Quality_Check, THE System SHALL record a passing Quality_Report, SHALL not raise a Quality_Alert, and SHALL exit with a zero status.
7. IF a Quality_Check run finds zero verified Completed_Matches to evaluate, THEN THE System SHALL record a Quality_Report indicating no matches were evaluated, SHALL raise a Quality_Alert indicating no matches were found, and SHALL exit with a non-zero status.

### Requirement 8: Scheduling as a Distinct Daily Post-Event Job

**User Story:** As the operator, I want the quality verification scheduled as its own daily post-event job, so that it runs after matches settle and remains separate from the continuous freshness check.

#### Acceptance Criteria

1. THE post-event-data-quality-verification design SHALL define the Quality_Check as a Rundeck job scheduled to run once per calendar day at a fixed configured time-of-day, distinct from and not sharing a schedule, trigger, or execution instance with the recurring Freshness_Check job.
2. WHEN the scheduled daily trigger fires, THE System SHALL run the Quality_Check over all Completed_Matches whose settlement time falls within the Look_Back_Window without requiring operator intervention.
3. IF a scheduled Quality_Check trigger fires while a previous Quality_Check run has not completed, THEN THE System SHALL skip the new run and record an indication that the run was skipped due to an in-progress execution, without terminating the in-progress run.
4. IF the scheduled Quality_Check run fails to start or does not complete within a configured maximum run duration of 3600 seconds, THEN THE System SHALL record an error indication identifying the failed or timed-out run and SHALL preserve any results produced by prior successful runs.
5. THE post-event-data-quality-verification design SHALL document that the Quality_Check is meaningful only for matches in the settled state and SHALL define the Quality_Check schedule independently of the continuous capture cadence such that no configuration value of the capture cadence alters the Quality_Check schedule.

### Requirement 9: Implementation Convention Alignment (SP-328 Patterns)

**User Story:** As the operator, I want the quality verification built to the same conventions as SP-328, so that the pure quality logic is property-testable and the feature fits the existing codebase.

#### Acceptance Criteria

1. THE post-event-data-quality-verification design SHALL place the quality-evaluation decision logic in the `logic/` package as pure functions that perform no input/output operations (no file access, no network calls, no Data_Store queries), such that each function returns identical outputs for identical inputs.
2. THE post-event-data-quality-verification design SHALL place the Data_Store queries and orchestration in a wrapper script under `scripts/` that contains no quality-evaluation decision logic, delegating all such decisions to the `logic/` package functions.
3. THE post-event-data-quality-verification design SHALL specify that the wrapper script obtains its Data_Store connection using the `.env`/`DotenvLoader` approach used by `scripts/check_freshness.py` and `scripts/verify_db.py`, with no connection parameters hard-coded in the script.
4. THE post-event-data-quality-verification design SHALL specify at least one integration test that executes the wrapper script against the `my_postgres` Data_Store and asserts a pass/fail quality result is produced.
5. IF the Odds_Value parser used by the Consistency dimension is given an input that cannot be parsed as a valid Odds_Value, THEN THE parser SHALL return a parse-failure indication rather than a parsed value, and SHALL leave no partially parsed result.
6. THE post-event-data-quality-verification design SHALL specify a round-trip property test for the Odds_Value parser such that, for any valid Odds_Value, parsing the value and then re-serializing the parsed result yields a value equal to the original input.

### Requirement 10: Documentation

**User Story:** As the operator, I want a short Confluence note describing the post-event quality check and how to act on its alerts, so that the workflow documentation requirement is met and the SP-332 definition of done is satisfied.

#### Acceptance Criteria

1. WHEN the operator creates the Confluence note in the Side Projects space, THE Confluence note SHALL contain a section describing where the Quality_Report is located, how to open it, and how to interpret each field it contains.
2. WHEN the operator creates the Confluence note in the Side Projects space, THE Confluence note SHALL contain a section describing how to interpret a Quality_Alert, including at least one documented operator action to take in response to an alert.
3. THE Confluence note SHALL document each of the five configured thresholds (coverage tolerance, maximum acceptable gap, null-price threshold, minimum samples per market, and lifecycle-span criteria), stating for each threshold both its configured value and the location where it is configured.
4. WHEN the operator publishes the Confluence note, THE operator SHALL add a link to the published note in the SP-332 ticket before the SP-332 ticket is transitioned to Done.
5. IF the SP-332 ticket is transitioned to Done while no link to the published Confluence note is present on the ticket, THEN THE workflow SHALL reject the transition and retain the ticket in its pre-transition status, with an indication that the documentation link is missing.

## Open Questions

These are recorded deliberately and are to be resolved during design, ideally informed by the real season data now accruing. They are not decided here.

1. **Coverage tolerance and maximum acceptable gap** — How far below the Expected_Sample_Count is acceptable, and the largest no-row interval tolerated per Cadence_Tier (especially the `IN_PLAY` 5s tier). To be calibrated against captured season data.
2. **Null-price threshold** — What proportion of rows lacking both a back and a lay price (for example, from a suspended market) constitutes a Consistency failure versus normal suspension noise.
3. **Minimum samples per market and lifecycle-span criteria** — The minimum row count and the definition of "spanning pre-match through settlement" that make a Completed_Match analysis-ready for the Useful dimension.
4. **Look-back window default** — Confirm the previous-calendar-day default and how it aligns with the daily Rundeck trigger time relative to match settlement.
5. **Runner count source** — Whether the expected runner count for the Consistency dimension is derived from the Target's `runner_ids` column, and how to handle Targets where that value is absent or malformed.
6. **Durable-record form** — The concrete form of the durable Quality_Report record (log file, state file, or a Data_Store table), aligning with the SP-328 logs-only visibility decision.

## Dependencies

- **SP-328 (Stand up background data capture for the new season)** — COMPLETE. Provides the capture flow, the `bf.market_table`/`bf.target` data model, the Cadence_Tier definitions, the continuous Freshness_Check (`scripts/check_freshness.py`), and the logs-only + durable-record + Rundeck-run-status alerting pattern this feature reuses.
- **Downstream VPA / ML analysis (future, not yet ticketed)** — The eventual consumer of the captured data. This feature's Useful dimension defines what "analysis-ready" means; no analysis follow-up task exists yet, and creating one is a known planning gap to be addressed in a future milestone.
