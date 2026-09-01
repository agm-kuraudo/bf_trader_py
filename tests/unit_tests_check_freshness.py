"""Unit tests for scripts/check_freshness.py wrapper branches (SP-328, Task 10, Req 5).

No live DB: the psycopg2 connection is mocked. These cover check_freshness's
own decision/alert branches (the pure freshness() logic is separately covered by
Property 1). In particular this pins the empty-store path (Req 5.5) and the
missing-config path without needing to mutate a real database.
"""

import os
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.check_freshness as cf


class _FakeCursor:
    def __init__(self, max_ts):
        self._max_ts = max_ts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sql = sql

    def fetchone(self):
        return (self._max_ts,)


class _FakeConn:
    def __init__(self, max_ts):
        self._max_ts = max_ts
        self.autocommit = False

    def cursor(self):
        return _FakeCursor(self._max_ts)

    def close(self):
        pass


def _patch_ok_config(monkeypatch):
    monkeypatch.setattr(cf, "_read_db_config", lambda env_path=None: {
        "DB_HOST": "h", "DB_PORT": "5432", "DB_NAME": "bf_trader", "DB_USER": "u", "DB_PWD": "p",
    })


def test_empty_store_raises_no_records_stall(monkeypatch, tmp_path):
    """MAX(timestamp) is NULL -> 'no captured odds' stall alert (Req 5.5)."""
    _patch_ok_config(monkeypatch)
    monkeypatch.setattr(cf.psycopg2, "connect", lambda **kw: _FakeConn(None))
    result = cf.check_freshness(now=datetime(2026, 9, 1, tzinfo=UTC),
                                state_path=str(tmp_path / "s.json"))
    assert result["reachable"] is True
    assert result["last_record_ts"] is None
    assert result["stalled"] is True
    assert "no captured odds" in result["alert"]


def test_fresh_store_has_no_alert(monkeypatch, tmp_path):
    _patch_ok_config(monkeypatch)
    latest = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(cf.psycopg2, "connect", lambda **kw: _FakeConn(latest))
    result = cf.check_freshness(now=datetime(2026, 9, 1, 12, 1, tzinfo=UTC),
                                threshold_s=15 * 60, state_path=str(tmp_path / "s.json"))
    assert result["stalled"] is False
    assert result["alert"] is None
    assert result["elapsed_s"] == 60


def test_stale_store_raises_stall(monkeypatch, tmp_path):
    _patch_ok_config(monkeypatch)
    latest = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(cf.psycopg2, "connect", lambda **kw: _FakeConn(latest))
    result = cf.check_freshness(now=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
                                threshold_s=15 * 60, state_path=str(tmp_path / "s.json"))
    assert result["stalled"] is True
    assert "STALL" in result["alert"]


def test_missing_config_raises_alert_without_connecting(monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "_read_db_config", lambda env_path=None: {
        "DB_HOST": "", "DB_PORT": "5432", "DB_NAME": "bf_trader", "DB_USER": "u", "DB_PWD": "p",
    })
    def _boom(**kw):
        raise AssertionError("should not connect when config is missing")
    monkeypatch.setattr(cf.psycopg2, "connect", _boom)
    result = cf.check_freshness(state_path=str(tmp_path / "s.json"))
    assert result["reachable"] is False
    assert "DB_HOST" in result["error"]
    assert result["alert"] is not None
