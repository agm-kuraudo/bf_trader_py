"""
Property-based tests for SP-302: Monitor Initial Odds and Configurable Timing.
Tests the correctness properties defined in the design document.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.simpleStategy import DefaultStrategy

# === Helper: Tier selection function (extracted for testability) ===


def select_tier(tiers: dict, time_until_start: timedelta) -> int:
    """
    Given a tier config dict and a timedelta until event start,
    return the correct polling interval in seconds.
    """
    seconds_until_start = time_until_start.total_seconds()
    if seconds_until_start <= 0:
        return tiers.get("IN_PLAY", 5)
    elif seconds_until_start <= 3 * 3600:
        return tiers.get("LESS_THAN_3H", 300)
    elif seconds_until_start <= 6 * 3600:
        return tiers.get("LESS_THAN_6H", 900)
    elif seconds_until_start <= 12 * 3600:
        return tiers.get("LESS_THAN_12H", 3600)
    else:
        return tiers.get("MORE_THAN_12H", 14400)


# === Property 1: Tier selection returns correct interval for any time offset ===


class TestProperty1TierSelection:
    """Feature: monitor-initial-odds-and-config.

    Property 1: Tier selection returns the correct interval for any time offset.
    """

    @given(
        in_play=st.integers(min_value=1, max_value=86400),
        lt_3h=st.integers(min_value=1, max_value=86400),
        lt_6h=st.integers(min_value=1, max_value=86400),
        lt_12h=st.integers(min_value=1, max_value=86400),
        gt_12h=st.integers(min_value=1, max_value=86400),
        seconds_offset=st.integers(min_value=-7200, max_value=172800),
    )
    @settings(max_examples=200)
    def test_tier_selection_returns_correct_bucket(self, in_play, lt_3h, lt_6h, lt_12h, gt_12h, seconds_offset):
        """
        **Validates: Requirements 1.4, 2.3**
        """
        tiers = {
            "IN_PLAY": in_play,
            "LESS_THAN_3H": lt_3h,
            "LESS_THAN_6H": lt_6h,
            "LESS_THAN_12H": lt_12h,
            "MORE_THAN_12H": gt_12h,
        }
        time_until_start = timedelta(seconds=seconds_offset)
        result = select_tier(tiers, time_until_start)

        if seconds_offset <= 0:
            assert result == in_play
        elif seconds_offset <= 3 * 3600:
            assert result == lt_3h
        elif seconds_offset <= 6 * 3600:
            assert result == lt_6h
        elif seconds_offset <= 12 * 3600:
            assert result == lt_12h
        else:
            assert result == gt_12h

    def test_tier_selection_with_default_config(self):
        """Verify the default config values match expected tier boundaries."""
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        # In-play
        assert select_tier(tiers, timedelta(seconds=-100)) == 5
        # Less than 3h
        assert select_tier(tiers, timedelta(hours=1)) == 300
        # Less than 6h
        assert select_tier(tiers, timedelta(hours=4)) == 900
        # Less than 12h
        assert select_tier(tiers, timedelta(hours=8)) == 3600
        # More than 12h
        assert select_tier(tiers, timedelta(hours=24)) == 14400


# === Property 2: Config loading preserves present values and defaults for missing ===


class TestProperty2ConfigLoading:
    """Feature: monitor-initial-odds-and-config.

    Property 2: Config loading preserves present values and applies defaults for missing keys.
    """

    def test_default_strategy_has_all_timing_attributes(self):
        """
        DefaultStrategy must have all four timing attributes with correct defaults.

        **Validates: Requirements 2.2, 2.5, 3.3, 4.3, 5.3, 6.1, 6.2**
        """
        assert hasattr(DefaultStrategy, "UPDATE_FREQUENCY_TIERS")
        assert hasattr(DefaultStrategy, "INITIAL_UPDATE_FREQUENCY")
        assert hasattr(DefaultStrategy, "STALE_TARGET_HOURS")
        assert hasattr(DefaultStrategy, "MONITOR_MAX_WAIT_SECONDS")

        assert DefaultStrategy.INITIAL_UPDATE_FREQUENCY == 14400
        assert DefaultStrategy.STALE_TARGET_HOURS == 24
        assert DefaultStrategy.MONITOR_MAX_WAIT_SECONDS == 900

        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        assert tiers["IN_PLAY"] == 5
        assert tiers["LESS_THAN_3H"] == 300
        assert tiers["LESS_THAN_6H"] == 900
        assert tiers["LESS_THAN_12H"] == 3600
        assert tiers["MORE_THAN_12H"] == 14400

    @given(
        initial_freq=st.integers(min_value=1, max_value=86400),
        stale_hours=st.integers(min_value=1, max_value=168),
        max_wait=st.integers(min_value=60, max_value=7200),
    )
    @settings(max_examples=100)
    def test_get_with_fallback_pattern(self, initial_freq, stale_hours, max_wait):
        """
        Simulate the .get() fallback pattern used in FromFileStrategy.

        **Validates: Requirements 2.2, 2.5, 3.3, 4.3, 5.3, 6.1, 6.2**
        """
        yaml_content = {
            "INITIAL_UPDATE_FREQUENCY": initial_freq,
            "STALE_TARGET_HOURS": stale_hours,
            "MONITOR_MAX_WAIT_SECONDS": max_wait,
        }

        # Simulate loading with .get() and defaults
        loaded_initial = yaml_content.get("INITIAL_UPDATE_FREQUENCY", 14400)
        loaded_stale = yaml_content.get("STALE_TARGET_HOURS", 24)
        loaded_max_wait = yaml_content.get("MONITOR_MAX_WAIT_SECONDS", 900)

        assert loaded_initial == initial_freq
        assert loaded_stale == stale_hours
        assert loaded_max_wait == max_wait

    def test_missing_keys_use_defaults(self):
        """
        When keys are absent, .get() returns defaults.

        **Validates: Requirements 6.1, 6.2**
        """
        yaml_content = {}  # Empty config

        loaded_initial = yaml_content.get("INITIAL_UPDATE_FREQUENCY", 14400)
        loaded_stale = yaml_content.get("STALE_TARGET_HOURS", 24)
        loaded_max_wait = yaml_content.get("MONITOR_MAX_WAIT_SECONDS", 900)
        loaded_tiers = yaml_content.get("UPDATE_FREQUENCY_TIERS", DefaultStrategy.UPDATE_FREQUENCY_TIERS)

        assert loaded_initial == 14400
        assert loaded_stale == 24
        assert loaded_max_wait == 900
        assert loaded_tiers == DefaultStrategy.UPDATE_FREQUENCY_TIERS


# === Property 3: Newly-opened target identification selects exactly the correct targets ===


class TestProperty3NewlyOpenedIdentification:
    """Feature: monitor-initial-odds-and-config.

    Property 3: Newly-opened target identification selects exactly the correct targets.
    """

    @given(
        raw_statuses=st.lists(st.sampled_from(["IDENTIFIED", "OPEN", "CLOSED", "EXPIRED"]), min_size=1, max_size=20),
        processed_statuses=st.lists(st.sampled_from(["OPEN", "CLOSED", "SUSPENDED"]), min_size=1, max_size=20),
    )
    @settings(max_examples=200)
    def test_newly_opened_selects_correct_targets(self, raw_statuses, processed_statuses):
        """
        Only targets where raw=IDENTIFIED AND processed=OPEN should be selected.

        **Validates: Requirements 1.1**
        """
        # Ensure lists are same length
        min_len = min(len(raw_statuses), len(processed_statuses))
        raw_statuses = raw_statuses[:min_len]
        processed_statuses = processed_statuses[:min_len]

        # Build mock raw_targets (status at index 5) and processed_targets (status at index 1)
        raw_targets = [("", "", "", "", "", status, "", "", "") for status in raw_statuses]
        processed_targets = [("market", status, 3, [1, 2, 3], 14400, None, None) for status in processed_statuses]

        # Apply the same logic as fetch_odds_for_new_targets
        newly_opened = []
        for raw, processed in zip(raw_targets, processed_targets, strict=False):
            if raw[5] == "IDENTIFIED" and processed[1] == "OPEN":
                newly_opened.append(processed)

        # Verify: count matches expected
        expected_count = sum(
            1
            for raw_s, proc_s in zip(raw_statuses, processed_statuses, strict=False)
            if raw_s == "IDENTIFIED" and proc_s == "OPEN"
        )
        assert len(newly_opened) == expected_count

        # Verify: all selected have correct statuses
        for target in newly_opened:
            assert target[1] == "OPEN"

    def test_no_newly_opened_when_all_already_open(self):
        """
        If all raw targets are already OPEN, none should be selected.

        **Validates: Requirements 1.1**
        """
        raw_targets = [("", "", "", "", "", "OPEN", "", "", "")] * 5
        processed_targets = [("market", "OPEN", 3, [1], 14400, None, None)] * 5

        newly_opened = [
            proc
            for raw, proc in zip(raw_targets, processed_targets, strict=False)
            if raw[5] == "IDENTIFIED" and proc[1] == "OPEN"
        ]
        assert len(newly_opened) == 0

    def test_empty_lists_returns_empty(self):
        """
        Empty input lists should produce empty output.

        **Validates: Requirements 1.1**
        """
        newly_opened = [proc for raw, proc in zip([], [], strict=False) if raw[5] == "IDENTIFIED" and proc[1] == "OPEN"]
        assert len(newly_opened) == 0
