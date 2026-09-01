"""
Property-based tests for SP-328: Season Background Data Capture.

Tests the correctness properties for the pure-logic deploy/verify functions in
``logic/deploy_checks.py`` as defined in the season-background-data-capture
design document.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.deploy_checks import expected_freshness_threshold, freshness, missing_tables, validate_env

# === Property 1: Freshness stall decision is exact and elapsed time is non-negative ===


class TestProperty1Freshness:
    """Feature: season-background-data-capture.

    Property 1: Freshness stall decision is exact and elapsed time is non-negative.
    """

    @given(
        base=st.datetimes(
            min_value=datetime(2000, 1, 1),
            max_value=datetime(2100, 1, 1),
            timezones=st.just(UTC),
        ),
        elapsed_seconds=st.floats(min_value=0, max_value=10_000_000, allow_nan=False, allow_infinity=False),
        threshold_s=st.floats(min_value=0.001, max_value=10_000_000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    # Feature: season-background-data-capture, Property 1: Freshness stall decision is exact and elapsed time is non-negative  # noqa: E501
    def test_freshness_stall_decision_is_exact(self, base, elapsed_seconds, threshold_s):
        """
        For any now, any last_record_ts <= now, and any positive threshold_s,
        freshness returns elapsed_s == (now - last_record_ts).total_seconds(),
        elapsed_s >= 0, and stalled == (elapsed_s > threshold_s).

        **Validates: Requirements 5.2, 5.3, 5.5**
        """
        now = base
        last_record_ts = now - timedelta(seconds=elapsed_seconds)

        result = freshness(now, last_record_ts, threshold_s)

        expected_elapsed = (now - last_record_ts).total_seconds()
        assert result["elapsed_s"] == expected_elapsed
        assert result["elapsed_s"] >= 0
        assert result["stalled"] == (result["elapsed_s"] > threshold_s)

    @given(
        now=st.datetimes(
            min_value=datetime(2000, 1, 1),
            max_value=datetime(2100, 1, 1),
            timezones=st.just(UTC),
        ),
        threshold_s=st.floats(min_value=0.001, max_value=10_000_000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    # Feature: season-background-data-capture, Property 1: Freshness stall decision is exact and elapsed time is non-negative  # noqa: E501
    def test_freshness_none_last_record_is_stalled(self, now, threshold_s):
        """
        When last_record_ts is None (no records exist), the result is always a
        stall with elapsed_s of None.

        **Validates: Requirements 5.2, 5.3, 5.5**
        """
        result = freshness(now, None, threshold_s)

        assert result["elapsed_s"] is None
        assert result["stalled"] is True


# === Property 2: Environment validation returns exactly the missing or empty keys ===


class TestProperty2ValidateEnv:
    """Feature: season-background-data-capture.

    Property 2: Environment validation returns exactly the missing or empty keys.
    """

    @given(
        # Values for keys that will be present with non-empty values.
        present_nonempty=st.dictionaries(
            keys=st.text(min_size=1, max_size=12),
            values=st.text(min_size=1, max_size=12).filter(lambda s: s.strip() != ""),
            max_size=6,
        ),
        # Keys that will be present but with an empty/whitespace-only value.
        empty_keys=st.lists(st.text(min_size=1, max_size=12), max_size=6, unique=True),
        empty_value=st.sampled_from(["", " ", "   ", "\t", "\n", "  \t\n "]),
        # Keys that are required but entirely absent from values.
        absent_keys=st.lists(st.text(min_size=1, max_size=12), max_size=6, unique=True),
        # Extra irrelevant keys present in values but not required.
        extra_keys=st.lists(st.text(min_size=1, max_size=12), max_size=6, unique=True),
    )
    @settings(max_examples=200)
    def test_validate_env_returns_exactly_missing_or_empty(
        self, present_nonempty, empty_keys, empty_value, absent_keys, extra_keys
    ):
        # Feature: season-background-data-capture, Property 2: Environment validation returns exactly the missing or empty keys  # noqa: E501
        """
        validate_env returns exactly the required keys that are absent from
        values or whose value is empty/whitespace-only, and returns [] iff every
        required key is present with a non-empty value.

        **Validates: Requirements 1.3, 7.8**
        """
        present_keys = set(present_nonempty)
        # Disjoint categories so the expected result is unambiguous.
        empty_keys = [k for k in empty_keys if k not in present_keys]
        empty_set = set(empty_keys)
        absent_set = {k for k in absent_keys if k not in present_keys and k not in empty_set}
        extra_set = {k for k in extra_keys if k not in present_keys and k not in empty_set and k not in absent_set}

        values = dict(present_nonempty)
        for k in empty_set:
            values[k] = empty_value
        for k in extra_set:
            values[k] = "irrelevant"

        required = list(present_keys | empty_set | absent_set)

        result = validate_env(values, required)

        expected = empty_set | absent_set
        assert set(result) == expected
        # Result contains only required keys, no duplicates, all within required.
        assert set(result).issubset(set(required))
        assert len(result) == len(set(result))
        # Empty iff all required present and non-empty.
        assert (result == []) == (expected == set())

    # Feature: season-background-data-capture, Property 2: Environment validation returns exactly the missing or empty keys  # noqa: E501
    def test_validate_env_all_present_returns_empty(self):
        """
        All required keys present and non-empty => empty result.

        **Validates: Requirements 1.3, 7.8**
        """
        values = {"DB_HOST": "my_postgres", "DB_PORT": "5432", "DB_NAME": "bf_trader"}
        assert validate_env(values, ["DB_HOST", "DB_PORT", "DB_NAME"]) == []


# === Property 3: Missing-tables is exact set difference ===


class TestProperty3MissingTables:
    """Feature: season-background-data-capture.

    Property 3: Missing-tables is exact set difference.
    """

    @given(
        present=st.sets(st.text(min_size=1, max_size=16), max_size=12),
        required=st.sets(st.text(min_size=1, max_size=16), max_size=12),
    )
    @settings(max_examples=200)
    def test_missing_tables_is_exact_set_difference(self, present, required):
        """
        # Feature: season-background-data-capture, Property 3: Missing-tables is exact set difference

        missing_tables(present, required) == required - present, and is empty
        iff required is a subset of present.

        **Validates: Requirements 1.4, 1.5**
        """
        result = missing_tables(present, required)

        assert result == required - present
        assert (result == set()) == required.issubset(present)
        # Nothing in the result is already present (never re-create existing tables).
        assert result.isdisjoint(present)

    def test_missing_tables_required_capture_tables(self):
        """
        # Feature: season-background-data-capture, Property 3: Missing-tables is exact set difference

        With one required table absent, only that table is reported.

        **Validates: Requirements 1.4, 1.5**
        """
        required = {"bf.target", "bf.market_table", "bf.log_file", "bf.betfair_object_ids"}
        present = {"bf.target", "bf.market_table", "bf.log_file"}
        assert missing_tables(present, required) == {"bf.betfair_object_ids"}


# === Property 1b: Cadence-aware freshness threshold (SP-328 refinement) ===


class TestExpectedFreshnessThreshold:
    """expected_freshness_threshold derives the stall threshold from the tightest
    active cadence + grace, or None when there are no active targets."""

    # Feature: season-background-data-capture, Property 1: Freshness stall decision is exact and elapsed time is non-negative
    @given(
        freqs=st.lists(st.integers(min_value=1, max_value=100000), min_size=1, max_size=10),
        grace=st.integers(min_value=0, max_value=3600),
        default=st.integers(min_value=1, max_value=100000),
    )
    @settings(max_examples=200)
    def test_threshold_is_tightest_cadence_plus_grace(self, freqs, grace, default):
        """With active targets, threshold == min(valid freq) + grace."""
        result = expected_freshness_threshold(freqs, grace_s=grace, default_s=default)
        assert result == min(freqs) + grace

    def test_no_active_targets_returns_none(self):
        # Feature: season-background-data-capture, Property 1: Freshness stall decision is exact and elapsed time is non-negative
        assert expected_freshness_threshold([], grace_s=300, default_s=900) is None

    def test_invalid_frequencies_fall_back_to_default(self):
        # Feature: season-background-data-capture, Property 1: Freshness stall decision is exact and elapsed time is non-negative
        # Non-positive / None values are ignored; if none are valid, use default+grace.
        assert expected_freshness_threshold([0, -5, None], grace_s=300, default_s=900) == 1200

    @given(
        freqs=st.lists(st.integers(min_value=1, max_value=100000), min_size=1, max_size=10),
        grace=st.integers(min_value=0, max_value=3600),
    )
    @settings(max_examples=100)
    def test_threshold_never_below_tightest_cadence(self, freqs, grace):
        """The threshold is always >= the tightest cadence (never alerts before
        an update could even be due)."""
        result = expected_freshness_threshold(freqs, grace_s=grace, default_s=900)
        assert result >= min(freqs)
