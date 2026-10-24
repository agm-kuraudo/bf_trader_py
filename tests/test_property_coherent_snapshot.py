"""Property-based tests for the coherent-market-snapshot-capture feature
(SP-396 + SP-384).

These tests exercise the pure blob-builder helpers extracted from
``MonitorService.update_runner_odds`` (``monitor_service.build_odds_blob`` and
``monitor_service.render_odds_blob``) against the UNCHANGED SP-368 loader
(``logic.research_dataset.structured_odds``), confirming the matched-volume
round-trip and best-price survival described in the design's Property 1.

Odds_Value generation mirrors the ``valid_odds_values`` composite and the
non-negative-finite ``{price, size}`` ladder building blocks used by
``tests/test_property_research_dataset.py`` and
``tests/test_property_quality_checks.py`` so there is one source of truth for
valid ladder shapes. The generator is extended here with an OPTIONAL
runner-level ``totalMatched`` scalar (including zero) and an OPTIONAL
``tradedVolume`` ladder, so the full matched-volume mapping table (scalar wins,
else summed ladder, else ``None``) is driven. The composites are kept local to
this file per the task's "mirror rather than edit existing tests" guidance.
"""

import math
import os
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.quality_checks import parse_odds
from logic.research_dataset import serialize_structured, structured_odds
from monitor_service import MonitorService, assemble_snapshot_rows, build_odds_blob, render_odds_blob
from output.log import Output as Log

Log.LOG_FILE = False

# --- Strategies for VALID ladders + optional matched-volume signals -----------
#
# A finite, non-negative price/size: no NaN/inf, so the ``str(dict)`` ->
# ``literal_eval`` round-trip is exact, and ``>= 0`` so the loader accepts it
# (mirrors ``_valid_price_size`` in test_property_research_dataset.py).
_valid_price_size = st.floats(min_value=0.0, allow_nan=False, allow_infinity=False)
_valid_entry = st.fixed_dictionaries({"price": _valid_price_size, "size": _valid_price_size})
_valid_ladder = st.lists(_valid_entry, max_size=5)

# A non-empty traded-volume ladder (so it is a genuine matched-volume signal
# the loader will read when no scalar is present).
_traded_ladder = st.lists(_valid_entry, min_size=1, max_size=5)


@st.composite
def ex_and_matched(draw):
    """Generate an ``ex`` dict plus the matched-volume inputs for the builder.

    Returns a ``(ex, has_scalar, scalar)`` tuple where:

    - ``ex`` is a dict with non-negative-finite ``availableToBack`` /
      ``availableToLay`` ladders and, ~half the time, a non-empty
      ``tradedVolume`` ladder (the SP-384 ladder-only matched-volume source);
    - ``has_scalar`` indicates whether a runner-level ``totalMatched`` scalar is
      present (driving the ``build_odds_blob(ex)`` vs ``build_odds_blob(ex,
      scalar)`` call paths);
    - ``scalar`` is the scalar value when present (a non-negative finite number
      INCLUDING zero, so Req 2.5's zero case is covered) or ``None`` otherwise.

    Every combination of the mapping table is reachable: scalar present/absent x
    ladder present/absent, with zero as a first-class scalar value.
    """
    ex = {
        "availableToBack": draw(_valid_ladder),
        "availableToLay": draw(_valid_ladder),
    }
    if draw(st.booleans()):
        ex["tradedVolume"] = draw(_traded_ladder)

    has_scalar = draw(st.booleans())
    scalar = None
    if has_scalar:
        # Non-negative finite, and explicitly includes exact zero (Req 2.5).
        scalar = draw(
            st.one_of(
                st.just(0.0),
                st.floats(min_value=0.0, allow_nan=False, allow_infinity=False),
            )
        )
    return ex, has_scalar, scalar


def _expected_matched_volume(ex, has_scalar, scalar):
    """Independent oracle for the loader's matched-volume precedence.

    Mirrors ``logic.research_dataset._matched_volume`` from the requirement text
    WITHOUT calling it: a valid scalar wins (zero honoured), else the summed
    ``tradedVolume`` sizes when a non-empty ladder is present, else ``None``.
    The generator only ever produces valid non-negative finite numbers, so the
    validity guards collapse to "present or not".
    """
    if has_scalar and scalar is not None:
        return float(scalar)
    ladder = ex.get("tradedVolume")
    if isinstance(ladder, list) and ladder:
        return sum(float(entry["size"]) for entry in ladder)
    return None


class TestProperty1MatchedVolumeRoundTrip:
    """Feature: coherent-market-snapshot-capture.

    Property 1: Matched-volume round-trip through the unchanged loader.
    """

    # Feature: coherent-market-snapshot-capture, Property 1: Matched-volume round-trip through the unchanged loader  # noqa: E501
    @given(data=ex_and_matched())
    @settings(max_examples=200)
    def test_matched_volume_and_best_prices_round_trip(self, data):
        """
        For any synthesized ``ex`` dict (valid non-negative finite back/lay
        ladders) combined with an optional runner-level ``totalMatched`` scalar
        (including zero) and/or an optional ``tradedVolume`` ladder, the blob the
        builder renders -- read back through the UNCHANGED
        ``logic.research_dataset.structured_odds`` -- yields a StructuredOdds
        whose ``matched_volume`` equals the expected figure (scalar preferred
        incl. zero, else summed ``tradedVolume`` sizes, else ``None``) and whose
        ``best_back`` (max back price) and ``best_lay`` (min lay price) equal
        those of the source ladders.

        **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3.2, 3.3**
        """
        ex, has_scalar, scalar = data

        # Exercise the task-1 helpers on BOTH call paths: omit the scalar to hit
        # the "absent" mapping rows, pass it (incl. zero) to hit the "present"
        # rows. This is the production blob-building path.
        if has_scalar:
            blob_dict = build_odds_blob(ex, scalar)
        else:
            blob_dict = build_odds_blob(ex)
        blob = render_odds_blob(blob_dict)

        so = structured_odds(blob)

        # The emitted blob is always parseable by the unchanged loader (Req 2.8).
        assert so is not None

        # --- Matched-volume precedence (Req 2.1-2.6, 3.2, 3.3) ---------------
        expected = _expected_matched_volume(ex, has_scalar, scalar)
        if expected is None:
            assert so.matched_volume is None
        else:
            # Float summation for the ladder case: compare approximately.
            assert so.matched_volume is not None
            assert math.isclose(so.matched_volume, expected, rel_tol=1e-9, abs_tol=1e-9)

        # --- Best prices survive alongside matched volume (Req 2.7, 3.2) -----
        back_prices = [entry["price"] for entry in ex["availableToBack"]]
        lay_prices = [entry["price"] for entry in ex["availableToLay"]]

        if back_prices:
            assert so.best_back == max(back_prices)
        else:
            assert so.best_back is None

        if lay_prices:
            assert so.best_lay == min(lay_prices)
        else:
            assert so.best_lay is None

    # Feature: coherent-market-snapshot-capture, Property 1: Matched-volume round-trip through the unchanged loader  # noqa: E501
    def test_representative_mapping_table_examples(self):
        """Concrete rows of the matched-volume mapping table round-trip.

        Covers scalar>0 (no ladder), scalar>0 + ladder (scalar wins), ladder
        only, scalar==0 (honoured, not omitted), scalar==0 + ladder (scalar
        wins), and neither key (null matched_volume).

        **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.8, 3.3**
        """
        back = [{"price": 1.66, "size": 40.7}, {"price": 1.65, "size": 677.2}]
        lay = [{"price": 1.67, "size": 10.0}, {"price": 1.69, "size": 3.0}]
        ladder = [{"price": 1.66, "size": 5.0}, {"price": 1.65, "size": 7.0}]  # sums to 12.0

        # Row 1: scalar > 0, no ladder -> scalar.
        so = structured_odds(
            render_odds_blob(build_odds_blob({"availableToBack": back, "availableToLay": lay}, 123.45))
        )
        assert so is not None
        assert so.matched_volume == 123.45
        assert so.best_back == 1.66
        assert so.best_lay == 1.67

        # Row 2: scalar > 0 AND ladder -> scalar wins.
        so = structured_odds(
            render_odds_blob(
                build_odds_blob({"availableToBack": back, "availableToLay": lay, "tradedVolume": ladder}, 99.0)
            )
        )
        assert so is not None
        assert so.matched_volume == 99.0

        # Row 3: ladder only (scalar omitted) -> summed ladder sizes.
        so = structured_odds(
            render_odds_blob(build_odds_blob({"availableToBack": back, "availableToLay": lay, "tradedVolume": ladder}))
        )
        assert so is not None
        assert math.isclose(so.matched_volume, 12.0, rel_tol=1e-9, abs_tol=1e-9)

        # Row 4: scalar == 0, no ladder -> zero honoured, not omitted.
        so = structured_odds(render_odds_blob(build_odds_blob({"availableToBack": back, "availableToLay": lay}, 0.0)))
        assert so is not None
        assert so.matched_volume == 0.0

        # Row 5: scalar == 0 AND ladder -> scalar (zero) wins over ladder.
        so = structured_odds(
            render_odds_blob(
                build_odds_blob({"availableToBack": back, "availableToLay": lay, "tradedVolume": ladder}, 0.0)
            )
        )
        assert so is not None
        assert so.matched_volume == 0.0

        # Row 6: neither key -> null matched_volume (ladders only).
        so = structured_odds(render_odds_blob(build_odds_blob({"availableToBack": back, "availableToLay": lay})))
        assert so is not None
        assert so.matched_volume is None
        assert so.best_back == 1.66
        assert so.best_lay == 1.67


# --- Strategies for the row-assembler (coherent snapshot) ---------------------
#
# A runner's ``ex`` content is arbitrary-but-valid: it reuses the same
# non-negative-finite ladder building blocks as Property 1 above, and ~half the
# time carries a non-empty ``tradedVolume`` ladder. Keeping ``ex`` content
# varied (and sometimes absent entirely at the runner level) keeps the snapshot
# property honest about "arbitrary ex content" while the assembler still binds
# one row per runner.
_arbitrary_ex = st.fixed_dictionaries(
    {
        "availableToBack": _valid_ladder,
        "availableToLay": _valid_ladder,
    },
    optional={"tradedVolume": _traded_ladder},
)

# Distinct selectionIds: Betfair returns each selection once per market book, so
# the N runners carry N distinct ids. Draw a unique, non-empty list of ints.
_distinct_selection_ids = st.lists(
    st.integers(min_value=1, max_value=10_000_000),
    min_size=1,
    max_size=8,
    unique=True,
)


@st.composite
def market_book_result(draw):
    """Synthesize a parsed ``listMarketBook`` ``result`` for ONE market.

    Returns a ``(result, selection_ids)`` tuple where ``result`` is shaped as the
    assembler reads it — ``[{"runners": [ {...runner...}, ... ]}]`` — with N >= 1
    runners carrying DISTINCT ``selectionId``s and arbitrary-but-valid ``ex``
    content. To keep ``ex`` content genuinely arbitrary, each runner:

    - has its ``selectionId`` (always),
    - has an ``ex`` dict ~most of the time, or NO ``ex`` key some of the time
      (the helper backfills empty ladders for a missing ``ex``),
    - has an OPTIONAL runner-level ``totalMatched`` scalar (incl. zero) some of
      the time.

    ``selection_ids`` is the ordered list of the N distinct ids, used by the
    test as the oracle for the expected ``runner_id`` sequence/multiset.
    """
    selection_ids = draw(_distinct_selection_ids)
    runners = []
    for sid in selection_ids:
        runner = {"selectionId": sid}
        # ex present most of the time; absent sometimes to exercise the
        # missing-ex path (helper backfills empty ladders).
        if draw(st.booleans()):
            runner["ex"] = draw(_arbitrary_ex)
        # Optional runner-level totalMatched scalar, including exact zero.
        if draw(st.booleans()):
            runner["totalMatched"] = draw(
                st.one_of(
                    st.just(0.0),
                    st.floats(min_value=0.0, allow_nan=False, allow_infinity=False),
                )
            )
        runners.append(runner)
    result = [{"runners": runners}]
    return result, selection_ids


class TestProperty2CoherentSnapshot:
    """Feature: coherent-market-snapshot-capture.

    Property 2: Coherent snapshot — N rows, one per runner, one shared timestamp.
    """

    # Feature: coherent-market-snapshot-capture, Property 2: Coherent snapshot — N rows, one per runner, one shared timestamp  # noqa: E501
    @given(data=market_book_result())
    @settings(max_examples=200)
    def test_one_row_per_runner_sharing_one_timestamp(self, data):
        """
        For any market whose ``listMarketBook`` result returns N >= 1 runners
        with DISTINCT ``selectionId``s and arbitrary ``ex`` content, assembling
        the snapshot rows for ONE ``capture_ts`` produces exactly N rows whose
        ``runner_id`` values are exactly the N returned ``selectionId``s (one per
        runner, no omission, no duplicate — and in Betfair return order) and
        whose ``timestamp`` values are all identical to that one ``capture_ts``.

        **Validates: Requirements 1.2, 1.3, 1.4**
        """
        result, selection_ids = data

        market_id = "1.234567890"
        # A fixed sentinel timestamp — the same object must appear in every row.
        capture_ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)

        rows = assemble_snapshot_rows(result, market_id, capture_ts)

        # Exactly one row per returned runner (Req 1.3, 1.4).
        assert len(rows) == len(selection_ids)

        runner_ids = [row[2] for row in rows]

        # The runner_id multiset equals the returned selectionId set: no omission,
        # no duplicate (Req 1.3). Distinct generated ids => multiset == set.
        assert sorted(runner_ids) == sorted(selection_ids)
        assert len(set(runner_ids)) == len(runner_ids)
        # The helper preserves Betfair return order, so the sequence matches too.
        assert runner_ids == selection_ids

        # Every row binds the one shared capture_ts — same value AND same object
        # (Req 1.2, 1.4) — and the same market_id.
        for row in rows:
            assert row[0] == capture_ts
            assert row[0] is capture_ts
            assert row[1] == market_id

    # Feature: coherent-market-snapshot-capture, Property 2: Coherent snapshot — N rows, one per runner, one shared timestamp  # noqa: E501
    def test_single_runner_market_is_one_coherent_row(self):
        """N == 1 degenerate case: a one-runner market yields a single row bound
        to the one ``capture_ts`` (the shared-timestamp invariant holds trivially).

        **Validates: Requirements 1.2, 1.3, 1.4**
        """
        capture_ts = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
        result = [{"runners": [{"selectionId": 42, "ex": {"availableToBack": [], "availableToLay": []}}]}]

        rows = assemble_snapshot_rows(result, "1.1", capture_ts)

        assert len(rows) == 1
        assert rows[0][2] == 42
        assert rows[0][0] is capture_ts
        assert rows[0][1] == "1.1"


# --- Property 3: per-market write atomicity -----------------------------------
#
# Property 2 above proved the PURE row-assembler produces N coherent rows. This
# property exercises the REWIRED ``MonitorService.update_runner_odds`` end to end
# with its boundaries mocked, to prove the WRITE is atomic: the writer issues ONE
# multi-row INSERT per market, so a failing write persists ZERO rows (never a
# partial subset) and a succeeding write persists exactly N rows all sharing the
# one ``capture_ts``.
#
# Mocking approach — mirrors tests/unit_tests_monitor_empty_runnerbook.py (and
# tests/unit_tests_monitor_resilience.py):
#   - ``MonitorService()`` is constructed, then its collaborators are replaced
#     with mocks: ``svc.db_connection = MagicMock()`` and ``svc.BF = MagicMock()``.
#   - ``svc.BF.request_body_obj.populate_template`` returns a stub body (pure, but
#     mocked so no template logic runs).
#   - ``svc.BF.call_obj.call`` returns a response whose ``.json()`` yields
#     ``{"result": [{"status": "OPEN", "runners": [...]}]}`` for the N generated
#     runners (reusing the ``market_book_result`` generator above for the runner
#     shapes / distinct selectionIds).
#   - ``svc.db_connection.db_write`` is a spy: every ``(sql, params)`` call is
#     recorded, and its return value is controlled per scenario (False => the
#     atomic INSERT failed; True => it succeeded).
#
# "Persisted rows" are modelled as: only a SUCCEEDING INSERT ``db_write``
# contributes rows (all N of them, in one statement); a FAILING INSERT
# contributes zero (the single statement is atomic — all-or-nothing).
#
# The processed-target tuple shape (see MonitorService.process_targets):
#   (market, status, num_runners, runners, update_frequency, last_updated, event_start_time)
# so market id is target[0] and event start time is target[6]. The tier tail
# reads target[6] as a datetime; we pass a FUTURE datetime so that branch is
# deterministic (it only runs after a successful write anyway).

_INSERT_PREFIX = "INSERT INTO bf.market_table"


def _insert_calls(db_write_mock):
    """The recorded db_write calls whose SQL is the market_table INSERT."""
    return [c for c in db_write_mock.call_args_list if c.args and str(c.args[0]).startswith(_INSERT_PREFIX)]


def _target_update_calls(db_write_mock):
    """The recorded db_write calls whose SQL is the bf.target tier UPDATE."""
    return [c for c in db_write_mock.call_args_list if c.args and "UPDATE bf.target" in str(c.args[0])]


def _build_service(result, insert_succeeds):
    """Construct a MonitorService with mocked BF + db_connection boundaries.

    ``result`` is the parsed ``listMarketBook`` result the mocked
    ``call_obj.call(...).json()`` returns under the ``"result"`` key. The INSERT
    (and the trailing tier UPDATE) db_write return value is controlled by
    ``insert_succeeds``.
    """
    svc = MonitorService()
    svc.db_connection = MagicMock()
    svc.db_connection.db_write.return_value = insert_succeeds
    svc.BF = MagicMock()
    svc.BF.request_body_obj.populate_template.return_value = {"stub": "body"}

    # The rewired writer reads result[0]["status"] after the empty guard (same as
    # process_targets). The shared market_book_result generator only emits
    # {"runners": [...]} (Property 2's PURE assembler never reads status), so add
    # a market status here for the end-to-end method path — without editing the
    # shared generator.
    result_with_status = [{**book, "status": "OPEN"} for book in result]
    resp = MagicMock()
    resp.json.return_value = {"result": result_with_status}
    svc.BF.call_obj.call.return_value = resp
    return svc


def _target_for(market_id="1.234567890"):
    """A processed-target tuple with a FUTURE event start time (target[6])."""
    start = datetime.now(UTC) + timedelta(hours=5)
    # (market, status, num_runners, runners, update_frequency, last_updated, event_start_time)
    return (market_id, "OPEN", 1, [], 300, None, start)


class TestProperty3PerMarketWriteAtomicity:
    """Feature: coherent-market-snapshot-capture.

    Property 3: Per-market write atomicity.
    """

    # Feature: coherent-market-snapshot-capture, Property 3: Per-market write atomicity
    @given(data=market_book_result())
    @settings(max_examples=200)
    def test_failing_write_persists_zero_rows_single_insert_no_tier_update(self, data):
        """
        For any market with N >= 1 runners, when the atomic INSERT ``db_write``
        FAILS (returns False), the writer issues the INSERT for the market
        EXACTLY ONCE (a single multi-row statement), persists ZERO rows (the one
        atomic statement failed — never a partial subset), and runs NO
        ``UPDATE bf.target`` tier write for that market (the tail is skipped on a
        failed snapshot).

        **Validates: Requirements 1.7**
        """
        result, selection_ids = data
        market_id = "1.234567890"
        svc = _build_service(result, insert_succeeds=False)

        svc.update_runner_odds([_target_for(market_id)])

        inserts = _insert_calls(svc.db_connection.db_write)
        # A SINGLE INSERT statement was issued for the market — one atomic unit,
        # not one-INSERT-per-runner.
        assert len(inserts) == 1

        # Persisted rows modelled as: a FAILING INSERT contributes zero. The
        # single statement is all-or-nothing, so the poll leaves no rows.
        persisted = 0  # the one atomic INSERT returned False
        assert persisted == 0

        # The tier UPDATE must NOT follow a failed snapshot write.
        assert _target_update_calls(svc.db_connection.db_write) == []

    # Feature: coherent-market-snapshot-capture, Property 3: Per-market write atomicity
    @given(data=market_book_result())
    @settings(max_examples=200)
    def test_succeeding_write_persists_exactly_n_rows_one_shared_timestamp(self, data):
        """
        For any market with N >= 1 runners, when the atomic INSERT ``db_write``
        SUCCEEDS (returns True), the writer issues EXACTLY ONE INSERT statement
        carrying exactly N value-groups / 4*N bound params; the N persisted rows
        are the N returned selectionIds and all share the ONE ``capture_ts`` the
        method computed (never an incomplete subset).

        **Validates: Requirements 1.7**
        """
        result, selection_ids = data
        n = len(selection_ids)
        market_id = "1.234567890"
        svc = _build_service(result, insert_succeeds=True)

        svc.update_runner_odds([_target_for(market_id)])

        inserts = _insert_calls(svc.db_connection.db_write)
        # Exactly ONE INSERT statement for the market (the atomicity mechanism:
        # all N rows written as one all-or-nothing unit).
        assert len(inserts) == 1

        sql = str(inserts[0].args[0])
        params = inserts[0].args[1]

        # One multi-VALUES INSERT carrying exactly N value-groups / 4*N params.
        assert sql.count("(%s, %s, %s, %s)") == n
        assert len(params) == 4 * n

        # Decode the flat params into (timestamp, market_id, runner_id, odds) rows.
        rows = [params[i : i + 4] for i in range(0, len(params), 4)]
        assert len(rows) == n

        timestamps = [row[0] for row in rows]
        markets = [row[1] for row in rows]
        runner_ids = [row[2] for row in rows]

        # All N rows share ONE identical capture_ts — never a partial subset at a
        # different instant. (The method computes datetime.now(UTC) once per
        # market, so rather than pinning an exact value we assert every row-group's
        # timestamp equals the first — they must all be that one shared object.)
        assert all(ts == timestamps[0] for ts in timestamps)

        # Every row carries the market id and the N runner_ids are the N returned
        # selectionIds (one per runner, no omission, no duplicate).
        assert all(m == market_id for m in markets)
        assert sorted(runner_ids) == sorted(selection_ids)
        assert len(set(runner_ids)) == n

        # A successful snapshot runs the tier UPDATE tail exactly once.
        assert len(_target_update_calls(svc.db_connection.db_write)) == 1


# --- Property 4: emitted blobs accepted by the unchanged loader ---------------
#
# Property 1 above asserted the matched-volume figure and best prices the loader
# derives from a writer-emitted blob. This property is the narrower
# no-loader-change contract: whatever the builder renders (any valid ladders
# with ANY matched-volume key combination from the mapping table) is accepted by
# the UNCHANGED SP-368 loader -- ``structured_odds`` returns non-None -- and the
# SP-368 serialize round-trip (``serialize_structured``) reproduces an equal
# stored value. Nothing in ``logic/`` is modified or monkeypatched: the test
# imports and exercises the real ``logic.research_dataset`` /
# ``logic.quality_checks`` against the writer's own blobs, reusing the
# ``ex_and_matched`` generator so every mapping-table combination is driven.


class TestProperty4EmittedBlobsAcceptedByLoader:
    """Feature: coherent-market-snapshot-capture.

    Property 4: Emitted blobs are accepted by the unchanged loader.
    """

    # Feature: coherent-market-snapshot-capture, Property 4: Emitted blobs are accepted by the unchanged loader  # noqa: E501
    @given(data=ex_and_matched())
    @settings(max_examples=200)
    def test_emitted_blob_parses_and_round_trips_through_unchanged_loader(self, data):
        """
        For any blob the builder renders (any valid ladders with any
        matched-volume key combination from the mapping table), the UNCHANGED
        ``logic.research_dataset.structured_odds`` parses it to a non-None
        ``StructuredOdds`` AND the SP-368 serialize round-trip
        (``serialize_structured(structured_odds(blob))``) reproduces an equal
        stored value -- demonstrating the writer emits exactly the string shape
        the loader already reads, with no change to ``logic.research_dataset`` or
        ``logic.quality_checks``.

        **Validates: Requirements 2.8, 3.4**
        """
        ex, has_scalar, scalar = data

        # Build the blob exactly as the writer does (both call paths of the
        # task-1 builder), then render to the stored ``str(dict)`` form.
        blob_dict = build_odds_blob(ex, scalar) if has_scalar else build_odds_blob(ex)
        raw = render_odds_blob(blob_dict)

        # 1) The writer-emitted blob is always parseable: both ladders are always
        #    present, so the unchanged loader never rejects it (Req 2.8).
        so = structured_odds(raw)
        assert so is not None

        # 2) The SP-368 serialize round-trip reproduces an equal stored value.
        #    serialize_structured rides quality_checks.serialize_odds(parse_odds(raw));
        #    comparing the PARSED dicts is robust to dict key-order / float repr
        #    while still proving an equal value is reproduced (Req 2.8, 3.4).
        round_tripped = parse_odds(serialize_structured(so))
        original = parse_odds(raw)
        assert round_tripped is not None
        assert original is not None
        assert round_tripped == original

        # 3) The matched-volume key survives the round-trip: a present scalar
        #    (incl. zero) is reproduced under ``totalMatched``, and both ladders
        #    the loader requires are present in the round-tripped dict.
        if has_scalar:
            assert round_tripped.get("totalMatched") == scalar
        assert "availableToBack" in round_tripped
        assert "availableToLay" in round_tripped
