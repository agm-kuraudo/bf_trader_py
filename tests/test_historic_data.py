"""Tests for the pure historic-data core (SP-401 / SP-491): ``logic/historic_data.py``.

Covers the maths that is easy to get wrong (commission, locked profit), the fast grid evaluator against the slow
reference simulator, the parser on a small synthetic stream, and the two guards born from real mistakes: the
look-ahead selection guard and the stale-price calibration guard.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.historic_data import (
    DRAW,
    OVER_25,
    LookAheadError,
    Trade,
    asof_calibration,
    assert_selection_ignores_future,
    build_trade,
    calibration_warnings,
    evaluate_grid,
    flat_stake_row,
    jump_profits,
    lengthens,
    locked_profit_fraction,
    longshot_lay,
    net_of_commission,
    parse_market,
    post_jump_events,
    run_exit,
    settle_bet,
    settle_hold,
)

KO = "2026-10-10T14:00:00.000Z"
KO_MS = 1_791_640_800_000  # 2026-10-10T14:00:00Z in epoch milliseconds


# ------------------------------------------------------------------------------------------------ helpers
def make_market(kind="MO", market_id="1.1", inplay=True, runners=None, rate=5.0, ko=KO):
    return {
        "kind": kind,
        "market_id": market_id,
        "event": "A v B",
        "ko": ko,
        "rate": rate,
        "inplay": inplay,
        "runners": runners or {},
    }


def runner(status="LOSER", entry=3.0, series=(), prices=None, sort=1):
    return {"status": status, "sort": sort, "entry": entry, "prices": prices or {}, "series": list(series)}


def trade(p0, times, prices, won=False, rate=5.0, month="2026-01"):
    return Trade("1.1", month, np.array(times, dtype=float), np.array(prices, dtype=float), p0, won, rate)


# ------------------------------------------------------------------------------------------------ maths
def test_commission_only_on_positive_results():
    assert net_of_commission(-10.0, 5.0) == -10.0
    assert net_of_commission(0.0, 5.0) == 0.0
    assert net_of_commission(100.0, 5.0) == pytest.approx(95.0)


def test_settle_bet_back_and_lay():
    gross, net, risk = settle_bet("BACK", 3.0, True, 5.0, 50.0)
    assert (gross, risk) == (100.0, 50.0) and net == pytest.approx(95.0)
    gross, net, risk = settle_bet("LAY", 3.0, True, 5.0, 50.0)
    assert (gross, net, risk) == (-100.0, -100.0, 100.0)
    gross, net, risk = settle_bet("LAY", 3.0, False, 5.0, 50.0)
    assert gross == 50.0 and net == pytest.approx(47.5)


@settings(max_examples=100)
@given(st.floats(1.05, 50.0), st.floats(1.05, 50.0), st.floats(1.0, 500.0))
def test_locked_profit_is_the_same_whichever_selection_wins(first, second, stake):
    """Lay at ``first`` then back at ``second`` with the equalising stake: P/L must not depend on the result."""
    back_stake = stake * first / second
    if_wins = -stake * (first - 1.0) + back_stake * (second - 1.0)
    if_loses = stake - back_stake
    expected = stake * locked_profit_fraction(first, second, "LAY")
    assert if_wins == pytest.approx(expected, rel=1e-9, abs=1e-9)
    assert if_loses == pytest.approx(expected, rel=1e-9, abs=1e-9)


# ------------------------------------------------------------------------------------------------ grid vs reference
@st.composite
def trades(draw):
    n = draw(st.integers(0, 12))
    times = sorted(draw(st.lists(st.floats(0.0, 6000.0), min_size=n, max_size=n)))
    prices = draw(st.lists(st.floats(1.05, 30.0), min_size=n, max_size=n))
    return trade(draw(st.floats(1.1, 20.0)), times, prices, draw(st.booleans()))


@settings(max_examples=150, deadline=None)
@given(st.lists(trades(), min_size=1, max_size=4), st.sampled_from(["LAY", "BACK"]))
def test_fast_grid_matches_reference_simulator(batch, direction):
    targets, stops, minutes = (1, 5, 20), (5, 30, None), (None, 30)
    keys, values = evaluate_grid(batch, direction, targets, stops, minutes)
    for i, (tg, stop, mi) in enumerate(keys):
        for j, tr in enumerate(batch):
            assert values[i, j] == pytest.approx(run_exit(tr, direction, tg, stop, mi), rel=1e-9, abs=1e-9)


def test_exit_rules_on_a_known_path():
    # Lay at 3.0, stake 50. Price rises to 4.0 -> locked profit 50 * (1 - 3/4) = 12.5.
    tr = trade(3.0, [60, 120, 180], [3.0, 3.4, 4.0], won=True)
    assert run_exit(tr, "LAY", 10, None, None) == pytest.approx(net_of_commission(12.5, 5.0))
    # A 5 stop is hit first when the price falls to 2.5: 50 * (1 - 3/2.5) = -10.
    tr = trade(3.0, [60, 120], [2.5, 4.0], won=False)
    assert run_exit(tr, "LAY", 10, 8, None) == pytest.approx(-10.0)
    # No thresholds hit and no time exit -> held to the result (draw won -> lose the liability).
    tr = trade(3.0, [60], [3.1], won=True)
    assert run_exit(tr, "LAY", 20, None, None) == settle_hold(tr, "LAY") == pytest.approx(-100.0)


# ------------------------------------------------------------------------------------------------ guard 1: look-ahead
def test_build_trade_selection_ignores_the_in_play_series():
    markets = [
        make_market(market_id=f"1.{i}", runners={DRAW: runner("WINNER", 3.0, series=s), "A": runner("LOSER")})
        for i, s in enumerate([[], [(1.0, 3.0)], [(float(k), 3.0) for k in range(30)]])
    ]
    assert assert_selection_ignores_future(markets, lambda m: build_trade(m, DRAW)) == 3


def test_look_ahead_guard_catches_a_filter_on_sample_count():
    """The original bug: requiring >= 15 in-play updates leaked the outcome (early-decided markets stop trading)."""
    markets = [
        make_market(
            market_id="1.1", runners={DRAW: runner("WINNER", 3.0, series=[(float(k), 3.0) for k in range(30)])}
        ),
        make_market(market_id="1.2", runners={DRAW: runner("WINNER", 3.0, series=[(1.0, 3.0)])}),
    ]

    def leaky(m):
        t = build_trade(m, DRAW)
        return t if t is not None and len(t.t) >= 15 else None

    with pytest.raises(LookAheadError):
        assert_selection_ignores_future(markets, leaky)


def test_build_trade_requires_entry_inplay_and_a_single_winner():
    ok = make_market(runners={DRAW: runner("WINNER", 3.0), "A": runner("LOSER")})
    assert build_trade(ok, DRAW) is not None
    assert build_trade(make_market(inplay=False, runners=ok["runners"]), DRAW) is None
    assert build_trade(make_market(runners={DRAW: runner("WINNER", None)}), DRAW) is None
    assert build_trade(make_market(runners={DRAW: runner("LOSER", 3.0), "A": runner("LOSER")}), DRAW) is None


# ------------------------------------------------------------------------------------------------ guard 2: calibration
def _calibration_markets(late_price_when_over_wins, n=40):
    """Half the games Over wins. When it wins, the late price is ``late_price_when_over_wins`` (stale or true)."""
    out = []
    for i in range(n):
        over_wins = i % 2 == 0
        late = late_price_when_over_wins if over_wins else 20.0
        out.append(
            make_market(
                kind="OU25",
                market_id=f"1.{i}",
                runners={
                    OVER_25: runner("WINNER" if over_wins else "LOSER", 2.0, series=[(3000.0, late)]),
                    "Under 2.5 Goals": runner("LOSER" if over_wins else "WINNER", 2.0),
                },
            )
        )
    return out


def test_calibration_passes_when_late_prices_reflect_the_outcome():
    rows = asof_calibration(_calibration_markets(1.01), "OU25", OVER_25, times_min=(0, 60))
    assert calibration_warnings(rows) == []
    late = rows[-1]
    assert late["realised"] == pytest.approx(0.5)
    assert late["implied"] == pytest.approx((0.99 + 0.05) / 2, abs=0.01)


def test_calibration_flags_stale_prices_that_never_moved_to_the_outcome():
    """Over wins half the games but the late price is still 8.0 (nobody traded at ~1.01): implied 0.125 vs 0.5."""
    rows = asof_calibration(_calibration_markets(8.0), "OU25", OVER_25, times_min=(60,))
    assert rows[0]["gap"] > 0.3
    assert calibration_warnings(rows)


# ------------------------------------------------------------------------------------------------ parser
def _messages(market_type="MATCH_ODDS", with_inplay=True):
    def md(in_play):
        return {
            "marketType": market_type,
            "marketTime": KO,
            "marketBaseRate": 5.0,
            "eventName": "Home v Away",
            "inPlay": in_play,
            "runners": [
                {"id": 1, "name": "Home", "status": "WINNER", "sortPriority": 1},
                {"id": 2, "name": DRAW, "status": "LOSER", "sortPriority": 3},
            ],
        }

    msgs = [
        {"pt": KO_MS - 80 * 3600 * 1000, "mc": [{"id": "1.1", "marketDefinition": md(False)}]},
        {"pt": KO_MS - 30 * 3600 * 1000, "mc": [{"id": "1.1", "rc": [{"id": 2, "ltp": 3.4}]}]},
        {"pt": KO_MS - 600 * 1000, "mc": [{"id": "1.1", "rc": [{"id": 2, "ltp": 3.6}]}]},
    ]
    if with_inplay:
        msgs += [
            {"pt": KO_MS + 1000, "mc": [{"id": "1.1", "marketDefinition": md(True)}]},
            {"pt": KO_MS + 61_000, "mc": [{"id": "1.1", "rc": [{"id": 2, "ltp": 3.2}]}]},
        ]
    return msgs


def test_parse_market_extracts_prices_entry_and_series():
    rec = parse_market(_messages(), "1.1")
    assert rec["kind"] == "MO" and rec["inplay"] and rec["rate"] == 5.0 and rec["ko"] == KO
    draw = rec["runners"][DRAW]
    assert draw["status"] == "LOSER" and draw["entry"] == 3.6
    assert draw["prices"]["close"] == 3.6
    assert draw["prices"]["24"] == 3.4 and draw["prices"]["72"] is None
    assert draw["series"] == [(60.0, 3.2)]  # in-play starts at +1s, the update arrives at +61s
    assert rec["runners"]["Home"]["entry"] is None


def test_parse_market_without_inplay_and_other_types():
    rec = parse_market(_messages(with_inplay=False), "1.1")
    assert not rec["inplay"] and rec["runners"][DRAW]["series"] == []
    assert parse_market(_messages("CORRECT_SCORE"), "1.1") is None


# ------------------------------------------------------------------------------------------------ flat / jump
def _mo(winner, prices, rate=5.0, market_id="1.1"):
    names = ["Home", DRAW, "Away"]
    runners = {
        n: runner("WINNER" if n == winner else "LOSER", prices=dict(close=p, **{"24": p}), sort=i)
        for i, (n, p) in enumerate(zip(names, prices, strict=True), start=1)
    }
    return make_market(market_id=market_id, runners=runners, rate=rate)


def test_flat_stake_row_back_favourite():
    markets = [_mo("Home", (1.5, 4.0, 6.0)), _mo("Away", (1.5, 4.0, 6.0))]
    row = flat_stake_row(markets, "close", "BACK", "favourite", 50.0)
    assert row["n"] == 2 and row["gross"] == pytest.approx(25.0 - 50.0)
    assert row["net"] == pytest.approx(net_of_commission(25.0, 5.0) - 50.0)


def test_longshot_lay_uses_fixed_liability():
    markets = [_mo("Home", (1.5, 4.0, 11.0)), _mo("Away", (1.5, 4.0, 11.0))]
    r = longshot_lay(markets, 10.0, liability=50.0)
    # Away at 11.0: stake = 50/10 = 5; one market it wins (-50), the other it loses (+5 less commission).
    assert r["n"] == 2 and r["win_rate"] == pytest.approx(0.5)
    assert r["net_per_bet"] == pytest.approx((-50.0 + net_of_commission(5.0, 5.0)) / 2)


def test_post_jump_events_and_slippage():
    series = [(60.0, 3.0), (120.0, 3.0), (180.0, 4.5), (240.0, 4.4), (500.0, 3.9), (900.0, 3.5)]
    markets = [make_market(runners={DRAW: runner("LOSER", 3.0, series=series)})]
    events = post_jump_events(markets, "MO", DRAW, lengthens(1.2), delay_s=60.0, horizon_min=5.0)
    assert events == [(4.4, 3.5, "2026-10", 5.0)]
    gross = 50.0 * (4.4 / 3.5 - 1.0)
    assert jump_profits(events, 0.0, "BACK_THEN_LAY")[0] == pytest.approx(net_of_commission(gross, 5.0))
    # Slippage on both legs always reduces the result.
    assert jump_profits(events, 0.02, "BACK_THEN_LAY")[0] < jump_profits(events, 0.0, "BACK_THEN_LAY")[0]
