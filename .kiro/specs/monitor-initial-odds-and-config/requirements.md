# Requirements Document

## Introduction

The Monitor Service polls Betfair for runner odds on targets identified by the Target Service. Currently, newly-identified targets never receive their first odds reading because the `update_frequency` timer (set to 4 hours at creation) must elapse before the first fetch occurs. Additionally, all timing thresholds in the monitor are hardcoded, making them difficult to tune without code changes.

This feature ensures targets get an immediate first odds fetch upon transitioning to OPEN status, and moves all timing configuration into the existing `config/strategy.yaml` file.

## Glossary

- **Monitor_Service**: The Python service (`monitor_service.py`) that polls Betfair for runner odds on active targets and writes them to the database.
- **Target_Service**: The Python service (`target_service.py`) that identifies betting markets and writes them as targets to the database with status IDENTIFIED.
- **Strategy_Config**: The YAML configuration file (`config/strategy.yaml`) that defines filtering and timing parameters for the trading system.
- **FromFileStrategy**: The strategy class (`logic/simpleStategy.py`) that loads configuration values from `Strategy_Config` into class-level attributes.
- **Target**: A database record representing a Betfair market being tracked, with columns for status, update_frequency, and last_updated.
- **Update_Frequency_Tiers**: A set of time-based rules that determine how often odds should be polled based on how close the event start time is to the current time.
- **Stale_Cleanup_Threshold**: The duration after an event's start time beyond which unresolved targets are automatically marked as EXPIRED.

## Requirements

### Requirement 1: Immediate Odds Fetch on Target Activation

**User Story:** As a trader, I want newly-identified targets to have their odds fetched immediately when they transition to OPEN status, so that I have market data available from the moment a target becomes active.

#### Acceptance Criteria

1. WHEN a Target transitions from IDENTIFIED to OPEN status, THE Monitor_Service SHALL fetch odds for that Target before evaluating the update_frequency timer.
2. WHEN a Target transitions from IDENTIFIED to OPEN status, THE Monitor_Service SHALL write the fetched odds to the `bf.market_table` database table.
3. WHEN a Target transitions from IDENTIFIED to OPEN status, THE Monitor_Service SHALL set the Target's `last_updated` timestamp to the current time after the initial odds fetch completes.
4. WHEN a Target transitions from IDENTIFIED to OPEN status, THE Monitor_Service SHALL set the Target's `update_frequency` based on the Update_Frequency_Tiers relative to the event start time.
5. IF the Betfair API returns an error during the initial odds fetch, THEN THE Monitor_Service SHALL log the error and continue processing remaining targets.

### Requirement 2: Configurable Update Frequency Tiers

**User Story:** As a trader, I want the odds polling frequency tiers to be configurable in strategy.yaml, so that I can tune polling rates without modifying code.

#### Acceptance Criteria

1. THE Strategy_Config SHALL support an `UPDATE_FREQUENCY_TIERS` section defining time-based polling intervals.
2. WHEN the Strategy_Config contains an `UPDATE_FREQUENCY_TIERS` section, THE FromFileStrategy SHALL load the tier values as class-level attributes.
3. WHEN the Monitor_Service determines the next update frequency for a Target, THE Monitor_Service SHALL use the tier values from the loaded strategy rather than hardcoded values.
4. THE Update_Frequency_Tiers SHALL define intervals for: in-play, less than 3 hours, less than 3 to 6 hours, less than 6 to 12 hours, and greater than 12 hours before event start.
5. IF the `UPDATE_FREQUENCY_TIERS` section is missing from Strategy_Config, THEN THE FromFileStrategy SHALL use default values matching the current hardcoded behaviour (in-play: 5s, <3h: 300s, <6h: 900s, <12h: 3600s, >12h: 14400s).

### Requirement 3: Configurable Initial Update Frequency

**User Story:** As a trader, I want the initial update_frequency assigned to new targets to be configurable, so that I can control how soon the first scheduled poll occurs if the immediate fetch (Requirement 1) is not used.

#### Acceptance Criteria

1. THE Strategy_Config SHALL support an `INITIAL_UPDATE_FREQUENCY` value defining the seconds assigned to newly-created targets.
2. WHEN the Target_Service creates a new Target, THE Target_Service SHALL use the `INITIAL_UPDATE_FREQUENCY` value from the loaded strategy instead of a hardcoded 14400.
3. IF the `INITIAL_UPDATE_FREQUENCY` value is missing from Strategy_Config, THEN THE FromFileStrategy SHALL default to 14400 seconds.

### Requirement 4: Configurable Stale Target Cleanup Threshold

**User Story:** As a trader, I want the stale target cleanup threshold to be configurable, so that I can adjust how long past-start-time targets remain active before being marked as EXPIRED.

#### Acceptance Criteria

1. THE Strategy_Config SHALL support a `STALE_TARGET_HOURS` value defining the number of hours after event start time before a target is marked EXPIRED.
2. WHEN the Monitor_Service performs stale target cleanup, THE Monitor_Service SHALL use the `STALE_TARGET_HOURS` value from the loaded strategy.
3. IF the `STALE_TARGET_HOURS` value is missing from Strategy_Config, THEN THE FromFileStrategy SHALL default to 24 hours.

### Requirement 5: Configurable Monitor Loop Break Threshold

**User Story:** As a trader, I want the monitor loop break threshold to be configurable, so that I can control how far into the future the monitor will wait for the next required update.

#### Acceptance Criteria

1. THE Strategy_Config SHALL support a `MONITOR_MAX_WAIT_SECONDS` value defining the maximum number of seconds in the future the monitor will wait before breaking out of its polling loop.
2. WHEN the nearest required update time exceeds `MONITOR_MAX_WAIT_SECONDS`, THE Monitor_Service SHALL exit its polling loop.
3. IF the `MONITOR_MAX_WAIT_SECONDS` value is missing from Strategy_Config, THEN THE FromFileStrategy SHALL default to 900 seconds.

### Requirement 6: Strategy Config Backwards Compatibility

**User Story:** As a developer, I want the system to remain functional with an existing strategy.yaml that does not contain the new configuration keys, so that the upgrade does not break existing deployments.

#### Acceptance Criteria

1. WHEN the Strategy_Config file does not contain any of the new timing keys (`UPDATE_FREQUENCY_TIERS`, `INITIAL_UPDATE_FREQUENCY`, `STALE_TARGET_HOURS`, `MONITOR_MAX_WAIT_SECONDS`), THE FromFileStrategy SHALL load without errors using default values for all missing keys.
2. WHEN only some of the new timing keys are present in Strategy_Config, THE FromFileStrategy SHALL use the provided values for present keys and defaults for missing keys.
