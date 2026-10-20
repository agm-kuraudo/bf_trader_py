"""Property-based + example tests for SP-370: green-out / arbitrage scan.

Tests the pure-logic green-out core in ``logic/green_out.py`` -- the back-all-
three book scan and its season-level summary/verdict. The functions consume the
SP-368 Research_Dataset (the 12-column ``DATASET_SCHEMA`` frame) and are
pure/I-O-free, so they are property-tested deterministically on synthetic
frames, with no Parquet or DB.

The arbitrage maths has a clean invariant that anchors the properties: for a
back-all-three Dutch at best-back prices, the locked-in profit per unit stake is
``1/book - 1`` (times ``1 - commission`` when positive), and a priced green-out
exists exactly when ``book < 1``. Commission scales a positive edge but never
moves that break-even.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from logic.green_out import (
    GREEN_OUT_COLUMNS,
    PLAUSIBLE_BOOK_FLOOR,
    green_out_scan,
    summarise_green_out,
)
from logic.research_dataset import DATASET_SCHEMA

_BASE_TS = pd.Timestamp("2026-03-14 10:00:00", tz="UTC")
_START_TS = pd.Timestamp("2026-03-14 15:00:00", tz="UTC")

# Positive decimal odds so every leg has a defined implied probability.
_price = st.floats(min_value=1.01, max_value=1000.0, allow_nan=False, allow_infinity=False)


def _dataset(rows):
    frame = pd.DataFrame(rows, columns=[name for name, _ in DATASET_SCHEMA])
    for name, dtype in DATASET_SCHEMA:
        if dtype == "datetime64[ns, UTC]":
            frame[name] = pd.to_datetime(frame[name], utc=True)
        else:
            frame[name] = frame[name].astype(dtype)
    return frame


def _instant(market_id, ts, bh, bd, ba, phase="PRE_MATCH", start=_START_TS):
    """Three rows (HOME/DRAW/AWAY) at one timestamp with given best-back prices."""
    rows = []
    for role, back in (("HOME", bh), ("DRAW", bd), ("AWAY", ba)):
        rows.append(
            {
                "market_id": market_id,
                "runner_id": f"{market_id}-{role}",
                "runner_name": role,
                "outcome_role": role,
                "timestamp": ts,
                "best_back": back,
                "best_lay": back + 0.1,
                "matched_volume": None,
                "match_phase": phase,
                "event_id": "e",
                "start_time": start,
                "target_id": "t",
            }
        )
    return rows


# --- Property 1: profit identity and green-out break-even ---------------------


class TestProperty1ProfitIdentity:
    """Property: profit == (1/book - 1)*(1-c) when book<1, else 0; flag iff book<1."""

    @given(
        bh=_price,
        bd=_price,
        ba=_price,
        commission=st.floats(min_value=0.0, max_value=0.2, allow_nan=False),
    )
    @settings(max_examples=300)
    def test_profit_and_flag_match_the_book(self, bh, bd, ba, commission):
        scan = green_out_scan(_dataset(_instant("1.1", _BASE_TS, bh, bd, ba)), commission=commission)
        assert list(scan.columns) == list(GREEN_OUT_COLUMNS)
        assert len(scan) == 1
        row = scan.iloc[0]
        book = 1.0 / bh + 1.0 / bd + 1.0 / ba
        assert abs(row["book"] - book) < 1e-9
        # Green-out flag is exactly book < 1 -- commission never moves this.
        assert bool(row["is_green_out"]) == (book < 1.0)
        if book < 1.0:
            expected = (1.0 / book - 1.0) * (1.0 - commission)
            assert abs(row["profit_after_commission"] - expected) < 1e-9
            assert row["profit_after_commission"] >= 0.0
        else:
            assert row["profit_after_commission"] == 0.0

    @given(bh=_price, bd=_price, ba=_price)
    @settings(max_examples=100)
    def test_commission_does_not_change_the_flag(self, bh, bd, ba):
        # The green-out COUNT is commission-independent.
        ds = _dataset(_instant("1.1", _BASE_TS, bh, bd, ba))
        a = green_out_scan(ds, commission=0.0).iloc[0]["is_green_out"]
        b = green_out_scan(ds, commission=0.2).iloc[0]["is_green_out"]
        assert bool(a) == bool(b)


# --- Property 2: determinism and ordering -------------------------------------


class TestProperty2Determinism:
    """Property: the scan is deterministic and ordered by (market_id, timestamp)."""

    @given(
        prices=st.lists(st.tuples(_price, _price, _price), min_size=1, max_size=6),
    )
    @settings(max_examples=60)
    def test_deterministic_and_sorted(self, prices):
        rows = []
        for i, (bh, bd, ba) in enumerate(prices):
            rows.append(_instant("1.1", _BASE_TS + pd.Timedelta(minutes=i), bh, bd, ba))
        rows = [r for group in rows for r in group]
        ds = _dataset(rows)
        first = green_out_scan(ds)
        second = green_out_scan(ds)
        pd.testing.assert_frame_equal(first, second)
        ordered = first.sort_values(["market_id", "timestamp"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(first, ordered)


# --- Example-based: known green-out and known non-green-out -------------------


class TestKnownCases:
    """Explicit cases with hand-computed books."""

    def test_clear_green_out_is_flagged_with_positive_margin(self):
        # Overround book = 1/2.1 + 1/4.5 + 1/5.0 = 0.4762 + 0.2222 + 0.2 = 0.8984 < 1
        scan = green_out_scan(_dataset(_instant("1.1", _BASE_TS, 2.1, 4.5, 5.0)), commission=0.05)
        row = scan.iloc[0]
        assert bool(row["is_green_out"]) is True
        assert row["profit_after_commission"] > 0.0

    def test_normal_overround_is_not_a_green_out(self):
        # Typical book just above 1.0 -> no green-out, zero margin.
        scan = green_out_scan(_dataset(_instant("1.1", _BASE_TS, 2.0, 3.5, 4.0)), commission=0.05)
        row = scan.iloc[0]
        assert bool(row["is_green_out"]) is False
        assert row["profit_after_commission"] == 0.0

    def test_incomplete_instant_dropped(self):
        # Only HOME present -> no complete book -> dropped from the scan.
        rows = _instant("1.1", _BASE_TS, 2.0, 3.5, 4.0)[:1]
        scan = green_out_scan(_dataset(rows))
        assert scan.empty


# --- Summary + verdict --------------------------------------------------------


class TestSummaryVerdict:
    """The verdict is pre-match-based, artifact-guarded, and margin-gated."""

    def test_empty_scan_is_not_viable(self):
        empty = pd.DataFrame({name: pd.Series(dtype="object") for name in GREEN_OUT_COLUMNS})
        summary = summarise_green_out(empty)
        assert summary.verdict == "NOT_VIABLE"
        assert summary.n_instants == 0
        # The liquidity caveat always rides on the summary.
        assert any("Liquidity not assessed" in n for n in summary.notes)

    def test_inplay_artifacts_do_not_make_it_viable(self):
        # A big crop of in-play "green-outs" with absurd books must NOT be viable:
        # the verdict is pre-match-only.
        rows = []
        for i in range(50):
            rows += _instant(
                "1.1", _BASE_TS + pd.Timedelta(seconds=i), 1.5, 1.5, 1.5, phase="IN_PLAY"
            )  # book = 2.0? no -> 1/1.5*3 = 2.0, not a green-out
        # Force a green-out crop in-play with a collapsed book (stale-leg style).
        for i in range(50):
            rows += _instant("1.1", _BASE_TS + pd.Timedelta(seconds=100 + i), 1.2, 10.0, 10.0, phase="IN_PLAY")
        summary = summarise_green_out(green_out_scan(_dataset(rows)))
        # Whatever the in-play crop does, with no pre-match green-outs it is not viable.
        assert summary.verdict == "NOT_VIABLE"
        assert "IN_PLAY" in summary.phase_breakdown

    def test_strong_repeatable_pre_match_edge_is_viable(self):
        # A clear, repeated, PLAUSIBLE pre-match green-out: book ~0.94 (above the
        # 0.90 artifact floor), ~6% gross -> ~5.9% after 5% commission, over many
        # instants and matches -> clears both the rate and the margin bar.
        # 1/2.6 + 1/4.6 + 1/5.1 = 0.3846 + 0.2174 + 0.1961 = 0.7981? too low.
        # Use 1/2.3 + 1/5.0 + 1/6.0 = 0.4348 + 0.2 + 0.1667 = 0.8014 -> too low.
        # Target book ~0.94: 1/2.5 + 1/4.5 + 1/6.0 = 0.4 + 0.2222 + 0.1667 = 0.7889? no.
        # Simpler: three equal legs at 3.19 -> 3/3.19 = 0.9404.
        rows = []
        for m in range(5):
            for i in range(30):
                rows += _instant(f"1.{m}", _BASE_TS + pd.Timedelta(minutes=i), 3.19, 3.19, 3.19, phase="PRE_MATCH")
        summary = summarise_green_out(green_out_scan(_dataset(rows)))
        pre = summary.phase_breakdown["PRE_MATCH"]
        assert pre.plausible_rate == 1.0
        assert pre.median_profit_after_commission > 0.005
        assert summary.verdict == "VIABLE"

    def test_tiny_pre_match_edge_is_not_viable(self):
        # A barely-sub-1.0 book (book ~0.999) is a real priced green-out but the
        # median margin is far below the viability bar -> NOT_VIABLE.
        rows = []
        for i in range(100):
            # 1/3.003 *3 ~= 0.9990 -> ~0.1% gross, well under 0.5% threshold.
            rows += _instant("1.1", _BASE_TS + pd.Timedelta(minutes=i), 3.003, 3.003, 3.003, phase="PRE_MATCH")
        summary = summarise_green_out(green_out_scan(_dataset(rows)))
        pre = summary.phase_breakdown["PRE_MATCH"]
        assert pre.n_green_out_instants > 0  # it IS a priced green-out
        assert summary.verdict == "NOT_VIABLE"  # but too small to be viable

    def test_plausible_floor_separates_artifacts(self):
        # One absurd book (0.5) + many plausible near-1.0 non-green-outs.
        rows = _instant("1.1", _BASE_TS, 2.0, 5.0, 100.0, phase="PRE_MATCH")  # 0.5+0.2+0.01=0.71 book
        scan = green_out_scan(_dataset(rows))
        summary = summarise_green_out(scan)
        pre = summary.phase_breakdown["PRE_MATCH"]
        # The single sub-floor book is flagged green but is NOT plausible.
        assert pre.n_green_out_instants == 1
        assert pre.n_plausible == 0
        assert pre.min_book is not None and pre.min_book < PLAUSIBLE_BOOK_FLOOR
