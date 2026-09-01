"""Unit tests for scripts/check_freshness.py wrapper branches (SP-328, Task 10, Req 5).

No live DB: the psycopg2 connection is mocked. These cover check_freshness's
own decision/alert branches (the pure freshness()/expected_freshness_threshold()
logic is separately covered by property/unit tests).

Includes the CADENCE-AWARE behaviour (refinement of the literal 15-min figure):
  * active target on the 4h tier -> a 63-min gap is NOT a stall
  * active in-play target (5s) -> a short gap IS a stall
  * no active targets -> idle, no alert
Plus the empty-store and missing-config paths.
"""

import os
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock  # noqa: F401 (kept for parity with other tests)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.check_freshness as cf


class _FakeCursor:
    """Serves two queries: MAX(timestamp) and the active-target frequencies."""

    def __init__(self, max_ts, frequencies):
        self._max_ts = max_ts
        self._frequencies = frequencies
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        # Only the MAX(timestamp) query uses fetchone.
        return (self._max_ts,)

    def fetchall(self):
        # The target-frequency query uses fetchall -> list of (freq,) rows.
        return [(f,) for f in self._frequencies]


class _FakeConn:
    def __init__(self, max_ts, frequencies):
        self._max_ts = max_ts
        self._frequencies = frequencies
        self.autocommit = False

    def cursor(self):
        return _FakeCursor(self._max_ts, self._frequencies)

    def close(self):
        pass


def _patch_ok_config(monkeypatch):
    monkeypatch.setattr(cf, "_read_db_config", lambda env_path=None: {
        "DB_HOST": "h", "DB_PORT": "5432", "DB_NAME": "bf_trader", "DB_USER": "u", "DB_PWD": "p",
    })


def _patch_conn(monkeypatch, max_ts, frequencies):
    monkeypatch.setattr(cf.psycopg2, "connect", lambda **kw: _FakeConn(max_ts, frequencies))


# --- Cadence-aware behaviour (the fix for the 4h-tier false positive) --------


def test_far_out_target_63min_gap_is_not_stall(monkeypatch, tmp_path):
    """4h-tier target (14400s): a 63-minute gap is well within cadence -> fresh."""
    _patch_ok_config(monkeypatch)
    latest = datetime(2026, 9, 1, 21, 35, tzinfo=UTC)
    _patch_conn(monkeypatch, latest, frequencies=[14400])  # MORE_THAN_12H tier
    now = datetime(2026, 9, 1, 22, 38, tzinfo=UTC)  # 63 min later
    result = cf.check_freshness(now=now, state_path=str(tmp_path / "s.json"))
    assert result["stalled"] is False
    assert result["alert"] is None
    # threshold = 14400 + GRACE_S
    assert result["threshold_s"] == 14400 + cf.GRACE_S
    assert result["active_targets"] == 1


def test_in_play_target_short_gap_is_stall(monkeypatch, tmp_path):
    """In-play target (5s): even a few minutes' gap exceeds cadence+grace -> stall."""
    _patch_ok_config(monkeypatch)
    latest = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
    _patch_conn(monkeypatch, latest, frequencies=[5])  # IN_PLAY tier
    now = datetime(2026, 9, 1, 15, 20, tzinfo=UTC)  # 20 min later, threshold ~5min+grace
    result = cf.check_freshness(now=now, state_path=str(tmp_path / "s.json"))
    assert result["stalled"] is True
    assert "STALL" in result["alert"]


def test_no_active_targets_is_idle_not_stall(monkeypatch, tmp_path):
    """No active targets -> nothing should be landing -> idle, no alert."""
    _patch_ok_config(monkeypatch)
    latest = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    _patch_conn(monkeypatch, latest, frequencies=[])  # no OPEN/IDENTIFIED targets
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)  # a full day later
    result = cf.check_freshness(now=now, state_path=str(tmp_path / "s.json"))
    assert result["idle"] is True
    assert result["stalled"] is False
    assert result["alert"] is None
    assert result["threshold_s"] is None


def test_tightest_cadence_wins_with_mixed_targets(monkeypatch, tmp_path):
    """With mixed tiers, the tightest cadence drives the threshold."""
    _patch_ok_config(monkeypatch)
    latest = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
    _patch_conn(monkeypatch, latest, frequencies=[14400, 300, 3600])  # min = 300 (LESS_THAN_3H)
    now = datetime(2026, 9, 1, 15, 20, tzinfo=UTC)  # 20 min; threshold = 300 + grace
    result = cf.check_freshness(now=now, state_path=str(tmp_path / "s.json"))
    assert result["threshold_s"] == 300 + cf.GRACE_S
    assert result["stalled"] is True  # 20 min > 5 min + grace


# --- Empty store / explicit threshold / missing config -----------------------


def test_empty_store_with_active_targets_raises_stall(monkeypatch, tmp_path):
    """MAX(timestamp) NULL but active targets exist -> stall (Req 5.5)."""
    _patch_ok_config(monkeypatch)
    _patch_conn(monkeypatch, None, frequencies=[300])
    result = cf.check_freshness(now=datetime(2026, 9, 1, tzinfo=UTC),
                                state_path=str(tmp_path / "s.json"))
    assert result["reachable"] is True
    assert result["last_record_ts"] is None
    assert result["stalled"] is True
    assert "no captured odds" in result["alert"]


def test_explicit_threshold_still_honoured(monkeypatch, tmp_path):
    """An explicit threshold_s bypasses cadence derivation (test/back-compat path)."""
    _patch_ok_config(monkeypatch)
    latest = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    _patch_conn(monkeypatch, latest, frequencies=[14400])
    # Explicit tight threshold -> a 60-min gap IS a stall despite the 4h cadence.
    result = cf.check_freshness(now=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
                                threshold_s=15 * 60, state_path=str(tmp_path / "s.json"))
    assert result["threshold_s"] == 15 * 60
    assert result["stalled"] is True


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
