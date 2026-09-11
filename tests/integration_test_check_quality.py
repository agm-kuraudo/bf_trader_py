"""Integration tests for scripts/check_quality.py (SP-332, Task 12.1, Req 9.4).

REQUIRE a running my_postgres reachable with the .env credentials (repo
convention: DB-dependent tests SKIP cleanly when no store is present). Mirrors
the conventions in tests/integration_test_check_freshness.py: the
_connect()/_db_reachable() helpers, the module-level pytestmark skipif so the
suite skips cleanly when no store is reachable, and the bad-host temp .env
pattern for the unreachable-after-3-attempts case.

Covers the cases from the design's Testing Strategy (Req 9.4):
  * a real run against my_postgres produces a pass/fail Quality_Result and
    writes the durable record: one new bf.quality_run row for the run and one
    bf.quality_match_result row per verified match, with the *_outcome columns
    and evidence populated (Req 7.4)
  * the two results tables are created if absent (the verify_db.py readiness
    pattern) without disturbing existing rows
  * the unreachable-after-3-attempts path returns a non-zero-exit-equivalent
    result (status UNREACHABLE / overall_alert True) with no result rows
    written (Req 7.5), via a bad-host temp .env

This test only writes the feature's own tables (bf.quality_run /
bf.quality_match_result) keyed by a fresh run_id, then DELETEs exactly those
rows in teardown so the store is left as it was found. It never touches
bf.target or bf.market_table.

Verified on the Pi (Linux/ARM); expected to SKIP on Windows without my_postgres.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from scripts.check_quality import (
    MATCH_RESULT_TABLE,
    RESULTS_SCHEMA,
    RUN_TABLE,
    STATUS_UNREACHABLE,
    check_quality,
)


def _connect():
    import psycopg2

    from api.auth.dotenv_loader import DotenvLoader

    loader = DotenvLoader()
    conn = psycopg2.connect(
        host=loader.get_secret("DB_HOST"),
        port=loader.get_secret("DB_PORT"),
        dbname=loader.get_secret("DB_NAME"),
        user=loader.get_secret("DB_USER"),
        password=loader.get_secret("DB_PWD"),
        connect_timeout=10,
    )
    conn.autocommit = True
    return conn


def _db_reachable() -> bool:
    try:
        _connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="No reachable my_postgres data store (expected on Windows/CI without the container)",
)


def _table_present(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (RESULTS_SCHEMA, table_name),
    )
    return cursor.fetchone() is not None


def _count_rows(cursor, table_name: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {RESULTS_SCHEMA}.{table_name}")
    return cursor.fetchone()[0]


def _delete_run(run_id: str) -> None:
    """Remove exactly the run + match rows this test created (repeatable, tidy)."""
    if run_id is None:
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {RESULTS_SCHEMA}.{MATCH_RESULT_TABLE} WHERE run_id = %s",
                (run_id,),
            )
            cur.execute(
                f"DELETE FROM {RESULTS_SCHEMA}.{RUN_TABLE} WHERE run_id = %s",
                (run_id,),
            )
    finally:
        conn.close()


class TestCheckQualityIntegration:
    def test_run_produces_result_and_writes_durable_record(self):
        """A real run yields a pass/fail Quality_Result and the durable record (Req 7.4, 9.4).

        Asserts a new bf.quality_run row for the run and one
        bf.quality_match_result row per verified match, with the per-dimension
        *_outcome columns and evidence populated. Cleans up the rows it created.
        """
        # Capture the pre-run row counts so we can assert existing rows are left
        # untouched (only this run's rows are added).
        conn = _connect()
        try:
            with conn.cursor() as cur:
                # Tables may or may not exist yet; treat absence as zero rows.
                run_rows_before = _count_rows(cur, RUN_TABLE) if _table_present(cur, RUN_TABLE) else 0
        finally:
            conn.close()

        run_id = None
        try:
            result = check_quality()

            # A pass/fail Quality_Result was produced (a completed run, not
            # unreachable/skipped from this reachable store).
            assert result["status"] in ("COMPLETED", "TIMEOUT")
            run_id = result["run_id"]
            assert run_id is not None
            assert isinstance(result["overall_alert"], bool)

            conn = _connect()
            try:
                with conn.cursor() as cur:
                    # The two results tables exist after the run.
                    assert _table_present(cur, RUN_TABLE)
                    assert _table_present(cur, MATCH_RESULT_TABLE)

                    # Exactly one new bf.quality_run row for this run.
                    cur.execute(
                        f"SELECT matches_verified, matches_passed, matches_failed, "
                        f"overall_alert, status FROM {RESULTS_SCHEMA}.{RUN_TABLE} "
                        f"WHERE run_id = %s",
                        (run_id,),
                    )
                    run_rows = cur.fetchall()
                    assert len(run_rows) == 1
                    verified, passed, failed, overall_alert, status = run_rows[0]
                    assert verified == result["matches_verified"]
                    assert passed == result["matches_passed"]
                    assert failed == result["matches_failed"]
                    assert overall_alert == result["overall_alert"]
                    assert status == result["status"]

                    # Existing rows undisturbed: total run rows grew by exactly one.
                    assert _count_rows(cur, RUN_TABLE) == run_rows_before + 1

                    # One bf.quality_match_result row per verified match, with the
                    # *_outcome columns and evidence populated.
                    cur.execute(
                        f"SELECT target_id, market_id, present_outcome, coverage_outcome, "
                        f"consistency_outcome, useful_outcome, overall_outcome, evidence "
                        f"FROM {RESULTS_SCHEMA}.{MATCH_RESULT_TABLE} WHERE run_id = %s",
                        (run_id,),
                    )
                    match_rows = cur.fetchall()
                    assert len(match_rows) == result["matches_verified"]
                    for (
                        target_id,
                        market_id,
                        present_outcome,
                        coverage_outcome,
                        consistency_outcome,
                        useful_outcome,
                        overall_outcome,
                        evidence,
                    ) in match_rows:
                        assert target_id is not None
                        assert market_id is not None
                        # present_outcome is always evaluated; the others may be
                        # NOT_EVALUATED for a no-rows match, but must be recorded.
                        assert present_outcome in ("PASS", "FAIL")
                        assert coverage_outcome in ("PASS", "FAIL", "NOT_EVALUATED")
                        assert consistency_outcome in ("PASS", "FAIL", "NOT_EVALUATED")
                        assert useful_outcome in ("PASS", "FAIL", "NOT_EVALUATED")
                        assert overall_outcome in ("PASS", "FAIL")
                        # evidence jsonb is populated for every dimension.
                        assert evidence is not None
                        for dim in ("present", "coverage", "consistency", "useful"):
                            assert dim in evidence
            finally:
                conn.close()
        finally:
            _delete_run(run_id)

    def test_results_tables_created_if_absent(self):
        """A run creates the two results tables when absent (verify_db.py pattern).

        After a run both tables must exist. This asserts the readiness path
        without dropping tables (dropping would disturb existing rows on the
        shared store); the row-untouched guarantee is covered by the durable
        record test above.
        """
        run_id = None
        try:
            result = check_quality()
            run_id = result["run_id"]

            conn = _connect()
            try:
                with conn.cursor() as cur:
                    assert _table_present(cur, RUN_TABLE)
                    assert _table_present(cur, MATCH_RESULT_TABLE)
            finally:
                conn.close()
        finally:
            _delete_run(run_id)

    def test_unreachable_store_alerts_and_writes_no_rows(self):
        """A bad host yields an unreachable alert with no result rows (Req 7.5).

        Mirrors the check_freshness bad-host temp .env pattern: an unroutable
        DB_HOST forces the connect to fail on every attempt, so after 3 attempts
        the run returns status UNREACHABLE with overall_alert True (the non-zero
        exit-equivalent) and writes no bf.quality_run / bf.quality_match_result
        rows for the aborted run.
        """
        # Snapshot the current row counts; an unreachable run must not add any.
        conn = _connect()
        try:
            with conn.cursor() as cur:
                run_before = _count_rows(cur, RUN_TABLE) if _table_present(cur, RUN_TABLE) else 0
                match_before = _count_rows(cur, MATCH_RESULT_TABLE) if _table_present(cur, MATCH_RESULT_TABLE) else 0
        finally:
            conn.close()

        # Build a temp .env whose DB_HOST is unroutable.
        from api.auth.dotenv_loader import DotenvLoader

        loader = DotenvLoader()
        env_lines = [
            "DB_HOST=10.255.255.1",  # unroutable -> connect timeout
            f"DB_PORT={loader.get_secret('DB_PORT')}",
            f"DB_NAME={loader.get_secret('DB_NAME')}",
            f"DB_USER={loader.get_secret('DB_USER')}",
            f"DB_PWD={loader.get_secret('DB_PWD')}",
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(env_lines))
            bad_env = fh.name

        try:
            result = check_quality(env_path=bad_env)
        finally:
            os.unlink(bad_env)

        assert result["status"] == STATUS_UNREACHABLE
        assert result["overall_alert"] is True
        assert result["run_id"] is None
        assert result["matches_verified"] == 0

        # No result rows were written for the unreachable run.
        conn = _connect()
        try:
            with conn.cursor() as cur:
                run_after = _count_rows(cur, RUN_TABLE) if _table_present(cur, RUN_TABLE) else 0
                match_after = _count_rows(cur, MATCH_RESULT_TABLE) if _table_present(cur, MATCH_RESULT_TABLE) else 0
        finally:
            conn.close()

        assert run_after == run_before
        assert match_after == match_before
