"""Property-based tests for SP-369: odds & volume behaviour characterisation.

Tests the correctness properties of the pure-logic analysis core in
``logic/odds_behaviour.py`` -- the implied-probability book, pre-match drift and
its summary, and volume-spike derivation. These functions consume the SP-368
Research_Dataset (the 12-column ``DATASET_SCHEMA`` frame,
``docs/research_dataset_schema.md``) and are pure/I-O-free, so they can be
property-tested deterministically with synthetic frames -- no Parquet, no DB,
and no dependence on the (richer) captured data that only exists on the Pi.

The generators here build schema-shaped DataFrames directly rather than reusing
the SP-368 record generators: SP-369 operates one layer up, on the assembled
frame, so generating frames keeps the properties close to what the functions
actually receive. Prices are constrained to realistic positive decimal odds so
``implied_probability`` is always defined; the drift/book null-handling paths
are covered by explicit example-based tests alongside these properties.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from logic.odds_behaviour import (
    BOOK_COLUMNS,
    DRIFT_COLUMNS,
    OUTCOME_ROLES,
    VOLUME_SPIKE_COLUMNS,
    DriftSummary,
    implied_probability,
    implied_probability_book,
    pre_match_drift,
    profile_behaviour,
    summarise_drift,
    volume_spikes,
)
from logic.research_dataset import DATASET_SCHEMA

# --- Synthetic Research_Dataset generation ------------------------------------

# A realistic positive decimal-odds price (Betfair range is ~1.01-1000). Kept
# strictly positive and finite so implied_probability is always defined and the
# book/drift maths has no divide-by-zero or null-price edge in the property
# space (those edges are covered by the example tests below).
_price = st.floats(min_value=1.01, max_value=1000.0, allow_nan=False, allow_infinity=False)
_volume = st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)

_BASE_TS = pd.Timestamp("2026-03-14 10:00:00", tz="UTC")
_START_TS = pd.Timestamp("2026-03-14 15:00:00", tz="UTC")


@st.composite
def research_datasets(draw):
    """Generate a schema-shaped Research_Dataset DataFrame.

    Builds a frame carrying exactly the 12 ``DATASET_SCHEMA`` columns and dtypes
    for a handful of matches, each with the three HOME/DRAW/AWAY outcomes and a
    short ascending pre-match capture series of positive best-back prices and
    non-decreasing (cumulative) matched volume. This mirrors how captured
    MATCH_ODDS data is shaped once it reaches the SP-369 functions.
    """
    n_matches = draw(st.integers(min_value=1, max_value=3))
    n_ticks = draw(st.integers(min_value=1, max_value=6))

    rows: list[dict] = []
    for m in range(n_matches):
        market_id = f"1.{m}"
        for role in OUTCOME_ROLES:
            cumulative = 0.0
            for t in range(n_ticks):
                cumulative += draw(_volume)
                rows.append(
                    {
                        "market_id": market_id,
                        "runner_id": f"{market_id}-{role}",
                        "runner_name": role,
                        "outcome_role": role,
                        "timestamp": _BASE_TS + pd.Timedelta(minutes=10 * t),
                        "best_back": draw(_price),
                        "best_lay": draw(_price),
                        "matched_volume": cumulative,
                        "match_phase": "PRE_MATCH",
                        "event_id": f"evt-{m}",
                        "start_time": _START_TS,
                        "target_id": f"tgt-{m}",
                    }
                )

    frame = pd.DataFrame(rows, columns=[name for name, _ in DATASET_SCHEMA])
    for name, dtype in DATASET_SCHEMA:
        if dtype == "datetime64[ns, UTC]":
            frame[name] = pd.to_datetime(frame[name], utc=True)
        else:
            frame[name] = frame[name].astype(dtype)
    return frame


# --- Property 1: implied_probability is a correct, bounded inverse ------------


class TestProperty1ImpliedProbability:
    """Property: implied_probability(p) == 1/p and lies in (0, 1] for p >= 1."""

    @given(price=_price)
    @settings(max_examples=200)
    def test_is_reciprocal_in_unit_interval(self, price):
        prob = implied_probability(price)
        assert prob is not None
        assert prob == 1.0 / price
        # Decimal odds >= 1 -> probability in (0, 1].
        assert 0.0 < prob <= 1.0

    def test_null_and_nonpositive_are_none(self):
        assert implied_probability(None) is None
        assert implied_probability(float("nan")) is None
        assert implied_probability(0.0) is None
        assert implied_probability(-2.0) is None
        assert implied_probability(pd.NA) is None


# --- Property 2: the book equals the sum of the three implied probabilities ---


class TestProperty2BookIsSumOfProbabilities:
    """Property: each book row's book == prob_home + prob_draw + prob_away."""

    @given(dataset=research_datasets())
    @settings(max_examples=100)
    def test_book_equals_sum_of_outcome_probabilities(self, dataset):
        book = implied_probability_book(dataset)
        assert list(book.columns) == list(BOOK_COLUMNS)
        for _, row in book.iterrows():
            parts = [row["prob_home"], row["prob_draw"], row["prob_away"]]
            # The generator supplies all three outcomes at every instant, so the
            # as-of forward-fill leaves no leading gap: every book value is
            # defined and equals the sum of the three forward-filled probs.
            assert all(pd.notna(part) for part in parts)
            assert abs(row["book"] - sum(parts)) < 1e-9

    @given(dataset=research_datasets())
    @settings(max_examples=50)
    def test_one_book_row_per_market_timestamp(self, dataset):
        book = implied_probability_book(dataset)
        # Exactly one book row per distinct (market_id, timestamp) instant.
        distinct = dataset[["market_id", "timestamp"]].drop_duplicates()
        assert len(book) == len(distinct)
        # And the output is ordered by (market_id, timestamp).
        ordered = book.sort_values(["market_id", "timestamp"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(book, ordered)


# --- Property 3: drift relates first/last prices consistently -----------------


class TestProperty3DriftConsistency:
    """Property: abs_drift == last - first and rel_drift == last/first - 1."""

    @given(dataset=research_datasets())
    @settings(max_examples=100)
    def test_drift_columns_are_internally_consistent(self, dataset):
        drift = pre_match_drift(dataset)
        assert list(drift.columns) == list(DRIFT_COLUMNS)
        for _, row in drift.iterrows():
            assert abs(row["abs_drift"] - (row["last_price"] - row["first_price"])) < 1e-9
            assert abs(row["rel_drift"] - (row["last_price"] / row["first_price"] - 1.0)) < 1e-9
            assert row["first_price"] > 0.0
            assert row["n_observations"] >= 2

    @given(dataset=research_datasets())
    @settings(max_examples=50)
    def test_determinism(self, dataset):
        # Identical input -> identical output (pure, sorted deterministically).
        first = pre_match_drift(dataset)
        second = pre_match_drift(dataset)
        pd.testing.assert_frame_equal(first, second)


# --- Property 4: drift summary statistics are bounded and faithful ------------


class TestProperty4DriftSummary:
    """Property: summary stats match the drift frame they aggregate."""

    @given(dataset=research_datasets())
    @settings(max_examples=100)
    def test_summary_matches_drift_frame(self, dataset):
        drift = pre_match_drift(dataset)
        summary = summarise_drift(drift)
        assert summary.n_selections == len(drift)
        if drift.empty:
            assert summary.mean_rel_drift is None
            assert summary.shorten_fraction is None
            return
        rel = drift["rel_drift"].astype(float)
        assert abs(summary.mean_rel_drift - float(rel.mean())) < 1e-9
        assert abs(summary.median_rel_drift - float(rel.median())) < 1e-9
        # shorten_fraction is a proportion in [0, 1].
        assert 0.0 <= summary.shorten_fraction <= 1.0


# --- Property 5: volume deltas reconstruct cumulative volume ------------------


class TestProperty5VolumeDeltas:
    """Property: per-series volume deltas are the consecutive differences."""

    @given(dataset=research_datasets(), threshold=st.floats(min_value=0.0, max_value=1e6, allow_nan=False))
    @settings(max_examples=100)
    def test_deltas_are_consecutive_differences_and_spikes_respect_threshold(self, dataset, threshold):
        spikes = volume_spikes(dataset, threshold)
        assert list(spikes.columns) == list(VOLUME_SPIKE_COLUMNS)
        for (_market, _runner), group in spikes.groupby(["market_id", "runner_id"]):
            ordered = group.sort_values("timestamp")
            vols = ordered["matched_volume"].astype(float).to_list()
            deltas = ordered["volume_delta"].to_list()
            # First delta is null (no predecessor); the rest are consecutive diffs.
            assert pd.isna(deltas[0])
            for i in range(1, len(vols)):
                assert abs(deltas[i] - (vols[i] - vols[i - 1])) < 1e-6
            # is_spike is exactly "delta strictly above threshold" (null -> not a spike).
            for delta, is_spike in zip(ordered["volume_delta"], ordered["is_spike"], strict=True):
                expected = (not pd.isna(delta)) and (delta > threshold)
                assert bool(is_spike) == expected


# --- Property 6: empty dataset yields a typed, zero-count profile -------------


class TestProperty6EmptyDataset:
    """Property: an empty dataset yields empty-but-typed frames and zero counts."""

    def _empty_dataset(self):
        frame = pd.DataFrame({name: pd.Series([], dtype="object") for name, _ in DATASET_SCHEMA})
        return frame

    def test_empty_profile_is_safe(self):
        profile = profile_behaviour(self._empty_dataset(), volume_spike_threshold=1000.0)
        assert profile.n_matches == 0
        assert profile.n_pre_match_rows == 0
        assert profile.n_in_play_rows == 0
        assert profile.drift_summary == DriftSummary()
        assert list(profile.book.columns) == list(BOOK_COLUMNS)
        assert list(profile.drift.columns) == list(DRIFT_COLUMNS)
        assert list(profile.volume.columns) == list(VOLUME_SPIKE_COLUMNS)
        assert profile.book.empty and profile.drift.empty and profile.volume.empty


# --- Example-based coverage of the null / undetermined paths ------------------


class TestNullHandlingExamples:
    """Explicit coverage of the null-price / undetermined-role edges."""

    def _dataset(self, rows):
        frame = pd.DataFrame(rows, columns=[name for name, _ in DATASET_SCHEMA])
        for name, dtype in DATASET_SCHEMA:
            if dtype == "datetime64[ns, UTC]":
                frame[name] = pd.to_datetime(frame[name], utc=True)
            else:
                frame[name] = frame[name].astype(dtype)
        return frame

    def _row(self, **kwargs):
        base = {
            "market_id": "1.1",
            "runner_id": "r1",
            "runner_name": "HOME",
            "outcome_role": "HOME",
            "timestamp": _BASE_TS,
            "best_back": 2.0,
            "best_lay": 2.1,
            "matched_volume": 10.0,
            "match_phase": "PRE_MATCH",
            "event_id": "e1",
            "start_time": _START_TS,
            "target_id": "t1",
        }
        base.update(kwargs)
        return base

    def test_undetermined_role_rows_excluded_from_book(self):
        rows = [
            self._row(runner_id="r1", outcome_role=None, best_back=2.0),
            self._row(runner_id="r2", outcome_role=None, best_back=3.0),
        ]
        book = implied_probability_book(self._dataset(rows))
        assert book.empty

    def test_partial_instant_yields_null_book(self):
        # Only HOME present at the instant -> draw/away null -> book null.
        rows = [self._row(runner_id="r1", outcome_role="HOME", best_back=2.0)]
        book = implied_probability_book(self._dataset(rows))
        assert len(book) == 1
        assert pd.isna(book.iloc[0]["book"])
        assert book.iloc[0]["prob_home"] == 0.5

    def test_staggered_timestamps_forward_fill_into_a_complete_book(self):
        # The real Pi shape: the three outcomes are captured at DISTINCT
        # timestamps, never simultaneously (observed 2757/2757 instants with a
        # single outcome). As-of forward-fill must still produce a complete book
        # once all three have been seen -- the exact-timestamp pivot produced an
        # all-null book here, which is the bug this fixes.
        t0 = _BASE_TS
        t1 = _BASE_TS + pd.Timedelta(seconds=10)
        t2 = _BASE_TS + pd.Timedelta(seconds=20)
        t3 = _BASE_TS + pd.Timedelta(seconds=30)
        rows = [
            self._row(runner_id="h", outcome_role="HOME", timestamp=t0, best_back=2.0),  # P=0.5
            self._row(runner_id="d", outcome_role="DRAW", timestamp=t1, best_back=4.0),  # P=0.25
            self._row(runner_id="a", outcome_role="AWAY", timestamp=t2, best_back=4.0),  # P=0.25
            self._row(runner_id="h", outcome_role="HOME", timestamp=t3, best_back=2.0),  # P=0.5
        ]
        book = implied_probability_book(self._dataset(rows))
        # One row per distinct timestamp in the match.
        assert len(book) == 4
        by_ts = book.set_index("timestamp")
        # Before all three outcomes have been seen (t0, t1) the book is still null.
        assert pd.isna(by_ts.loc[t0, "book"])
        assert pd.isna(by_ts.loc[t1, "book"])
        # From t2 onward all three are known -> complete, forward-filled book.
        assert abs(by_ts.loc[t2, "book"] - (0.5 + 0.25 + 0.25)) < 1e-9
        # At t3 HOME updates; DRAW/AWAY carried forward -> book still complete.
        assert abs(by_ts.loc[t3, "book"] - (0.5 + 0.25 + 0.25)) < 1e-9

    def test_in_play_rows_excluded_from_drift(self):
        rows = [
            self._row(runner_id="r1", match_phase="IN_PLAY", timestamp=_BASE_TS, best_back=2.0),
            self._row(
                runner_id="r1", match_phase="IN_PLAY", timestamp=_BASE_TS + pd.Timedelta(minutes=5), best_back=1.8
            ),
        ]
        drift = pre_match_drift(self._dataset(rows))
        assert drift.empty

    def test_single_pre_match_price_is_not_measurable_drift(self):
        rows = [self._row(runner_id="r1", timestamp=_BASE_TS, best_back=2.0)]
        drift = pre_match_drift(self._dataset(rows))
        assert drift.empty

    def test_null_price_rows_do_not_count_toward_drift(self):
        rows = [
            self._row(runner_id="r1", timestamp=_BASE_TS, best_back=2.0),
            self._row(runner_id="r1", timestamp=_BASE_TS + pd.Timedelta(minutes=5), best_back=None),
        ]
        # Only one usable price -> not measurable.
        drift = pre_match_drift(self._dataset(rows))
        assert drift.empty
