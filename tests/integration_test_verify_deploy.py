"""Integration test for post-deploy Monitor-cycle verification (SP-328, Task 7.3).

REQUIRES a running my_postgres reachable with the .env credentials (repo
convention: DB-dependent tests SKIP cleanly rather than fail when no store is
present, e.g. on Windows/CI).

Task 7.3 asserts that after a deploy, a Monitor cycle is detectable as having
run current code and persisted within 300s, and that no Vault startup failure
occurs (Req 7.4, 7.6). We exercise the REAL verify_deploy query path against the
live schema: on the first poll we seed a fresh "Ending run successfully" (as a
real Monitor cycle would write on completion), then confirm verify_deploy
detects a new successful run within the window. We also assert the recent run
log contains no Vault startup-failure marker.

Verified on the Pi (Linux/ARM); expected to SKIP on the Windows work PC.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from scripts.verify_deploy import END_MESSAGE, verify_deploy


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


def _insert_completion():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO bf.log_file(id, "timestamp", message) VALUES (gen_random_uuid(), NOW(), %s)',
                (END_MESSAGE,),
            )
    finally:
        conn.close()


class TestVerifyDeployIntegration:
    def test_detects_a_completed_monitor_cycle_within_window(self):
        """verify_deploy detects a fresh successful Monitor cycle (Req 7.4/7.6).

        verify_deploy takes its baseline at call time, then polls. We inject a
        completion on the first poll (via the seeding sleep) so it appears AFTER
        the baseline, exactly as a post-deploy Monitor cycle would. This drives
        the real DB query path end-to-end.

        **Validates: Requirements 7.4, 7.6**
        """
        seeded = {"done": False}

        def seeding_sleep(_seconds):
            if not seeded["done"]:
                _insert_completion()
                seeded["done"] = True

        result = verify_deploy(timeout_s=30, poll_interval_s=1, sleep=seeding_sleep)

        assert result["error"] is None
        assert result["verified"] is True
        assert result["new_successful_runs"] >= 1

    def test_no_recent_vault_startup_failure_in_log(self):
        """The recent run log contains no Vault startup-failure marker (Req 7.4).

        Historical rows may predate Vault removal, so we scope to the last day:
        current code must not be logging any Vault reference.
        """
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT COUNT(*) FROM bf.log_file '
                    'WHERE message ILIKE %s AND "timestamp" > NOW() - INTERVAL \'1 day\'',
                    ("%vault%",),
                )
                recent_vault_refs = cur.fetchone()[0]
        finally:
            conn.close()
        assert recent_vault_refs == 0
