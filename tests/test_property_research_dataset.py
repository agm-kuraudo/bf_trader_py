"""Property-based tests for SP-368: Betfair Research_Dataset Odds_Transform.

Tests the correctness properties for the pure-logic Odds_Transform functions in
``logic/research_dataset.py`` as defined in the betfair-research-dataset design
document.

Odds_Value generation reuses the SP-332 odds generators from
``tests/test_property_quality_checks.py`` (the ``odds_values`` composite and its
``_price_size_ladder`` building blocks) so there is one source of truth for
valid ladder/volume shapes. The SP-332 generator emits arbitrary finite floats
-- including negatives and zeros -- but ``structured_odds`` treats a negative or
otherwise out-of-range price/size as unparseable and returns ``None`` (Req 1.8).
The round-trip property only applies to VALID Odds_Values, so the generator's
price/size space is constrained to non-negative finite numbers here, reusing the
SP-332 ladder structure rather than inventing a second shape.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.quality_checks import parse_odds
from logic.research_dataset import (
    DEFAULT_DRAW_NAME,
    PriceLevel,
    TransformResult,
    build_records,
    parse_runner_ids,
    serialize_structured,
    structured_odds,
)

# --- Strategies for VALID Odds_Value inputs (reusing the SP-332 structure) ----
#
# A valid Odds_Value is the stored ``str(dict)`` form of a dict whose
# ``availableToBack`` and ``availableToLay`` keys each map to a list of
# ``{price, size}`` entries, plus an OPTIONAL ``tradedVolume`` list -- exactly
# the shape the SP-332 ``odds_values`` composite in
# ``tests/test_property_quality_checks.py`` generates. The only constraint added
# here is that every price and size is a NON-NEGATIVE, finite number, because
# Req 1.8 makes ``structured_odds`` reject (return ``None`` for) any negative or
# out-of-range price/size; the round-trip property only ranges over valid
# Odds_Values.

# A finite, non-negative price/size: no NaN/inf, so the ``str(dict)`` ->
# ``literal_eval`` round-trip is exact, and ``>= 0`` so Req 1.8 accepts it.
_valid_price_size = st.floats(min_value=0.0, allow_nan=False, allow_infinity=False)
_valid_entry = st.fixed_dictionaries({"price": _valid_price_size, "size": _valid_price_size})
_valid_ladder = st.lists(_valid_entry, max_size=5)


@st.composite
def valid_odds_values(draw):
    """Generate a VALID stored Odds_Value string (a ``str(dict)`` repr).

    Mirrors the SP-332 ``odds_values`` composite (list-valued
    ``availableToBack`` / ``availableToLay`` ladders of ``{price, size}``
    entries, plus an optional ``tradedVolume`` list, rendered to its stored
    ``str(dict)`` form) but constrains every price and size to be non-negative
    and finite so ``structured_odds`` accepts it (Req 1.8). The value is
    rendered to its stored ``str(dict)`` form so it matches how the ``odds``
    column is captured.

    Returns the stored Odds_Value string.
    """
    value = {
        "availableToBack": draw(_valid_ladder),
        "availableToLay": draw(_valid_ladder),
    }
    if draw(st.booleans()):
        value["tradedVolume"] = draw(_valid_ladder)
    return str(value)


class TestProperty1OddsValueRoundTrip:
    """Feature: betfair-research-dataset.

    Property 1: Odds_Value round-trip.
    """

    # Feature: betfair-research-dataset, Property 1: For any valid generated Odds_Value, parsing it with structured_odds and then serializing the result with serialize_structured produces a string that parses to a structure whose back/lay prices, sizes, entry order, and Matched_Volume all equal those of the original  # noqa: E501
    @given(stored=valid_odds_values())
    @settings(max_examples=200)
    def test_parse_then_serialize_reproduces_ladders_and_volume(self, stored):
        """
        For any valid generated Odds_Value v, structured_odds(v) is not None,
        and serialize_structured(structured_odds(v)) parses (via
        logic.quality_checks.parse_odds) to a structure whose back ladder, lay
        ladder (prices + sizes + order) and matched volume all equal those of
        parse_odds(v).

        **Validates: Requirements 1.9, 1.10, 7.1**
        """
        # structured_odds accepts every valid Odds_Value (Req 1.8 only rejects
        # out-of-range values, which this generator never produces).
        parsed_structured = structured_odds(stored)
        assert parsed_structured is not None

        # Round-trip: serialize the parsed structure, then re-parse the result
        # through the single odds-reading path.
        round_tripped = parse_odds(serialize_structured(parsed_structured))
        original = parse_odds(stored)

        assert round_tripped is not None
        assert original is not None

        # Back ladder: prices, sizes, and entry order are preserved exactly.
        assert round_tripped["availableToBack"] == original["availableToBack"]
        # Lay ladder: prices, sizes, and entry order are preserved exactly.
        assert round_tripped["availableToLay"] == original["availableToLay"]

        # Matched_Volume signal (the optional tradedVolume key) is preserved
        # when present and never invented when absent.
        assert ("tradedVolume" in round_tripped) == ("tradedVolume" in original)
        if "tradedVolume" in original:
            assert round_tripped["tradedVolume"] == original["tradedVolume"]

    # Feature: betfair-research-dataset, Property 1: For any valid generated Odds_Value, parsing it with structured_odds and then serializing the result with serialize_structured produces a string that parses to a structure whose back/lay prices, sizes, entry order, and Matched_Volume all equal those of the original  # noqa: E501
    def test_representative_valid_examples_round_trip(self):
        """A handful of concrete valid Odds_Values round-trip exactly.

        **Validates: Requirements 1.9, 1.10, 7.1**
        """
        examples = [
            # both ladders populated, tradedVolume present and populated
            str(
                {
                    "availableToBack": [{"price": 1.66, "size": 40.7}, {"price": 1.65, "size": 677.2}],
                    "availableToLay": [{"price": 1.67, "size": 10.0}],
                    "tradedVolume": [{"price": 1.66, "size": 5.0}],
                }
            ),
            # tradedVolume absent entirely
            str(
                {
                    "availableToBack": [{"price": 2.0, "size": 5.0}],
                    "availableToLay": [{"price": 2.02, "size": 3.0}],
                }
            ),
            # empty ladders, tradedVolume present and empty
            str(
                {
                    "availableToBack": [],
                    "availableToLay": [],
                    "tradedVolume": [],
                }
            ),
        ]
        for stored in examples:
            parsed_structured = structured_odds(stored)
            assert parsed_structured is not None, f"expected valid parse for {stored!r}"

            round_tripped = parse_odds(serialize_structured(parsed_structured))
            original = parse_odds(stored)
            assert round_tripped == original, f"round-trip failed for {stored!r}"


# --- Strategies for Property 2 (best-price correctness) -----------------------
#
# Property 2 asserts best_back/best_lay equal max/min regardless of ladder
# order, and that an empty ladder yields a null best + empty depth. The round-
# trip generator above does not force shuffled or sometimes-empty ladders, so
# this adds a dedicated generator that reuses the same validated
# ``_valid_entry`` / non-negative-finite price-size building blocks but
# deliberately produces ladders whose entries are in arbitrary (shuffled) order
# and that are sometimes empty on either side. parse_odds(v) is used as the
# ground truth for the assertions.


@st.composite
def shuffled_odds_values(draw):
    """Generate a VALID Odds_Value string with shuffled, sometimes-empty ladders.

    Reuses the SP-332 ``_valid_entry`` / non-negative-finite price-size building
    blocks from the Property 1 strategies above, but drives each side's ladder
    through ``st.lists`` with no ordering guarantee so entries appear in
    arbitrary order, and allows either ladder to be empty (``min_size=0``). This
    exercises Property 2's "regardless of ladder order" and "empty ladder" cases
    directly. The value is rendered to its stored ``str(dict)`` form.

    Returns the stored Odds_Value string.
    """
    value = {
        "availableToBack": draw(st.lists(_valid_entry, min_size=0, max_size=6)),
        "availableToLay": draw(st.lists(_valid_entry, min_size=0, max_size=6)),
    }
    if draw(st.booleans()):
        value["tradedVolume"] = draw(_valid_ladder)
    return str(value)


class TestProperty2BestPriceCorrectness:
    """Feature: betfair-research-dataset.

    Property 2: Best-price correctness.
    """

    # Feature: betfair-research-dataset, Property 2: For any valid generated Odds_Value, best_back equals the maximum availableToBack price and best_lay equals the minimum availableToLay price regardless of the order entries appear in the ladder; and for any Odds_Value with an empty ladder on a side, the corresponding best price is null and that side's depth is an empty sequence  # noqa: E501
    @given(stored=shuffled_odds_values())
    @settings(max_examples=200)
    def test_best_prices_and_depth_regardless_of_order(self, stored):
        """
        For any valid generated Odds_Value v (with shuffled, sometimes-empty
        ladders), structured_odds(v) is not None, and:

        - best_back == max of all availableToBack prices (or None if empty);
          best_lay == min of all availableToLay prices (or None if empty) --
          holding regardless of entry order.
        - back_depth / lay_depth are all non-best entries best-to-worst (back
          descending price, lay ascending price), preserving price + size, with
          the single best entry excluded (empty for a single-entry ladder).
        - An empty ladder on a side yields a null best and an empty depth.

        parse_odds(v) supplies the ground-truth ladders.

        **Validates: Requirements 1.3, 1.4, 1.5, 1.6**
        """
        so = structured_odds(stored)
        assert so is not None

        original = parse_odds(stored)
        assert original is not None
        back_ladder = original["availableToBack"]
        lay_ladder = original["availableToLay"]

        back_prices = [entry["price"] for entry in back_ladder]
        lay_prices = [entry["price"] for entry in lay_ladder]

        # --- Best prices equal max/min regardless of order (Req 1.3, 1.4) ---
        if back_prices:
            assert so.best_back == max(back_prices)
        else:
            # Empty ladder -> null best + empty depth (Req 1.6).
            assert so.best_back is None
            assert so.back_depth == ()

        if lay_prices:
            assert so.best_lay == min(lay_prices)
        else:
            # Empty ladder -> null best + empty depth (Req 1.6).
            assert so.best_lay is None
            assert so.lay_depth == ()

        # --- Depth = non-best entries, best-to-worst, best excluded (Req 1.5) ---
        # Ground truth: sort each side best-to-worst (back descending price,
        # lay ascending price), coerce to float, and drop the single best entry.
        expected_back_depth = tuple(
            PriceLevel(price=float(entry["price"]), size=float(entry["size"]))
            for entry in sorted(back_ladder, key=lambda e: e["price"], reverse=True)[1:]
        )
        expected_lay_depth = tuple(
            PriceLevel(price=float(entry["price"]), size=float(entry["size"]))
            for entry in sorted(lay_ladder, key=lambda e: e["price"])[1:]
        )
        assert so.back_depth == expected_back_depth
        assert so.lay_depth == expected_lay_depth

        # A single-entry ladder has no non-best entries, so its depth is empty.
        if len(back_ladder) == 1:
            assert so.back_depth == ()
        if len(lay_ladder) == 1:
            assert so.lay_depth == ()

    # Feature: betfair-research-dataset, Property 2: For any valid generated Odds_Value, best_back equals the maximum availableToBack price and best_lay equals the minimum availableToLay price regardless of the order entries appear in the ladder; and for any Odds_Value with an empty ladder on a side, the corresponding best price is null and that side's depth is an empty sequence  # noqa: E501
    def test_representative_best_price_examples(self):
        """Concrete shuffled and empty ladders yield the expected best + depth.

        **Validates: Requirements 1.3, 1.4, 1.5, 1.6**
        """
        # Shuffled (not best-first) back ladder: best_back is the max price and
        # the depth is the remaining entries, descending.
        shuffled_back = str(
            {
                "availableToBack": [
                    {"price": 1.65, "size": 677.2},
                    {"price": 1.66, "size": 40.7},
                    {"price": 1.64, "size": 12.0},
                ],
                "availableToLay": [
                    {"price": 1.69, "size": 3.0},
                    {"price": 1.67, "size": 10.0},
                ],
            }
        )
        so = structured_odds(shuffled_back)
        assert so is not None
        assert so.best_back == 1.66
        assert so.back_depth == (
            PriceLevel(price=1.65, size=677.2),
            PriceLevel(price=1.64, size=12.0),
        )
        assert so.best_lay == 1.67
        assert so.lay_depth == (PriceLevel(price=1.69, size=3.0),)

        # Empty back ladder -> null best_back + empty back_depth; populated lay.
        empty_back = str(
            {
                "availableToBack": [],
                "availableToLay": [{"price": 2.02, "size": 3.0}],
            }
        )
        so_empty_back = structured_odds(empty_back)
        assert so_empty_back is not None
        assert so_empty_back.best_back is None
        assert so_empty_back.back_depth == ()
        assert so_empty_back.best_lay == 2.02
        assert so_empty_back.lay_depth == ()

        # Both ladders empty -> both bests null, both depths empty (Req 1.6).
        both_empty = str({"availableToBack": [], "availableToLay": []})
        so_both_empty = structured_odds(both_empty)
        assert so_both_empty is not None
        assert so_both_empty.best_back is None
        assert so_both_empty.best_lay is None
        assert so_both_empty.back_depth == ()
        assert so_both_empty.lay_depth == ()


# --- Strategies for Property 6 (record determinism and ordering) --------------
#
# Property 6 asserts build_records is deterministic (identical input yields
# identical output, regardless of input row order) and that each runner's
# records come out ordered by ascending Capture_Timestamp with ties broken by
# ascending runner_id -- i.e. the whole records tuple matches the design's
# stable key (market_id, timestamp, runner_id).
#
# To exercise multi-runner, multi-timestamp AND duplicate-timestamp cases, the
# generator draws Market_Rows from SMALL fixed pools of market_ids, runner_ids
# and timestamps, so collisions on timestamp (and on the full
# (market_id, runner_id, timestamp) key) occur often. Each row's odds reuses the
# Property 1 valid_odds_values() strategy so every row parses (an unparseable
# row would just be excluded, which Property 7 covers -- here we want rows that
# survive into records so ordering is observable).

# Small pools so duplicate timestamps and multi-runner/multi-timestamp groupings
# arise naturally.
_MARKET_IDS = ("1.100", "1.200")
_RUNNER_IDS = ("10", "2", "333", "40")
_TIMESTAMPS = (
    datetime(2026, 1, 1, 12, 0, 0),
    datetime(2026, 1, 1, 12, 5, 0),
    datetime(2026, 1, 1, 12, 10, 0),
)

_market_id_strategy = st.sampled_from(_MARKET_IDS)
_runner_id_strategy = st.sampled_from(_RUNNER_IDS)
_timestamp_strategy = st.sampled_from(_TIMESTAMPS)


@st.composite
def market_rows_and_targets(draw):
    """Generate a list of MarketRow tuples plus a matching targets_by_market.

    Each MarketRow is ``(timestamp, market_id, runner_id, odds)`` drawn from the
    small fixed pools of timestamps / market_ids / runner_ids above (so
    duplicate timestamps and multi-runner, multi-timestamp groups occur often),
    with a VALID odds string from the Property 1 ``valid_odds_values()``
    strategy so every row survives into records. The matching
    ``targets_by_market`` dict covers each market_id that appears, with a
    ``runner_ids`` string listing the runner pool (and optionally "The Draw") so
    identity resolution has something to work with -- though Property 6 only
    cares about ordering/determinism, not identity.

    Returns a ``(market_rows, targets_by_market)`` tuple.
    """
    # Draw rows keyed uniquely on (market_id, runner_id, timestamp): the design
    # produces one record per distinct such key (Req 3.1), and the output order
    # is the stable key (market_id, timestamp, runner_id). Two rows colliding on
    # the FULL key but differing in odds would make "same output regardless of
    # input order" genuinely order-dependent (the sort cannot distinguish them),
    # which is outside Property 6's scope. Keying the rows uniquely keeps the
    # ordering total so the determinism claim is well-defined, while still
    # giving duplicate TIMESTAMPS across different runners/markets (the tie-break
    # case Property 6 targets) and multi-runner, multi-timestamp groups.
    keys = draw(
        st.lists(
            st.tuples(_market_id_strategy, _runner_id_strategy, _timestamp_strategy),
            min_size=1,
            max_size=20,
            unique=True,
        )
    )
    rows = [(timestamp, market_id, runner_id, draw(valid_odds_values())) for market_id, runner_id, timestamp in keys]

    # Build a TargetRow for every market_id that appears, so each row's market
    # has metadata. runner_ids lists the runner pool as "id-name" entries, with
    # "The Draw" optionally appended under the configured draw name.
    include_draw = draw(st.booleans())
    runner_entries = [f"{rid}-Runner {rid}" for rid in _RUNNER_IDS]
    if include_draw:
        runner_entries.append(f"999-{DEFAULT_DRAW_NAME}")
    runner_ids_str = "|".join(runner_entries)

    markets_present = {market_id for _, market_id, _, _ in rows}
    targets_by_market = {
        market_id: (
            f"target-{market_id}",  # target_id
            f"event-{market_id}",  # event_id
            market_id,  # market_id
            runner_ids_str,  # runner_ids
            datetime(2026, 1, 1, 12, 7, 0),  # start_time
            "OPEN",  # status
            datetime(2026, 1, 1, 12, 7, 0),  # last_updated
            "",  # notes
        )
        for market_id in markets_present
    }
    return rows, targets_by_market


def _order_key(record):
    """The design's stable ordering key: (market_id, timestamp, runner_id)."""
    return (record.market_id, record.timestamp, record.runner_id)


class TestProperty6RecordDeterminismAndOrdering:
    """Feature: betfair-research-dataset.

    Property 6: Record determinism and ordering.
    """

    # Feature: betfair-research-dataset, Property 6: For any set of Market_Rows and Target metadata, build_records is deterministic (identical input yields identical output), and within each Runner the records are ordered by ascending Capture_Timestamp with ties broken by ascending runner_id  # noqa: E501
    @given(data=market_rows_and_targets())
    @settings(max_examples=200)
    def test_records_are_deterministic_and_ordered(self, data):
        """
        For any generated Market_Rows + Target metadata:

        - Determinism (Req 3.1): calling build_records twice with the SAME
          input yields equal records tuples and equal counts; and shuffling the
          input rows yields the SAME output ordering (the stable sort makes the
          result independent of input order).
        - Ordering (Req 3.6): within each (market_id, runner_id) group the
          records are in non-decreasing timestamp order; the full records tuple
          equals itself sorted by (market_id, timestamp, runner_id); and any two
          consecutive records sharing market_id and timestamp have
          non-decreasing runner_id.

        **Validates: Requirements 3.1, 3.6**
        """
        rows, targets = data

        result = build_records(rows, targets)
        assert isinstance(result, TransformResult)

        # --- Determinism: identical input -> identical output (Req 3.1) -------
        result_again = build_records(rows, targets)
        assert result.records == result_again.records
        assert result.unparseable_excluded == result_again.unparseable_excluded
        assert result.unresolved_runners == result_again.unresolved_runners

        # --- Determinism regardless of input order (exercises stable sort) ----
        # Reverse the input rows: a different input order that must still yield
        # the SAME output ordering because the sort key is total/stable.
        shuffled_rows = list(reversed(rows))
        shuffled_result = build_records(shuffled_rows, targets)
        assert shuffled_result.records == result.records

        records = result.records

        # --- Ordering: full tuple equals itself sorted by the stable key -----
        # (Req 3.6: ascending timestamp, ties broken by ascending runner_id,
        # grouped per market.)
        assert list(records) == sorted(records, key=_order_key)

        # --- Per-(market_id, runner_id) group: non-decreasing timestamp -------
        last_ts_by_group: dict[tuple[str, str], datetime] = {}
        for record in records:
            group = (record.market_id, record.runner_id)
            previous_ts = last_ts_by_group.get(group)
            if previous_ts is not None:
                assert record.timestamp >= previous_ts
            last_ts_by_group[group] = record.timestamp

        # --- Consecutive records sharing market_id+timestamp: runner_id up ----
        for earlier, later in zip(records, records[1:], strict=False):
            if earlier.market_id == later.market_id and earlier.timestamp == later.timestamp:
                assert earlier.runner_id <= later.runner_id


# --- Strategies for Property 7 (exclusion and unresolved accounting) ----------
#
# Property 7 asserts two accounting identities over build_records:
#   - produced records + unparseable-excluded count == number of distinct
#     (market_id, runner_id, timestamp) input observations (Req 3.5);
#   - unresolved_runners == number of produced records whose runner_id had no
#     matching Runner_Identity in that market's Target (Req 3.8).
#
# To exercise both, this generator mixes:
#   - VALID odds (reusing the Property 1 valid_odds_values() strategy, which
#     structured_odds always accepts) and UNPARSEABLE odds (strings that
#     structured_odds returns None for -- a non-mapping, a mapping missing a
#     ladder, or a ladder with a negative price), so unparseable_excluded is
#     driven and the ground truth is computed by calling structured_odds on each
#     row's odds directly.
#   - runner_ids that ARE in the market's Target identities and runner_ids that
#     are NOT, by generating the Target's runner_ids string from a KNOWN SUBSET
#     of the runner pool (the omitted runners drive unresolved_runners).
#
# Rows are kept unique on (market_id, runner_id, timestamp) -- exactly as
# Property 6 does -- so every input row is itself a distinct observation and the
# "distinct observations" count is simply the number of input rows.

# Odds strings structured_odds rejects (returns None for), per Req 1.7 / 1.8:
# a non-mapping string, a mapping missing both ladders, a mapping missing one
# ladder, and a ladder carrying a negative price.
_UNPARSEABLE_ODDS = (
    "not a dict",
    "{}",
    str({"availableToBack": [{"price": 2.0, "size": 5.0}]}),  # missing availableToLay
    str({"availableToLay": [{"price": 2.0, "size": 5.0}]}),  # missing availableToBack
    str(
        {
            "availableToBack": [{"price": -1.0, "size": 5.0}],  # negative price -> None
            "availableToLay": [{"price": 2.0, "size": 3.0}],
        }
    ),
)
_unparseable_odds_strategy = st.sampled_from(_UNPARSEABLE_ODDS)


@st.composite
def mixed_rows_and_targets(draw):
    """Generate Market_Rows mixing parseable/unparseable odds + resolved/unresolved runners.

    Each MarketRow is ``(timestamp, market_id, runner_id, odds)`` drawn from the
    small fixed pools of timestamps / market_ids / runner_ids reused from
    Property 6 (so the key space is shared), keyed UNIQUELY on
    ``(market_id, runner_id, timestamp)`` so every input row is a distinct
    observation. Each row's odds is EITHER a VALID Odds_Value (from the
    Property 1 ``valid_odds_values()`` strategy) OR an UNPARSEABLE string (from
    ``_UNPARSEABLE_ODDS``) chosen per row, so ``unparseable_excluded`` is driven.

    For every market_id that appears, a Target is built whose ``runner_ids``
    string lists only a KNOWN SUBSET of the runner pool: the subset is drawn
    per market, so some rows reference a ``runner_id`` present in the Target
    (resolved) and some reference a ``runner_id`` the Target omits (unresolved,
    driving ``unresolved_runners``). The Draw name is never used here, so no
    runner from the pool collides with it.

    Returns a ``(market_rows, targets_by_market)`` tuple.
    """
    keys = draw(
        st.lists(
            st.tuples(_market_id_strategy, _runner_id_strategy, _timestamp_strategy),
            min_size=1,
            max_size=24,
            unique=True,
        )
    )

    # Per row, choose a valid or an unparseable odds string so the mix of
    # parseable/unparseable observations is driven directly.
    rows = []
    for market_id, runner_id, timestamp in keys:
        if draw(st.booleans()):
            odds = draw(valid_odds_values())
        else:
            odds = draw(_unparseable_odds_strategy)
        rows.append((timestamp, market_id, runner_id, odds))

    # For each market, the Target lists only a drawn SUBSET of the runner pool,
    # so runners outside that subset are unresolved. ``min_size=0`` lets a
    # market resolve no runners at all; the full pool resolves every runner.
    markets_present = {market_id for _, market_id, _, _ in rows}
    targets_by_market = {}
    for market_id in markets_present:
        resolved_runner_ids = draw(
            st.lists(st.sampled_from(_RUNNER_IDS), min_size=0, max_size=len(_RUNNER_IDS), unique=True)
        )
        runner_ids_str = "|".join(f"{rid}-Runner {rid}" for rid in resolved_runner_ids)
        targets_by_market[market_id] = (
            f"target-{market_id}",  # target_id
            f"event-{market_id}",  # event_id
            market_id,  # market_id
            runner_ids_str,  # runner_ids
            datetime(2026, 1, 1, 12, 7, 0),  # start_time
            "OPEN",  # status
            datetime(2026, 1, 1, 12, 7, 0),  # last_updated
            "",  # notes
        )

    return rows, targets_by_market


class TestProperty7ExclusionAndUnresolvedAccounting:
    """Feature: betfair-research-dataset.

    Property 7: Exclusion and unresolved accounting.
    """

    # Feature: betfair-research-dataset, Property 7: For any set of Market_Rows, the number of Dataset_Records produced plus the number of Market_Rows counted as unparseable equals the number of distinct (market_id, runner_id, Capture_Timestamp) observations in the input, and the unresolved-Runner_Identity count equals the number of produced records whose runner_id had no matching Runner_Identity  # noqa: E501
    @given(data=mixed_rows_and_targets())
    @settings(max_examples=200)
    def test_exclusion_and_unresolved_counts_reconcile(self, data):
        """
        For any generated mix of parseable/unparseable Market_Rows and
        resolved/unresolved runners:

        - Req 3.5: ``unparseable_excluded`` equals the number of input rows
          whose odds is unparseable (ground truth from calling
          ``structured_odds`` on each row and counting ``None``); and
          ``len(records) + unparseable_excluded`` equals the total number of
          (unique) input rows -- the distinct observation count.
        - Req 3.8: ``unresolved_runners`` equals the number of PRODUCED records
          whose ``runner_id`` is absent from that market's Target identities
          (ground truth from ``parse_runner_ids`` on each Target). Every such
          unresolved record has ``runner_name is None`` and
          ``outcome_role is None``; every produced record whose ``runner_id``
          IS in the Target has a non-None ``runner_name``.

        **Validates: Requirements 3.5, 3.8**
        """
        rows, targets = data

        result = build_records(rows, targets)
        assert isinstance(result, TransformResult)

        # --- Ground truth: unparseable rows via the real parse path ----------
        expected_unparseable = sum(1 for _, _, _, odds in rows if structured_odds(odds) is None)

        # Req 3.5: excluded count matches, and produced + excluded == distinct
        # observations. Rows are keyed uniquely, so each input row is one
        # distinct (market_id, runner_id, timestamp) observation.
        assert result.unparseable_excluded == expected_unparseable
        assert len(result.records) + result.unparseable_excluded == len(rows)

        # --- Ground truth: resolved runner_ids per market from the Target ----
        resolved_ids_by_market = {
            market_id: {identity.runner_id for identity in parse_runner_ids(target[3])}
            for market_id, target in targets.items()
        }

        # Req 3.8: unresolved_runners == produced records whose runner_id has no
        # identity in its market's Target.
        expected_unresolved = sum(
            1
            for record in result.records
            if record.runner_id not in resolved_ids_by_market.get(record.market_id, set())
        )
        assert result.unresolved_runners == expected_unresolved

        # Per-record identity: unresolved -> null name + null role; resolved ->
        # non-null name.
        for record in result.records:
            resolved_ids = resolved_ids_by_market.get(record.market_id, set())
            if record.runner_id in resolved_ids:
                assert record.runner_name is not None
            else:
                assert record.runner_name is None
                assert record.outcome_role is None
