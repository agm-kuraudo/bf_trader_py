"""Unit tests for scripts/verify_deploy.py decision logic (SP-328, Task 7.3 support).

These do NOT require a live DB: verify_deploy is exercised with a fake psycopg2
connection and injected sleep/now, so the poll/decision behaviour is tested
deterministically and cross-platform.

Covers the three cases from the design rationale:
  1. A fresh successful Monitor cycle WITH new odds  -> verified + odds_persisted.
  2. A fresh successful Monitor cycle WITHOUT new odds (nothing due) -> verified,
     odds_persisted False (still a pass).
  3. No successful cycle within the window -> not verified, error set.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.verify_deploy as vd


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        # Return the next scripted (runs, odds) pair based on which query ran.
        if "log_file" in self._last:
            return (self._conn.next_runs(),)
        return (self._conn.next_odds(),)


class _FakeConn:
    """Serves scripted successful-run and odds counts across successive polls."""

    def __init__(self, run_counts, odds_counts):
        self._run_counts = list(run_counts)
        self._odds_counts = list(odds_counts)
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return _FakeCursor(self)

    def next_runs(self):
        return self._run_counts.pop(0) if len(self._run_counts) > 1 else self._run_counts[0]

    def next_odds(self):
        return self._odds_counts.pop(0) if len(self._odds_counts) > 1 else self._odds_counts[0]

    def close(self):
        self.closed = True


def _patch(monkeypatch, conn):
    monkeypatch.setattr(
        vd,
        "_read_db_config",
        lambda env_path=None: {
            "DB_HOST": "h",
            "DB_PORT": "5432",
            "DB_NAME": "bf_trader",
            "DB_USER": "u",
            "DB_PWD": "p",
        },
    )
    monkeypatch.setattr(vd, "_connect", lambda config: conn)


def test_successful_cycle_with_odds(monkeypatch):
    # baseline runs=10, odds=100; then runs=11, odds=103 (3 new odds rows).
    conn = _FakeConn(run_counts=[10, 11], odds_counts=[100, 103])
    _patch(monkeypatch, conn)
    ticks = iter([0.0, 1.0, 2.0])
    result = vd.verify_deploy(timeout_s=300, poll_interval_s=1, sleep=lambda s: None, now=lambda: next(ticks))
    assert result["verified"] is True
    assert result["odds_persisted"] is True
    assert result["new_successful_runs"] == 1
    assert result["new_odds_rows"] == 3
    assert result["error"] is None
    assert conn.closed is True


def test_successful_cycle_without_odds_is_still_pass(monkeypatch):
    # A run completes (10 -> 11) but no new odds (nothing due): still verified.
    conn = _FakeConn(run_counts=[10, 11], odds_counts=[100, 100])
    _patch(monkeypatch, conn)
    ticks = iter([0.0, 1.0, 2.0])
    result = vd.verify_deploy(timeout_s=300, poll_interval_s=1, sleep=lambda s: None, now=lambda: next(ticks))
    assert result["verified"] is True
    assert result["odds_persisted"] is False
    assert result["new_successful_runs"] == 1
    assert result["new_odds_rows"] == 0
    assert result["error"] is None


def test_no_successful_cycle_times_out(monkeypatch):
    # runs never advance past baseline -> not verified, error set.
    conn = _FakeConn(run_counts=[10], odds_counts=[100])
    _patch(monkeypatch, conn)
    # now() advances past the deadline after one poll.
    ticks = iter([0.0, 0.0, 301.0])
    result = vd.verify_deploy(timeout_s=300, poll_interval_s=1, sleep=lambda s: None, now=lambda: next(ticks))
    assert result["verified"] is False
    assert result["new_successful_runs"] == 0
    assert result["error"] is not None
    assert "within 300s" in result["error"]


def test_missing_db_config_errors_without_connecting(monkeypatch):
    monkeypatch.setattr(
        vd,
        "_read_db_config",
        lambda env_path=None: {
            "DB_HOST": "",
            "DB_PORT": "5432",
            "DB_NAME": "bf_trader",
            "DB_USER": "u",
            "DB_PWD": "p",
        },
    )

    # _connect must NOT be called; make it explode if it is.
    def _boom(config):
        raise AssertionError("should not connect when config is missing")

    monkeypatch.setattr(vd, "_connect", _boom)
    result = vd.verify_deploy()
    assert result["verified"] is False
    assert "DB_HOST" in result["error"]
