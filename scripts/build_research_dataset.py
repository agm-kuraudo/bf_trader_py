"""Build the Betfair Research_Dataset from captured odds (SP-368, Req 4, 5).

This is the thin Dataset_Reader: the I/O + orchestration wrapper over the pure
``logic/research_dataset.py`` Odds_Transform. It reads Market_Rows from
``bf.market_table`` and the associated Target_Row metadata from ``bf.target``,
delegates ALL parsing and shaping to the Odds_Transform (``build_records``),
assembles the 12-column schema DataFrame, optionally persists it to Parquet, and
reports the run summary.

Following the SP-328 / SP-332 house pattern, this module owns every side effect
(the connection, the parameterised queries, the DataFrame assembly, the Parquet
write, the operator-facing reporting and exit code) while the shaping stays pure
and property-testable. It reuses the same ``.env``/``DotenvLoader`` +
``psycopg2`` connection approach as ``scripts/verify_db.py`` and
``scripts/check_quality.py``: a direct ``psycopg2.connect`` with a
``connect_timeout`` (the shared ``DBOutputConnection`` helper sets no timeout).

Every filter value (``market_id`` list, timestamp bounds) is passed to the data
store as a BOUND query parameter, never string-interpolated, which closes the
injection path (Req 4.7).

The ``.env``/connection scaffolding and the two row-read functions
(``read_market_rows`` / ``read_targets``) are the DB layer (task 8.1); the
``build_dataset`` orchestration and the ``main`` CLI (Req 4.5/4.6/4.8/4.9, 5.*)
are implemented here (task 9.1).
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import psycopg2

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from logic.deploy_checks import validate_env
from logic.research_dataset import (
    TargetRow,
    build_records,
    empty_dataset,
    records_to_dataframe,
)
from output.log import Output as Log

# Required DB connection keys read from .env (mirrors verify_db.py / check_quality.py).
REQUIRED_DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]

# Connection timeout in seconds (mirrors verify_db.py / check_quality.py).
CONNECT_TIMEOUT_S = 10

# The schema the captured odds live in.
CAPTURE_SCHEMA = "bf"

# ``BuildResult.status`` values. A run is OK only when it connected, shaped the
# rows, and (where an out path was given) persisted the artifact. The two
# non-OK statuses are the only ones that drive a non-zero exit code (Req 4.6,
# 5.4): the store could not be reached, or the Parquet write failed.
STATUS_OK = "OK"
STATUS_UNREACHABLE = "UNREACHABLE"
STATUS_PERSIST_FAILED = "PERSIST_FAILED"


@dataclass
class BuildResult:
    """The outcome of a :func:`build_dataset` run: frame plus run-summary counts.

    Carries the produced Research_Dataset DataFrame together with the counts and
    status the operator-facing summary and the process exit code are derived
    from. ``status`` is :data:`STATUS_OK` on a clean run; a non-OK status
    (:data:`STATUS_UNREACHABLE` or :data:`STATUS_PERSIST_FAILED`) is what makes
    :func:`main` return a non-zero exit code (Req 4.6, 5.4).

    On an unreachable Data_Store the frame is ``None`` and no artifact is
    produced (Req 4.6); on a persist failure the in-memory frame is retained
    even though the on-disk artifact was not written (Req 5.4).

    Attributes:
        dataframe: The produced 12-column Research_Dataset frame, or ``None``
            when the Data_Store was unreachable (Req 4.6).
        produced: The count of Dataset_Records produced (Req 5.7).
        unparseable_excluded: The count of Market_Rows excluded as unparseable
            (Req 5.7).
        status: One of :data:`STATUS_OK` / :data:`STATUS_UNREACHABLE` /
            :data:`STATUS_PERSIST_FAILED`.
        out_path: The path the artifact was (or would have been) persisted to,
            or ``None`` when no output path was supplied.
        persisted: ``True`` when the Parquet artifact was written to
            ``out_path``.
        excluded_markets: The ``market_id`` values excluded for having rows but
            no Target metadata (Req 4.9).
        error: An operator-facing error message, or ``None``.
    """

    dataframe: pd.DataFrame | None = None
    produced: int = 0
    unparseable_excluded: int = 0
    status: str = STATUS_OK
    out_path: str | None = None
    persisted: bool = False
    excluded_markets: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the run completed without an unreachable store or persist failure."""
        return self.status == STATUS_OK


def _read_db_config(env_path: str = None) -> dict:
    """Read the required DB keys from ``.env`` via ``DotenvLoader`` (see verify_db.py).

    Missing or empty keys are returned as empty strings rather than raising, so
    ``validate_env`` can report the full set of offending keys at once instead
    of failing on the first one.

    Args:
        env_path: Optional explicit path to the ``.env`` file. Defaults to the
            project root ``.env`` resolved by ``DotenvLoader``.

    Returns:
        A dict mapping each key in ``REQUIRED_DB_KEYS`` to its value (or "").
    """
    loader = DotenvLoader(env_path)
    config = {}
    for key in REQUIRED_DB_KEYS:
        try:
            config[key] = loader.get_secret(key)
        except ConfigurationException:
            # Absent/empty -> record as empty so validate_env reports it.
            config[key] = ""
    return config


def _missing_db_config(env_path: str = None) -> list[str]:
    """Return the required DB keys that are absent/empty in ``.env``.

    Reads the config via ``_read_db_config`` and applies
    ``logic.deploy_checks.validate_env`` so the orchestration (task 9.1) can
    gate on required connection details before attempting to connect, reporting
    the full set of offending keys at once (the ``verify_db.py`` pattern).

    Args:
        env_path: Optional explicit path to the ``.env`` file (mainly for tests).

    Returns:
        The subset of ``REQUIRED_DB_KEYS`` (in order) that is missing or empty.
    """
    config = _read_db_config(env_path)
    return validate_env(config, REQUIRED_DB_KEYS)


def _connect(config: dict):
    """Open a psycopg2 connection with the shared connect timeout.

    Mirrors the ``verify_db.py`` / ``check_quality.py`` connection approach: a
    direct ``psycopg2.connect`` with ``connect_timeout=CONNECT_TIMEOUT_S`` and
    autocommit enabled. A read-only run never writes, but autocommit keeps the
    connection free of an idle-in-transaction state while the reads run.

    Args:
        config: DB config mapping (as returned by ``_read_db_config``).

    Returns:
        An open, autocommit psycopg2 connection.
    """
    conn = psycopg2.connect(
        host=config["DB_HOST"],
        port=config["DB_PORT"],
        dbname=config["DB_NAME"],
        user=config["DB_USER"],
        password=config["DB_PWD"],
        connect_timeout=CONNECT_TIMEOUT_S,
    )
    conn.autocommit = True
    return conn


def read_market_rows(cursor, market_ids=None, ts_start=None, ts_end=None) -> list:
    """Read ``(timestamp, market_id, runner_id, odds)`` from ``bf.market_table``.

    All filter values are passed to the data store as BOUND query parameters,
    never string-interpolated (Req 4.7). With no ``market_ids`` the read spans
    every captured market (Req 4.1); supplying ``market_ids`` restricts it to
    those markets (Req 4.3). A Capture_Timestamp range restricts rows to
    ``timestamp >= ts_start`` and/or ``timestamp <= ts_end``; a range with only
    one bound is treated as open on the other side (Req 4.4).

    Args:
        cursor: An open psycopg2 cursor.
        market_ids: Optional iterable of ``market_id`` values to restrict to.
            ``None`` (or empty) reads all markets.
        ts_start: Optional inclusive lower Capture_Timestamp bound. ``None``
            leaves the range open below.
        ts_end: Optional inclusive upper Capture_Timestamp bound. ``None``
            leaves the range open above.

    Returns:
        A list of ``(timestamp, market_id, runner_id, odds)`` row tuples.
    """
    # Build the WHERE clause from only the supplied filters, collecting each
    # value into params so it is bound by psycopg2 rather than interpolated
    # into the SQL text (Req 4.7). An empty market_ids list is treated as "no
    # filter" so it does not degenerate into "= ANY(ARRAY[])" (matches nothing).
    clauses: list[str] = []
    params: list = []

    if market_ids:
        clauses.append("market_id = ANY(%s)")
        params.append(list(market_ids))
    if ts_start is not None:
        clauses.append('"timestamp" >= %s')
        params.append(ts_start)
    if ts_end is not None:
        clauses.append('"timestamp" <= %s')
        params.append(ts_end)

    sql = 'SELECT "timestamp", market_id, runner_id, odds FROM bf.market_table'
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    cursor.execute(sql, params)
    return list(cursor.fetchall())


def read_targets(cursor, market_ids=None) -> dict[str, TargetRow]:
    """Read Target metadata keyed by ``market_id`` from ``bf.target`` (Req 4.2).

    Selects the eight columns matching the ``TargetRow`` tuple shape
    (``target_id, event_id, market_id, runner_ids, start_time, status,
    last_updated, notes``) and returns them in a dict keyed by ``market_id`` so
    the orchestration can look up each market's metadata by key. Any supplied
    ``market_id`` filter is passed as a BOUND query parameter, never
    string-interpolated (Req 4.7); with no filter every Target is read.

    Args:
        cursor: An open psycopg2 cursor.
        market_ids: Optional iterable of ``market_id`` values to restrict to.
            ``None`` (or empty) reads every Target.

    Returns:
        A dict mapping ``market_id`` to its ``TargetRow`` tuple.
    """
    sql = "SELECT target_id, event_id, market_id, runner_ids, start_time, status, last_updated, notes FROM bf.target"
    params: list = []
    # Empty market_ids -> no filter (read all), avoiding a match-nothing
    # "= ANY(ARRAY[])"; a supplied list is bound (Req 4.7).
    if market_ids:
        sql += " WHERE market_id = ANY(%s)"
        params.append(list(market_ids))

    cursor.execute(sql, params)

    # Key by market_id (column index 2 of the TargetRow tuple).
    targets: dict[str, TargetRow] = {}
    for row in cursor.fetchall():
        targets[row[2]] = tuple(row)
    return targets


def build_dataset(env_path=None, market_ids=None, ts_start=None, ts_end=None, out_path=None):
    """Orchestrate a Research_Dataset run: read, shape, assemble, persist, report.

    Connects to the Data_Store, reads Market_Rows and Target metadata via the
    bound-parameter readers, delegates all parsing and shaping to the
    Odds_Transform (``build_records``), assembles the 12-column schema
    DataFrame, optionally persists it to ``out_path`` as Parquet, and returns a
    result carrying the DataFrame plus the run-summary counts.

    Error/edge paths it owns: a Data_Store unreachable within the connect
    timeout is reported as an operator-facing error and produces no artifact
    (Req 4.6); a ``market_id`` with Market_Rows but no Target_Row in
    ``bf.target`` is excluded with an operator-facing warning (Req 4.9); a
    ``market_id`` filter or timestamp range matching zero Market_Rows yields an
    empty but fully-typed 12-column schema DataFrame and an operator-facing
    notice (Req 4.8, 5.2); a Parquet persist failure is reported, leaves any
    existing file at ``out_path`` unchanged, and retains the in-memory frame
    (Req 5.4); and every run reports the produced-record and
    unparseable-excluded counts (Req 5.7).

    Args:
        env_path: Optional explicit ``.env`` path (mainly for tests).
        market_ids: Optional iterable of ``market_id`` values to restrict to.
        ts_start: Optional inclusive lower Capture_Timestamp bound.
        ts_end: Optional inclusive upper Capture_Timestamp bound.
        out_path: Optional path to persist the Research_Dataset to as Parquet.

    Returns:
        A :class:`BuildResult` carrying the produced Research_Dataset DataFrame
        plus the run-summary counts (produced records, unparseable-excluded).
    """
    # --- Config gate (Req 4.6): a missing/misconfigured connection is treated
    # as the store being unreachable -- report the offending keys and produce no
    # artifact without attempting to connect.
    missing = _missing_db_config(env_path)
    if missing:
        error = "Data_Store unreachable: missing required DB connection details in .env: " + ", ".join(missing)
        Log.log_error(error)
        return BuildResult(
            dataframe=None,
            status=STATUS_UNREACHABLE,
            out_path=out_path,
            error=error,
        )

    # --- Connect (Req 4.6): a connection failure/timeout means the store is
    # unreachable -> operator-facing error, no artifact, non-OK status.
    config = _read_db_config(env_path)
    try:
        conn = _connect(config)
    except (Exception, psycopg2.DatabaseError) as connect_error:
        error = f"Data_Store unreachable within {CONNECT_TIMEOUT_S}s: {connect_error}"
        Log.log_error(error)
        return BuildResult(
            dataframe=None,
            status=STATUS_UNREACHABLE,
            out_path=out_path,
            error=error,
        )

    # --- Read rows + Target metadata, then delegate ALL shaping to the pure
    # Odds_Transform (Req 4.5). The reader owns only the I/O.
    try:
        with conn.cursor() as cursor:
            market_rows = read_market_rows(cursor, market_ids, ts_start, ts_end)
            targets_by_market = read_targets(cursor, market_ids)
    finally:
        conn.close()

    # --- Req 4.9: a market_id with Market_Rows but no Target_Row is excluded
    # with an operator-facing warning naming it. Filter those rows out before
    # shaping and warn once per excluded market_id (in first-seen order).
    kept_rows: list = []
    excluded_markets: list[str] = []
    seen_excluded: set[str] = set()
    for row in market_rows:
        market_id = row[1]
        if market_id in targets_by_market:
            kept_rows.append(row)
        elif market_id not in seen_excluded:
            seen_excluded.add(market_id)
            excluded_markets.append(market_id)
    for market_id in excluded_markets:
        Log.log_warning(f"Excluding market_id {market_id}: no Target metadata in {CAPTURE_SCHEMA}.target")

    # --- Shape + assemble the 12-column schema frame. ---
    transform_result = build_records(kept_rows, targets_by_market)
    produced = len(transform_result.records)
    unparseable_excluded = transform_result.unparseable_excluded

    # Req 4.8 / 5.2: a filter matching zero Market_Rows still yields the empty
    # but fully-typed 12-column frame, plus an operator-facing "no rows matched"
    # notice. ``records_to_dataframe`` already returns ``empty_dataset()`` for a
    # zero-record result; the explicit guard makes the notice unambiguous.
    if not kept_rows:
        frame = empty_dataset()
        Log.log_info("No rows matched the supplied filter; produced an empty Research_Dataset.")
    else:
        frame = records_to_dataframe(transform_result)

    result = BuildResult(
        dataframe=frame,
        produced=produced,
        unparseable_excluded=unparseable_excluded,
        status=STATUS_OK,
        out_path=out_path,
        persisted=False,
        excluded_markets=excluded_markets,
        error=None,
    )

    # --- Persist (Req 5.3/5.4). On failure, report the error, leave any
    # existing file at out_path untouched (we never delete/truncate it -- a
    # failing to_parquet raises before replacing a good existing file), retain
    # the in-memory frame, and signal a non-zero exit via a non-OK status.
    if out_path:
        try:
            frame.to_parquet(out_path)
            result.persisted = True
            Log.log_info(f"Persisted Research_Dataset to {out_path}")
        except Exception as persist_error:
            result.status = STATUS_PERSIST_FAILED
            result.error = f"Failed to persist Research_Dataset to {out_path}: {persist_error}"
            Log.log_error(result.error)

    # --- Always report the run summary (Req 5.7), reporting zero where none
    # apply, regardless of the persist outcome.
    Log.log_info(f"Research_Dataset run complete: produced={produced} unparseable_excluded={unparseable_excluded}")

    return result


def main() -> int:
    """CLI entry: parse args, run the dataset build, print the summary, exit.

    Parses the ``--market-id``, ``--from``, ``--to``, and ``--out`` arguments,
    runs :func:`build_dataset`, prints the produced-record and
    unparseable-excluded run summary (Req 5.7), and returns a non-zero exit code
    when the Data_Store was unreachable (Req 4.6) or Parquet persistence failed
    (Req 5.4), so a scheduler/deploy step can gate on it.

    Returns:
        The process exit code: ``0`` on success, non-zero on an unreachable
        store or a persistence failure.
    """
    parser = argparse.ArgumentParser(
        description="Build the Betfair Research_Dataset from captured odds (SP-368).",
    )
    parser.add_argument(
        "--market-id",
        dest="market_ids",
        action="append",
        default=None,
        metavar="MARKET_ID",
        help="Restrict the read to this market_id. Repeat to supply several.",
    )
    parser.add_argument(
        "--from",
        dest="ts_start",
        type=_parse_timestamp,
        default=None,
        metavar="TIMESTAMP",
        help="Inclusive lower Capture_Timestamp bound (ISO 8601). Open below when omitted.",
    )
    parser.add_argument(
        "--to",
        dest="ts_end",
        type=_parse_timestamp,
        default=None,
        metavar="TIMESTAMP",
        help="Inclusive upper Capture_Timestamp bound (ISO 8601). Open above when omitted.",
    )
    parser.add_argument(
        "--out",
        dest="out_path",
        default=None,
        metavar="PATH",
        help="Persist the Research_Dataset to this path as Parquet.",
    )
    args = parser.parse_args()

    result = build_dataset(
        market_ids=args.market_ids,
        ts_start=args.ts_start,
        ts_end=args.ts_end,
        out_path=args.out_path,
    )

    # Print the run summary (Req 5.7). build_dataset also logs it; the printed
    # line is the operator's at-a-glance stdout surface.
    print(_summary_line(result))

    if not result.ok:
        if result.error:
            print(result.error, file=sys.stderr)
        return 1
    return 0


def _parse_timestamp(value: str) -> datetime:
    """Parse a CLI ``--from`` / ``--to`` value into a ``datetime`` (argparse type).

    Accepts an ISO 8601 timestamp (``datetime.fromisoformat``). Raises
    ``argparse.ArgumentTypeError`` on an unparseable value so argparse reports a
    clean usage error rather than a traceback.

    Args:
        value: The raw CLI argument string.

    Returns:
        The parsed :class:`datetime`.
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO 8601 timestamp {value!r}: {error}") from error


def _summary_line(result: BuildResult) -> str:
    """Build the one-line run summary for the operator's stdout surface (Req 5.7).

    A single readable line carrying the run status, the produced-record and
    unparseable-excluded counts (reported as zero where none apply), the persist
    outcome where an output path was given, and the operator-facing error where
    one occurred.

    Args:
        result: The :func:`build_dataset` result.

    Returns:
        The one-line summary string.
    """
    parts = [
        f"produced={result.produced}",
        f"unparseable_excluded={result.unparseable_excluded}",
    ]
    if result.excluded_markets:
        parts.append(f"missing_target_markets={len(result.excluded_markets)}")
    if result.out_path:
        parts.append(f"persisted={'YES' if result.persisted else 'no'}")
    line = f"Research_Dataset[{result.status}]: " + " ".join(parts)
    if result.error:
        line += f" ({result.error})"
    return line


if __name__ == "__main__":
    raise SystemExit(main())
