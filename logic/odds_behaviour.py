"""Pure-logic characterisation of odds & volume behaviour (SP-369).

This module is the pure, I/O-free analysis core for the SP-369 exploratory
study: given the SP-368 Research_Dataset (a pandas DataFrame carrying the 12
``DATASET_SCHEMA`` columns, see ``docs/research_dataset_schema.md``), it derives
the summary signals the exploration is built on -- pre-match odds drift, the
cross-outcome implied-probability book, and volume spikes -- without touching
the database, filesystem, or network.

Like ``logic/research_dataset.py`` and ``logic/quality_checks.py`` it contains
no ``os``, no ``psycopg2``, and no file or network access, so every derivation
can be property-tested deterministically. The thin wrapper that loads the
Parquet dataset, calls into here, and writes charts + a run summary lives in
``scripts/explore_odds_behaviour.py`` (following the SP-328/SP-368 ``logic/`` +
``scripts/`` split).

Scope (SP-369): *characterise* how the captured odds behave; produce the
numbers and tidy frames the charts and the Confluence writeup are built from.
It deliberately does NOT evaluate strategies -- the green-out viability maths is
SP-370 and the drift-trading P&L simulation is SP-371. This module only
describes the raw material (e.g. it computes the implied-probability book so
SP-370 can scan it, but it draws no profit conclusion).

All odds-only (Req / epic scoping): every signal here is derived from the
captured odds time-series alone, using no match results or settlement data.

NOTE (sketch): this is the SP-369 scaffolding -- data models, the module
contract, and the function docstrings are defined here; the behavioural bodies
are filled in as the exploration proceeds. Functions that are not yet
implemented raise ``NotImplementedError`` so an accidental early call is loud
rather than silently wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

__all__ = [
    "OUTCOME_ROLES",
    "BOOK_COLUMNS",
    "DRIFT_COLUMNS",
    "VOLUME_SPIKE_COLUMNS",
    "DriftSummary",
    "BehaviourProfile",
    "implied_probability",
    "implied_probability_book",
    "pre_match_drift",
    "summarise_drift",
    "volume_spikes",
    "profile_behaviour",
]

# The three MATCH_ODDS outcomes, in the canonical Home/Draw/Away order the
# charts and the book overlay present them in. Matches an SP-368 record's
# ``outcome_role`` values; a row whose role is null (undetermined match, Req 2.7)
# is excluded from any role-keyed derivation here.
OUTCOME_ROLES: tuple[str, ...] = ("HOME", "DRAW", "AWAY")

# Columns of the per-match implied-probability-book frame produced by
# ``implied_probability_book`` -- one row per (market_id, timestamp), carrying
# each outcome's implied probability plus their sum (the "book" / overround).
BOOK_COLUMNS: tuple[str, ...] = (
    "market_id",
    "timestamp",
    "match_phase",
    "prob_home",
    "prob_draw",
    "prob_away",
    "book",
)

# Columns of the per-(match, runner) pre-match drift frame produced by
# ``pre_match_drift``: the first and last pre-match best-back prices observed in
# the measured window and the absolute / relative move between them.
DRIFT_COLUMNS: tuple[str, ...] = (
    "market_id",
    "runner_id",
    "outcome_role",
    "first_price",
    "last_price",
    "abs_drift",
    "rel_drift",
    "n_observations",
)

# Columns of the volume-spike frame produced by ``volume_spikes``: the
# per-observation matched-volume increments and a flag marking the ones that
# exceed the configured spike threshold.
VOLUME_SPIKE_COLUMNS: tuple[str, ...] = (
    "market_id",
    "runner_id",
    "timestamp",
    "match_phase",
    "matched_volume",
    "volume_delta",
    "is_spike",
)


@dataclass(frozen=True)
class DriftSummary:
    """Aggregate characterisation of pre-match drift across the dataset.

    Produced by ``summarise_drift`` from the per-(match, runner) drift frame. It
    answers the SP-369 question "do odds drift as kick-off approaches, how much
    and how consistently?" with distribution statistics over the relative drift,
    split by whether the selection was the (pre-match) favourite.

    Attributes:
        n_selections: Number of (match, runner) series that had enough pre-match
            observations to measure a drift.
        mean_rel_drift: Mean relative drift (``last/first - 1``) across those
            selections; positive means prices lengthened (drifted out) on
            average, negative means they shortened (came in).
        median_rel_drift: Median relative drift, less sensitive to outliers.
        shorten_fraction: Fraction of selections whose price shortened
            (``rel_drift < 0``) -- the "how consistently" measure.
        favourite_mean_rel_drift: Mean relative drift restricted to the
            selection that was the favourite (shortest first price) in its
            match, or ``None`` when no favourites were measurable.
    """

    n_selections: int = 0
    mean_rel_drift: float | None = None
    median_rel_drift: float | None = None
    shorten_fraction: float | None = None
    favourite_mean_rel_drift: float | None = None


@dataclass(frozen=True)
class BehaviourProfile:
    """The full SP-369 characterisation bundle for one dataset.

    The top-level return of ``profile_behaviour``: the tidy frames the charts
    are drawn from plus the headline drift summary and a few dataset-shape
    counts for the Confluence writeup. Every frame carries its declared
    ``*_COLUMNS`` contract so the plotting wrapper can rely on column presence
    regardless of row count.

    Attributes:
        book: Per-(match, timestamp) implied-probability-book frame
            (``BOOK_COLUMNS``) -- the Home/Draw/Away overlay + overround that
            sets up the SP-370 green-out scan.
        drift: Per-(match, runner) pre-match drift frame (``DRIFT_COLUMNS``).
        drift_summary: Aggregate ``DriftSummary`` over ``drift``.
        volume: Per-observation volume-spike frame (``VOLUME_SPIKE_COLUMNS``).
        n_matches: Distinct ``market_id`` count in the input dataset.
        n_pre_match_rows: Row count with ``match_phase == "PRE_MATCH"``.
        n_in_play_rows: Row count with ``match_phase == "IN_PLAY"``.
    """

    book: pd.DataFrame
    drift: pd.DataFrame
    drift_summary: DriftSummary
    volume: pd.DataFrame
    n_matches: int = 0
    n_pre_match_rows: int = 0
    n_in_play_rows: int = 0
    notes: list[str] = field(default_factory=list)


# --- Implied-probability book (Req: overlay outcomes, sets up SP-370) ---------


def implied_probability(odds: float | None) -> float | None:
    """Convert a single decimal-odds price to an implied probability.

    The implied probability of a decimal price ``p`` is ``1 / p`` (ignoring the
    back/lay spread and commission -- this is the raw book signal SP-370 refines,
    not a strategy calculation). Returns ``None`` for a null, non-positive, or
    otherwise unusable price so a missing ladder (Req 1.6 null ``best_back``)
    propagates as a null probability rather than raising or dividing by zero.

    Args:
        odds: A decimal-odds price (e.g. ``best_back``), or ``None``.

    Returns:
        The implied probability in ``(0, 1]``, or ``None`` when the price is
        absent or not a usable positive number.
    """
    if odds is None:
        return None
    # pandas NA / NaN and non-numeric values are "no price": guard before the
    # comparison so a null best_back (Req 1.6) propagates rather than raising.
    if pd.isna(odds):
        return None
    try:
        price = float(odds)
    except (TypeError, ValueError):
        return None
    if price <= 0.0:
        return None
    return 1.0 / price


def implied_probability_book(dataset: pd.DataFrame) -> pd.DataFrame:
    """Build the per-(match, timestamp) implied-probability book across outcomes.

    For each ``(market_id, timestamp)`` in the dataset, pivot the three
    ``outcome_role`` selections' ``best_back`` prices into Home/Draw/Away implied
    probabilities and sum them into the ``book`` (the overround): a book above
    1.0 is the bookmaker's margin, a book below 1.0 at some instant is the
    green-out signal SP-370 scans for. Rows whose outcome role is undetermined
    (null, Req 2.7) are excluded, and an instant missing one of the three
    outcomes yields a null for that outcome's probability and a null ``book``.

    The result carries exactly ``BOOK_COLUMNS`` and is ordered by
    ``(market_id, timestamp)`` so identical input yields identical output and the
    overlay chart reads left-to-right in capture order.

    Args:
        dataset: The SP-368 Research_Dataset frame (``DATASET_SCHEMA`` columns).

    Returns:
        A ``BOOK_COLUMNS`` DataFrame, one row per ``(market_id, timestamp)``.
    """
    # Keep only rows with a determined outcome role (Req 2.7: null role rows are
    # excluded from any role-keyed derivation). A role outside HOME/DRAW/AWAY
    # (should not occur per the schema) is dropped the same way.
    rows = dataset[dataset["outcome_role"].isin(OUTCOME_ROLES)]
    if rows.empty:
        return _empty_framed(BOOK_COLUMNS)

    role_to_prob = {"HOME": "prob_home", "DRAW": "prob_draw", "AWAY": "prob_away"}

    records: list[dict] = []
    # One output row per (market_id, timestamp) instant.
    grouped = rows.groupby(["market_id", "timestamp"], sort=True, dropna=False)
    for (market_id, timestamp), group in grouped:
        probs: dict[str, float | None] = {"prob_home": None, "prob_draw": None, "prob_away": None}
        # match_phase is per-(market,timestamp) constant in practice; take the
        # first seen so the book row can be split pre-match / in-play downstream.
        phase = group["match_phase"].iloc[0]
        for role, column in role_to_prob.items():
            role_rows = group[group["outcome_role"] == role]
            if not role_rows.empty:
                probs[column] = implied_probability(role_rows["best_back"].iloc[0])

        # The book (overround) is the sum of the three implied probabilities;
        # null whenever any outcome's probability is missing at this instant, so
        # a partial instant does not masquerade as a complete book.
        if any(value is None for value in probs.values()):
            book: float | None = None
        else:
            book = probs["prob_home"] + probs["prob_draw"] + probs["prob_away"]

        records.append(
            {
                "market_id": market_id,
                "timestamp": timestamp,
                "match_phase": phase,
                "prob_home": probs["prob_home"],
                "prob_draw": probs["prob_draw"],
                "prob_away": probs["prob_away"],
                "book": book,
            }
        )

    frame = pd.DataFrame.from_records(records, columns=list(BOOK_COLUMNS))
    # Deterministic order: identical input -> identical output, left-to-right in
    # capture order per match.
    return frame.sort_values(["market_id", "timestamp"]).reset_index(drop=True)


# --- Pre-match drift (Req: do favourites' odds drift toward kick-off?) --------


def pre_match_drift(dataset: pd.DataFrame) -> pd.DataFrame:
    """Measure each selection's pre-match best-back drift toward kick-off.

    Restricts to ``match_phase == "PRE_MATCH"`` rows (odds-only, no results:
    Req 8), groups by ``(market_id, runner_id)``, orders each series by ascending
    ``timestamp``, and records the first and last pre-match ``best_back`` price
    plus the absolute (``last - first``) and relative (``last / first - 1``)
    move. A positive drift means the price lengthened (drifted out); negative
    means it shortened (came in). Series with fewer than two usable pre-match
    prices, or a non-positive first price, are dropped (no measurable drift).

    The result carries exactly ``DRIFT_COLUMNS`` and is ordered by
    ``(market_id, runner_id)``.

    Args:
        dataset: The SP-368 Research_Dataset frame.

    Returns:
        A ``DRIFT_COLUMNS`` DataFrame, one row per measured ``(market_id,
        runner_id)``.
    """
    # Odds-only, pre-match window only (Req 8): in-play rows are driven by
    # in-game events we don't model in 0.7, so drift is measured strictly before
    # kick-off.
    pre = dataset[dataset["match_phase"] == "PRE_MATCH"]
    if pre.empty:
        return _empty_framed(DRIFT_COLUMNS)

    records: list[dict] = []
    for (market_id, runner_id), group in pre.groupby(["market_id", "runner_id"], sort=True, dropna=False):
        ordered = group.sort_values("timestamp")
        # Only usable (non-null) best-back prices count toward a drift.
        prices = ordered["best_back"].dropna()
        if len(prices) < 2:
            continue
        first_price = float(prices.iloc[0])
        last_price = float(prices.iloc[-1])
        # A non-positive first price can't anchor a relative move.
        if first_price <= 0.0:
            continue
        # outcome_role is per-(market,runner) constant; take the first seen.
        outcome_role = group["outcome_role"].iloc[0]
        records.append(
            {
                "market_id": market_id,
                "runner_id": runner_id,
                "outcome_role": outcome_role,
                "first_price": first_price,
                "last_price": last_price,
                "abs_drift": last_price - first_price,
                "rel_drift": last_price / first_price - 1.0,
                "n_observations": int(len(prices)),
            }
        )

    frame = pd.DataFrame.from_records(records, columns=list(DRIFT_COLUMNS))
    if frame.empty:
        return _empty_framed(DRIFT_COLUMNS)
    return frame.sort_values(["market_id", "runner_id"]).reset_index(drop=True)


def summarise_drift(drift: pd.DataFrame) -> DriftSummary:
    """Aggregate a ``pre_match_drift`` frame into the headline drift summary.

    Computes the distribution statistics that answer "how much, how
    consistently" -- mean/median relative drift, the fraction of selections that
    shortened, and the mean relative drift of the per-match favourite (the
    selection with the shortest first pre-match price). An empty input yields a
    zero-count ``DriftSummary`` with null statistics rather than raising.

    Args:
        drift: A ``DRIFT_COLUMNS`` frame from ``pre_match_drift``.

    Returns:
        The aggregate :class:`DriftSummary`.
    """
    if drift.empty:
        return DriftSummary()

    rel = drift["rel_drift"].astype(float)
    n_selections = int(len(rel))
    mean_rel_drift = float(rel.mean())
    median_rel_drift = float(rel.median())
    # "How consistently": the share of selections whose price shortened
    # (rel_drift < 0, i.e. came in toward kick-off).
    shorten_fraction = float((rel < 0.0).mean())

    # The favourite is the shortest first pre-match price within each match; its
    # mean relative drift is the headline "do favourites drift?" number.
    favourite_mean_rel_drift: float | None = None
    favourites = drift.loc[drift.groupby("market_id")["first_price"].idxmin()]
    if not favourites.empty:
        favourite_mean_rel_drift = float(favourites["rel_drift"].astype(float).mean())

    return DriftSummary(
        n_selections=n_selections,
        mean_rel_drift=mean_rel_drift,
        median_rel_drift=median_rel_drift,
        shorten_fraction=shorten_fraction,
        favourite_mean_rel_drift=favourite_mean_rel_drift,
    )


# --- Volume behaviour (Req: in-play movement & volume spikes) -----------------


def volume_spikes(dataset: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Derive per-observation matched-volume increments and flag spikes.

    Groups by ``(market_id, runner_id)``, orders each series by ascending
    ``timestamp``, and computes the ``volume_delta`` between consecutive
    ``matched_volume`` readings (matched volume is cumulative, so the increment
    is the activity in that interval). A row is flagged ``is_spike`` when its
    delta exceeds ``threshold``. Rows with a null ``matched_volume`` (Req 1.2)
    carry a null delta and are not spikes. ``match_phase`` is retained so the
    writeup can relate spikes to the pre-match / in-play boundary (where in-play
    data exists at all).

    The result carries exactly ``VOLUME_SPIKE_COLUMNS`` and is ordered by
    ``(market_id, runner_id, timestamp)``.

    Args:
        dataset: The SP-368 Research_Dataset frame.
        threshold: The ``volume_delta`` above which an interval counts as a
            spike (in matched-volume units).

    Returns:
        A ``VOLUME_SPIKE_COLUMNS`` DataFrame.
    """
    if dataset.empty:
        return _empty_framed(VOLUME_SPIKE_COLUMNS)

    frames: list[pd.DataFrame] = []
    for (market_id, runner_id), group in dataset.groupby(["market_id", "runner_id"], sort=True, dropna=False):
        ordered = group.sort_values("timestamp")
        volume = pd.to_numeric(ordered["matched_volume"], errors="coerce")
        # Matched volume is cumulative, so the per-interval activity is the
        # consecutive increment. The first reading has no predecessor -> null
        # delta (not a spike). A null reading yields a null delta either side.
        delta = volume.diff()
        part = pd.DataFrame(
            {
                "market_id": market_id,
                "runner_id": runner_id,
                "timestamp": ordered["timestamp"].to_numpy(),
                "match_phase": ordered["match_phase"].to_numpy(),
                "matched_volume": volume.to_numpy(),
                "volume_delta": delta.to_numpy(),
            }
        )
        # A spike is a delta strictly above the threshold; a null delta is never
        # a spike (NaN > threshold is False, which is what we want).
        part["is_spike"] = part["volume_delta"] > threshold
        frames.append(part)

    frame = pd.concat(frames, ignore_index=True)
    frame = frame[list(VOLUME_SPIKE_COLUMNS)]
    return frame.sort_values(["market_id", "runner_id", "timestamp"]).reset_index(drop=True)


# --- Top-level characterisation bundle ----------------------------------------


def profile_behaviour(dataset: pd.DataFrame, volume_spike_threshold: float) -> BehaviourProfile:
    """Produce the full SP-369 characterisation bundle for one dataset.

    The single pure entry point the ``scripts/explore_odds_behaviour.py`` wrapper
    calls: assembles the implied-probability book, the pre-match drift frame and
    its summary, and the volume-spike frame, plus the dataset-shape counts
    (distinct matches, pre-match vs in-play row counts) the Confluence writeup
    reports. Performs no I/O. An empty dataset yields a profile of empty-but-
    typed frames and zero counts rather than raising, so the wrapper can report
    "no data" cleanly.

    Args:
        dataset: The SP-368 Research_Dataset frame (``DATASET_SCHEMA`` columns).
        volume_spike_threshold: Passed through to :func:`volume_spikes`.

    Returns:
        The assembled :class:`BehaviourProfile`.
    """
    notes: list[str] = []

    if dataset.empty:
        notes.append("Dataset is empty: no matches, no analysable rows.")
        return BehaviourProfile(
            book=_empty_framed(BOOK_COLUMNS),
            drift=_empty_framed(DRIFT_COLUMNS),
            drift_summary=DriftSummary(),
            volume=_empty_framed(VOLUME_SPIKE_COLUMNS),
            n_matches=0,
            n_pre_match_rows=0,
            n_in_play_rows=0,
            notes=notes,
        )

    book = implied_probability_book(dataset)
    drift = pre_match_drift(dataset)
    drift_summary = summarise_drift(drift)
    volume = volume_spikes(dataset, volume_spike_threshold)

    n_matches = int(dataset["market_id"].nunique(dropna=True))
    n_pre_match_rows = int((dataset["match_phase"] == "PRE_MATCH").sum())
    n_in_play_rows = int((dataset["match_phase"] == "IN_PLAY").sum())

    # Surface coverage honesty for the writeup: capture targets pre-match, so
    # in-play material may be thin or absent (plan draft). Flag it rather than
    # let the volume/in-play section silently over-claim.
    if n_in_play_rows == 0:
        notes.append("No IN_PLAY rows in this dataset; in-play characterisation is not possible.")
    if drift_summary.n_selections == 0:
        notes.append("No selection had two or more usable pre-match prices; drift is not measurable.")

    return BehaviourProfile(
        book=book,
        drift=drift,
        drift_summary=drift_summary,
        volume=volume,
        n_matches=n_matches,
        n_pre_match_rows=n_pre_match_rows,
        n_in_play_rows=n_in_play_rows,
        notes=notes,
    )


def _empty_framed(columns: Sequence[str]) -> pd.DataFrame:
    """Return an empty DataFrame carrying exactly ``columns`` (helper).

    Used by the empty-dataset paths so an all-empty profile still presents the
    declared ``*_COLUMNS`` contract to the plotting wrapper, mirroring the SP-368
    ``empty_dataset`` guarantee that columns are present regardless of row count.
    """
    return pd.DataFrame({name: pd.Series(dtype="object") for name in columns})
