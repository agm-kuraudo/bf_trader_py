"""
Property-based tests for SP-332: Post-Event Data-Quality Verification.

Tests the correctness properties for the pure-logic quality-checks functions in
``logic/quality_checks.py`` as defined in the
post-event-data-quality-verification design document.
"""

import ast
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.quality_checks import (
    DimensionOutcome,
    MatchQualityResult,
    QualityThresholds,
    aggregate_match,
    consistency_result,
    coverage_result,
    has_any_price,
    parse_odds,
    serialize_odds,
    useful_result,
)

# --- Strategies for INVALID Odds_Value inputs --------------------------------
#
# A valid Odds_Value is a ``str(dict)`` repr whose ``availableToBack`` and
# ``availableToLay`` keys each map to a list of ``{price, size}`` mappings. The
# strategies below deliberately violate one or more of those constraints so
# every generated value is NOT a valid Odds_Value.

# A price/size ladder entry that parse_odds must accept (used to build dicts
# that fail for a *different* reason than the ladder contents).
_finite_float = st.floats(allow_nan=False, allow_infinity=False)
_valid_entry = st.fixed_dictionaries({"price": _finite_float, "size": _finite_float})
_valid_ladder = st.lists(_valid_entry, max_size=4)

# A ladder entry missing ``price`` and/or ``size`` (so the ladder is not a valid
# list of {price, size} entries).
_bad_entry = st.one_of(
    st.fixed_dictionaries({"price": _finite_float}),  # missing size
    st.fixed_dictionaries({"size": _finite_float}),  # missing price
    st.fixed_dictionaries({}),  # missing both
    st.integers(),  # not a mapping at all
    st.text(max_size=5),
)
_bad_ladder_list = st.lists(_bad_entry, min_size=1, max_size=4)


def _repr_or_none(value):
    """repr(value), or None if value is not literal_eval round-trippable.

    We only keep generated dicts whose repr evaluates back to the same object,
    so the ONLY reason parse_odds can reject them is the Odds_Value structural
    rules, never an ast.literal_eval failure.
    """
    r = repr(value)
    try:
        if ast.literal_eval(r) == value:
            return r
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        pass
    return None


# 1) Non-dict reprs: strings, numbers, lists, tuples, None, bools, empty dict.
_non_dict_repr = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=10),
    st.lists(st.integers(), max_size=4),
    st.tuples(st.integers(), st.integers()),
    st.none(),
    st.booleans(),
).map(repr)

# 2) Dicts missing one or both ladder keys.
_missing_ladder_dict = (
    st.dictionaries(
        keys=st.text(max_size=8).filter(lambda k: k not in ("availableToBack", "availableToLay")),
        values=st.one_of(st.integers(), st.text(max_size=5), st.lists(st.integers(), max_size=3)),
        max_size=3,
    )
    .flatmap(
        lambda base: st.sampled_from(
            [
                base,  # neither ladder key
                {**base, "availableToBack": []},  # only back
                {**base, "availableToLay": []},  # only lay
            ]
        )
    )
    .map(_repr_or_none)
    .filter(lambda r: r is not None)
)

# 3) Dicts with both ladder keys present but at least one ladder is NOT a list
#    of {price, size} (either a non-list, or a list containing bad entries).
_bad_back_ladder = st.builds(
    lambda back, lay: {"availableToBack": back, "availableToLay": lay},
    back=st.one_of(_bad_ladder_list, st.integers(), st.text(max_size=5), st.none()),
    lay=_valid_ladder,
)
_bad_lay_ladder = st.builds(
    lambda back, lay: {"availableToBack": back, "availableToLay": lay},
    back=_valid_ladder,
    lay=st.one_of(_bad_ladder_list, st.integers(), st.text(max_size=5), st.none()),
)
_bad_ladder_dict = st.one_of(_bad_back_ladder, _bad_lay_ladder).map(_repr_or_none).filter(lambda r: r is not None)


def _unparseable(s: str) -> bool:
    """True iff ast.literal_eval(s) fails (so s is a genuine garbage string)."""
    try:
        ast.literal_eval(s)
        return False
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return True


# 4) Non-evaluable garbage strings that ast.literal_eval cannot parse.
_garbage_str = st.one_of(
    st.text(max_size=20),
    st.sampled_from(
        [
            "not a dict",
            "{unclosed",
            "{'availableToBack': ",
            "<object>",
            "def foo(): pass",
            "availableToBack=[]",
            "{'a': undefined_name}",
            "{'a': 1/0}",
            "",
            "   ",
            "None None",
        ]
    ),
).filter(_unparseable)


invalid_odds = st.one_of(
    _non_dict_repr,
    _missing_ladder_dict,
    _bad_ladder_dict,
    _garbage_str,
)


class TestProperty2ParserRejectsMalformed:
    """Feature: post-event-data-quality-verification.

    Property 2: Parser rejects malformed input with no partial result.
    """

    # Feature: post-event-data-quality-verification, Property 2: Parser rejects malformed input with no partial result  # noqa: E501
    @given(raw=invalid_odds)
    @settings(max_examples=200)
    def test_parser_rejects_malformed_input_with_no_partial_result(self, raw):
        """
        For any input that is not a valid Odds_Value (non-dict repr, missing
        either ladder key, or a ladder that is not a list of {price, size}),
        parse_odds returns None and produces no partially parsed value.

        **Validates: Requirements 4.2, 9.5**
        """
        result = parse_odds(raw)

        # No partial result: the only permitted return for invalid input is None.
        assert result is None

    # Feature: post-event-data-quality-verification, Property 2: Parser rejects malformed input with no partial result  # noqa: E501
    def test_representative_malformed_examples_return_none(self):
        """A handful of concrete malformed inputs each parse to None.

        **Validates: Requirements 4.2, 9.5**
        """
        malformed = [
            "42",  # non-dict repr
            "[1, 2, 3]",  # list, not a dict
            "'a string'",  # string, not a dict
            "{}",  # dict but no ladders
            "{'availableToBack': []}",  # missing availableToLay
            "{'availableToLay': []}",  # missing availableToBack
            "{'availableToBack': 5, 'availableToLay': []}",  # back not a list
            "{'availableToBack': [], 'availableToLay': [{'price': 1.5}]}",  # lay entry missing size
            "{'availableToBack': [{'size': 1.0}], 'availableToLay': []}",  # back entry missing price
            "{unclosed",  # unparseable garbage
            "not python at all",  # unparseable garbage
        ]
        for raw in malformed:
            assert parse_odds(raw) is None, f"expected None for {raw!r}"


# --- Strategies for VALID Odds_Value inputs ----------------------------------
#
# A valid Odds_Value is the stored ``str(dict)`` form of a dict whose
# ``availableToBack`` and ``availableToLay`` keys each map to a list of
# ``{price, size}`` entries, plus an OPTIONAL ``tradedVolume`` list. Floats are
# constrained to be finite (no NaN/inf) so the ``str(dict) -> literal_eval``
# round-trip is exact.

# A finite float: no NaN/inf, so repr(x) -> literal_eval(repr(x)) is exact.
_finite_price_size = st.floats(allow_nan=False, allow_infinity=False)
_price_size_entry = st.fixed_dictionaries({"price": _finite_price_size, "size": _finite_price_size})
_price_size_ladder = st.lists(_price_size_entry, max_size=5)


@st.composite
def odds_values(draw):
    """Generate a valid stored Odds_Value string (a ``str(dict)`` repr).

    The dict always has list-valued ``availableToBack`` and ``availableToLay``
    ladders of ``{price, size}`` entries with finite floats, and ~half the time
    also carries an optional ``tradedVolume`` list. The value is rendered to its
    stored ``str(dict)`` form so it matches how the ``odds`` column is captured.

    Returns a ``(stored_str, expected_dict)`` pair so tests can assert both the
    round-trip string equality and per-key preservation.
    """
    value = {
        "availableToBack": draw(_price_size_ladder),
        "availableToLay": draw(_price_size_ladder),
    }
    if draw(st.booleans()):
        value["tradedVolume"] = draw(_price_size_ladder)
    return str(value), value


class TestProperty1ParserRoundTrip:
    """Feature: post-event-data-quality-verification.

    Property 1: Odds_Value parser round-trip.
    """

    # Feature: post-event-data-quality-verification, Property 1: Odds_Value parser round-trip  # noqa: E501
    @given(pair=odds_values())
    @settings(max_examples=200)
    def test_serialize_parse_round_trip_preserves_stored_form(self, pair):
        """
        For any valid Odds_Value v (a stored str(dict) with list-valued
        availableToBack and availableToLay ladders of {price, size} entries,
        plus an optional tradedVolume list), serialize_odds(parse_odds(v))
        equals the original stored form v, preserving all three keys including
        tradedVolume.

        **Validates: Requirements 9.6**
        """
        stored, expected = pair

        parsed = parse_odds(stored)

        # parse_odds must accept every valid Odds_Value.
        assert parsed is not None

        # Round-trip: re-serializing the parsed value reproduces the stored form.
        assert serialize_odds(parsed) == stored

        # Key preservation: both ladders are present and preserved...
        assert parsed["availableToBack"] == expected["availableToBack"]
        assert parsed["availableToLay"] == expected["availableToLay"]

        # ...and the optional tradedVolume key is preserved when it was present,
        # and never invented when it was absent.
        assert ("tradedVolume" in parsed) == ("tradedVolume" in expected)
        if "tradedVolume" in expected:
            assert parsed["tradedVolume"] == expected["tradedVolume"]

    # Feature: post-event-data-quality-verification, Property 1: Odds_Value parser round-trip  # noqa: E501
    def test_representative_valid_examples_round_trip(self):
        """A handful of concrete valid Odds_Values round-trip exactly.

        **Validates: Requirements 9.6**
        """
        examples = [
            # both ladders populated, tradedVolume present and empty
            str(
                {
                    "availableToBack": [{"price": 1.66, "size": 40.7}, {"price": 1.65, "size": 677.2}],
                    "availableToLay": [{"price": 1.67, "size": 10.0}],
                    "tradedVolume": [],
                }
            ),
            # tradedVolume absent entirely
            str(
                {
                    "availableToBack": [{"price": 2.0, "size": 5.0}],
                    "availableToLay": [{"price": 2.02, "size": 3.0}],
                }
            ),
            # empty ladders, tradedVolume present and populated
            str(
                {
                    "availableToBack": [],
                    "availableToLay": [],
                    "tradedVolume": [{"price": 1.5, "size": 100.0}],
                }
            ),
        ]
        for stored in examples:
            parsed = parse_odds(stored)
            assert parsed is not None, f"expected valid parse for {stored!r}"
            assert serialize_odds(parsed) == stored, f"round-trip failed for {stored!r}"


# --- Property 4: Coverage passes only within tolerance and gap limits --------
#
# coverage_result(actual_count, expected_count, row_timestamps, start_time,
# thresholds) passes IFF all three conditions from the design hold:
#
#   (a) count check: actual_count >= (1 - coverage_shortfall_ratio) * expected
#   (b) no in-window contiguous no-row gap exceeds the applicable per-tier
#       max_gap_s (gaps wholly before the pre-match window are benign and
#       excluded; a gap starting at/after start_time is held to the IN_PLAY
#       limit, otherwise to the tightest pre-match tier limit)
#   (c) the in-play period is not empty *while the pre-match count check passes*
#
# On failure it records actual / expected / shortfall and the bounds of the
# largest offending gap.
#
# The strategies below generate an actual count, an expected count, and a
# synthetic in-window timestamp sequence with controllable gaps around a fixed
# start_time, plus a QualityThresholds instance. An INDEPENDENT oracle (not the
# implementation's own code path) recomputes the expected pass/fail from the
# three conditions, and the test asserts the biconditional and the documented
# evidence keys.

# A fixed reference kick-off; all generated timestamps are offsets around it.
_P4_START = datetime(2025, 6, 1, 15, 0, 0)


def _p4_oracle(actual_count, expected_count, row_timestamps, start_time, thresholds):
    """Independent reference for coverage_result's pass/fail biconditional.

    Recomputes the three design conditions directly from the requirement text,
    without calling into the implementation's helpers, so a bug in
    coverage_result cannot hide behind a shared code path.

    Returns (expected_passed, offending_gap_bounds_or_None).
    """
    # (a) count-based shortfall.
    tolerated_floor = (1.0 - thresholds.coverage_shortfall_ratio) * expected_count
    count_check_passes = actual_count >= tolerated_floor

    # (c) in-play-empty special case.
    inplay_empty = not any(ts >= start_time for ts in row_timestamps)
    inplay_empty_fail = inplay_empty and count_check_passes

    # (b) largest in-window gap vs the applicable per-tier limit.
    window_start = start_time - timedelta(seconds=thresholds.prematch_window_s)
    in_window = sorted(ts for ts in row_timestamps if ts >= window_start)
    offending_gap = None
    gap_fail = False
    if len(in_window) >= 2:
        # Largest contiguous gap between consecutive in-window rows.
        biggest = None
        for earlier, later in zip(in_window, in_window[1:], strict=False):
            gap_s = (later - earlier).total_seconds()
            if biggest is None or gap_s > biggest[0]:
                biggest = (gap_s, earlier, later)
        gap_s, g_start, g_end = biggest
        if g_start >= start_time:
            limit = thresholds.max_gap_s.get("IN_PLAY", 0)
        else:
            prematch_limits = [v for k, v in thresholds.max_gap_s.items() if k != "IN_PLAY"]
            limit = min(prematch_limits) if prematch_limits else 0
        if limit and gap_s > limit:
            gap_fail = True
            offending_gap = (g_start, g_end)

    expected_passed = count_check_passes and not inplay_empty_fail and not gap_fail
    return expected_passed, offending_gap


# Non-negative counts within a modest range so the count-check boundary is
# exercised on both sides.
_p4_count = st.integers(min_value=0, max_value=500)

# A single timestamp expressed as an integer second-offset from _P4_START.
# The range straddles start_time (offset 0) and reaches back well beyond the
# default 3h pre-match window (10800s) so both in-window and benign
# out-of-window rows, and both pre-match and in-play rows, are generated.
_p4_offset = st.integers(min_value=-20000, max_value=8000)
_p4_offsets = st.lists(_p4_offset, min_size=0, max_size=12)


@st.composite
def _p4_thresholds(draw):
    """A QualityThresholds instance: the default, or a lightly perturbed one.

    Perturbing the shortfall ratio and pre-match window (while keeping the
    default per-tier max_gap_s map) exercises the calibrated-threshold clause
    of the property without inventing an invalid threshold set.
    """
    if draw(st.booleans()):
        return QualityThresholds()
    ratio = draw(st.sampled_from([0.10, 0.25, 0.40, 0.60, 0.90]))
    prematch_window_s = draw(st.sampled_from([3600, 3 * 3600, 6 * 3600]))
    return QualityThresholds(
        coverage_shortfall_ratio=ratio,
        prematch_window_s=prematch_window_s,
    )


class TestProperty4Coverage:
    """Feature: post-event-data-quality-verification.

    Property 4: Coverage passes only within tolerance and gap limits.
    """

    # Feature: post-event-data-quality-verification, Property 4: Coverage passes only within tolerance and gap limits  # noqa: E501
    @given(
        actual_count=_p4_count,
        expected_count=_p4_count,
        offsets=_p4_offsets,
        thresholds=_p4_thresholds(),
    )
    @settings(max_examples=300)
    def test_coverage_pass_iff_within_tolerance_and_gap_limits(self, actual_count, expected_count, offsets, thresholds):
        """
        For any actual count, Expected_Sample_Count, in-window row-timestamp
        sequence and calibrated thresholds, coverage_result passes if and only
        if the shortfall ratio is within coverage_shortfall_ratio AND no
        in-window contiguous no-row gap exceeds the applicable per-tier
        max_gap_s AND the in-play period is not empty while the pre-match count
        check passes. On failure it records actual / expected / shortfall and
        the bounds of the largest offending gap.

        **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**
        """
        row_timestamps = [_P4_START + timedelta(seconds=o) for o in offsets]

        result = coverage_result(actual_count, expected_count, row_timestamps, _P4_START, thresholds)

        expected_passed, expected_gap = _p4_oracle(actual_count, expected_count, row_timestamps, _P4_START, thresholds)

        # The pass/fail biconditional matches the three conditions.
        assert result["passed"] == expected_passed

        # Recorded evidence is always present with the documented shape.
        assert result["actual"] == actual_count
        assert result["expected"] == expected_count
        assert result["shortfall"] == max(0, expected_count - actual_count)
        assert isinstance(result["inplay_empty"], bool)
        assert isinstance(result["reasons"], list)

        # The largest-offending-gap bounds match the oracle (both None, or the
        # same (start, end) pair).
        assert result["largest_gap"] == expected_gap

        # On any failure the documented count evidence is carried, and a reason
        # is always recorded; on a gap failure the gap bounds are recorded.
        if not result["passed"]:
            assert result["reasons"], "a failing result must record at least one reason"
            assert result["shortfall"] >= 0
            if expected_gap is not None:
                assert result["largest_gap"] == expected_gap

    # Feature: post-event-data-quality-verification, Property 4: Coverage passes only within tolerance and gap limits  # noqa: E501
    def test_representative_coverage_examples(self):
        """Concrete Coverage cases covering each failure mode and a clean pass.

        **Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6**
        """
        thresholds = QualityThresholds()

        # 1) Clean pass: dense in-play rows every 5s, count meets the floor.
        dense = [_P4_START + timedelta(seconds=5 * i) for i in range(20)]
        ok = coverage_result(100, 100, dense, _P4_START, thresholds)
        assert ok["passed"] is True
        assert ok["largest_gap"] is None
        assert ok["shortfall"] == 0

        # 2) Count shortfall: actual far below the 60% floor of expected.
        short = coverage_result(10, 100, dense, _P4_START, thresholds)
        assert short["passed"] is False
        assert short["shortfall"] == 90
        assert short["actual"] == 10
        assert short["expected"] == 100

        # 3) In-play gap exceeds the IN_PLAY 60s limit: a 120s in-play hole.
        gapped = [
            _P4_START,
            _P4_START + timedelta(seconds=10),
            _P4_START + timedelta(seconds=130),  # 120s gap > 60s IN_PLAY limit
            _P4_START + timedelta(seconds=140),
        ]
        gres = coverage_result(100, 100, gapped, _P4_START, thresholds)
        assert gres["passed"] is False
        assert gres["largest_gap"] == (
            _P4_START + timedelta(seconds=10),
            _P4_START + timedelta(seconds=130),
        )

        # 4) In-play-empty while the count check passes: only pre-match rows.
        prematch_only = [
            _P4_START - timedelta(seconds=300),
            _P4_START - timedelta(seconds=200),
            _P4_START - timedelta(seconds=100),
        ]
        empty = coverage_result(100, 100, prematch_only, _P4_START, thresholds)
        assert empty["passed"] is False
        assert empty["inplay_empty"] is True

        # 5) Benign overnight gap wholly before the pre-match window is ignored:
        # one row ~98,000s before kick-off (outside the 10,800s window) plus
        # dense in-play rows -> passes.
        benign = [_P4_START - timedelta(seconds=98000)] + dense
        bres = coverage_result(100, 100, benign, _P4_START, thresholds)
        assert bres["passed"] is True
        assert bres["largest_gap"] is None


# --- Property 5: Consistency passes only when every sub-check holds ----------
#
# consistency_result(odds_values, runner_ids_in_rows, declared_runner_ids,
# rows_in_storage_order, dedup_keys, null_price_threshold) passes IFF every
# sub-check holds:
#
#   (parse)        every Odds_Value in odds_values parses (Req 4.1/4.2)
#   (null_price)   among *parseable* rows, the both-empty-price proportion does
#                  not exceed null_price_threshold (Req 4.3)
#   (runner_count) the number of distinct runner_id equals the declared runner
#                  count (Req 4.4)
#   (ordering)     per-runner timestamps are non-decreasing in storage order
#                  (Req 4.5)
#   (dedup)        no two rows share (market_id, runner_id, timestamp) (Req 4.6)
#
# and declared_runner_ids being None (absent/unparseable) ALWAYS fails (Req 4.7).
#
# The composite strategy below builds a synthetic row set in which each of these
# five conditions can be toggled valid/invalid independently, plus a
# declared_runner_ids-None toggle. An INDEPENDENT oracle recomputes the expected
# overall pass/fail from the requirement text (mirroring the "proportion over
# parseable rows only" rule) without calling into consistency_result's helpers,
# and the test asserts the biconditional plus per-sub-check evidence.

# A valid stored Odds_Value with at least one price (so it is NOT both-empty).
_P5_PRICED_ODDS = str(
    {
        "availableToBack": [{"price": 1.5, "size": 10.0}],
        "availableToLay": [{"price": 1.52, "size": 8.0}],
    }
)
# A valid stored Odds_Value with empty ladders (so it IS both-empty-price).
_P5_EMPTY_ODDS = str({"availableToBack": [], "availableToLay": []})
# An unparseable garbage string (parse_odds returns None).
_P5_UNPARSEABLE_ODDS = "{not a dict"

_P5_MARKET_ID = "1.234567890"
_P5_BASE_TS = datetime(2025, 6, 1, 12, 0, 0)


@st.composite
def _p5_scenarios(draw):
    """Generate a consistency_result input set with independently toggled checks.

    Returns a dict of keyword arguments for consistency_result. Each of the five
    sub-checks (parse, null-price, runner-count, ordering, dedup) plus the
    declared-None case is toggled by an independent boolean so the oracle can
    exercise every combination of holding / violating sub-checks.
    """
    # Independent toggles for each condition.
    inject_unparseable = draw(st.booleans())
    exceed_null_price = draw(st.booleans())
    mismatch_runner_count = draw(st.booleans())
    out_of_order = draw(st.booleans())
    duplicate_key = draw(st.booleans())
    declared_none = draw(st.booleans())

    null_price_threshold = draw(st.sampled_from([0.0, 0.25, 0.5, 0.75, 1.0]))

    # Build a base set of rows: one per runner, each a distinct runner_id.
    n_runners = draw(st.integers(min_value=1, max_value=4))
    runner_ids = [f"r{i}" for i in range(n_runners)]

    # Number of both-empty (null-price) rows to add among priced rows. We add
    # a controlled count of extra rows for the same first runner so the
    # both-empty proportion can straddle the threshold.
    n_priced_extra = draw(st.integers(min_value=0, max_value=4))
    n_empty_extra = draw(st.integers(min_value=0, max_value=4))

    # Each row is (runner_id, timestamp, odds_value). Start with one priced row
    # per distinct runner so the distinct-runner count is well-defined.
    rows: list[tuple[str, datetime, str]] = []
    ts_counter = 0
    for rid in runner_ids:
        rows.append((rid, _P5_BASE_TS + timedelta(seconds=ts_counter), _P5_PRICED_ODDS))
        ts_counter += 1

    first_runner = runner_ids[0]
    for _ in range(n_priced_extra):
        rows.append((first_runner, _P5_BASE_TS + timedelta(seconds=ts_counter), _P5_PRICED_ODDS))
        ts_counter += 1
    for _ in range(n_empty_extra):
        rows.append((first_runner, _P5_BASE_TS + timedelta(seconds=ts_counter), _P5_EMPTY_ODDS))
        ts_counter += 1

    # --- Toggle: inject an unparseable Odds_Value (parse sub-check fails). ---
    if inject_unparseable:
        rows.append((first_runner, _P5_BASE_TS + timedelta(seconds=ts_counter), _P5_UNPARSEABLE_ODDS))
        ts_counter += 1

    # --- Toggle: force the both-empty proportion to exceed the threshold. ---
    # Add enough empty-priced rows that (empty / parseable) > threshold. We add
    # empty rows until the proportion strictly exceeds the threshold; when the
    # toggle is off we leave the proportion as-is (it may still exceed for a 0.0
    # threshold, which the oracle accounts for independently).
    if exceed_null_price:
        # Add empties until strictly above threshold (bounded to avoid runaway).
        for _ in range(12):
            parseable = [o for (_, _, o) in rows if o != _P5_UNPARSEABLE_ODDS]
            empties = sum(1 for o in parseable if not has_any_price(parse_odds_safe(o)))
            prop = empties / len(parseable) if parseable else 0.0
            if prop > null_price_threshold:
                break
            rows.append((first_runner, _P5_BASE_TS + timedelta(seconds=ts_counter), _P5_EMPTY_ODDS))
            ts_counter += 1

    # --- Toggle: create an out-of-order timestamp for the first runner. ---
    # Append a first-runner row whose timestamp precedes an earlier-stored row.
    if out_of_order:
        rows.append((first_runner, _P5_BASE_TS - timedelta(seconds=1), _P5_PRICED_ODDS))

    # --- Toggle: introduce a duplicate (market_id, runner_id, timestamp). ---
    # Duplicate the first row exactly (same runner + same timestamp).
    if duplicate_key:
        rid0, ts0, odds0 = rows[0]
        rows.append((rid0, ts0, odds0))

    # Storage order is the row insertion order above.
    odds_values = [o for (_, _, o) in rows]
    runner_ids_in_rows = [rid for (rid, _, _) in rows]
    rows_in_storage_order = [(rid, ts) for (rid, ts, _) in rows]
    dedup_keys = [(_P5_MARKET_ID, rid, str(ts)) for (rid, ts, _) in rows]

    # --- Declared runner ids: either None (Req 4.7) or matched/mismatched. ---
    if declared_none:
        declared_runner_ids = None
    elif mismatch_runner_count:
        # Declare a different number of runners than the distinct count present.
        distinct = len(set(runner_ids_in_rows))
        declared_runner_ids = [f"d{i}" for i in range(distinct + 1)]
    else:
        distinct = len(set(runner_ids_in_rows))
        declared_runner_ids = [f"d{i}" for i in range(distinct)]

    return {
        "odds_values": odds_values,
        "runner_ids_in_rows": runner_ids_in_rows,
        "declared_runner_ids": declared_runner_ids,
        "rows_in_storage_order": rows_in_storage_order,
        "dedup_keys": dedup_keys,
        "null_price_threshold": null_price_threshold,
    }


def parse_odds_safe(raw):
    """parse_odds wrapper used only by the strategy helper above."""
    from logic.quality_checks import parse_odds as _parse

    return _parse(raw)


def _p5_oracle(kwargs):
    """Independent reference for consistency_result's pass/fail biconditional.

    Recomputes each sub-check straight from the requirement text (mirroring the
    "proportion over parseable rows only" rule and the declared-None short
    circuit) without reusing consistency_result's own control flow.

    Returns (expected_passed, per_check_expected_dict).
    """
    from logic.quality_checks import parse_odds

    odds_values = kwargs["odds_values"]
    runner_ids_in_rows = kwargs["runner_ids_in_rows"]
    declared_runner_ids = kwargs["declared_runner_ids"]
    rows_in_storage_order = kwargs["rows_in_storage_order"]
    dedup_keys = kwargs["dedup_keys"]
    threshold = kwargs["null_price_threshold"]

    # (parse) every Odds_Value parses.
    parsed = [parse_odds(o) for o in odds_values]
    unparseable = sum(1 for p in parsed if p is None)
    parse_passed = unparseable == 0

    # (null_price) proportion over PARSEABLE rows only.
    parseable = [p for p in parsed if p is not None]
    both_empty = sum(1 for p in parseable if not has_any_price(p))
    proportion = (both_empty / len(parseable)) if parseable else 0.0
    null_price_passed = proportion <= threshold

    # (runner_count) distinct runner_id count equals declared count; None fails.
    distinct = len(set(runner_ids_in_rows))
    if declared_runner_ids is None:
        runner_count_passed = False
    else:
        runner_count_passed = distinct == len(declared_runner_ids)

    # (ordering) per-runner timestamps non-decreasing in storage order.
    last_ts: dict = {}
    violations = 0
    for rid, ts in rows_in_storage_order:
        prev = last_ts.get(rid)
        if prev is not None and ts < prev:
            violations += 1
        last_ts[rid] = ts
    ordering_passed = violations == 0

    # (dedup) no duplicate (market_id, runner_id, timestamp).
    seen: set = set()
    duplicates = 0
    for key in dedup_keys:
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    dedup_passed = duplicates == 0

    expected_passed = parse_passed and null_price_passed and runner_count_passed and ordering_passed and dedup_passed
    return expected_passed, {
        "parse": parse_passed,
        "null_price": null_price_passed,
        "runner_count": runner_count_passed,
        "ordering": ordering_passed,
        "dedup": dedup_passed,
    }


class TestProperty5Consistency:
    """Feature: post-event-data-quality-verification.

    Property 5: Consistency passes only when every sub-check holds.
    """

    # Feature: post-event-data-quality-verification, Property 5: Consistency passes only when every sub-check holds  # noqa: E501
    @given(kwargs=_p5_scenarios())
    @settings(max_examples=300)
    def test_consistency_pass_iff_every_sub_check_holds(self, kwargs):
        """
        For any set of associated rows, consistency_result passes if and only if
        every sub-check holds: every Odds_Value parses, the both-empty-price
        proportion does not exceed null_price_threshold, the number of distinct
        runner_id equals the declared runner count, per-runner timestamps are
        non-decreasing in storage order, and no two rows share
        (market_id, runner_id, timestamp). Absent or unparseable declared
        runner_ids always fails.

        **Validates: Requirements 4.1, 4.3, 4.4, 4.5, 4.6, 4.7**
        """
        result = consistency_result(**kwargs)

        expected_passed, expected_checks = _p5_oracle(kwargs)

        # The overall pass/fail biconditional matches the AND of all sub-checks.
        assert result["passed"] == expected_passed

        # Each sub-check outcome matches the independent oracle.
        assert result["parse"]["passed"] == expected_checks["parse"]
        assert result["null_price"]["passed"] == expected_checks["null_price"]
        assert result["runner_count"]["passed"] == expected_checks["runner_count"]
        assert result["ordering"]["passed"] == expected_checks["ordering"]
        assert result["duplicates"]["passed"] == expected_checks["dedup"]

        # Absent/unparseable declared runner_ids always fails overall (Req 4.7).
        if kwargs["declared_runner_ids"] is None:
            assert result["runner_count"]["passed"] is False
            assert result["passed"] is False

        # A failing overall result always records at least one reason.
        if not result["passed"]:
            assert result["reasons"], "a failing result must record at least one reason"

    # Feature: post-event-data-quality-verification, Property 5: Consistency passes only when every sub-check holds  # noqa: E501
    def test_representative_consistency_examples(self):
        """Concrete Consistency cases: a clean pass plus each failure mode.

        **Validates: Requirements 4.1, 4.3, 4.4, 4.5, 4.6, 4.7**
        """
        base_ts = datetime(2025, 6, 1, 12, 0, 0)

        # Clean pass: two runners, priced odds, matching declared count, ordered,
        # no duplicates, no empties.
        ok = consistency_result(
            odds_values=[_P5_PRICED_ODDS, _P5_PRICED_ODDS],
            runner_ids_in_rows=["r0", "r1"],
            declared_runner_ids=["r0", "r1"],
            rows_in_storage_order=[("r0", base_ts), ("r1", base_ts)],
            dedup_keys=[(_P5_MARKET_ID, "r0", str(base_ts)), (_P5_MARKET_ID, "r1", str(base_ts))],
            null_price_threshold=0.5,
        )
        assert ok["passed"] is True

        # Unparseable odds fails the parse sub-check (Req 4.1/4.2).
        bad_parse = consistency_result(
            odds_values=[_P5_UNPARSEABLE_ODDS],
            runner_ids_in_rows=["r0"],
            declared_runner_ids=["r0"],
            rows_in_storage_order=[("r0", base_ts)],
            dedup_keys=[(_P5_MARKET_ID, "r0", str(base_ts))],
            null_price_threshold=0.5,
        )
        assert bad_parse["passed"] is False
        assert bad_parse["parse"]["passed"] is False

        # Both-empty proportion exceeds threshold (Req 4.3): 1 of 1 empty > 0.0.
        bad_null = consistency_result(
            odds_values=[_P5_EMPTY_ODDS],
            runner_ids_in_rows=["r0"],
            declared_runner_ids=["r0"],
            rows_in_storage_order=[("r0", base_ts)],
            dedup_keys=[(_P5_MARKET_ID, "r0", str(base_ts))],
            null_price_threshold=0.0,
        )
        assert bad_null["passed"] is False
        assert bad_null["null_price"]["passed"] is False

        # Runner-count mismatch (Req 4.4): 1 distinct runner, 2 declared.
        bad_count = consistency_result(
            odds_values=[_P5_PRICED_ODDS],
            runner_ids_in_rows=["r0"],
            declared_runner_ids=["r0", "r1"],
            rows_in_storage_order=[("r0", base_ts)],
            dedup_keys=[(_P5_MARKET_ID, "r0", str(base_ts))],
            null_price_threshold=0.5,
        )
        assert bad_count["passed"] is False
        assert bad_count["runner_count"]["passed"] is False

        # Out-of-order timestamps for a runner (Req 4.5).
        bad_order = consistency_result(
            odds_values=[_P5_PRICED_ODDS, _P5_PRICED_ODDS],
            runner_ids_in_rows=["r0", "r0"],
            declared_runner_ids=["r0"],
            rows_in_storage_order=[("r0", base_ts), ("r0", base_ts - timedelta(seconds=1))],
            dedup_keys=[
                (_P5_MARKET_ID, "r0", str(base_ts)),
                (_P5_MARKET_ID, "r0", str(base_ts - timedelta(seconds=1))),
            ],
            null_price_threshold=0.5,
        )
        assert bad_order["passed"] is False
        assert bad_order["ordering"]["passed"] is False

        # Duplicate (market_id, runner_id, timestamp) key (Req 4.6).
        bad_dup = consistency_result(
            odds_values=[_P5_PRICED_ODDS, _P5_PRICED_ODDS],
            runner_ids_in_rows=["r0", "r0"],
            declared_runner_ids=["r0"],
            rows_in_storage_order=[("r0", base_ts), ("r0", base_ts)],
            dedup_keys=[(_P5_MARKET_ID, "r0", str(base_ts)), (_P5_MARKET_ID, "r0", str(base_ts))],
            null_price_threshold=0.5,
        )
        assert bad_dup["passed"] is False
        assert bad_dup["duplicates"]["passed"] is False

        # Absent declared runner_ids always fails (Req 4.7).
        bad_declared = consistency_result(
            odds_values=[_P5_PRICED_ODDS],
            runner_ids_in_rows=["r0"],
            declared_runner_ids=None,
            rows_in_storage_order=[("r0", base_ts)],
            dedup_keys=[(_P5_MARKET_ID, "r0", str(base_ts))],
            null_price_threshold=0.5,
        )
        assert bad_declared["passed"] is False
        assert bad_declared["runner_count"]["passed"] is False


# --- Property 6: Useful requires both resolution and lifecycle span ----------
#
# useful_result(row_count, earliest_ts, latest_ts, start_time, settlement_ts,
# thresholds) produces a single boolean that passes IFF all three hold
# (design Property 6, Req 5.1-5.4):
#
#   (a) resolution: row_count >= thresholds.min_samples_per_market
#   (b) pre-match-window row: at least one row within prematch_window_s before
#       start_time -- i.e. the earliest row falls in
#       [start_time - prematch_window_s, start_time]
#   (c) at/after-settlement row: the latest row is at or after the settlement
#       boundary (settlement_ts + settlement_grace_s)
#
# When there are no rows (earliest_ts / latest_ts are None) neither span portion
# can be present. On failure the reason names which lifecycle portion is absent
# (pre-match window, settlement, or both) and the insufficient-resolution case.
#
# The strategy generates row counts straddling min_samples_per_market and
# earliest/latest offsets straddling the pre-match-window and settlement
# boundaries (including the no-rows None case), across the default and a lightly
# perturbed QualityThresholds. An INDEPENDENT oracle recomputes the expected
# pass/fail biconditional and the missing-portion set straight from the
# requirement text, and the test asserts both.

# A fixed reference kick-off and a settlement ~2h later; generated timestamps
# are integer-second offsets around these fixed anchors.
_P6_START = datetime(2025, 6, 1, 15, 0, 0)
_P6_SETTLEMENT = _P6_START + timedelta(hours=2)


def _p6_oracle(row_count, earliest_ts, latest_ts, start_time, settlement_ts, thresholds):
    """Independent reference for useful_result's pass/fail biconditional.

    Recomputes the three design conditions directly from the requirement text,
    without calling into useful_result's own control flow, so a bug there
    cannot hide behind a shared code path.

    Returns (expected_passed, resolution_ok, prematch_present,
    settlement_present).
    """
    resolution_ok = row_count >= thresholds.min_samples_per_market

    prematch_window_start = start_time - timedelta(seconds=thresholds.prematch_window_s)
    settlement_boundary = settlement_ts + timedelta(seconds=thresholds.settlement_grace_s)

    if earliest_ts is None or latest_ts is None:
        prematch_present = False
        settlement_present = False
    else:
        prematch_present = prematch_window_start <= earliest_ts <= start_time
        settlement_present = latest_ts >= settlement_boundary

    expected_passed = resolution_ok and prematch_present and settlement_present
    return expected_passed, resolution_ok, prematch_present, settlement_present


# Row counts straddling the default min_samples_per_market (200) on both sides.
_p6_row_count = st.integers(min_value=0, max_value=400)

# An offset (in seconds) for the earliest row expressed relative to start_time.
# The range reaches back well beyond the default 3h (10800s) pre-match window
# and forward past start_time, so the earliest row straddles both the
# window-open boundary (-prematch_window_s) and the start_time boundary.
_p6_earliest_offset = st.integers(min_value=-20000, max_value=2000)

# An offset (in seconds) for the latest row expressed relative to settlement_ts.
# The range straddles the settlement boundary (0) on both sides.
_p6_latest_offset = st.integers(min_value=-8000, max_value=8000)


@st.composite
def _p6_thresholds(draw):
    """A QualityThresholds instance: the default, or a lightly perturbed one.

    Perturbs min_samples_per_market, prematch_window_s and settlement_grace_s
    (keeping the default max_gap_s map) so the biconditional is exercised across
    the calibrated-threshold clause without inventing an invalid set.
    """
    if draw(st.booleans()):
        return QualityThresholds()
    min_samples = draw(st.sampled_from([0, 1, 50, 200, 300]))
    prematch_window_s = draw(st.sampled_from([3600, 3 * 3600, 6 * 3600]))
    settlement_grace_s = draw(st.sampled_from([0, 300, 1800]))
    return QualityThresholds(
        min_samples_per_market=min_samples,
        prematch_window_s=prematch_window_s,
        settlement_grace_s=settlement_grace_s,
    )


@st.composite
def _p6_timestamps(draw):
    """Generate (earliest_ts, latest_ts): either both None (no rows) or a pair.

    The no-rows case (both None) is drawn ~a third of the time so the
    "no rows -> both portions absent" branch is exercised; otherwise an earliest
    and latest offset are drawn independently around the fixed anchors. The
    latest row is clamped to be no earlier than the earliest so the pair is a
    coherent span, while still straddling the settlement boundary.
    """
    if draw(st.integers(min_value=0, max_value=2)) == 0:
        return None, None
    earliest = _P6_START + timedelta(seconds=draw(_p6_earliest_offset))
    latest = _P6_SETTLEMENT + timedelta(seconds=draw(_p6_latest_offset))
    if latest < earliest:
        latest = earliest
    return earliest, latest


class TestProperty6Useful:
    """Feature: post-event-data-quality-verification.

    Property 6: Useful requires both resolution and lifecycle span.
    """

    # Feature: post-event-data-quality-verification, Property 6: Useful requires both resolution and lifecycle span  # noqa: E501
    @given(
        row_count=_p6_row_count,
        timestamps=_p6_timestamps(),
        thresholds=_p6_thresholds(),
    )
    @settings(max_examples=300)
    def test_useful_pass_iff_resolution_and_lifecycle_span(self, row_count, timestamps, thresholds):
        """
        For any row count and earliest/latest timestamps relative to start_time
        and settlement, useful_result passes if and only if the row count is at
        least min_samples_per_market AND at least one row falls within
        prematch_window_s before start_time AND at least one row falls at or
        after settlement. Any missing portion is recorded in the failure reason.

        **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
        """
        earliest_ts, latest_ts = timestamps

        result = useful_result(row_count, earliest_ts, latest_ts, _P6_START, _P6_SETTLEMENT, thresholds)

        (
            expected_passed,
            resolution_ok,
            prematch_present,
            settlement_present,
        ) = _p6_oracle(row_count, earliest_ts, latest_ts, _P6_START, _P6_SETTLEMENT, thresholds)

        # The pass/fail biconditional matches the three design conditions.
        assert result["passed"] == expected_passed

        # The earliest/latest timestamps are recorded regardless of outcome
        # (Req 5.2), along with the determined per-portion flags.
        assert result["evidence"]["row_count"] == row_count
        assert result["evidence"]["min_samples"] == thresholds.min_samples_per_market
        assert result["evidence"]["earliest_ts"] == earliest_ts
        assert result["evidence"]["latest_ts"] == latest_ts
        assert result["evidence"]["resolution_ok"] == resolution_ok
        assert result["evidence"]["prematch_present"] == prematch_present
        assert result["evidence"]["settlement_present"] == settlement_present

        if result["passed"]:
            assert result["reason"] is None
        else:
            # A failing result always records a reason, and every absent portion
            # is named in it (Req 5.3).
            reason = result["reason"]
            assert reason, "a failing result must record a reason"
            if not resolution_ok:
                assert "resolution" in reason
            if not prematch_present:
                assert "pre-match window" in reason
            if not settlement_present:
                assert "settlement" in reason

    # Feature: post-event-data-quality-verification, Property 6: Useful requires both resolution and lifecycle span  # noqa: E501
    def test_representative_useful_examples(self):
        """Concrete Useful cases: a clean pass plus each missing portion.

        **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
        """
        thresholds = QualityThresholds()  # min_samples 200, window 3h, grace 0

        prematch_row = _P6_START - timedelta(hours=1)  # within the 3h window
        settled_row = _P6_SETTLEMENT + timedelta(minutes=5)  # at/after settlement

        # 1) Clean pass: enough rows, a pre-match-window row, a settled row.
        ok = useful_result(250, prematch_row, settled_row, _P6_START, _P6_SETTLEMENT, thresholds)
        assert ok["passed"] is True
        assert ok["reason"] is None
        assert ok["evidence"]["earliest_ts"] == prematch_row
        assert ok["evidence"]["latest_ts"] == settled_row

        # 2) Insufficient resolution: full span but too few rows (Req 5.1).
        thin = useful_result(10, prematch_row, settled_row, _P6_START, _P6_SETTLEMENT, thresholds)
        assert thin["passed"] is False
        assert "resolution" in thin["reason"]

        # 3) Missing pre-match window: earliest row is before the 3h window opens
        #    (Req 5.2/5.3).
        too_early = _P6_START - timedelta(hours=5)  # outside the 3h window
        no_prematch = useful_result(250, too_early, settled_row, _P6_START, _P6_SETTLEMENT, thresholds)
        assert no_prematch["passed"] is False
        assert "pre-match window" in no_prematch["reason"]

        # 4) Missing settlement: latest row is before settlement (Req 5.2/5.3).
        before_settle = _P6_SETTLEMENT - timedelta(minutes=10)
        no_settle = useful_result(250, prematch_row, before_settle, _P6_START, _P6_SETTLEMENT, thresholds)
        assert no_settle["passed"] is False
        assert "settlement" in no_settle["reason"]

        # 5) No rows at all: both lifecycle portions absent (Req 5.3).
        no_rows = useful_result(0, None, None, _P6_START, _P6_SETTLEMENT, thresholds)
        assert no_rows["passed"] is False
        assert "pre-match window" in no_rows["reason"]
        assert "settlement" in no_rows["reason"]


# --- Property 7: Aggregate outcome is pass only when all dimensions pass -----
#
# aggregate_match(target_id, market_id, present, coverage, consistency, useful)
# rolls the four dimension result dicts into one MatchQualityResult (design
# Property 7, Req 6.1-6.5). For any four dimension outcomes drawn from
# {PASS, FAIL, NOT_EVALUATED}:
#
#   (biconditional)  overall == PASS  iff  all four dimensions are PASS;
#                    otherwise overall == FAIL (Req 6.2, 6.3, 6.5)
#   (mapping)        each dimension's DimensionOutcome.outcome reflects the
#                    dimension it was built from: a dict with truthy 'passed' ->
#                    PASS; None or {'evaluated': False} -> NOT_EVALUATED; a
#                    non-passing dict -> FAIL (Req 6.1, 6.5)
#   (evidence)       every non-passing dimension (FAIL or NOT_EVALUATED) carries
#                    a reason and its recorded evidence into the outcome; a
#                    passing dimension carries no reason (Req 6.4, 6.5)
#
# The strategy independently draws one of the three outcome kinds for each of
# the four dimensions, building a realistic result dict for PASS/FAIL and using
# either None or {'evaluated': False} for NOT_EVALUATED. An INDEPENDENT oracle
# recomputes the expected per-dimension outcome and the overall roll-up straight
# from the requirement text, and the test asserts the biconditional, the
# per-dimension mapping, and the reason/evidence carry-through.

# The three outcome kinds a dimension can resolve to.
_P7_PASS = "PASS"
_P7_FAIL = "FAIL"
_P7_NOT_EVALUATED = "NOT_EVALUATED"

_P7_TARGET_ID = "t-1.234567890"
_P7_MARKET_ID = "1.234567890"


@st.composite
def _p7_dimension(draw):
    """Draw one dimension as (kind, result_dict_or_None, expected_evidence).

    Returns a triple:
      - ``kind``: the expected DimensionOutcome.outcome ("PASS"/"FAIL"/
        "NOT_EVALUATED").
      - the value to pass to aggregate_match for that dimension (a result dict,
        or ``None``).
      - ``expected_evidence``: the evidence dict aggregate_match is expected to
        carry into the DimensionOutcome (``{}`` for a PASS).

    For NOT_EVALUATED, ~half the draws use ``None`` (the wrapper's
    could-not-evaluate signal) and ~half use an explicit
    ``{'evaluated': False, ...}`` dict carrying a reason/evidence, so both
    NOT_EVALUATED shapes are exercised (Req 6.5).
    """
    kind = draw(st.sampled_from([_P7_PASS, _P7_FAIL, _P7_NOT_EVALUATED]))

    if kind == _P7_PASS:
        # A passing dimension: truthy 'passed', no reason carried through.
        return _P7_PASS, {"passed": True, "evidence": {"note": "ok"}}, {}

    if kind == _P7_FAIL:
        # A failing dimension: falsy 'passed' plus a reason and evidence. Use a
        # single-'reason' shape or a 'reasons'-list shape (both dimension
        # families are represented in the codebase) and either nested evidence
        # or top-level count keys.
        use_reasons_list = draw(st.booleans())
        count = draw(st.integers(min_value=0, max_value=99))
        if use_reasons_list:
            # coverage/consistency shape: 'reasons' list + top-level evidence.
            result = {
                "passed": False,
                "reasons": ["something went wrong", "and another thing"],
                "actual": count,
                "expected": count + 10,
            }
            expected_evidence = {"actual": count, "expected": count + 10}
        else:
            # present/useful shape: single 'reason' + nested 'evidence' dict.
            result = {
                "passed": False,
                "reason": "the dimension failed",
                "evidence": {"row_count": count},
            }
            expected_evidence = {"row_count": count}
        return _P7_FAIL, result, expected_evidence

    # NOT_EVALUATED: either None, or an explicit {'evaluated': False} dict.
    if draw(st.booleans()):
        return _P7_NOT_EVALUATED, None, {}
    count = draw(st.integers(min_value=0, max_value=99))
    result = {
        "evaluated": False,
        "reason": "dimension skipped",
        "evidence": {"row_count": count},
    }
    return _P7_NOT_EVALUATED, result, {"row_count": count}


class TestProperty7Aggregation:
    """Feature: post-event-data-quality-verification.

    Property 7: Aggregate outcome is pass only when all dimensions pass.
    """

    # Feature: post-event-data-quality-verification, Property 7: Aggregate outcome is pass only when all dimensions pass  # noqa: E501
    @given(
        present=_p7_dimension(),
        coverage=_p7_dimension(),
        consistency=_p7_dimension(),
        useful=_p7_dimension(),
    )
    @settings(max_examples=300)
    def test_aggregate_pass_iff_all_dimensions_pass(self, present, coverage, consistency, useful):
        """
        For any four dimension outcomes drawn from {PASS, FAIL, NOT_EVALUATED},
        aggregate_match sets overall = PASS if and only if all four dimensions
        are PASS; otherwise FAIL. Each resulting DimensionOutcome reflects the
        correct PASS/FAIL/NOT_EVALUATED, and every non-passing dimension carries
        its reason and recorded evidence into the aggregated result.

        **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
        """
        dims = {
            "present": present,
            "coverage": coverage,
            "consistency": consistency,
            "useful": useful,
        }
        kinds = {name: kind for name, (kind, _, _) in dims.items()}

        result = aggregate_match(
            _P7_TARGET_ID,
            _P7_MARKET_ID,
            present[1],
            coverage[1],
            consistency[1],
            useful[1],
        )

        # The result is one MatchQualityResult carrying the match identity.
        assert isinstance(result, MatchQualityResult)
        assert result.target_id == _P7_TARGET_ID
        assert result.market_id == _P7_MARKET_ID

        # Independent oracle for the overall roll-up: PASS iff every dimension
        # is PASS, else FAIL (Req 6.2, 6.3, 6.5).
        expected_overall = _P7_PASS if all(k == _P7_PASS for k in kinds.values()) else _P7_FAIL
        assert result.overall == expected_overall

        # Per-dimension mapping and reason/evidence carry-through (Req 6.1, 6.4,
        # 6.5).
        for name, (kind, _payload, expected_evidence) in dims.items():
            outcome = getattr(result, name)
            assert isinstance(outcome, DimensionOutcome)
            assert outcome.outcome == kind

            if kind == _P7_PASS:
                # A passing dimension carries no reason and empty evidence.
                assert outcome.reason is None
                assert outcome.evidence == {}
            else:
                # A FAIL / NOT_EVALUATED dimension carries a reason and its
                # recorded evidence.
                assert outcome.reason, f"{name} ({kind}) must carry a reason"
                assert outcome.evidence == expected_evidence

    # Feature: post-event-data-quality-verification, Property 7: Aggregate outcome is pass only when all dimensions pass  # noqa: E501
    def test_representative_aggregation_examples(self):
        """Concrete aggregation cases: all-pass, one FAIL, one NOT_EVALUATED.

        **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**
        """
        passing = {"passed": True, "evidence": {"row_count": 250}}
        failing = {
            "passed": False,
            "reason": "no Market_Table rows captured (row_count == 0)",
            "evidence": {"row_count": 0},
        }

        # 1) All four dimensions pass -> overall PASS, no reasons.
        all_pass = aggregate_match(_P7_TARGET_ID, _P7_MARKET_ID, passing, passing, passing, passing)
        assert all_pass.overall == "PASS"
        for name in ("present", "coverage", "consistency", "useful"):
            outcome = getattr(all_pass, name)
            assert outcome.outcome == "PASS"
            assert outcome.reason is None
            assert outcome.evidence == {}

        # 2) One failing dimension -> overall FAIL; the failing dimension carries
        #    its reason + evidence, the others still PASS (Req 6.2, 6.4).
        one_fail = aggregate_match(_P7_TARGET_ID, _P7_MARKET_ID, failing, passing, passing, passing)
        assert one_fail.overall == "FAIL"
        assert one_fail.present.outcome == "FAIL"
        assert one_fail.present.reason
        assert one_fail.present.evidence == {"row_count": 0}
        assert one_fail.coverage.outcome == "PASS"

        # 3) A NOT_EVALUATED dimension (None) forces overall FAIL and is recorded
        #    as NOT_EVALUATED with a reason (Req 6.5).
        one_not_eval = aggregate_match(_P7_TARGET_ID, _P7_MARKET_ID, passing, None, passing, passing)
        assert one_not_eval.overall == "FAIL"
        assert one_not_eval.coverage.outcome == "NOT_EVALUATED"
        assert one_not_eval.coverage.reason

        # 4) An explicit {'evaluated': False} dict is also NOT_EVALUATED and
        #    carries its recorded reason + evidence (Req 6.5).
        explicit_not_eval = aggregate_match(
            _P7_TARGET_ID,
            _P7_MARKET_ID,
            passing,
            passing,
            {"evaluated": False, "reason": "skipped", "evidence": {"row_count": 5}},
            passing,
        )
        assert explicit_not_eval.overall == "FAIL"
        assert explicit_not_eval.consistency.outcome == "NOT_EVALUATED"
        assert explicit_not_eval.consistency.reason == "skipped"
        assert explicit_not_eval.consistency.evidence == {"row_count": 5}
