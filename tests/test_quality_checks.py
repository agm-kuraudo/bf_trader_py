"""Example-based unit tests for SP-332 pure quality-checks logic.

These anchor the concrete, calibration-derived scenarios from the
post-event-data-quality-verification design ("Testing Strategy -- Unit tests for
pure logic" and "Threshold Calibration"). Broad input coverage is delegated to
the Hypothesis property tests in ``tests/test_property_quality_checks.py`` --
this file enumerates the specific, readable cases.

Every threshold is read from the single documented source
(``logic.quality_checks.QualityThresholds()``) rather than hard-coded, so a
revision of the calibrated values updates these tests through one edit.
"""

import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.quality_checks import (
    QualityThresholds,
    aggregate_match,
    consistency_result,
    coverage_result,
    expected_sample_count,
    present_result,
    useful_result,
)

# The single documented source of the calibrated thresholds. Nothing below
# hard-codes a threshold number; every scenario is built relative to THRESHOLDS.
THRESHOLDS = QualityThresholds()

# Cadence_Tier interval map used to compute a synthetic Expected_Sample_Count
# from the same intended-cadence basis the production code uses.
_TIER_INTERVALS_S = {
    "IN_PLAY": 5,
    "LESS_THAN_3H": 300,
    "LESS_THAN_6H": 900,
    "LESS_THAN_12H": 3600,
    "MORE_THAN_12H": 14400,
}

# A fixed kick-off anchor for building lifecycle timestamps.
START = datetime(2026, 1, 15, 15, 0, 0, tzinfo=UTC)


def _valid_odds(price: float = 1.66) -> str:
    """A well-formed stored Odds_Value (Python dict repr) with a back price."""
    return str(
        {
            "availableToBack": [{"price": price, "size": 40.7}],
            "availableToLay": [{"price": price + 0.02, "size": 12.0}],
            "tradedVolume": [],
        }
    )


def _timestamps(start: datetime, count: int, interval_s: float) -> list[datetime]:
    """``count`` timestamps beginning at ``start`` spaced ``interval_s`` apart."""
    return [start + timedelta(seconds=i * interval_s) for i in range(count)]


# --- Present dimension (Req 2.2, 2.3, 2.4) ------------------------------------


def test_present_zero_rows_fails():
    """A settled target with a market_id but no rows fails Present (Req 2.2)."""
    result = present_result(market_id="1.261452692", row_count=0)
    assert result["passed"] is False
    assert result["evidence"]["row_count"] == 0


def test_present_null_market_id_fails():
    """A target whose market_id is absent fails Present (Req 2.4)."""
    result = present_result(market_id=None, row_count=5)
    assert result["passed"] is False
    assert result["evidence"]["market_id_absent"] is True


def test_present_one_row_passes():
    """A target with a market_id and at least one row passes Present (Req 2.3)."""
    result = present_result(market_id="1.261452692", row_count=1)
    assert result["passed"] is True
    assert result["reason"] is None


# --- Coverage dimension (Req 3.2, 3.3, 3.4, 3.5, 3.6) -------------------------


def _prematch_start() -> datetime:
    """The earliest evaluated pre-match point (edge of the pre-match window)."""
    return START - timedelta(seconds=THRESHOLDS.prematch_window_s)


def test_coverage_synthetic_5s_cadence_match_passes():
    """A match sampled at the intended 5s in-play cadence passes Coverage.

    Rows fill the whole pre-match window (every 300s) and the intended in-play
    span at the intended 5s cadence, so the actual count meets the 5s-anchored
    Expected_Sample_Count and no gap exceeds its tier limit (Req 3.2-3.6).
    """
    prematch_start = _prematch_start()
    inplay_end = START + timedelta(seconds=THRESHOLDS.inplay_duration_s)

    # Pre-match rows every 300s across the 3h window (dense enough that the
    # largest pre-match gap stays under the tightest pre-match tier limit).
    prematch_rows = _timestamps(prematch_start, count=THRESHOLDS.prematch_window_s // 300 + 1, interval_s=300)
    # In-play rows at the intended 5s cadence across the intended in-play span.
    inplay_rows = _timestamps(
        START,
        count=THRESHOLDS.inplay_duration_s // THRESHOLDS.inplay_interval_s + 1,
        interval_s=THRESHOLDS.inplay_interval_s,
    )
    row_timestamps = prematch_rows + inplay_rows

    expected = expected_sample_count(
        start_time=START,
        prematch_start=prematch_start,
        inplay_end=inplay_end,
        tier_intervals=_TIER_INTERVALS_S,
        inplay_interval_s=THRESHOLDS.inplay_interval_s,
    )

    result = coverage_result(
        actual_count=len(row_timestamps),
        expected_count=expected,
        row_timestamps=row_timestamps,
        start_time=START,
        thresholds=THRESHOLDS,
    )
    assert result["passed"] is True, result["reasons"]


def test_coverage_96_row_900s_cadence_fails_on_shortfall():
    """The SP-343 case: ~96 rows at the observed ~900s cadence fails shortfall.

    Expected_Sample_Count is anchored to the intended 5s cadence, so a match
    captured at ~900s falls far below the tolerated floor and correctly fails
    Coverage (Req 3.2-3.4).
    """
    prematch_start = _prematch_start()
    inplay_end = START + timedelta(seconds=THRESHOLDS.inplay_duration_s)
    expected = expected_sample_count(
        start_time=START,
        prematch_start=prematch_start,
        inplay_end=inplay_end,
        tier_intervals=_TIER_INTERVALS_S,
        inplay_interval_s=THRESHOLDS.inplay_interval_s,
    )

    # ~96 rows: a handful pre-match plus in-play sampled at the observed ~900s
    # cadence (the defect). Far below the 5s-anchored expectation.
    prematch_rows = _timestamps(START - timedelta(hours=2), count=8, interval_s=900)
    inplay_rows = _timestamps(START, count=88, interval_s=900)
    row_timestamps = prematch_rows + inplay_rows
    assert 90 <= len(row_timestamps) <= 100

    result = coverage_result(
        actual_count=len(row_timestamps),
        expected_count=expected,
        row_timestamps=row_timestamps,
        start_time=START,
        thresholds=THRESHOLDS,
    )
    assert result["passed"] is False
    assert result["shortfall"] > 0


def test_coverage_three_row_stub_fails_on_shortfall():
    """A 3-row stub capture fails Coverage on shortfall (Req 3.2-3.4)."""
    prematch_start = _prematch_start()
    inplay_end = START + timedelta(seconds=THRESHOLDS.inplay_duration_s)
    expected = expected_sample_count(
        start_time=START,
        prematch_start=prematch_start,
        inplay_end=inplay_end,
        tier_intervals=_TIER_INTERVALS_S,
        inplay_interval_s=THRESHOLDS.inplay_interval_s,
    )
    row_timestamps = _timestamps(START, count=3, interval_s=5)

    result = coverage_result(
        actual_count=len(row_timestamps),
        expected_count=expected,
        row_timestamps=row_timestamps,
        start_time=START,
        thresholds=THRESHOLDS,
    )
    assert result["passed"] is False
    assert result["shortfall"] > 0


def test_coverage_inplay_empty_special_case_fails():
    """1.243049256-style: 63 pre-match rows, 0 in-play fails the in-play-empty
    special case even though the pre-match count check could pass (Req 3.6)."""
    # 63 pre-match rows densely packed just before kick-off so the count check
    # passes, but nothing at or after START -- the in-play period is empty.
    prematch_rows = _timestamps(START - timedelta(seconds=63 * 5), count=63, interval_s=5)
    # A deliberately tiny expected count so the count check passes and the ONLY
    # reason to fail is the empty in-play period.
    result = coverage_result(
        actual_count=len(prematch_rows),
        expected_count=1,
        row_timestamps=prematch_rows,
        start_time=START,
        thresholds=THRESHOLDS,
    )
    assert result["inplay_empty"] is True
    assert result["passed"] is False


def test_coverage_benign_overnight_gap_outside_prematch_window_does_not_fail():
    """A ~98,000s overnight gap wholly outside the pre-match window is benign
    and must not fail Coverage (Threshold Calibration, Req 3.5)."""
    # A cluster of rows the night before, ~98,000s (~27h) before START, then a
    # dense healthy capture through the pre-match window and in-play span.
    overnight = [START - timedelta(seconds=98_000)]
    prematch_rows = _timestamps(_prematch_start(), count=THRESHOLDS.prematch_window_s // 300 + 1, interval_s=300)
    inplay_rows = _timestamps(
        START,
        count=THRESHOLDS.inplay_duration_s // THRESHOLDS.inplay_interval_s + 1,
        interval_s=THRESHOLDS.inplay_interval_s,
    )
    row_timestamps = overnight + prematch_rows + inplay_rows

    inplay_end = START + timedelta(seconds=THRESHOLDS.inplay_duration_s)
    expected = expected_sample_count(
        start_time=START,
        prematch_start=_prematch_start(),
        inplay_end=inplay_end,
        tier_intervals=_TIER_INTERVALS_S,
        inplay_interval_s=THRESHOLDS.inplay_interval_s,
    )

    result = coverage_result(
        actual_count=len(row_timestamps),
        expected_count=expected,
        row_timestamps=row_timestamps,
        start_time=START,
        thresholds=THRESHOLDS,
    )
    # The overnight gap is excluded, so Coverage passes on this healthy capture.
    assert result["passed"] is True, result["reasons"]


# --- Consistency dimension (Req 4.1, 4.3, 4.4, 4.5, 4.6, 4.7) -----------------

# A well-formed 3-runner match: two rows per runner in storage order, all with
# parseable odds carrying a price, unique dedup keys, non-decreasing timestamps.
_RUNNERS = ["56764", "56343", "58805"]
_TS_A = "2026-01-15T14:00:00+00:00"
_TS_B = "2026-01-15T14:05:00+00:00"


def _wellformed_consistency_inputs():
    """Return the six-argument tuple for a fully consistent match."""
    odds_values = [_valid_odds() for _ in range(len(_RUNNERS) * 2)]
    runner_ids_in_rows = _RUNNERS + _RUNNERS
    declared_runner_ids = list(_RUNNERS)
    rows_in_storage_order = [(r, _TS_A) for r in _RUNNERS] + [(r, _TS_B) for r in _RUNNERS]
    dedup_keys = [("1.99", r, _TS_A) for r in _RUNNERS] + [("1.99", r, _TS_B) for r in _RUNNERS]
    return (
        odds_values,
        runner_ids_in_rows,
        declared_runner_ids,
        rows_in_storage_order,
        dedup_keys,
    )


def test_consistency_wellformed_match_passes():
    """A well-formed match passes every Consistency sub-check (Req 4.1-4.7)."""
    inputs = _wellformed_consistency_inputs()
    result = consistency_result(*inputs, THRESHOLDS.null_price_ratio)
    assert result["passed"] is True, result["reasons"]


def test_consistency_unparseable_odds_fails():
    """An injected unparseable Odds_Value fails the parse sub-check (Req 4.1)."""
    odds, runners, declared, storage, dedup = _wellformed_consistency_inputs()
    odds[0] = "not-a-valid-dict-repr"
    result = consistency_result(odds, runners, declared, storage, dedup, THRESHOLDS.null_price_ratio)
    assert result["passed"] is False
    assert result["parse"]["passed"] is False


def test_consistency_duplicate_key_fails():
    """A duplicate (market_id, runner_id, timestamp) fails dedup (Req 4.6)."""
    odds, runners, declared, storage, dedup = _wellformed_consistency_inputs()
    dedup[1] = dedup[0]  # force an exact duplicate key
    result = consistency_result(odds, runners, declared, storage, dedup, THRESHOLDS.null_price_ratio)
    assert result["passed"] is False
    assert result["duplicates"]["passed"] is False


def test_consistency_out_of_order_timestamp_fails():
    """A later-stored row with an earlier timestamp fails ordering (Req 4.5)."""
    odds, runners, declared, storage, dedup = _wellformed_consistency_inputs()
    # Make one runner's second (later-stored) row carry an earlier timestamp.
    storage[3] = (_RUNNERS[0], "2026-01-15T13:00:00+00:00")
    result = consistency_result(odds, runners, declared, storage, dedup, THRESHOLDS.null_price_ratio)
    assert result["passed"] is False
    assert result["ordering"]["passed"] is False


def test_consistency_wrong_runner_count_fails():
    """A distinct-runner count that differs from declared fails (Req 4.4)."""
    odds, runners, declared, storage, dedup = _wellformed_consistency_inputs()
    declared = _RUNNERS + ["99999"]  # declare 4 runners, rows only have 3
    result = consistency_result(odds, runners, declared, storage, dedup, THRESHOLDS.null_price_ratio)
    assert result["passed"] is False
    assert result["runner_count"]["passed"] is False


def test_consistency_absent_runner_ids_fails():
    """Absent declared runner_ids always fails Consistency (Req 4.7)."""
    odds, runners, _declared, storage, dedup = _wellformed_consistency_inputs()
    result = consistency_result(odds, runners, None, storage, dedup, THRESHOLDS.null_price_ratio)
    assert result["passed"] is False
    assert result["runner_count"]["passed"] is False


# --- Useful dimension (Req 5.1, 5.2, 5.3, 5.4) -------------------------------

# Settlement ~110 min after kick-off (just past the intended in-play span).
SETTLEMENT = START + timedelta(seconds=110 * 60)


def test_useful_full_lifecycle_match_passes():
    """A full-lifecycle match with >=200 rows spanning pre-match through
    settlement passes Useful (Req 5.1-5.3)."""
    earliest = START - timedelta(hours=1)  # inside the 3h pre-match window
    latest = SETTLEMENT  # at/after settlement boundary
    result = useful_result(
        row_count=THRESHOLDS.min_samples_per_market,
        earliest_ts=earliest,
        latest_ts=latest,
        start_time=START,
        settlement_ts=SETTLEMENT,
        thresholds=THRESHOLDS,
    )
    assert result["passed"] is True, result["reason"]


def test_useful_96_row_900s_cadence_fails_resolution():
    """A ~96-row match (observed ~900s cadence) is below min_samples_per_market
    and fails the Useful resolution check (Req 5.1, the SP-343 case)."""
    result = useful_result(
        row_count=96,
        earliest_ts=START - timedelta(hours=1),
        latest_ts=SETTLEMENT,
        start_time=START,
        settlement_ts=SETTLEMENT,
        thresholds=THRESHOLDS,
    )
    assert result["passed"] is False
    assert result["evidence"]["resolution_ok"] is False


def test_useful_three_row_stub_fails():
    """A 3-row stub fails Useful on resolution (Req 5.1, 5.4)."""
    result = useful_result(
        row_count=3,
        earliest_ts=START - timedelta(hours=1),
        latest_ts=SETTLEMENT,
        start_time=START,
        settlement_ts=SETTLEMENT,
        thresholds=THRESHOLDS,
    )
    assert result["passed"] is False
    assert result["evidence"]["resolution_ok"] is False


def test_useful_prematch_only_capture_fails():
    """A pre-match-only capture (no at/after-settlement row) fails the
    lifecycle-span check (Req 5.2, 5.3)."""
    earliest = START - timedelta(hours=2)
    latest = START - timedelta(minutes=1)  # nothing at/after settlement
    result = useful_result(
        row_count=THRESHOLDS.min_samples_per_market,
        earliest_ts=earliest,
        latest_ts=latest,
        start_time=START,
        settlement_ts=SETTLEMENT,
        thresholds=THRESHOLDS,
    )
    assert result["passed"] is False
    assert result["evidence"]["settlement_present"] is False


# --- Aggregation (Req 6.5) ----------------------------------------------------


def test_aggregate_not_evaluated_dimension_forces_overall_fail():
    """A NOT_EVALUATED dimension forces overall = FAIL even when the other three
    dimensions pass (Req 6.5). Coverage is passed as None (not evaluated)."""
    present_pass = {"passed": True}
    consistency_pass = {"passed": True}
    useful_pass = {"passed": True}

    result = aggregate_match(
        target_id="t-1",
        market_id="1.261452692",
        present=present_pass,
        coverage=None,  # could not be evaluated -> NOT_EVALUATED
        consistency=consistency_pass,
        useful=useful_pass,
    )
    assert result.coverage.outcome == "NOT_EVALUATED"
    assert result.overall == "FAIL"
