"""Property-based + example tests for SP-371: pre-match drift trading sim.

Tests the pure-logic drift-trading core in ``logic/drift_trading.py`` -- the
back/lay round-trip P&L, the per-selection trade simulation, and the summary/
verdict. Pure/I-O-free, so tested deterministically on synthetic frames.

The P&L has a clean invariant that anchors the properties: a fully-hedged back+
lay round trip locks in ``back/lay - 1`` per unit backed before commission, and
commission (charged on net winnings only) scales a positive profit but leaves a
loss untaxed. Profit is positive exactly when you backed higher than you laid.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from logic.drift_trading import (
    MEAN_REVERSION,
    MOMENTUM,
    TRADE_COLUMNS,
    round_trip_profit,
    simulate_drift_trades,
    summarise_drift_trades,
)
from logic.research_dataset import DATASET_SCHEMA

_START_TS = pd.Timestamp("2026-03-14 15:00:00", tz="UTC")

_price = st.floats(min_value=1.01, max_value=1000.0, allow_nan=False, allow_infinity=False)


def _dataset(rows):
    frame = pd.DataFrame(rows, columns=[name for name, _ in DATASET_SCHEMA])
    for name, dtype in DATASET_SCHEMA:
        if dtype == "datetime64[ns, UTC]":
            frame[name] = pd.to_datetime(frame[name], utc=True)
        else:
            frame[name] = frame[name].astype(dtype)
    return frame


def _obs(market_id, runner_id, role, hours_before, best_back, best_lay, start=_START_TS):
    """One pre-match observation for a selection at ``hours_before`` kick-off."""
    return {
        "market_id": market_id,
        "runner_id": runner_id,
        "runner_name": role,
        "outcome_role": role,
        "timestamp": start - pd.Timedelta(hours=hours_before),
        "best_back": best_back,
        "best_lay": best_lay,
        "matched_volume": None,
        "match_phase": "PRE_MATCH",
        "event_id": "e",
        "start_time": start,
        "target_id": "t",
    }


# --- Property 1: round-trip P&L identity --------------------------------------


class TestProperty1RoundTripProfit:
    """Property: profit == (back/lay - 1), commission-scaled only when positive."""

    @given(back=_price, lay=_price, commission=st.floats(min_value=0.0, max_value=0.2, allow_nan=False))
    @settings(max_examples=300)
    def test_profit_identity(self, back, lay, commission):
        profit = round_trip_profit(back, lay, commission)
        gross = back / lay - 1.0
        if gross > 0.0:
            assert abs(profit - gross * (1.0 - commission)) < 1e-9
            assert profit >= 0.0
        else:
            # A loss is untaxed.
            assert abs(profit - gross) < 1e-9
            assert profit <= 0.0

    @given(back=_price, lay=_price)
    @settings(max_examples=100)
    def test_profit_sign_follows_back_vs_lay(self, back, lay):
        profit = round_trip_profit(back, lay, 0.05)
        if back > lay:
            assert profit > 0.0
        elif back < lay:
            assert profit < 0.0
        else:
            assert profit == 0.0

    def test_missing_or_nonpositive_price_is_flat(self):
        assert round_trip_profit(None, 2.0) == 0.0
        assert round_trip_profit(2.0, None) == 0.0
        assert round_trip_profit(float("nan"), 2.0) == 0.0
        assert round_trip_profit(0.0, 2.0) == 0.0
        assert round_trip_profit(2.0, -1.0) == 0.0


# --- Property 2: simulation shape & determinism -------------------------------


class TestProperty2Simulation:
    """Property: the sim produces the declared columns, is sorted and deterministic."""

    @given(
        prices=st.lists(st.tuples(_price, _price), min_size=2, max_size=5),
    )
    @settings(max_examples=60)
    def test_columns_sorted_deterministic(self, prices):
        rows = []
        for i, (back, lay) in enumerate(prices):
            # Spread observations across the pre-match window, entry..exit covered.
            rows.append(_obs("1.1", "1.1-HOME", "HOME", hours_before=72 - i * 10, best_back=back, best_lay=lay))
        ds = _dataset(rows)
        first = simulate_drift_trades(ds, MOMENTUM, entry_hours_before=72.0, exit_hours_before=1.0)
        second = simulate_drift_trades(ds, MOMENTUM, entry_hours_before=72.0, exit_hours_before=1.0)
        assert list(first.columns) == list(TRADE_COLUMNS)
        pd.testing.assert_frame_equal(first, second)


# --- Example-based: known momentum / mean-reversion trades --------------------


class TestKnownTrades:
    """Hand-built drift scenarios with a known outcome."""

    def test_momentum_profits_when_price_drifts_out(self):
        # Price drifts OUT: entry (72h) lay 2.0, exit (1h) back 2.4 -> back high,
        # lay low -> momentum wins.
        rows = [
            _obs("1.1", "r", "HOME", hours_before=72, best_back=1.9, best_lay=2.0),
            _obs("1.1", "r", "HOME", hours_before=1, best_back=2.4, best_lay=2.5),
        ]
        trades = simulate_drift_trades(_dataset(rows), MOMENTUM, 72.0, 1.0, commission=0.05)
        assert len(trades) == 1
        t = trades.iloc[0]
        # back at exit (2.4), lay at entry (2.0) -> profit.
        assert t["back_price"] == 2.4
        assert t["lay_price"] == 2.0
        assert t["profit_per_unit"] > 0.0

    def test_momentum_loses_when_price_comes_in(self):
        # Price comes IN: back at exit (1.8) < lay at entry (2.0) -> momentum loses.
        rows = [
            _obs("1.1", "r", "HOME", hours_before=72, best_back=1.9, best_lay=2.0),
            _obs("1.1", "r", "HOME", hours_before=1, best_back=1.8, best_lay=1.85),
        ]
        trades = simulate_drift_trades(_dataset(rows), MOMENTUM, 72.0, 1.0, commission=0.05)
        assert trades.iloc[0]["profit_per_unit"] < 0.0

    def test_mean_reversion_is_the_mirror(self):
        # Same drift-out scenario: mean-reversion backs at entry (1.9), lays at
        # exit (2.5) -> back low, lay high -> loses.
        rows = [
            _obs("1.1", "r", "HOME", hours_before=72, best_back=1.9, best_lay=2.0),
            _obs("1.1", "r", "HOME", hours_before=1, best_back=2.4, best_lay=2.5),
        ]
        trades = simulate_drift_trades(_dataset(rows), MEAN_REVERSION, 72.0, 1.0, commission=0.05)
        t = trades.iloc[0]
        assert t["back_price"] == 1.9
        assert t["lay_price"] == 2.5
        assert t["profit_per_unit"] < 0.0

    def test_entry_must_precede_exit(self):
        rows = [_obs("1.1", "r", "HOME", hours_before=72, best_back=2.0, best_lay=2.1)]
        with pytest.raises(ValueError):
            simulate_drift_trades(_dataset(rows), MOMENTUM, entry_hours_before=1.0, exit_hours_before=72.0)

    def test_invalid_framing_rejected(self):
        rows = [_obs("1.1", "r", "HOME", hours_before=72, best_back=2.0, best_lay=2.1)]
        with pytest.raises(ValueError):
            simulate_drift_trades(_dataset(rows), "SIDEWAYS", 72.0, 1.0)

    def test_in_play_rows_ignored(self):
        # Only an in-play row -> no pre-match trade.
        row = _obs("1.1", "r", "HOME", hours_before=72, best_back=2.0, best_lay=2.1)
        row["match_phase"] = "IN_PLAY"
        trades = simulate_drift_trades(_dataset([row]), MOMENTUM, 72.0, 1.0)
        assert trades.empty


# --- Summary + verdict --------------------------------------------------------


class TestSummaryVerdict:
    """The verdict is mean-based and conservative."""

    def test_empty_is_not_viable(self):
        empty = pd.DataFrame({name: pd.Series(dtype="object") for name in TRADE_COLUMNS})
        result = summarise_drift_trades(empty, MOMENTUM, 72.0, 1.0)
        assert result.verdict == "NOT_VIABLE"
        assert result.n_trades == 0
        assert any("Best-price fills only" in n for n in result.notes)

    def test_positive_mean_is_viable(self):
        # Several clearly-profitable drift-out momentum trades.
        rows = []
        for m in range(10):
            rows += [
                _obs(f"1.{m}", "r", "HOME", hours_before=72, best_back=1.9, best_lay=2.0),
                _obs(f"1.{m}", "r", "HOME", hours_before=1, best_back=2.4, best_lay=2.5),
            ]
        trades = simulate_drift_trades(_dataset(rows), MOMENTUM, 72.0, 1.0, commission=0.05)
        result = summarise_drift_trades(trades, MOMENTUM, 72.0, 1.0, commission=0.05)
        assert result.n_trades == 10
        assert result.mean_profit_per_unit > 0.0
        assert result.verdict == "VIABLE"
        assert result.hit_rate == 1.0

    def test_negative_mean_is_not_viable(self):
        # Spread-crossing losers: back lower than lay every time.
        rows = []
        for m in range(10):
            rows += [
                _obs(f"1.{m}", "r", "HOME", hours_before=72, best_back=2.0, best_lay=2.1),
                _obs(f"1.{m}", "r", "HOME", hours_before=1, best_back=2.0, best_lay=2.1),
            ]
        # Momentum: back at exit (2.0), lay at entry (2.1) -> back < lay -> loss.
        trades = simulate_drift_trades(_dataset(rows), MOMENTUM, 72.0, 1.0, commission=0.05)
        result = summarise_drift_trades(trades, MOMENTUM, 72.0, 1.0, commission=0.05)
        assert result.mean_profit_per_unit < 0.0
        assert result.verdict == "NOT_VIABLE"
