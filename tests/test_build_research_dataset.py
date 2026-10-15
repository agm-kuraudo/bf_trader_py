"""Reader tests for scripts/build_research_dataset.py (SP-368, task 9.2).

Example-based unit tests (pure stdlib + pytest + monkeypatch; pytest tmp_path
for the Parquet round-trip) covering the Dataset_Reader's orchestration,
persistence, and reporting, plus the bound-parameter usage of the two row-read
functions (which folds in the still-open optional task 8.2 concern).

No live database is touched: build_dataset's module-level ``_connect`` /
``_read_db_config`` / ``_missing_db_config`` are monkeypatched so a FakeConn
yielding a FakeCursor stands in for psycopg2, and the module's ``Log`` is
monkeypatched to capture the operator-facing error/warning/notice/summary
output (``output.log.Output`` writes to a file / stdout rather than the stdlib
``logging`` module, so caplog cannot see it).

Covered:
  * Bound params (Req 4.7, folds in 8.2): read_market_rows / read_targets emit
    %s placeholders with the filter values carried in a bound parameter
    sequence, never string-interpolated; no-filter -> no WHERE + empty params;
    one-sided timestamp ranges.
  * Orchestration: unreachable store -> error + no artifact + non-zero exit
    (Req 4.6); rows-without-Target -> excluded + warning (Req 4.9); zero-match
    -> empty typed 12-column frame + notice (Req 4.8); produced /
    unparseable_excluded reported (Req 5.7).
  * Parquet: round-trip to a real temp path reloads equal in columns / dtypes /
    row count (Req 5.3); a forced write failure reports an error, leaves an
    existing file unchanged, retains the in-memory frame, non-zero exit
    (Req 5.4).
"""

import os
import sys
from datetime import UTC, datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.build_research_dataset as brd
from logic.research_dataset import DATASET_SCHEMA, empty_dataset

# --- Fakes --------------------------------------------------------------------


class _RecordingCursor:
    """A fake psycopg2 cursor that records every ``execute(sql, params)`` call.

    Serves preset rows from ``fetchall`` and lets a test inspect the exact SQL
    text and the bound parameter sequence passed to the data store, so the
    bound-parameter assertions never have to touch a real database.
    """

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.calls = []  # list of (sql, params)

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return list(self.rows)


class _FakeCursor:
    """A context-manager fake cursor that switches rows by queried table.

    ``build_dataset`` calls ``read_market_rows`` and then ``read_targets`` on
    the SAME cursor, so this fake inspects the SQL of each ``execute`` and
    serves the market rows for a ``bf.market_table`` query and the target rows
    for a ``bf.target`` query. Every ``(sql, params)`` is recorded.
    """

    def __init__(self, market_rows, target_rows):
        self._market_rows = market_rows
        self._target_rows = target_rows
        self._last_rows = []
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "bf.market_table" in sql:
            self._last_rows = self._market_rows
        elif "bf.target" in sql:
            self._last_rows = self._target_rows
        else:  # pragma: no cover - defensive, no other table is queried
            self._last_rows = []

    def fetchall(self):
        return list(self._last_rows)


class _FakeConn:
    """A fake psycopg2 connection yielding one shared ``_FakeCursor``."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


class _CapturingLog:
    """Captures the module's operator-facing Log output by level.

    ``output.log.Output`` writes to a file / stdout, not the stdlib logging
    module, so caplog cannot see it. Tests swap ``brd.Log`` for an instance of
    this and assert on the captured error / warning / info messages.
    """

    def __init__(self):
        self.errors = []
        self.warnings = []
        self.infos = []

    def log_error(self, msg, force_console_log=False):
        self.errors.append(msg)

    def log_warning(self, msg, force_console_log=False):
        self.warnings.append(msg)

    def log_info(self, msg, force_console_log=False):
        self.infos.append(msg)


# --- Shared row/data helpers --------------------------------------------------

# A capture timestamp strictly before the Target start_time (-> PRE_MATCH).
_TS = datetime(2025, 3, 1, 12, 0, 0, tzinfo=UTC)
_START = datetime(2025, 3, 1, 15, 0, 0, tzinfo=UTC)

# A valid Odds_Value blob (stored dict-repr form) the Odds_Transform parses.
_ODDS = "{'availableToBack': [{'price': 2.0, 'size': 10.0}], 'availableToLay': [{'price': 2.2, 'size': 8.0}], 'totalMatched': 100.0}"  # noqa: E501


def _target_row(market_id, runner_ids="r1-Alpha|r2-The Draw|r3-Beta"):
    """Build a ``bf.target`` row tuple for ``market_id`` (TargetRow shape)."""
    return (
        f"t-{market_id}",  # target_id
        f"e-{market_id}",  # event_id
        market_id,  # market_id
        runner_ids,  # runner_ids
        _START,  # start_time
        "ACTIVE",  # status
        _TS,  # last_updated
        "",  # notes
    )


def _ok_config(monkeypatch):
    """Monkeypatch the reader so config is present and never touches .env."""
    monkeypatch.setattr(
        brd,
        "_missing_db_config",
        lambda env_path=None: [],
    )
    monkeypatch.setattr(
        brd,
        "_read_db_config",
        lambda env_path=None: {
            "DB_HOST": "h",
            "DB_PORT": "5432",
            "DB_NAME": "bf_trader",
            "DB_USER": "u",
            "DB_PWD": "p",
        },
    )


# --- Bound-parameter usage (Req 4.7; folds in optional task 8.2) --------------


class TestReadMarketRowsBoundParameters:
    """read_market_rows passes every filter value as a bound parameter (Req 4.7)."""

    def test_filters_arrive_as_bound_parameters_not_interpolated(self):
        """market_ids + both timestamp bounds are %s placeholders, values bound.

        The executed SQL must carry ``%s`` placeholders and must NOT contain the
        literal filter values, and the recorded parameter sequence must carry
        the market_id list and the two timestamps in order.

        **Validates: Requirements 4.3, 4.4, 4.7**
        """
        cursor = _RecordingCursor(rows=[(_TS, "1.1", "r1", _ODDS)])
        market_ids = ["1.1", "1.2"]

        rows = brd.read_market_rows(cursor, market_ids=market_ids, ts_start=_TS, ts_end=_START)

        assert rows == [(_TS, "1.1", "r1", _ODDS)]
        assert len(cursor.calls) == 1
        sql, params = cursor.calls[0]

        # Placeholders present; literal filter values never interpolated.
        assert "%s" in sql
        assert "1.1" not in sql
        assert "1.2" not in sql
        assert str(_TS) not in sql
        assert "WHERE" in sql

        # Params is a sequence carrying the market_id list then the timestamps.
        assert isinstance(params, list | tuple)
        assert list(market_ids) in params
        assert _TS in params
        assert _START in params

    def test_no_filter_emits_no_where_and_empty_params(self):
        """No market_ids and no timestamp bounds -> no WHERE, empty params.

        **Validates: Requirements 4.3, 4.7**
        """
        cursor = _RecordingCursor(rows=[])

        brd.read_market_rows(cursor)

        sql, params = cursor.calls[0]
        assert "WHERE" not in sql
        assert list(params) == []

    def test_one_sided_range_binds_only_the_supplied_bound(self):
        """A start-only range binds just ts_start (open above); end-only binds ts_end.

        **Validates: Requirement 4.4**
        """
        start_cursor = _RecordingCursor(rows=[])
        brd.read_market_rows(start_cursor, ts_start=_TS)
        start_sql, start_params = start_cursor.calls[0]
        assert ">=" in start_sql
        assert "<=" not in start_sql
        assert list(start_params) == [_TS]

        end_cursor = _RecordingCursor(rows=[])
        brd.read_market_rows(end_cursor, ts_end=_START)
        end_sql, end_params = end_cursor.calls[0]
        assert "<=" in end_sql
        assert ">=" not in end_sql
        assert list(end_params) == [_START]


class TestReadTargetsBoundParameters:
    """read_targets passes any market_id filter as a bound parameter (Req 4.7)."""

    def test_filter_arrives_as_bound_parameter_not_interpolated(self):
        """A market_id filter is a %s placeholder with the list bound in params.

        **Validates: Requirements 4.3, 4.7**
        """
        target = _target_row("1.1")
        cursor = _RecordingCursor(rows=[target])
        market_ids = ["1.1"]

        targets = brd.read_targets(cursor, market_ids=market_ids)

        assert targets == {"1.1": target}
        sql, params = cursor.calls[0]
        assert "%s" in sql
        assert "WHERE" in sql
        assert "1.1" not in sql
        assert list(market_ids) in params

    def test_no_filter_emits_no_where_and_empty_params(self):
        """No market_ids -> read all targets, no WHERE, empty params.

        **Validates: Requirements 4.2, 4.7**
        """
        cursor = _RecordingCursor(rows=[])

        brd.read_targets(cursor)

        sql, params = cursor.calls[0]
        assert "WHERE" not in sql
        assert list(params) == []


# --- Orchestration: unreachable store (Req 4.6) -------------------------------


class TestUnreachableStore:
    """An unreachable Data_Store -> error, no artifact, non-zero exit (Req 4.6)."""

    def test_missing_config_reports_error_and_no_artifact(self, monkeypatch):
        """Missing DB config is treated as unreachable: error + no frame.

        **Validates: Requirement 4.6**
        """
        log = _CapturingLog()
        monkeypatch.setattr(brd, "Log", log)
        monkeypatch.setattr(brd, "_missing_db_config", lambda env_path=None: ["DB_HOST", "DB_PWD"])

        result = brd.build_dataset(out_path=None)

        assert result.ok is False
        assert result.status == brd.STATUS_UNREACHABLE
        assert result.dataframe is None
        assert result.persisted is False
        assert result.error is not None
        assert any("unreachable" in msg.lower() for msg in log.errors)

    def test_connect_failure_reports_error_and_no_artifact(self, monkeypatch):
        """A connect-time OperationalError is reported as unreachable, no frame.

        **Validates: Requirement 4.6**
        """
        log = _CapturingLog()
        monkeypatch.setattr(brd, "Log", log)
        _ok_config(monkeypatch)

        def _boom(config):
            raise brd.psycopg2.OperationalError("connection refused")

        monkeypatch.setattr(brd, "_connect", _boom)

        result = brd.build_dataset(out_path=None)

        assert result.ok is False
        assert result.status == brd.STATUS_UNREACHABLE
        assert result.dataframe is None
        assert any("unreachable" in msg.lower() for msg in log.errors)

    def test_main_returns_nonzero_on_unreachable(self, monkeypatch):
        """main() returns a non-zero exit code when the store is unreachable.

        **Validates: Requirement 4.6**
        """
        monkeypatch.setattr(sys, "argv", ["build_research_dataset.py"])
        monkeypatch.setattr(
            brd,
            "build_dataset",
            lambda **kwargs: brd.BuildResult(
                dataframe=None,
                status=brd.STATUS_UNREACHABLE,
                error="Data_Store unreachable",
            ),
        )

        assert brd.main() != 0


# --- Orchestration: rows without a Target (Req 4.9) ---------------------------


class TestRowsWithoutTarget:
    """A market with rows but no Target is excluded with a warning (Req 4.9)."""

    def test_market_without_target_excluded_and_warned(self, monkeypatch):
        """Market B (no Target) is excluded + warned; its rows leave the frame.

        **Validates: Requirement 4.9**
        """
        log = _CapturingLog()
        monkeypatch.setattr(brd, "Log", log)
        _ok_config(monkeypatch)

        # Market A has a Target; market B has rows but NO Target.
        market_rows = [
            (_TS, "1.A", "r1", _ODDS),
            (_TS, "1.B", "r1", _ODDS),
        ]
        target_rows = [_target_row("1.A")]
        cursor = _FakeCursor(market_rows, target_rows)
        monkeypatch.setattr(brd, "_connect", lambda config: _FakeConn(cursor))

        result = brd.build_dataset(out_path=None)

        assert result.ok is True
        assert "1.B" in result.excluded_markets
        assert "1.A" not in result.excluded_markets
        # A warning naming the excluded market was emitted.
        assert any("1.B" in msg for msg in log.warnings)
        # B's rows are absent from the produced frame; A's remain.
        produced_markets = set(result.dataframe["market_id"].tolist())
        assert "1.B" not in produced_markets
        assert "1.A" in produced_markets


# --- Orchestration: zero-match filter (Req 4.8) -------------------------------


class TestZeroMatch:
    """A filter matching zero rows -> empty typed frame + notice (Req 4.8)."""

    def test_zero_rows_yields_empty_typed_frame_and_notice(self, monkeypatch):
        """No market rows -> the empty 12-column typed frame + a 'no rows' notice.

        **Validates: Requirements 4.8, 5.2**
        """
        log = _CapturingLog()
        monkeypatch.setattr(brd, "Log", log)
        _ok_config(monkeypatch)

        cursor = _FakeCursor([], [])
        monkeypatch.setattr(brd, "_connect", lambda config: _FakeConn(cursor))

        result = brd.build_dataset(market_ids=["1.nope"], out_path=None)

        assert result.ok is True
        assert result.produced == 0
        assert len(result.dataframe) == 0

        # The empty frame carries exactly the 12 schema columns + dtypes.
        reference = empty_dataset()
        assert list(result.dataframe.columns) == [name for name, _ in DATASET_SCHEMA]
        assert list(result.dataframe.dtypes) == list(reference.dtypes)

        # An operator-facing "no rows matched" notice was emitted.
        assert any("no rows matched" in msg.lower() for msg in log.infos)


# --- Reporting: produced + unparseable counts (Req 5.7) -----------------------


class TestReporting:
    """Produced + unparseable-excluded counts appear in result and summary (Req 5.7)."""

    def test_counts_reported_in_result_and_summary_line(self, monkeypatch):
        """One parseable + one unparseable row -> produced=1, unparseable=1 reported.

        **Validates: Requirement 5.7**
        """
        log = _CapturingLog()
        monkeypatch.setattr(brd, "Log", log)
        _ok_config(monkeypatch)

        market_rows = [
            (_TS, "1.A", "r1", _ODDS),  # parseable
            (_TS, "1.A", "r2", "not-a-dict"),  # unparseable
        ]
        target_rows = [_target_row("1.A")]
        cursor = _FakeCursor(market_rows, target_rows)
        monkeypatch.setattr(brd, "_connect", lambda config: _FakeConn(cursor))

        result = brd.build_dataset(out_path=None)

        assert result.produced == 1
        assert result.unparseable_excluded == 1

        # Both counts appear on the operator-facing summary line.
        summary = brd._summary_line(result)
        assert "produced=1" in summary
        assert "unparseable_excluded=1" in summary
        # The run-summary line was also logged.
        assert any("produced=1" in msg and "unparseable_excluded=1" in msg for msg in log.infos)


# --- Parquet round-trip (Req 5.3) ---------------------------------------------


class TestParquetRoundTrip:
    """Persisting then reloading yields an equal frame (Req 5.3)."""

    def test_roundtrip_reloads_equal_in_columns_dtypes_rowcount(self, monkeypatch, tmp_path):
        """A successful read persists Parquet; reload equals produced frame.

        **Validates: Requirement 5.3**
        """
        log = _CapturingLog()
        monkeypatch.setattr(brd, "Log", log)
        _ok_config(monkeypatch)

        market_rows = [
            (_TS, "1.A", "r1", _ODDS),
            (_TS, "1.A", "r2", _ODDS),
        ]
        target_rows = [_target_row("1.A")]
        cursor = _FakeCursor(market_rows, target_rows)
        monkeypatch.setattr(brd, "_connect", lambda config: _FakeConn(cursor))

        out_path = str(tmp_path / "dataset.parquet")
        result = brd.build_dataset(out_path=out_path)

        assert result.ok is True
        assert result.persisted is True
        assert os.path.exists(out_path)

        reloaded = pd.read_parquet(out_path)

        # Equal in columns, dtypes, and row count.
        assert list(reloaded.columns) == list(result.dataframe.columns)
        assert list(reloaded.dtypes) == list(result.dataframe.dtypes)
        assert len(reloaded) == len(result.dataframe)
        pd.testing.assert_frame_equal(reloaded, result.dataframe)


# --- Parquet persist failure (Req 5.4) ----------------------------------------


class TestParquetPersistFailure:
    """A forced write failure is reported; existing file + in-memory frame intact (Req 5.4)."""

    def test_write_failure_reports_error_keeps_file_and_frame_and_nonzero(self, monkeypatch, tmp_path):
        """to_parquet raising -> PERSIST_FAILED, file unchanged, frame retained.

        **Validates: Requirement 5.4**
        """
        log = _CapturingLog()
        monkeypatch.setattr(brd, "Log", log)
        _ok_config(monkeypatch)

        market_rows = [(_TS, "1.A", "r1", _ODDS)]
        target_rows = [_target_row("1.A")]
        cursor = _FakeCursor(market_rows, target_rows)
        monkeypatch.setattr(brd, "_connect", lambda config: _FakeConn(cursor))

        # Pre-existing file at the output path must be left unchanged on failure.
        out_path = tmp_path / "dataset.parquet"
        sentinel = "pre-existing-contents"
        out_path.write_text(sentinel, encoding="utf-8")

        # Force the Parquet write to raise.
        def _boom(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(pd.DataFrame, "to_parquet", _boom, raising=True)

        result = brd.build_dataset(out_path=str(out_path))

        assert result.ok is False
        assert result.status == brd.STATUS_PERSIST_FAILED
        assert result.persisted is False
        assert result.error is not None
        assert any("persist" in msg.lower() or "failed" in msg.lower() for msg in log.errors)

        # The in-memory frame is retained (not None).
        assert result.dataframe is not None
        assert len(result.dataframe) == 1

        # The existing file is left unchanged.
        assert out_path.read_text(encoding="utf-8") == sentinel

    def test_main_returns_nonzero_on_persist_failure(self, monkeypatch):
        """main() returns a non-zero exit code when persistence failed.

        **Validates: Requirement 5.4**
        """
        monkeypatch.setattr(sys, "argv", ["build_research_dataset.py", "--out", "x.parquet"])
        monkeypatch.setattr(
            brd,
            "build_dataset",
            lambda **kwargs: brd.BuildResult(
                dataframe=empty_dataset(),
                status=brd.STATUS_PERSIST_FAILED,
                out_path="x.parquet",
                error="Failed to persist",
            ),
        )

        assert brd.main() != 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
