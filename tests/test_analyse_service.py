"""MarketAnalyser equivalence + empty-result tests (SP-368, task 11.2).

Example-based unit tests (pure stdlib + pytest + monkeypatch; no live database,
no real plotting) covering the refactored ``MarketAnalyser`` read path, which
now delegates all odds parsing to ``logic.research_dataset.build_records`` (the
single odds-parsing path, Req 6.1/6.4):

  * Equivalence (Req 6.2): for one ``market_id`` with multiple runners and
    multiple (deliberately out-of-order) Capture_Timestamps, the per-runner
    ``odds_to_back`` / ``odds_to_lay`` series ``MarketAnalyser`` projects into
    ``self.df`` -- their values, their timestamps, and their ascending-timestamp
    ordering -- equal the ``best_back`` / ``best_lay`` / timestamp series the
    loader (``build_records``) produces for that same market and runner.
  * Empty result (Req 6.3): when the loader yields no record (all rows
    unparseable, or an empty input), ``self.df`` is left empty, a "no captured
    odds" notice is logged, no exception is raised, and ``plot_data()`` is a
    no-op over the empty frame.

``MarketAnalyser.__init__`` builds a ``DBOutputConnection`` and a ``BFDriver``,
both of which carry heavy construction side effects. To keep these tests pure we
construct the instance via ``object.__new__(MarketAnalyser)`` and set only the
attributes the methods under test touch (``market_id`` and the initial empty
``df``, exactly as ``__init__`` does), plus a monkeypatched ``get_target_row``.
The module ``Log`` (``output.log.Output``) writes to a file / stdout rather than
the stdlib ``logging`` module, so caplog cannot see it; tests swap
``analyse_service.Log`` for a capturing fake (the brd test pattern).
"""

import os
import sys
from datetime import UTC, datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import analyse_service
from analyse_service import MarketAnalyser
from logic.research_dataset import build_records

# --- Fakes --------------------------------------------------------------------


class _CapturingLog:
    """Captures the module's operator-facing Log output by level.

    ``output.log.Output`` writes to a file / stdout, not the stdlib logging
    module, so caplog cannot see it. Tests swap ``analyse_service.Log`` for an
    instance of this and assert on the captured info/warning/error messages.
    DEBUG and INFO log-level sinks accept the same ``(msg, force_console_log)``
    signature the real ``Output`` classmethods expose.
    """

    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.infos = []
        self.debugs = []

    def log_error(self, msg, force_console_log=False):
        self.errors.append(msg)

    def log_warning(self, msg, force_console_log=False):
        self.warnings.append(msg)

    def log_info(self, msg, force_console_log=False):
        self.infos.append(msg)

    def log_debug(self, msg, force_console_log=False):
        self.debugs.append(msg)


# --- Shared row/data helpers --------------------------------------------------

_MARKET_ID = "1.240323372"

# Three runners; the middle one is "The Draw" (the configured draw name), so the
# Target is a canonical MATCH_ODDS trio (HOME/DRAW/AWAY) -- though the role is
# incidental here, this exercises a realistic Target_Row shape.
_RUNNER_IDS = "r1-Alpha|r2-The Draw|r3-Beta"

_START = datetime(2025, 3, 14, 15, 0, 0, tzinfo=UTC)

# Three distinct Capture_Timestamps, supplied to the analyser OUT OF ORDER so
# the test proves both the analyser and the loader sort ascending by timestamp.
_T0 = datetime(2025, 3, 14, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 3, 14, 13, 0, 0, tzinfo=UTC)
_T2 = datetime(2025, 3, 14, 14, 0, 0, tzinfo=UTC)


def _odds(back_price, back_size, lay_price, lay_size, total_matched=100.0):
    """Build a valid Odds_Value blob (stored dict-repr form) the loader parses.

    Each side carries a two-entry ladder so the best price is the max back /
    min lay rather than simply index 0 -- this also exercises the fix for the
    former index-1 ladder bug. Values are chosen so ``best_back`` / ``best_lay``
    are unambiguous.
    """
    return (
        "{'availableToBack': ["
        f"{{'price': {back_price}, 'size': {back_size}}}, "
        f"{{'price': {back_price - 0.1}, 'size': {back_size}}}], "
        "'availableToLay': ["
        f"{{'price': {lay_price}, 'size': {lay_size}}}, "
        f"{{'price': {lay_price + 0.1}, 'size': {lay_size}}}], "
        f"'totalMatched': {total_matched}}}"
    )


def _target_row(market_id=_MARKET_ID, runner_ids=_RUNNER_IDS):
    """Build a ``bf.target`` row tuple for ``market_id`` (TargetRow shape).

    ``(target_id, event_id, market_id, runner_ids, start_time, status,
    last_updated, notes)`` -- matching ``logic.research_dataset.TargetRow``.
    """
    return (
        f"t-{market_id}",  # target_id
        f"e-{market_id}",  # event_id
        market_id,  # market_id
        runner_ids,  # runner_ids
        _START,  # start_time
        "ACTIVE",  # status
        _T0,  # last_updated
        "",  # notes
    )


def _make_analyser(market_id=_MARKET_ID, target_row=None):
    """Construct a MarketAnalyser WITHOUT the DB/BFDriver constructor side effects.

    Uses ``object.__new__`` to bypass ``__init__`` (which builds a
    ``DBOutputConnection`` and a ``BFDriver``), then sets only what the methods
    under test touch: ``market_id`` (coerced to ``str`` like ``__init__``), the
    initial empty ``df`` with the same four columns ``__init__`` declares, and a
    ``get_target_row`` returning the supplied Target_Row (``None`` for the
    no-Target case). ``db_connection`` is left unset because the monkeypatched
    ``get_target_row`` is the only consumer of it in ``process_data``.
    """
    analyser = object.__new__(MarketAnalyser)
    analyser.market_id = str(market_id)
    analyser.start_time = None
    analyser.end_time = None
    analyser.df = pd.DataFrame(columns=["timestamp", "runner_id", "odds_to_back", "odds_to_lay"])
    analyser.get_target_row = lambda: target_row
    return analyser


# --- Equivalence: MarketAnalyser series == loader series (Req 6.2) ------------


class TestMarketAnalyserLoaderEquivalence:
    """MarketAnalyser's per-runner series equal the loader's (Req 6.2)."""

    def test_best_back_best_lay_series_and_ordering_match_loader(self):
        """Per runner, odds_to_back/odds_to_lay/timestamp (ascending) == loader.

        Build Market_Rows for one market across multiple runners and multiple
        OUT-OF-ORDER timestamps; run both ``MarketAnalyser.process_data`` and
        ``build_records`` over the same rows; then for each runner assert the
        analyser's ``(timestamp, odds_to_back, odds_to_lay)`` series -- in order
        -- equals the loader's ``(timestamp, best_back, best_lay)`` series. This
        proves the analyser exposes exactly the series the loader produces for
        the same market and runner, in ascending-timestamp order.

        **Validates: Requirement 6.2**
        """
        target_row = _target_row()

        # Two runners, three timestamps each, supplied deliberately out of order
        # (and interleaved across runners) so a correct result must sort them.
        market_rows = [
            (_T2, _MARKET_ID, "r1", _odds(2.5, 10.0, 2.6, 8.0)),
            (_T0, _MARKET_ID, "r3", _odds(3.0, 5.0, 3.2, 4.0)),
            (_T1, _MARKET_ID, "r1", _odds(2.4, 12.0, 2.5, 9.0)),
            (_T2, _MARKET_ID, "r3", _odds(3.1, 6.0, 3.3, 5.0)),
            (_T0, _MARKET_ID, "r1", _odds(2.6, 11.0, 2.7, 7.0)),
            (_T1, _MARKET_ID, "r3", _odds(2.9, 7.0, 3.0, 6.0)),
        ]

        # --- Run the analyser (uses the single loader parse path). ---
        analyser = _make_analyser(target_row=target_row)
        analyser.process_data(market_rows)

        # --- Independently run the loader over the same rows. ---
        result = build_records(market_rows, {_MARKET_ID: target_row})
        assert result.records  # sanity: the loader produced records

        analyser_df = analyser.df

        # Compare per runner so the two series line up unambiguously.
        for runner_id in ("r1", "r3"):
            # Analyser side: its rows for this runner, in the order the analyser
            # placed them (already ascending-timestamp per the loader ordering).
            runner_df = analyser_df[analyser_df["runner_id"] == runner_id].reset_index(drop=True)
            analyser_timestamps = [pd.Timestamp(ts) for ts in runner_df["timestamp"].tolist()]
            analyser_back = runner_df["odds_to_back"].tolist()
            analyser_lay = runner_df["odds_to_lay"].tolist()

            # Loader side: its records for this runner, in produced order.
            loader_records = [r for r in result.records if r.runner_id == runner_id]
            loader_timestamps = [pd.Timestamp(r.timestamp) for r in loader_records]
            loader_back = [r.best_back for r in loader_records]
            loader_lay = [r.best_lay for r in loader_records]

            # Same timestamps, same ascending-timestamp ordering.
            assert analyser_timestamps == loader_timestamps
            assert analyser_timestamps == sorted(analyser_timestamps)

            # Same best_back / best_lay values, position for position.
            assert analyser_back == loader_back
            assert analyser_lay == loader_lay

        # The analyser surfaced exactly the loader's observations (no more, no
        # fewer) across all runners.
        assert len(analyser_df) == len(result.records)


# --- Empty result + "no captured odds" notice (Req 6.3) -----------------------


class TestMarketAnalyserEmptyResult:
    """An empty loader result -> empty df + logged notice, no raise (Req 6.3)."""

    def test_all_unparseable_rows_leave_empty_df_and_log_notice(self, monkeypatch):
        """All-unparseable rows -> loader yields no record -> empty df + notice.

        ``process_data`` must not raise, must leave ``self.df`` empty, and must
        log a "no captured odds" notice for the market.

        **Validates: Requirement 6.3**
        """
        log = _CapturingLog()
        monkeypatch.setattr(analyse_service, "Log", log)

        analyser = _make_analyser(target_row=_target_row())

        # Rows whose odds blobs cannot be parsed -> the loader produces no
        # record and excludes them (so result.records is empty).
        market_rows = [
            (_T0, _MARKET_ID, "r1", "not-a-dict"),
            (_T1, _MARKET_ID, "r3", "{'availableToBack': []}"),  # missing lay ladder
        ]

        # Sanity: the loader really does yield no record for these rows.
        assert build_records(market_rows, {_MARKET_ID: _target_row()}).records == ()

        # Must not raise.
        analyser.process_data(market_rows)

        # self.df left empty.
        assert analyser.df.empty

        # A "no captured odds" notice naming the market was logged.
        assert any("no captured odds" in msg.lower() and _MARKET_ID in msg for msg in log.infos)
        assert not log.errors

    def test_empty_input_leaves_empty_df_and_log_notice(self, monkeypatch):
        """An empty Market_Row list -> empty df + "no captured odds" notice.

        **Validates: Requirement 6.3**
        """
        log = _CapturingLog()
        monkeypatch.setattr(analyse_service, "Log", log)

        analyser = _make_analyser(target_row=_target_row())

        analyser.process_data([])

        assert analyser.df.empty
        assert any("no captured odds" in msg.lower() for msg in log.infos)
        assert not log.errors

    def test_plot_data_is_a_noop_over_the_empty_frame(self, monkeypatch):
        """plot_data() returns without error (and without plotting) on empty df.

        The empty-frame guard in ``plot_data`` must short-circuit before any
        matplotlib call, so this needs no real plotting backend and writes no
        files.

        **Validates: Requirement 6.3**
        """
        log = _CapturingLog()
        monkeypatch.setattr(analyse_service, "Log", log)

        analyser = _make_analyser(target_row=_target_row())
        analyser.process_data([])
        assert analyser.df.empty

        # Guard it so a stray matplotlib call (if the guard regressed) surfaces
        # as a clear failure rather than a chart file being written.
        monkeypatch.setattr(
            analyse_service.plt,
            "figure",
            lambda *a, **k: pytest.fail("plot_data must not plot over an empty frame"),
        )

        # No exception, no plotting.
        analyser.plot_data()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
