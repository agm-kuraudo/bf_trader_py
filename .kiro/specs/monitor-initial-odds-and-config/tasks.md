# Implementation Plan: Monitor Initial Odds and Configurable Timing

## Overview

Extend the trading bot's monitor service to immediately fetch odds when targets transition to OPEN status, and move all hardcoded timing values into `config/strategy.yaml` with backwards-compatible defaults. Changes span `logic/simpleStategy.py`, `monitor_service.py`, `target_service.py`, `output/dboutput.py`, and `config/strategy.yaml`.

## Tasks

- [x] 1. Add timing configuration attributes to the strategy layer
  - [x] 1.1 Add new class-level attributes to `DefaultStrategy` in `logic/simpleStategy.py`
    - Add `UPDATE_FREQUENCY_TIERS` dict with keys: `IN_PLAY` (5), `LESS_THAN_3H` (300), `LESS_THAN_6H` (900), `LESS_THAN_12H` (3600), `MORE_THAN_12H` (14400)
    - Add `INITIAL_UPDATE_FREQUENCY = 14400`
    - Add `STALE_TARGET_HOURS = 24`
    - Add `MONITOR_MAX_WAIT_SECONDS = 900`
    - _Requirements: 2.1, 2.5, 3.3, 4.3, 5.3_

  - [x] 1.2 Load new timing keys in `FromFileStrategy.__init__()` with `.get()` fallbacks
    - Use `yaml_content.get('UPDATE_FREQUENCY_TIERS', DefaultStrategy.UPDATE_FREQUENCY_TIERS)` pattern for all four keys
    - Assign to `DefaultStrategy` class-level attributes (matching existing pattern)
    - _Requirements: 2.2, 3.2, 4.2, 5.2, 6.1, 6.2_

  - [x] 1.3 Add new timing keys to `config/strategy.yaml`
    - Add `UPDATE_FREQUENCY_TIERS` section with all tier keys and default values
    - Add `INITIAL_UPDATE_FREQUENCY: 14400`
    - Add `STALE_TARGET_HOURS: 24`
    - Add `MONITOR_MAX_WAIT_SECONDS: 900`
    - _Requirements: 2.1, 3.1, 4.1, 5.1_

- [x] 2. Implement configurable timing in MonitorService
  - [x] 2.1 Replace hardcoded tier values in `update_runner_odds()` with `DefaultStrategy.UPDATE_FREQUENCY_TIERS`
    - Replace the `if/elif` chain with lookups from the tier dict
    - Use `.get()` with hardcoded fallback for each tier key to guard against malformed config
    - _Requirements: 2.3, 2.4_

  - [x] 2.2 Replace hardcoded stale cleanup threshold in `run()` with `DefaultStrategy.STALE_TARGET_HOURS`
    - Change `INTERVAL '24 hours'` to use the configured value via f-string
    - _Requirements: 4.2_

  - [x] 2.3 Replace hardcoded loop break threshold in `run()` with `DefaultStrategy.MONITOR_MAX_WAIT_SECONDS`
    - Change `if nearest_update_seconds > 900` to use the configured value
    - _Requirements: 5.2_

- [x] 3. Implement immediate odds fetch for newly-opened targets
  - [x] 3.1 Add `fetch_odds_for_new_targets(raw_targets, processed_targets)` method to `MonitorService`
    - Identify targets where raw DB status is IDENTIFIED and API status is OPEN
    - Call `update_runner_odds()` for matched targets
    - Wrap per-target fetch in try/except to log errors and continue processing
    - Set `last_updated` and `update_frequency` based on tier config after successful fetch
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 3.2 Call `fetch_odds_for_new_targets()` in the `run()` method after `update_target_status()`
    - Insert the call before the polling loop begins
    - Pass both `raw_targets` and `processed_targets`
    - _Requirements: 1.1_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Update target creation to use configurable initial frequency
  - [x] 5.1 Add `update_frequency` parameter to `db_write_target()` in `output/dboutput.py`
    - Add optional parameter `update_frequency=None` to the method signature
    - Default to `14400` if None is passed (backwards compatibility for any other callers)
    - Use the parameter in the INSERT statement instead of hardcoded 14400
    - _Requirements: 3.2_

  - [x] 5.2 Pass `DefaultStrategy.INITIAL_UPDATE_FREQUENCY` from `target_service.py` to `db_write_target()`
    - Import `DefaultStrategy` (already available via `FromFileStrategy`)
    - Add `update_frequency=DefaultStrategy.INITIAL_UPDATE_FREQUENCY` to the `db_write_target()` call
    - _Requirements: 3.2_

- [x] 6. Write property-based tests
  - [x]* 6.1 Write property test for tier selection correctness
    - **Property 1: Tier selection returns the correct interval for any time offset**
    - Use Hypothesis with `st.integers` for tier values and `st.timedeltas` for time offsets
    - Extract tier selection logic into a testable function or test inline
    - **Validates: Requirements 1.4, 2.3**

  - [x]* 6.2 Write property test for config loading with fallbacks
    - **Property 2: Config loading preserves present values and applies defaults for missing keys**
    - Use Hypothesis `st.fixed_dictionaries` to generate partial config dicts
    - Verify loaded attributes match provided values for present keys, defaults for absent keys
    - **Validates: Requirements 2.2, 2.5, 3.3, 4.3, 5.3, 6.1, 6.2**

  - [x]* 6.3 Write property test for newly-opened target identification
    - **Property 3: Newly-opened target identification selects exactly the correct targets**
    - Use Hypothesis `st.lists` with `st.sampled_from(['IDENTIFIED', 'OPEN'])` for status generation
    - Verify the function selects targets where raw status is IDENTIFIED AND processed status is OPEN
    - **Validates: Requirements 1.1**

- [x] 7. Write unit tests
  - [x]* 7.1 Write unit tests for MonitorService error handling and timing
    - Test: API error during initial fetch continues processing remaining targets
    - Test: Loop breaks when `nearest_update > MONITOR_MAX_WAIT_SECONDS`
    - Test: Stale cleanup SQL uses configured `STALE_TARGET_HOURS`
    - Test: `db_write_target` uses provided `update_frequency`
    - Test: `target_service` passes `INITIAL_UPDATE_FREQUENCY` to `db_write_target`
    - Add tests to `tests/unit_tests_monitor_service.py`
    - _Requirements: 1.5, 4.2, 5.2, 3.2_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design
- Unit tests validate specific examples and edge cases
- The design uses Python throughout — all implementation tasks target Python
- The existing `FromFileStrategy` pattern of setting `DefaultStrategy` class-level attributes is preserved
- Backwards compatibility is maintained: missing config keys use defaults matching current hardcoded values

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1", "2.2", "2.3", "5.1"] },
    { "id": 3, "tasks": ["3.1", "5.2"] },
    { "id": 4, "tasks": ["3.2"] },
    { "id": 5, "tasks": ["6.1", "6.2", "6.3", "7.1"] }
  ]
}
```
