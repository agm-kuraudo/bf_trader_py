"""Integration tests for ``scripts/verify_db.py`` (SP-328, Req 1).

These tests REQUIRE a running ``my_postgres`` PostgreSQL instance reachable with
the credentials in the project ``.env`` (mirroring the repo convention that
``test_db_connection`` needs a live DB — see the README "Running Tests" note).

They SKIP cleanly (rather than fail) when no data store is reachable, because
CI and the Windows work PC have no ``my_postgres`` container. The skip decision
is made once at module import via a real 10-second connection attempt.

Verified on the Pi (Linux/ARM), NOT on the Windows work PC: on Windows with no
``my_postgres`` these tests are expected to skip.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.verify_db import (
    CAPTURE_SCHEMA,
    REQUIRED_DB_KEYS,
    REQUIRED_TABLES,
    verify_db,
)

# --- Module-level skip guard: only run when a live data store is reachable. ---


def _db_reachable() -> bool:
    """Return True only if verify_db can reach the store with valid config."""
    try:
        result = verify_db()
    except Exception:
        return False
    return result["reachable"] and not result["missing_config"]


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="No reachable my_postgres data store (expected on Windows/CI without the container)",
)


def _connect():
    """Open a raw psycopg2 connection using the same .env config as verify_db."""
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


class TestVerifyDbIntegration:
    """Integration coverage for the reachable + schema-ready happy path."""

    def test_connects_and_confirms_required_tables(self):
        """Connects within the 10s timeout and confirms the four required tables.

        After a successful verify_db run every required table must exist, so the
        post-run missing set (present ∪ created) covers all REQUIRED_TABLES.

        **Validates: Requirements 1.2, 1.4, 1.5**
        """
        result = verify_db()

        assert result["reachable"] is True
        assert result["missing_config"] == []
        assert result["error"] is None

        # Everything reported missing must have been created this run.
        assert set(result["missing_tables"]) == set(result["created_tables"])

        # After the run, all required tables are present in the bf schema.
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (CAPTURE_SCHEMA,),
                )
                present = {row[0] for row in cursor.fetchall()}
        finally:
            conn.close()

        assert REQUIRED_TABLES.issubset(present)

    def test_second_run_creates_nothing_and_leaves_data_untouched(self):
        """Running verify_db twice must not re-create or disturb existing tables.

        The first run makes the schema ready; the second must report no missing
        tables and create none. A sentinel row written between the runs must
        survive, proving existing data is left unchanged (Req 1.5).

        **Validates: Requirements 1.4, 1.5**
        """
        # First run makes the schema ready.
        verify_db()

        # Write a sentinel row into bf.target that the second run must not touch.
        sentinel_id = "sp328-verify-db-sentinel"
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM bf.target WHERE target_id = %s", (sentinel_id,))
                cursor.execute(
                    "INSERT INTO bf.target (target_id, status) VALUES (%s, %s)",
                    (sentinel_id, "verify_db_integration"),
                )

            # Second run: idempotent — nothing missing, nothing created.
            second = verify_db()
            assert second["reachable"] is True
            assert second["missing_tables"] == []
            assert second["created_tables"] == []

            # Sentinel row survived untouched.
            with conn.cursor() as cursor:
                cursor.execute("SELECT status FROM bf.target WHERE target_id = %s", (sentinel_id,))
                row = cursor.fetchone()
            assert row is not None
            assert row[0] == "verify_db_integration"
        finally:
            # Clean up the sentinel row.
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM bf.target WHERE target_id = %s", (sentinel_id,))
            conn.close()

    def test_unreachable_store_surfaces_error(self):
        """A bad host makes the store unreachable and surfaces an error (Req 1.7).

        Uses a throwaway .env pointing at an unroutable host so the 10s connect
        times out / fails without touching the real store.

        **Validates: Requirements 1.2, 1.7**
        """
        import tempfile

        env_body = (
            "DB_HOST=10.255.255.1\n"  # unroutable test-net address -> connect fails/times out
            "DB_PORT=5432\n"
            "DB_NAME=bf_trader\n"
            "DB_USER=postgres\n"
            "DB_PWD=irrelevant\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write(env_body)
            tmp_path = f.name

        try:
            result = verify_db(tmp_path)
            assert result["reachable"] is False
            assert result["missing_config"] == []
            assert result["error"] is not None
            assert "unreachable" in result["error"].lower()
        finally:
            os.unlink(tmp_path)

    def test_missing_config_surfaces_which_keys_without_connecting(self):
        """Missing required DB keys are surfaced and no connection is attempted.

        **Validates: Requirements 1.3, 1.7**
        """
        import tempfile

        # DB_HOST empty and DB_PWD absent -> both should be reported.
        env_body = "DB_HOST=\nDB_PORT=5432\nDB_NAME=bf_trader\nDB_USER=postgres\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write(env_body)
            tmp_path = f.name

        try:
            result = verify_db(tmp_path)
            assert result["reachable"] is False
            assert set(result["missing_config"]) == {"DB_HOST", "DB_PWD"}
            assert result["error"] is not None
            # Only known required keys are ever reported.
            assert set(result["missing_config"]).issubset(set(REQUIRED_DB_KEYS))
        finally:
            os.unlink(tmp_path)
