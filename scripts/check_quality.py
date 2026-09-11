"""Daily post-event data-quality check for Betfair capture (SP-332, Req 7).

This is the recurring Quality_Check wrapper: the thin I/O + orchestration layer
over the pure ``logic/quality_checks.py`` decision functions. It is intended to
be scheduled by Rundeck once per calendar day at a fixed time (Req 8.1, 8.2),
independent of the capture cadence.

The durable Quality_Report record lives in two Postgres results tables in the
``bf`` schema (Req 7.4): ``bf.quality_run`` (one row per run) and
``bf.quality_match_result`` (one row per verified match). Following the
``scripts/verify_db.py`` readiness pattern, this wrapper creates only the absent
results tables on startup (the ``missing_tables`` set difference), leaving any
existing rows untouched.

This module reuses the same ``.env``/``DotenvLoader`` + ``psycopg2`` connection
approach as ``verify_db.py`` and ``check_freshness.py``. The pure quality
decisions are cross-platform and property-tested; this wrapper does the I/O and
is expected to run against ``my_postgres`` on the always-on Raspberry Pi 500
(Linux/ARM), the sole capture host.

Scaffolding note: this task (9.1) defines the constants, the results-table DDL,
and the schema-readiness helper. The full ``check_quality`` orchestration (9.2)
and the ``main`` one-line summary / exit codes (9.3) are implemented in later
tasks.
"""

import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from logic.deploy_checks import missing_tables, validate_env
from logic.quality_checks import (
    CADENCE_TIER_INTERVALS_S,
    QualityThresholds,
    aggregate_match,
    consistency_result,
    coverage_result,
    default_look_back_window,
    expected_sample_count,
    present_result,
    useful_result,
)
from output.log import Output as Log

# Required DB connection keys read from .env (mirrors verify_db.py / check_freshness.py).
REQUIRED_DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]

# Connection timeout in seconds and connection retry budget (Req 7.5).
CONNECT_TIMEOUT_S = 10
CONNECT_ATTEMPTS = 3

# Maximum wall-clock duration a single run may take before it is abandoned (Req 8.4).
MAX_RUN_DURATION_S = 3600

# Single-run guard: the run is skipped if this lock is already held (Req 8.3).
LOCK_FILE = os.path.join(os.path.dirname(__file__), ".quality_run.lock")

# Durable record (Req 7.4): two Postgres results tables in the bf schema, not a
# JSONL file. RESULTS_SCHEMA + these names are the single documented location
# the operator reads the Quality_Report from (Req 7.4, 10.1).
RESULTS_SCHEMA = "bf"
RUN_TABLE = "quality_run"  # one row per Quality_Check run
MATCH_RESULT_TABLE = "quality_match_result"  # one row per verified match

# Rows older than this are pruned each run (>= Req 7.4's 30-day minimum).
RETENTION_DAYS = 90

# Bare-table-name -> exact CREATE TABLE DDL for the two results tables. Follows
# the exact conventions in scripts/verify_db.py: CREATE TABLE IF NOT EXISTS, the
# bf schema, text COLLATE pg_catalog."default" string columns, timestamp with
# time zone for timestamps, jsonb evidence, TABLESPACE pg_default, and
# ALTER TABLE IF EXISTS ... OWNER to postgres. Only the entries reported absent
# by missing_tables are ever executed, so existing rows are left untouched.
RESULTS_TABLE_DDL = {
    RUN_TABLE: """
        CREATE TABLE IF NOT EXISTS bf.quality_run
        (
            run_id uuid NOT NULL,
            run_started timestamp with time zone,
            run_finished timestamp with time zone,
            look_back_start timestamp with time zone,
            look_back_end timestamp with time zone,
            matches_verified integer,
            matches_passed integer,
            matches_failed integer,
            overall_alert boolean,
            status text COLLATE pg_catalog."default",
            notes text COLLATE pg_catalog."default"
        )
        TABLESPACE pg_default;
        ALTER TABLE IF EXISTS bf.quality_run
            OWNER to postgres;
    """,
    MATCH_RESULT_TABLE: """
        CREATE TABLE IF NOT EXISTS bf.quality_match_result
        (
            run_id uuid NOT NULL,
            target_id text COLLATE pg_catalog."default",
            market_id text COLLATE pg_catalog."default",
            present_outcome text COLLATE pg_catalog."default",
            coverage_outcome text COLLATE pg_catalog."default",
            consistency_outcome text COLLATE pg_catalog."default",
            useful_outcome text COLLATE pg_catalog."default",
            overall_outcome text COLLATE pg_catalog."default",
            evidence jsonb
        )
        TABLESPACE pg_default;
        ALTER TABLE IF EXISTS bf.quality_match_result
            OWNER to postgres;
    """,
}

# The bare names of the results tables required for the durable record (Req 7.4).
REQUIRED_RESULTS_TABLES = {RUN_TABLE, MATCH_RESULT_TABLE}

# bf.quality_run.status values (see design "Error Handling").
STATUS_COMPLETED = "COMPLETED"
STATUS_UNREACHABLE = "UNREACHABLE"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_SKIPPED = "SKIPPED"

# The data source this check covers (named in alerts, mirrors check_freshness).
DATA_SOURCE = f"{RESULTS_SCHEMA}.target/{RESULTS_SCHEMA}.market_table"


def _read_db_config(env_path: str = None) -> dict:
    """Read the required DB keys from ``.env`` via ``DotenvLoader`` (see verify_db.py).

    Missing or empty keys are returned as empty strings rather than raising, so
    ``validate_env`` can report the full set of offending keys at once instead of
    failing on the first one.

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
    """Return the required DB keys that are absent/empty in ``.env`` (Req 7.5).

    Reads the config via ``_read_db_config`` and applies
    ``logic.deploy_checks.validate_env`` so the orchestration (task 9.2) can gate
    on required connection details before attempting to connect, reporting the
    full set of offending keys at once (the ``verify_db.py`` pattern).

    Args:
        env_path: Optional explicit path to the ``.env`` file (mainly for tests).

    Returns:
        The subset of ``REQUIRED_DB_KEYS`` (in order) that is missing or empty.
    """
    config = _read_db_config(env_path)
    return validate_env(config, REQUIRED_DB_KEYS)


def _present_results_tables(cursor) -> set:
    """Return the set of bare table names present in the ``bf`` schema."""
    cursor.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (RESULTS_SCHEMA,),
    )
    return {row[0] for row in cursor.fetchall()}


def _connect(config: dict):
    """Open a psycopg2 connection with the shared connect timeout (Req 7.5).

    Mirrors the ``verify_db.py`` / ``check_freshness.py`` connection approach: a
    direct ``psycopg2.connect`` with ``connect_timeout=CONNECT_TIMEOUT_S`` and
    autocommit enabled. The retry-up-to-``CONNECT_ATTEMPTS`` loop and the
    ``validate_env`` config gate are applied by the orchestration in task 9.2;
    this is the low-level connection scaffolding they build on.

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


def ensure_results_tables(cursor) -> list[str]:
    """Ensure the two results tables exist, creating only the absent ones.

    Follows the ``scripts/verify_db.py`` readiness pattern: the schema is created
    if needed, then ``logic.deploy_checks.missing_tables`` computes which of the
    required results tables are absent, and ONLY those are created from
    ``RESULTS_TABLE_DDL``. Tables that already exist are never re-created, so
    existing rows are left untouched (Req 9.2, 9.3).

    Args:
        cursor: An open psycopg2 cursor on a connection with autocommit enabled.

    Returns:
        The sorted list of results tables this call created (empty when both
        already existed).
    """
    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {RESULTS_SCHEMA}")

    present = _present_results_tables(cursor)
    absent = missing_tables(present, REQUIRED_RESULTS_TABLES)

    created: list[str] = []
    for table in sorted(absent):
        cursor.execute(RESULTS_TABLE_DDL[table])
        created.append(table)
        Log.log_info(f"Created missing results table: {RESULTS_SCHEMA}.{table}")
    return created


def _acquire_lock(lock_path: str = LOCK_FILE) -> bool:
    """Acquire the single-run guard, returning True on success (Req 8.3).

    Uses an exclusive-create open (``O_CREAT | O_EXCL``): if the lock file
    already exists a prior run is still in progress, so the new invocation
    should skip. The pid is written for diagnostics.

    Args:
        lock_path: Path to the lock file.

    Returns:
        ``True`` when the lock was acquired, ``False`` when it is already held.
    """
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)
    return True


def _release_lock(lock_path: str = LOCK_FILE) -> None:
    """Release the single-run guard (best-effort)."""
    try:
        os.remove(lock_path)
    except OSError:
        pass


def _parse_declared_runner_ids(runner_ids: str | None) -> list[str] | None:
    """Parse the Target ``runner_ids`` column into a list of runner id strings.

    The stored form is ``id-name`` pairs, pipe-delimited (e.g.
    ``56764-Fulham|56343-Everton|58805-The Draw``), as written by
    ``target_service.py``. Each entry's runner id is the text before the first
    ``-``. Returns ``None`` when the column is absent, empty, or has no usable
    entries so the pure Consistency check fails it (Req 4.7).

    Args:
        runner_ids: The raw ``runner_ids`` column value.

    Returns:
        The list of declared runner id strings, or ``None`` when unavailable.
    """
    if not runner_ids or not runner_ids.strip():
        return None
    ids: list[str] = []
    for entry in runner_ids.split("|"):
        entry = entry.strip()
        if not entry:
            continue
        ids.append(entry.split("-", 1)[0])
    return ids or None


def _json_default(value):
    """JSON serializer fallback for ``datetime`` (and other) evidence values."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _dump_evidence(match_result) -> str:
    """Serialize a MatchQualityResult's per-dimension evidence to JSON text.

    Folds each dimension's ``reason`` and ``evidence`` into a single ``jsonb``
    payload keyed by dimension, so one ``bf.quality_match_result`` row explains
    every failure without a per-dimension schema. ``datetime`` values are
    ISO-serialized via ``_json_default``.
    """
    payload = {}
    for name in ("present", "coverage", "consistency", "useful"):
        outcome = getattr(match_result, name)
        payload[name] = {
            "outcome": outcome.outcome,
            "reason": outcome.reason,
            "evidence": outcome.evidence,
        }
    return json.dumps(payload, default=_json_default)


def _evaluate_match(target, rows, thresholds: QualityThresholds, now: datetime):
    """Evaluate one Completed_Match against the four pure quality dimensions.

    This is I/O-free glue: it derives the per-match inputs the pure
    ``logic/quality_checks.py`` functions expect from the already-fetched target
    tuple and its associated Market_Table rows, calls each pure decision
    function, and rolls them up via :func:`aggregate_match`. All quality
    decisions live in the pure layer; this only shapes inputs (Req 9.2).

    A target with no associated rows is a Present failure with the other three
    dimensions ``NOT_EVALUATED`` (Req 1.6, 6.5) -- there is no captured data to
    evaluate them against.

    Args:
        target: ``(target_id, market_id, runner_ids, start_time, status,
            last_updated)`` for the settled Target.
        rows: The associated ``(timestamp, market_id, runner_id, odds)`` rows in
            storage order (``ctid``), possibly empty.
        thresholds: The calibrated :class:`QualityThresholds`.
        now: The Quality_Check run start time (settlement proxy fallback).

    Returns:
        A :class:`logic.quality_checks.MatchQualityResult` for the match.
    """
    target_id, market_id, runner_ids, start_time, _status, last_updated = target

    row_count = len(rows)
    present = present_result(market_id, row_count)

    # No captured data: Present fails; the other three cannot be evaluated
    # against absent rows (Req 1.6, 6.5).
    if row_count == 0:
        return aggregate_match(
            target_id=target_id,
            market_id=market_id,
            present=present,
            coverage=None,
            consistency=None,
            useful=None,
        )

    timestamps = [r[0] for r in rows]
    earliest_ts = min(timestamps)
    latest_ts = max(timestamps)

    # Settlement proxy: the Target's last_updated where present, else the latest
    # captured row timestamp (per the design's settlement-proxy note).
    settlement_ts = last_updated if last_updated is not None else latest_ts

    # --- Coverage inputs ---
    # Expected volume is anchored to the intended cadence across the lifecycle:
    # pre-match spans the earliest captured row to start_time; in-play spans
    # start_time to the intended in-play end (start_time + inplay_duration_s).
    prematch_start = min(earliest_ts, start_time)
    inplay_end = start_time + timedelta(seconds=thresholds.inplay_duration_s)
    expected = expected_sample_count(
        start_time=start_time,
        prematch_start=prematch_start,
        inplay_end=inplay_end,
        tier_intervals=CADENCE_TIER_INTERVALS_S,
        inplay_interval_s=thresholds.inplay_interval_s,
    )
    coverage = coverage_result(
        actual_count=row_count,
        expected_count=expected,
        row_timestamps=timestamps,
        start_time=start_time,
        thresholds=thresholds,
    )

    # --- Consistency inputs ---
    odds_values = [r[3] for r in rows]
    runner_ids_in_rows = [r[2] for r in rows]
    rows_in_storage_order = [(r[2], r[0]) for r in rows]
    dedup_keys = [(r[1], r[2], str(r[0])) for r in rows]
    declared_runner_ids = _parse_declared_runner_ids(runner_ids)
    consistency = consistency_result(
        odds_values=odds_values,
        runner_ids_in_rows=runner_ids_in_rows,
        declared_runner_ids=declared_runner_ids,
        rows_in_storage_order=rows_in_storage_order,
        dedup_keys=dedup_keys,
        null_price_threshold=thresholds.null_price_ratio,
    )

    # --- Useful inputs ---
    useful = useful_result(
        row_count=row_count,
        earliest_ts=earliest_ts,
        latest_ts=latest_ts,
        start_time=start_time,
        settlement_ts=settlement_ts,
        thresholds=thresholds,
    )

    return aggregate_match(
        target_id=target_id,
        market_id=market_id,
        present=present,
        coverage=coverage,
        consistency=consistency,
        useful=useful,
    )


def _select_settled_targets(cursor, window_start: datetime, run_start: datetime) -> list:
    """Select settled targets whose ``start_time`` is within the window (Req 1.1).

    Returns ``(target_id, market_id, runner_ids, start_time, status,
    last_updated)`` tuples for Targets whose ``status`` is ``CLOSED``/``EXPIRED``
    and whose ``start_time`` falls between the window start and the run start.
    """
    cursor.execute(
        """
        SELECT target_id, market_id, runner_ids, start_time, status, last_updated
        FROM bf.target
        WHERE status IN ('CLOSED', 'EXPIRED')
          AND start_time BETWEEN %(window_start)s AND %(run_start)s
        """,
        {"window_start": window_start, "run_start": run_start},
    )
    return list(cursor.fetchall())


def _select_market_rows(cursor, market_id: str) -> list:
    """Select a match's Market_Table rows in storage order (``ctid``) (Req 1.5, 4.5)."""
    cursor.execute(
        """
        SELECT "timestamp", market_id, runner_id, odds
        FROM bf.market_table
        WHERE market_id = %(market_id)s
        ORDER BY ctid
        """,
        {"market_id": market_id},
    )
    return list(cursor.fetchall())


def _insert_run_row(cursor, run_summary: dict) -> None:
    """Insert the single ``bf.quality_run`` row for this run (Req 7.4)."""
    cursor.execute(
        """
        INSERT INTO bf.quality_run
            (run_id, run_started, run_finished, look_back_start, look_back_end,
             matches_verified, matches_passed, matches_failed, overall_alert,
             status, notes)
        VALUES
            (%(run_id)s, %(run_started)s, %(run_finished)s, %(look_back_start)s,
             %(look_back_end)s, %(matches_verified)s, %(matches_passed)s,
             %(matches_failed)s, %(overall_alert)s, %(status)s, %(notes)s)
        """,
        run_summary,
    )


def _insert_match_row(cursor, run_id: str, match_result) -> None:
    """Insert one ``bf.quality_match_result`` row for a verified match (Req 7.4).

    Maps the four ``DimensionOutcome`` values into the ``*_outcome`` columns and
    folds every dimension's reason/evidence into the ``evidence`` ``jsonb``.
    """
    cursor.execute(
        """
        INSERT INTO bf.quality_match_result
            (run_id, target_id, market_id, present_outcome, coverage_outcome,
             consistency_outcome, useful_outcome, overall_outcome, evidence)
        VALUES
            (%(run_id)s, %(target_id)s, %(market_id)s, %(present)s, %(coverage)s,
             %(consistency)s, %(useful)s, %(overall)s, %(evidence)s)
        """,
        {
            "run_id": run_id,
            "target_id": match_result.target_id,
            "market_id": match_result.market_id,
            "present": match_result.present.outcome,
            "coverage": match_result.coverage.outcome,
            "consistency": match_result.consistency.outcome,
            "useful": match_result.useful.outcome,
            "overall": match_result.overall,
            "evidence": _dump_evidence(match_result),
        },
    )


def _prune_old_rows(cursor, now: datetime) -> None:
    """Delete run + match rows older than ``RETENTION_DAYS`` (Req 7.4).

    Each run prunes both results tables of rows whose parent run started before
    the retention cutoff (default 90 days, comfortably over the 30-day minimum).
    Match rows are removed first via their ``run_id`` to avoid orphans.
    """
    cutoff = now - timedelta(days=RETENTION_DAYS)
    cursor.execute(
        """
        DELETE FROM bf.quality_match_result
        WHERE run_id IN (
            SELECT run_id FROM bf.quality_run WHERE run_started < %(cutoff)s
        )
        """,
        {"cutoff": cutoff},
    )
    cursor.execute(
        "DELETE FROM bf.quality_run WHERE run_started < %(cutoff)s",
        {"cutoff": cutoff},
    )


def check_quality(env_path: str = None, now=None, look_back_hours=None, thresholds=None) -> dict:
    """Run the daily Quality_Check and write the durable record (Req 7).

    This is the thin I/O + orchestration layer: it gates on required config,
    acquires the single-run lock (skipping if held, Req 8.3), connects with a
    retry budget (Req 7.5), ensures the results tables exist (creating only
    absent ones, the verify_db.py pattern), selects settled targets in the
    Look_Back_Window and their Market_Table rows, delegates every quality
    decision to the pure ``logic/quality_checks.py`` functions per match, writes
    one ``bf.quality_run`` row plus one ``bf.quality_match_result`` row per
    match, and prunes rows older than ``RETENTION_DAYS`` (Req 7.4). It contains
    no quality decisions.

    Validates: Requirements 1.1, 1.4, 1.5, 1.6, 2.5, 6.5, 7.1, 7.4, 7.5, 8.3, 8.4

    Args:
        env_path: Optional explicit ``.env`` path (mainly for tests).
        now: The Quality_Check run start time (timezone-aware). Defaults to
            ``datetime.now(UTC)``.
        look_back_hours: Optional Look_Back_Window override in hours (1-168,
            Req 1.2). When ``None`` the previous-calendar-day default is used.
        thresholds: Optional :class:`QualityThresholds` override. Defaults to the
            single documented source in ``logic/quality_checks.py``.

    Returns:
        A dict result the ``main`` summary/exit-code layer (task 9.3) renders:
            ``status`` (str): one of ``COMPLETED``/``UNREACHABLE``/``TIMEOUT``/
                ``SKIPPED``.
            ``run_id`` (str | None): the run's uuid (``None`` when skipped or
                never connected).
            ``matches_verified`` / ``matches_passed`` / ``matches_failed`` (int).
            ``overall_alert`` (bool): ``True`` on any FAIL, unreachable, or zero
                verified matches (Req 7.2, 7.5, 7.7).
            ``look_back_start`` / ``look_back_end`` (datetime | None).
            ``results`` (list): the per-match ``MatchQualityResult`` objects.
            ``error`` (str | None): operator-facing error, or ``None``.
    """
    if now is None:
        now = datetime.now(UTC)
    if thresholds is None:
        thresholds = QualityThresholds()

    # Look_Back_Window: explicit hours override, else previous-calendar-day.
    if look_back_hours is not None:
        window_start = now - timedelta(hours=look_back_hours)
        window_end = now
    else:
        window_start, window_end = default_look_back_window(now)

    result = {
        "status": STATUS_UNREACHABLE,
        "run_id": None,
        "matches_verified": 0,
        "matches_passed": 0,
        "matches_failed": 0,
        "overall_alert": True,
        "look_back_start": window_start,
        "look_back_end": window_end,
        "results": [],
        "error": None,
    }

    # --- Config gate (Req 7.5): do not connect if required keys are missing. ---
    missing = _missing_db_config(env_path)
    if missing:
        result["error"] = "Missing required DB connection details in .env: " + ", ".join(missing)
        Log.log_error(result["error"])
        return result

    # --- Single-run guard (Req 8.3): skip if a prior run is still in progress. ---
    if not _acquire_lock():
        result["status"] = STATUS_SKIPPED
        result["overall_alert"] = False
        result["error"] = "skipped due to in-progress execution"
        Log.log_warning(f"Quality_Check skipped: {result['error']}")
        # Best-effort record the skip as a run row when a connection is available.
        _record_skipped_run(env_path, now, window_start, window_end)
        return result

    try:
        config = _read_db_config(env_path)

        # --- Connect with a retry budget (Req 7.5). ---
        conn = None
        last_error = None
        for _attempt in range(CONNECT_ATTEMPTS):
            try:
                conn = _connect(config)
                break
            except (Exception, psycopg2.DatabaseError) as error:
                last_error = error
                Log.log_warning(f"Data store connection attempt failed: {error}")
        if conn is None:
            result["error"] = (
                f"Data store unreachable after {CONNECT_ATTEMPTS} attempts: {last_error}"
            )
            Log.log_error(result["error"])
            return result

        run_started = datetime.now(UTC)
        run_id = str(uuid.uuid4())
        deadline = run_started + timedelta(seconds=MAX_RUN_DURATION_S)
        timed_out = False

        try:
            with conn.cursor() as cursor:
                ensure_results_tables(cursor)

                targets = _select_settled_targets(cursor, window_start, window_end)

                match_results = []
                for target in targets:
                    if datetime.now(UTC) > deadline:
                        timed_out = True
                        Log.log_error(
                            f"Quality_Check run {run_id} exceeded "
                            f"{MAX_RUN_DURATION_S}s; abandoning remaining matches."
                        )
                        break
                    market_id = target[1]
                    rows = _select_market_rows(cursor, market_id) if market_id else []
                    match_results.append(_evaluate_match(target, rows, thresholds, now))

                passed = sum(1 for m in match_results if m.overall == "PASS")
                failed = len(match_results) - passed
                # Alert on any FAIL, zero verified matches (Req 7.7), or timeout.
                overall_alert = failed > 0 or len(match_results) == 0 or timed_out
                status = STATUS_TIMEOUT if timed_out else STATUS_COMPLETED

                run_finished = datetime.now(UTC)
                notes = None
                if timed_out:
                    notes = f"run exceeded MAX_RUN_DURATION_S={MAX_RUN_DURATION_S}s"
                elif len(match_results) == 0:
                    notes = "no verifiable Completed_Matches in window"

                _insert_run_row(
                    cursor,
                    {
                        "run_id": run_id,
                        "run_started": run_started,
                        "run_finished": run_finished,
                        "look_back_start": window_start,
                        "look_back_end": window_end,
                        "matches_verified": len(match_results),
                        "matches_passed": passed,
                        "matches_failed": failed,
                        "overall_alert": overall_alert,
                        "status": status,
                        "notes": notes,
                    },
                )
                for match_result in match_results:
                    _insert_match_row(cursor, run_id, match_result)

                _prune_old_rows(cursor, now)

            result.update(
                {
                    "status": status,
                    "run_id": run_id,
                    "matches_verified": len(match_results),
                    "matches_passed": passed,
                    "matches_failed": failed,
                    "overall_alert": overall_alert,
                    "results": match_results,
                    "error": None,
                }
            )
        finally:
            conn.close()
    finally:
        _release_lock()

    return result


def _record_skipped_run(env_path, now, window_start, window_end) -> None:
    """Best-effort record a SKIPPED run row when a connection is available (Req 8.3).

    A concurrent-trigger skip is recorded durably as a ``bf.quality_run`` row
    with ``status = 'SKIPPED'`` where the store is reachable; if it is not, the
    log line emitted by the caller is the only record. Failures here are
    swallowed -- the skip must never itself abort or corrupt anything.
    """
    try:
        config = _read_db_config(env_path)
        conn = _connect(config)
    except (Exception, psycopg2.DatabaseError):
        return
    try:
        with conn.cursor() as cursor:
            ensure_results_tables(cursor)
            _insert_run_row(
                cursor,
                {
                    "run_id": str(uuid.uuid4()),
                    "run_started": now,
                    "run_finished": now,
                    "look_back_start": window_start,
                    "look_back_end": window_end,
                    "matches_verified": 0,
                    "matches_passed": 0,
                    "matches_failed": 0,
                    "overall_alert": False,
                    "status": STATUS_SKIPPED,
                    "notes": "skipped due to in-progress execution",
                },
            )
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_warning(f"Could not record SKIPPED run row: {error}")
    finally:
        conn.close()


def _summary_line(result: dict) -> str:
    """Build the single-line run summary for the Rundeck stdout surface (b).

    A single readable line carrying the headline result at a glance: the run
    status, the verified/passed/failed counts, the alert flag, and the run_id
    when one exists. On a run that never wrote a run row (missing config,
    unreachable, or skipped) the operator-facing ``error`` is appended so the
    one-liner still explains itself.

    Args:
        result: The :func:`check_quality` result dict.

    Returns:
        The one-line summary string.
    """
    parts = [
        f"Quality_Check {result['status']}",
        f"verified={result['matches_verified']}",
        f"passed={result['matches_passed']}",
        f"failed={result['matches_failed']}",
        f"alert={'YES' if result['overall_alert'] else 'no'}",
    ]
    if result.get("run_id"):
        parts.append(f"run_id={result['run_id']}")
    line = f"Quality_Check[{result['status']}]: " + " ".join(parts[1:])
    if result.get("error"):
        line += f" ({result['error']})"
    return line


def main() -> int:
    """CLI entry: print a one-line run summary and return an exit code (Req 7).

    Runs :func:`check_quality`, prints a single readable run-summary line for the
    Rundeck output (surface (b)), and returns the exit code. The exit code is
    driven by ``overall_alert`` from the result dict, which already encodes "any
    FAIL / zero verified matches / unreachable / timeout" -- so a non-zero exit
    is returned on any FAIL, an unreachable store, or zero verifiable matches
    (Req 7.2, 7.3, 7.5, 7.7) and a zero exit when all verified matches pass
    (Req 7.6). A SKIPPED run (a concurrent trigger while a prior run is still in
    progress, Req 8.3) is not a data failure: ``check_quality`` sets its
    ``overall_alert`` to ``False`` so the skip exits zero and does not turn the
    Rundeck run red as though the data were deficient. The durable record lives
    in the ``bf.quality_run`` / ``bf.quality_match_result`` tables, not stdout.

    Returns:
        ``0`` when the run raised no Quality_Alert (all-pass COMPLETED or a
        SKIPPED run); ``1`` when ``overall_alert`` is set.
    """
    result = check_quality()

    line = _summary_line(result)
    print(line)

    if result["overall_alert"]:
        # Mirror check_freshness: echo the alert line to stderr so the Rundeck
        # run status surfaces the failure (Req 7.2, 7.3, 7.5, 7.7).
        print(line, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
