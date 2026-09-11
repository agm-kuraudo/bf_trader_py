"""On-demand Quality_Report reader for Betfair capture (SP-332, Req 10).

This is surface (c) of the feature: a separate command the operator runs by hand
(from Windows or the Pi) to read the durable Quality_Report record and print a
presentable formatted report. It is the supported, presentable path over the two
Postgres results tables in the ``bf`` schema (``bf.quality_run`` and
``bf.quality_match_result``) that the daily check (``scripts/check_quality.py``)
writes.

Unlike the daily check, this command is strictly **read-only**: it never creates
tables, never writes rows, and performs no quality evaluation. It only issues
``SELECT``s to fetch the requested run and its match rows, then delegates all
formatting to the pure formatters in ``logic/quality_report.py`` for
``--format text|markdown|csv``.

It reuses the same ``.env``/``DotenvLoader`` + ``psycopg2`` connection approach as
``scripts/check_quality.py`` / ``scripts/verify_db.py`` / ``scripts/check_freshness.py``
(Req 9.3), with no connection parameters hard-coded. Rows are fetched with a
``RealDictCursor`` so each row is a mapping matching what the ``logic/quality_report.py``
formatters expect.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2
from psycopg2.extras import RealDictCursor

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from logic.deploy_checks import validate_env
from logic.quality_report import (
    format_report_csv,
    format_report_markdown,
    format_report_text,
)
from output.log import Output as Log

# Required DB connection keys read from .env (mirrors check_quality.py / verify_db.py).
REQUIRED_DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]

# Connection timeout in seconds (mirrors check_quality.py).
CONNECT_TIMEOUT_S = 10

# The durable Quality_Report record lives in these two tables in the bf schema.
# This command reads them; it never creates or writes them.
RESULTS_SCHEMA = "bf"
RUN_TABLE = "quality_run"  # one row per Quality_Check run
MATCH_RESULT_TABLE = "quality_match_result"  # one row per verified match

# Supported --format values, mapped to their pure formatter. text is the default.
VALID_FORMATS = ("text", "markdown", "csv")


def _read_db_config(env_path: str = None) -> dict:
    """Read the required DB keys from ``.env`` via ``DotenvLoader`` (see check_quality.py).

    Missing or empty keys are returned as empty strings rather than raising, so
    ``validate_env`` can report the full set of offending keys at once.

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
            config[key] = ""
    return config


def _connect(config: dict):
    """Open a read-only-intent psycopg2 connection with the shared connect timeout.

    Mirrors the ``check_quality.py`` connection approach: a direct
    ``psycopg2.connect`` with ``connect_timeout=CONNECT_TIMEOUT_S``. This command
    only ever issues ``SELECT``s; nothing here creates tables or writes rows.

    Args:
        config: DB config mapping (as returned by ``_read_db_config``).

    Returns:
        An open psycopg2 connection.
    """
    return psycopg2.connect(
        host=config["DB_HOST"],
        port=config["DB_PORT"],
        dbname=config["DB_NAME"],
        user=config["DB_USER"],
        password=config["DB_PWD"],
        connect_timeout=CONNECT_TIMEOUT_S,
    )


def select_run(conn, run_id=None, on_date=None, date_from=None, date_to=None):
    """Resolve which run to report and fetch its ``bf.quality_run`` row.

    Read-only ``SELECT`` on ``bf.quality_run``. The selection modes are mutually
    exclusive (the CLI enforces this); this resolves them in priority order:

      - ``run_id``: the specific run with that id;
      - ``on_date`` (a ``YYYY-MM-DD`` string): the latest run whose
        ``run_started`` falls on that calendar day;
      - ``date_from`` + ``date_to`` (``YYYY-MM-DD`` strings): the latest run whose
        ``run_started`` falls in the inclusive ``[from, to]`` range;
      - default (nothing supplied): the latest run overall
        (``ORDER BY run_started DESC LIMIT 1``).

    Args:
        conn: An open psycopg2 connection.
        run_id: Optional specific run uuid to report.
        on_date: Optional ``YYYY-MM-DD`` string for a single-day lookup.
        date_from: Optional inclusive range start (``YYYY-MM-DD``).
        date_to: Optional inclusive range end (``YYYY-MM-DD``).

    Returns:
        The matching ``bf.quality_run`` row as a mapping, or ``None`` when no run
        matches the selection.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        if run_id is not None:
            cursor.execute(
                "SELECT * FROM bf.quality_run WHERE run_id = %(run_id)s "
                "ORDER BY run_started DESC LIMIT 1",
                {"run_id": run_id},
            )
        elif on_date is not None:
            cursor.execute(
                "SELECT * FROM bf.quality_run "
                "WHERE run_started::date = %(on_date)s "
                "ORDER BY run_started DESC LIMIT 1",
                {"on_date": on_date},
            )
        elif date_from is not None and date_to is not None:
            cursor.execute(
                "SELECT * FROM bf.quality_run "
                "WHERE run_started::date BETWEEN %(date_from)s AND %(date_to)s "
                "ORDER BY run_started DESC LIMIT 1",
                {"date_from": date_from, "date_to": date_to},
            )
        else:
            cursor.execute(
                "SELECT * FROM bf.quality_run ORDER BY run_started DESC LIMIT 1"
            )
        return cursor.fetchone()


def select_match_rows(conn, run_id, failures_only=False):
    """Fetch a run's ``bf.quality_match_result`` rows (read-only ``SELECT``).

    Args:
        conn: An open psycopg2 connection.
        run_id: The run whose match rows to fetch.
        failures_only: When ``True``, restrict to rows with
            ``overall_outcome = 'FAIL'`` (Req 10.2 alert focus).

    Returns:
        The list of matching ``bf.quality_match_result`` rows as mappings.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        if failures_only:
            cursor.execute(
                "SELECT * FROM bf.quality_match_result "
                "WHERE run_id = %(run_id)s AND overall_outcome = 'FAIL' "
                "ORDER BY target_id",
                {"run_id": run_id},
            )
        else:
            cursor.execute(
                "SELECT * FROM bf.quality_match_result "
                "WHERE run_id = %(run_id)s ORDER BY target_id",
                {"run_id": run_id},
            )
        return list(cursor.fetchall())


def report_quality(
    env_path: str = None,
    run_id=None,
    on_date=None,
    date_from=None,
    date_to=None,
    failures_only: bool = False,
    fmt: str = "text",
) -> str:
    """Connect (read-only), select the run + match rows, and format the report.

    This is the thin read-only I/O layer over the pure formatters: it gates on
    required config, connects with the shared ``.env``/``DotenvLoader`` +
    ``psycopg2`` approach, selects the requested run and its match rows via
    read-only ``SELECT``s, and delegates all formatting to
    ``logic/quality_report.py`` for the chosen ``fmt``. It contains no quality
    decisions and performs no quality evaluation.

    Validates: Requirements 9.3, 10.1, 10.2

    Args:
        env_path: Optional explicit ``.env`` path (mainly for tests).
        run_id: Optional specific run uuid to report.
        on_date: Optional ``YYYY-MM-DD`` string for a single-day lookup.
        date_from: Optional inclusive range start (``YYYY-MM-DD``).
        date_to: Optional inclusive range end (``YYYY-MM-DD``).
        failures_only: When ``True``, include only ``overall_outcome = 'FAIL'``
            match rows.
        fmt: Output format, one of ``text`` (default), ``markdown``, ``csv``.

    Returns:
        The formatted report string.

    Raises:
        ValueError: When ``fmt`` is not one of the supported formats, or when no
            run matches the selection.
        ConfigurationException: When required DB connection details are absent.
    """
    if fmt not in VALID_FORMATS:
        raise ValueError(
            f"Unsupported format '{fmt}'; expected one of {', '.join(VALID_FORMATS)}"
        )

    config = _read_db_config(env_path)
    missing = validate_env(config, REQUIRED_DB_KEYS)
    if missing:
        raise ConfigurationException(
            "Missing required DB connection details in .env: " + ", ".join(missing)
        )

    conn = _connect(config)
    try:
        run_row = select_run(
            conn,
            run_id=run_id,
            on_date=on_date,
            date_from=date_from,
            date_to=date_to,
        )
        if run_row is None:
            raise ValueError("No Quality_Check run found for the given selection")

        match_rows = select_match_rows(
            conn, run_row["run_id"], failures_only=failures_only
        )
    finally:
        conn.close()

    if fmt == "csv":
        return format_report_csv(match_rows)
    if fmt == "markdown":
        return format_report_markdown(run_row, match_rows)
    return format_report_text(run_row, match_rows)


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``argparse`` CLI parser (run-selection options mutually exclusive)."""
    parser = argparse.ArgumentParser(
        prog="report_quality.py",
        description=(
            "Read-only on-demand Quality_Report reader (SP-332). Selects a "
            "Quality_Check run from bf.quality_run / bf.quality_match_result and "
            "prints a formatted report. Never writes to the Data_Store."
        ),
    )

    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--run-id",
        dest="run_id",
        metavar="UUID",
        help="report a specific run by its run_id",
    )
    selection.add_argument(
        "--date",
        dest="on_date",
        metavar="YYYY-MM-DD",
        help="report the run(s) on a given date",
    )
    selection.add_argument(
        "--from",
        dest="date_from",
        metavar="YYYY-MM-DD",
        help="report run(s) in a date range (requires --to)",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        metavar="YYYY-MM-DD",
        help="end of the date range (requires --from)",
    )
    parser.add_argument(
        "--failures-only",
        dest="failures_only",
        action="store_true",
        help="include only matches with overall_outcome = FAIL",
    )
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=VALID_FORMATS,
        default="text",
        help="output format (default: text)",
    )
    return parser


def main(argv=None) -> int:
    """CLI entry point: parse options, print the report, return an exit code.

    Parses the CLI options (run-selection mutually exclusive; ``--from``/``--to``
    must be given together), calls :func:`report_quality`, prints the formatted
    result to stdout, and returns ``0`` on success. A non-zero exit is returned
    only on a connection or argument error (a missing/unreachable Data_Store,
    missing ``.env`` connection details, an invalid selection, or an unusable
    argument combination) -- never as a quality signal, since this command makes
    no quality decisions.

    Args:
        argv: Optional argument list (mainly for tests). Defaults to
            ``sys.argv[1:]``.

    Returns:
        ``0`` on success; a non-zero code on a connection or argument error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --from and --to must be supplied together (they define a single range).
    if bool(args.date_from) != bool(args.date_to):
        parser.error("--from and --to must be used together")

    try:
        report = report_quality(
            run_id=args.run_id,
            on_date=args.on_date,
            date_from=args.date_from,
            date_to=args.date_to,
            failures_only=args.failures_only,
            fmt=args.fmt,
        )
    except ConfigurationException as error:
        Log.log_error(f"Cannot read Quality_Report: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        Log.log_error(f"Cannot read Quality_Report: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(f"Data store error reading Quality_Report: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
