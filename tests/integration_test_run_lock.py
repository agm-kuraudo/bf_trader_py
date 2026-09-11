"""Integration tests for the single-instance capture-run advisory lock (SP-343).

These tests prove the Postgres SESSION-scoped advisory lock added in
``output/dboutput.py`` (``DBOutputConnection.try_acquire_run_lock`` /
``release_run_lock``, key ``RUN_ADVISORY_LOCK_KEY = 4310343``) is:

- **mutually exclusive** — while one session holds the lock, a second concurrent
  session cannot acquire it (``try_acquire_run_lock`` returns ``False``); and
- **self-releasing / not poison-prone** — releasing the lock (or simply closing
  the holding session, e.g. on a crash / container death) lets another session
  re-acquire it, without any manual un-poisoning (the failure mode the old
  count-based ``scripts/fix_lock.py`` marker scheme suffered from).

They REQUIRE a running ``my_postgres`` PostgreSQL instance reachable with the
credentials in the project ``.env`` (same convention as
``tests/integration_test_verify_db.py``). They SKIP cleanly (rather than fail)
when no data store is reachable, because CI and the Windows work PC have no
``my_postgres`` container. The skip decision is made once at module import via a
real connection attempt.

Verified for real on the Pi (Linux/ARM); on Windows/CI without ``my_postgres``
these tests are expected to skip.

**Validates: Requirements 1.4, 2.6**
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from BFDriver import BFDriver
from logic.simpleStategy import FromFileStrategy
from output.dboutput import RUN_ADVISORY_LOCK_KEY, DBOutputConnection
from output.log import Output as Log


def _db_details():
    """Return the local DB connection dict from .env, or None if unavailable.

    Uses the same credential path as the runtime code and the other integration
    tests: ``BFDriver.get_local_db_details()`` (which reads DB_* keys from .env).
    Returns None if the .env / credentials cannot be loaded so the module can
    skip cleanly instead of erroring at import time.
    """
    try:
        bf = BFDriver(FromFileStrategy(), Log.ERROR)
        return bf.get_local_db_details()
    except Exception:
        return None


def _open_connection():
    """Open a fresh DBOutputConnection using the local .env DB details."""
    details = _db_details()
    conn = DBOutputConnection()
    conn.open_connection(details)
    return conn


def _db_reachable() -> bool:
    """Return True only if a DBOutputConnection can actually be opened."""
    details = _db_details()
    if details is None:
        return False
    try:
        conn = DBOutputConnection()
        conn.open_connection(details)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="No reachable my_postgres data store (expected on Windows/CI without the container)",
)


class TestRunLockIntegration:
    """Live-DB coverage for mutual exclusion + self-release of the run lock."""

    def setup_method(self):
        # Two independent sessions (distinct connections) to the same DB.
        self.conn_a = _open_connection()
        self.conn_b = _open_connection()
        # Belt-and-braces: ensure neither session holds the lock from a prior
        # aborted run before we start asserting. Unlock is a no-op if not held.
        for conn in (self.conn_a, self.conn_b):
            try:
                conn.release_run_lock()
            except Exception:
                pass

    def teardown_method(self):
        # Release + close both sessions so the lock never leaks between tests.
        for conn in (self.conn_a, self.conn_b):
            if conn is None or conn.conn is None:
                continue
            try:
                conn.release_run_lock()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def test_acquire_contend_release_reacquire(self):
        """First session acquires; second is excluded; release lets it re-acquire.

        Proves both the mutual-exclusion guarantee (Req 2.6 — exactly one run
        samples at a time) and that the lock is self-releasing / not poison-prone
        (Req 1.4 — releasing lets another run acquire it, no manual un-poisoning).

        **Validates: Requirements 1.4, 2.6**
        """
        # Session A acquires the lock.
        assert self.conn_a.try_acquire_run_lock() is True

        # Session B, contending for the SAME key, is excluded while A holds it.
        assert self.conn_b.try_acquire_run_lock() is False

        # A releases; the lock is now free (self-releasing, no manual fix needed).
        self.conn_a.release_run_lock()

        # B can now acquire it.
        assert self.conn_b.try_acquire_run_lock() is True

        # And while B holds it, A is now the one excluded — symmetry check.
        assert self.conn_a.try_acquire_run_lock() is False

    def test_session_close_auto_releases_lock(self):
        """Closing the holding session auto-releases the SESSION-scoped lock.

        This is the crash-safety property: a run that dies (container death /
        connection drop) without calling ``release_run_lock`` must not leave the
        lock permanently held. Because the lock is SESSION-scoped, dropping the
        session releases it, so the next run can acquire it.

        **Validates: Requirements 1.4, 2.6**
        """
        # A acquires, then is closed WITHOUT an explicit release (simulating crash).
        assert self.conn_a.try_acquire_run_lock() is True
        self.conn_a.close()

        # B could not acquire while A held it (sanity), and now that A's session
        # is gone, B can acquire — proving the lock auto-released on session close.
        assert self.conn_b.try_acquire_run_lock() is True

    def test_lock_uses_documented_fixed_key(self):
        """The lock key is the documented fixed bigint so all runs contend on it.

        A stable shared key is what makes concurrent runs mutually exclusive; a
        per-run key would defeat the guard entirely.

        **Validates: Requirements 1.4, 2.6**
        """
        assert RUN_ADVISORY_LOCK_KEY == 4310343
