"""One-off threshold-calibration VALIDATION for Betfair capture (SP-332, task 15).

This is **not** a scheduled job and it is **not** part of the daily pipeline. It is
a single-use, read-only validation utility tied to the SP-332 ``tasks.md`` task 15
("Validate calibrated thresholds against real season data"). Its job is to confirm
that the fixed, design-anchored :class:`~logic.quality_checks.QualityThresholds`
correctly distinguish *good* captures from *deficient* ones when run over the whole
accruing season sample in ``bf.market_table`` / ``bf.target`` on the Pi's
``my_postgres``.

Crucially, it **VALIDATES** the thresholds; it does **not** re-derive or re-fit them.
The Coverage/Useful thresholds are anchored to the *intended* ``IN_PLAY`` 5s cadence
(see the design's "Threshold Calibration"), so matches captured at the currently
observed ~900s cadence are **EXPECTED to fail** Coverage and/or Useful. That coarse
cadence is a real capture defect tracked separately as **SP-343**; this feature (and
this script) only detects and reports it — it does not change the polling. Seeing the
~69-99-row "populated" matches, the 3-row stubs, and the 0-row targets fail while any
genuinely 5s-sampled match passes is the *success* signal for this validation.

Read-only guarantees:
    - It connects with the same ``.env`` / ``DotenvLoader`` + ``psycopg2`` approach as
      ``scripts/check_quality.py`` (reusing that module's ``_read_db_config`` /
      ``_connect``), but it issues only ``SELECT``s.
    - It **never** creates tables and **never** writes rows. It does **not** call
      ``check_quality.check_quality()`` (which writes to ``bf.quality_run`` /
      ``bf.quality_match_result``). Instead it reuses the *pure* evaluation glue
      (``check_quality._evaluate_match``, itself pure over ``logic/quality_checks.py``)
      and the same read-only selection ``SELECT``s.
    - The per-match report is built entirely in memory into ``run_row`` +
      ``match_rows`` dict structures shaped like the ``bf.quality_run`` /
      ``bf.quality_match_result`` columns, then rendered via the pure
      ``logic/quality_report.py`` formatters (text / markdown / csv).

Deployment note: on the Pi the wrapper venv lives at ``/usr/local/bf_trader_py/.venv``
(not ``venv``). This script itself is path-agnostic — it only needs the repo on
``sys.path`` (handled below) and a resolvable ``.env``. The live run against
``my_postgres`` is performed separately over SSH per the pi-access workflow, since the
Data_Store is only reachable on the Pi's network.

Exit codes: ``0`` on a successful validation pass (report + summary printed);
non-zero only on a connection or argument error — never as a quality signal, since a
"deficient" verdict on the current season data is the *expected* SP-343 outcome, not a
script failure.
"""

import argparse
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2

from api.auth.dotenv_loader import ConfigurationException
from logic.deploy_checks import validate_env
from logic.quality_checks import QualityThresholds
from logic.quality_report import (
    format_report_csv,
    format_report_markdown,
    format_report_text,
)
from output.log import Output as Log
from scripts.check_quality import (
    REQUIRED_DB_KEYS,
    _connect,
    _dump_evidence,
    _evaluate_match,
    _read_db_config,
    _select_market_rows,
    _select_settled_targets,
)

# A deliberately wide default look-back (~10 years) so the validation covers the
# whole accruing season sample, not just a recent day. Override with --days, or use
# --all for an explicit unbounded window. The point of task 15 is to validate the
# fixed thresholds against everything settled, not to scope to a daily window.
DEFAULT_LOOK_BACK_DAYS = 3650

# Supported --format values, mapped to the matching pure formatter (text default).
VALID_FORMATS = ("text", "markdown", "csv")


def _match_result_to_row(run_id: str, match_result) -> dict:
    """Shape one in-memory ``MatchQualityResult`` as a ``bf.quality_match_result`` row.

    Mirrors ``check_quality._insert_match_row`` exactly, but returns a plain dict
    (keyed by the ``bf.quality_match_result`` column names) instead of writing it.
    This is what the ``logic/quality_report.py`` formatters expect as ``match_rows``.

    Args:
        run_id: The synthetic in-memory run id shared by every match row.
        match_result: A :class:`logic.quality_checks.MatchQualityResult`.

    Returns:
        A mapping with the ``bf.quality_match_result`` column keys.
    """
    return {
        "run_id": run_id,
        "target_id": match_result.target_id,
        "market_id": match_result.market_id,
        "present_outcome": match_result.present.outcome,
        "coverage_outcome": match_result.coverage.outcome,
        "consistency_outcome": match_result.consistency.outcome,
        "useful_outcome": match_result.useful.outcome,
        "overall_outcome": match_result.overall,
        "evidence": _dump_evidence(match_result),
    }


def _build_run_row(
    run_id: str,
    run_started: datetime,
    run_finished: datetime,
    window_start,
    window_end: datetime,
    match_results: list,
) -> dict:
    """Shape the in-memory ``bf.quality_run`` summary row for the formatters.

    Mirrors the column set ``check_quality._insert_run_row`` writes, but is built
    entirely in memory (never inserted). ``overall_alert`` follows the same rule as
    the daily check: any FAIL match, or zero verified matches, raises the flag.

    Args:
        run_id: The synthetic in-memory run id.
        run_started: Validation start time.
        run_finished: Validation finish time.
        window_start: The look-back window start (``None`` for an unbounded --all run).
        window_end: The look-back window end (the run start).
        match_results: The per-match ``MatchQualityResult`` objects.

    Returns:
        A mapping with the ``bf.quality_run`` column keys.
    """
    passed = sum(1 for m in match_results if m.overall == "PASS")
    failed = len(match_results) - passed
    overall_alert = failed > 0 or len(match_results) == 0
    return {
        "run_id": run_id,
        "run_started": run_started,
        "run_finished": run_finished,
        "look_back_start": window_start,
        "look_back_end": window_end,
        "matches_verified": len(match_results),
        "matches_passed": passed,
        "matches_failed": failed,
        "overall_alert": overall_alert,
        "status": "VALIDATION",
        "notes": "SP-332 task 15 calibration validation (read-only; no rows written)",
    }


def _dimension_failed(match_result, name: str) -> bool:
    """True when the named dimension did not PASS (FAIL or NOT_EVALUATED)."""
    return getattr(match_result, name).outcome != "PASS"


def build_validation_summary(match_results: list) -> str:
    """Build the operator-facing validation summary text.

    Counts matches by overall PASS/FAIL and tallies, per dimension, how many matches
    did not pass it. This lets the operator eyeball that the fixed, 5s-anchored
    thresholds correctly flag the deficient captures (0-row Present failures, 3-row
    stubs, and the ~69-99-row ~900s-cadence matches expected to fail Coverage/Useful
    per SP-343) while any genuinely 5s-sampled match passes. It VALIDATES the
    design-anchored thresholds; it does not re-derive them.

    Args:
        match_results: The per-match ``MatchQualityResult`` objects.

    Returns:
        The multi-line validation summary string.
    """
    total = len(match_results)
    passed = sum(1 for m in match_results if m.overall == "PASS")
    failed = total - passed

    dims = ("present", "coverage", "consistency", "useful")
    dim_failed = {name: sum(1 for m in match_results if _dimension_failed(m, name)) for name in dims}

    lines: list[str] = []
    lines.append("Validation Summary (SP-332 task 15)")
    lines.append("=" * 35)
    lines.append(f"Matches verified: {total}")
    lines.append(f"  Overall PASS:   {passed}")
    lines.append(f"  Overall FAIL:   {failed}")
    lines.append("")
    lines.append("Non-passing matches by dimension:")
    for name in dims:
        lines.append(f"  {name:<12} {dim_failed[name]}")
    lines.append("")
    lines.append(
        "Expected per the design's Threshold Calibration: 0-row Present failures, "
        "3-row stubs, and ~69-99-row ~900s-cadence matches SHOULD fail "
        "Coverage/Useful (the SP-343 under-sampling defect, detect-only). Any "
        "genuinely 5s-sampled match SHOULD pass. This VALIDATES the fixed, "
        "5s-anchored thresholds; it does not re-derive them."
    )
    return "\n".join(lines) + "\n"


def calibrate_quality(
    env_path: str = None,
    now=None,
    look_back_days: int = DEFAULT_LOOK_BACK_DAYS,
    all_targets: bool = False,
    thresholds: QualityThresholds = None,
    fmt: str = "text",
) -> tuple[str, str]:
    """Run the read-only calibration validation and return (report, summary).

    Connects read-only, selects settled targets (``status IN ('CLOSED','EXPIRED')``)
    within the look-back window and their ``bf.market_table`` rows (``ORDER BY ctid``),
    runs the SAME pure per-match evaluation the daily wrapper uses (via
    ``check_quality._evaluate_match`` over ``logic/quality_checks.py``), then builds
    in-memory ``run_row`` + ``match_rows`` structures and formats them with the pure
    ``logic/quality_report.py`` formatters. It writes **nothing** and never calls
    ``check_quality.check_quality()``.

    Args:
        env_path: Optional explicit ``.env`` path (mainly for tests).
        now: Validation run start (timezone-aware). Defaults to ``datetime.now(UTC)``.
        look_back_days: Look-back window in days (ignored when ``all_targets``).
        all_targets: When ``True``, use an unbounded window (all settled targets).
        thresholds: Optional :class:`QualityThresholds` override (defaults to the
            single documented source — the values being validated).
        fmt: Report format, one of ``text`` (default), ``markdown``, ``csv``.

    Returns:
        A ``(report, summary)`` tuple: the formatted Quality_Report and the
        operator-facing validation summary.

    Raises:
        ValueError: When ``fmt`` is not supported.
        ConfigurationException: When required DB connection details are absent.
        psycopg2.DatabaseError / Exception: When the Data_Store is unreachable.
    """
    if fmt not in VALID_FORMATS:
        raise ValueError(f"Unsupported format '{fmt}'; expected one of {', '.join(VALID_FORMATS)}")
    if now is None:
        now = datetime.now(UTC)
    if thresholds is None:
        thresholds = QualityThresholds()

    # Wide/unbounded window: the point is to validate against the whole accruing
    # season sample, not a daily slice. --all uses the epoch as a practical floor.
    window_end = now
    if all_targets:
        window_start = datetime(1970, 1, 1, tzinfo=UTC)
    else:
        window_start = now - timedelta(days=look_back_days)

    config = _read_db_config(env_path)
    missing = validate_env(config, REQUIRED_DB_KEYS)
    if missing:
        raise ConfigurationException("Missing required DB connection details in .env: " + ", ".join(missing))

    run_started = datetime.now(UTC)
    run_id = str(uuid.uuid4())

    conn = _connect(config)
    try:
        match_results = []
        with conn.cursor() as cursor:
            targets = _select_settled_targets(cursor, window_start, window_end)
            for target in targets:
                market_id = target[1]
                rows = _select_market_rows(cursor, market_id) if market_id else []
                match_results.append(_evaluate_match(target, rows, thresholds, now))
    finally:
        conn.close()

    run_finished = datetime.now(UTC)

    run_row = _build_run_row(
        run_id,
        run_started,
        run_finished,
        None if all_targets else window_start,
        window_end,
        match_results,
    )
    match_rows = [_match_result_to_row(run_id, m) for m in match_results]

    if fmt == "csv":
        report = format_report_csv(match_rows)
    elif fmt == "markdown":
        report = format_report_markdown(run_row, match_rows)
    else:
        report = format_report_text(run_row, match_rows)

    summary = build_validation_summary(match_results)
    return report, summary


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``argparse`` CLI parser (``--days`` and ``--all`` mutually exclusive)."""
    parser = argparse.ArgumentParser(
        prog="calibrate_quality.py",
        description=(
            "One-off, read-only SP-332 task-15 validation: runs the pure quality "
            "evaluation over the accruing season data and prints a sample report + "
            "a validation summary. Never writes to the Data_Store; does not "
            "re-derive thresholds. Matches captured at the observed ~900s cadence "
            "are EXPECTED to fail (SP-343 under-sampling, detect-only)."
        ),
    )
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--days",
        dest="days",
        type=int,
        default=DEFAULT_LOOK_BACK_DAYS,
        metavar="N",
        help=(
            "look-back window in days for settled targets "
            f"(default: {DEFAULT_LOOK_BACK_DAYS}, effectively all settled data)"
        ),
    )
    window.add_argument(
        "--all",
        dest="all_targets",
        action="store_true",
        help="validate against ALL settled targets (unbounded window)",
    )
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=VALID_FORMATS,
        default="text",
        help="report output format (default: text)",
    )
    return parser


def main(argv=None) -> int:
    """CLI entry point: run the validation, print report + summary, return exit code.

    Parses the CLI options, runs :func:`calibrate_quality`, prints the formatted
    sample report followed by the validation summary to stdout, and returns ``0`` on
    success. A non-zero exit is returned only on a connection or argument error (a
    missing/unreachable Data_Store, missing ``.env`` connection details, or an
    unsupported format) — never as a quality signal, since a "deficient" verdict on
    the current season data is the expected SP-343 outcome.

    Args:
        argv: Optional argument list (mainly for tests). Defaults to ``sys.argv[1:]``.

    Returns:
        ``0`` on success; a non-zero code on a connection or argument error.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.days is not None and args.days < 1:
        parser.error("--days must be a positive integer")

    try:
        report, summary = calibrate_quality(
            look_back_days=args.days,
            all_targets=args.all_targets,
            fmt=args.fmt,
        )
    except ConfigurationException as error:
        Log.log_error(f"Cannot run calibration validation: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        Log.log_error(f"Cannot run calibration validation: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(f"Data store error during calibration validation: {error}")
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(report, end="")
    print()
    print(summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
