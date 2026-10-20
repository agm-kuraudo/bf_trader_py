"""Pure-logic green-out / cross-outcome arbitrage scan (SP-370).

This module is the pure, I/O-free analysis core for the SP-370 green-out scan:
given the SP-368 Research_Dataset (the 12-column ``DATASET_SCHEMA`` frame, see
``docs/research_dataset_schema.md``), it tests the first 0.7 strategy idea --
can you back all three MATCH_ODDS outcomes (Home/Draw/Away) at once for a profit
that is locked in regardless of which outcome wins? -- as pure maths on the
captured best-back prices, with realistic Betfair commission.

Like ``logic/odds_behaviour.py`` and ``logic/research_dataset.py`` it does no
database, filesystem, or network access, so every derivation is property-
testable deterministically. The thin wrapper that loads the Parquet dataset,
calls into here, writes a chart, and reports the verdict lives in
``scripts/scan_green_out.py`` (the SP-328/SP-368 ``logic/`` + ``scripts/`` split).

## The maths (back-all-three Dutching)

For an instant with best-back prices ``b_home, b_draw, b_away`` the implied book
(overround) is ``B = 1/b_home + 1/b_draw + 1/b_away`` -- exactly the quantity
SP-369's ``implied_probability_book`` already computes, which this module reuses
rather than re-deriving. A back-all-three Dutch stakes each outcome in
proportion to its implied probability (``s_i = (1/b_i) / B`` for unit total
stake), so whichever outcome wins returns the same gross ``1/B`` per unit staked.
The profit before commission is therefore ``1/B - 1`` on **every** outcome -- a
locked-in result. Betfair commission ``c`` is charged on net market winnings, so
it only scales a POSITIVE profit: ``profit_after = max(1/B - 1, ...) * (1 - c)``
when ``B < 1``. Commission does not move the break-even point: a locked-in profit
after commission exists **iff ``B < 1``** (equivalently ``1/B > 1``). Commission
determines the *size* of the edge, not its existence.

## Honest scope / data limitation

The SP-368 dataset carries only best-back / best-lay prices -- NOT available
sizes or ladder depth (those live inside the loader's ``StructuredOdds`` but are
deliberately out of the tabular schema), and ``matched_volume`` is null for the
whole captured season (SP-384, deferred out of 0.7). So this scan answers
"does a PRICED back-all-three arb exist after commission?" but CANNOT constrain
by available stake size or report real liquidity -- you could not know how much
you'd actually get matched. That liquidity limitation is surfaced explicitly in
the scan output (and the writeup), exactly as SP-369 surfaced the volume gap; it
is not silently ignored. The book is reconstructed by the same as-of forward-
fill alignment as SP-369 (outcomes are captured at distinct timestamps), so a
flagged instant may pair a fresh price on one leg with a slightly stale price on
another -- another reason a priced edge is a CANDIDATE, not a booked profit.

NOTE (sketch): data models, the module contract, and function docstrings are
defined here; behavioural bodies are implemented below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from logic.odds_behaviour import implied_probability_book

__all__ = [
    "DEFAULT_COMMISSION",
    "PLAUSIBLE_BOOK_FLOOR",
    "GREEN_OUT_COLUMNS",
    "GreenOutSummary",
    "PhaseStats",
    "green_out_scan",
    "summarise_green_out",
]

# Betfair's standard exchange commission on net market winnings. The UK base
# rate is 5% (0.05); the actual rate varies with the Market Base Rate and a
# customer's discount, so it is a parameter everywhere and this is only the
# default. Commission scales a positive edge but does NOT move the B < 1
# break-even, so the opportunity COUNT is commission-independent; the reported
# MARGIN is not.
DEFAULT_COMMISSION: float = 0.05

# Books below this floor are treated as forward-fill artifacts, not real priced
# green-outs. A genuine MATCH_ODDS book sits around 1.0; a book that has
# collapsed FAR below 1.0 is almost always a stale leg paired with a fresh one
# during a fast (in-play) move -- a ~10%+ locked-in return on a liquid single
# exchange does not happen. The floor separates "plausible" green-outs (a book
# modestly below 1.0, which could be a real if rare priced edge) from obvious
# artifacts (a book that has collapsed implausibly low). It is set at 0.90: on
# the captured data, pre-match green-outs bottom out around 0.966 (all
# plausible) while in-play reaches ~0.31 (clear artifacts), so 0.90 cleanly
# separates the two while still admitting a generous ~11% gross edge as
# "plausible" rather than prejudging it.
PLAUSIBLE_BOOK_FLOOR: float = 0.90

# Columns of the per-(match, timestamp) green-out frame produced by
# ``green_out_scan``: the reconstructed book, whether this instant is a priced
# green-out (book < 1), and the locked-in profit per unit total stake after
# commission (0.0 when not a green-out).
GREEN_OUT_COLUMNS: tuple[str, ...] = (
    "market_id",
    "timestamp",
    "match_phase",
    "book",
    "is_green_out",
    "profit_after_commission",
)


@dataclass(frozen=True)
class GreenOutSummary:
    """Season-level quantification + verdict for the green-out scan (SP-370 DoD).

    Produced by ``summarise_green_out`` from the per-instant scan frame. It
    carries the counts, typical margin, and reach (how many matches, how long
    the opportunities persist) the SP-370 Definition of Done asks for, plus the
    viable/not-viable ``verdict`` and the commission the margins were computed
    at. Every rate/margin is ``None`` when there were no complete book instants
    to measure, rather than a misleading zero.

    Attributes:
        commission: The commission rate the margins were computed at.
        n_instants: Complete book instants scanned (both an input count and the
            denominator for ``green_out_rate``).
        n_green_out_instants: Instants flagged as a priced green-out (book < 1).
        green_out_rate: ``n_green_out_instants / n_instants`` (fraction), or
            ``None`` when nothing was scanned.
        n_matches_total: Distinct matches with at least one complete book instant.
        n_matches_with_green_out: Distinct matches with at least one green-out.
        max_profit_after_commission: Largest per-unit locked-in profit seen.
        median_profit_after_commission: Median per-unit locked-in profit across
            the flagged instants (the "typical margin").
        max_run_instants: Longest consecutive run of green-out instants within a
            single match (a crude duration/persistence proxy, in instants).
        verdict: ``"VIABLE"`` or ``"NOT_VIABLE"`` -- the SP-370 conclusion.
        notes: Operator-facing caveats (e.g. the liquidity limitation).
    """

    commission: float = DEFAULT_COMMISSION
    n_instants: int = 0
    n_green_out_instants: int = 0
    green_out_rate: float | None = None
    n_matches_total: int = 0
    n_matches_with_green_out: int = 0
    max_profit_after_commission: float | None = None
    median_profit_after_commission: float | None = None
    max_run_instants: int = 0
    verdict: str = "NOT_VIABLE"
    phase_breakdown: dict[str, PhaseStats] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PhaseStats:
    """Green-out statistics for one match phase (PRE_MATCH / IN_PLAY).

    Produced per phase by ``summarise_green_out`` so the headline numbers can be
    read with the pre-match / in-play distinction that matters for this scan: the
    as-of forward-fill produces impossible sub-1.0 books in-play (a stale leg
    paired with a fresh one during a fast price move), so in-play green-outs are
    largely artifacts, while pre-match prices move slowly enough that a flagged
    book is far more trustworthy. The ``plausible_*`` fields exclude books below
    the artifact floor so an obviously-impossible margin does not inflate the
    picture.

    Attributes:
        n_instants: Complete book instants in this phase.
        n_green_out_instants: Instants flagged as a green-out (book < 1).
        green_out_rate: Flagged fraction of this phase's instants, or ``None``.
        median_profit_after_commission: Median flagged margin, or ``None``.
        max_profit_after_commission: Largest flagged margin, or ``None``.
        min_book: Smallest book seen among flagged instants, or ``None``.
        n_plausible: Flagged instants whose book is at/above the artifact floor
            (i.e. not an absurd sub-floor book) -- the trustworthy green-outs.
        plausible_rate: ``n_plausible / n_instants``, or ``None``.
    """

    n_instants: int = 0
    n_green_out_instants: int = 0
    green_out_rate: float | None = None
    median_profit_after_commission: float | None = None
    max_profit_after_commission: float | None = None
    min_book: float | None = None
    n_plausible: int = 0
    plausible_rate: float | None = None


def green_out_scan(dataset: pd.DataFrame, commission: float = DEFAULT_COMMISSION) -> pd.DataFrame:
    """Scan every instant for a priced back-all-three green-out (SP-370).

    Reconstructs the per-(match, timestamp) back-all-three book via SP-369's
    ``implied_probability_book`` (the single book-building path, shared so the
    two tickets cannot drift), then for each complete book instant flags a
    green-out when ``book < 1`` and computes the locked-in profit per unit total
    stake after commission: ``(1/book - 1) * (1 - commission)`` where positive,
    else ``0.0``. Instants with an incomplete book (an outcome not yet observed
    in the match) are dropped -- a green-out cannot be priced without all three
    legs.

    The result carries exactly ``GREEN_OUT_COLUMNS`` and is ordered by
    ``(market_id, timestamp)`` so identical input yields identical output.

    Args:
        dataset: The SP-368 Research_Dataset frame (``DATASET_SCHEMA`` columns).
        commission: Betfair commission on net winnings (defaults to
            :data:`DEFAULT_COMMISSION`).

    Returns:
        A ``GREEN_OUT_COLUMNS`` DataFrame, one row per complete book instant.
    """
    book = implied_probability_book(dataset)
    # Only instants with a complete book (all three legs priced) can be a
    # priced green-out; drop the leading-gap NaN books.
    book = book[book["book"].notna()].copy()
    if book.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in GREEN_OUT_COLUMNS})

    b = book["book"].astype(float)
    # A priced green-out exists iff the book is below 1.0 (commission scales a
    # positive edge, it does not move this break-even).
    is_green_out = b < 1.0
    gross_profit = 1.0 / b - 1.0
    # Profit after commission per unit total stake, floored at 0 for non-green
    # instants so the column is a clean "edge you could bank" signal.
    profit = (gross_profit * (1.0 - commission)).where(is_green_out, 0.0)

    out = pd.DataFrame(
        {
            "market_id": book["market_id"].to_numpy(),
            "timestamp": book["timestamp"].to_numpy(),
            "match_phase": book["match_phase"].to_numpy(),
            "book": b.to_numpy(),
            "is_green_out": is_green_out.to_numpy(),
            "profit_after_commission": profit.to_numpy(),
        }
    )
    out = out[list(GREEN_OUT_COLUMNS)]
    return out.sort_values(["market_id", "timestamp"]).reset_index(drop=True)


def summarise_green_out(
    scan: pd.DataFrame,
    commission: float = DEFAULT_COMMISSION,
    viable_min_rate: float = 0.01,
    viable_min_median_margin: float = 0.005,
) -> GreenOutSummary:
    """Quantify the scan and produce the SP-370 viable/not-viable verdict.

    Aggregates a ``green_out_scan`` frame into the counts, typical margin, and
    persistence the SP-370 Definition of Done requires, split by match phase
    (``phase_breakdown``) so the pre-match / in-play distinction that matters for
    this scan is explicit. An empty scan yields a zero-count summary with null
    rates and a ``NOT_VIABLE`` verdict rather than raising.

    The ``verdict`` is deliberately conservative and **pre-match-based**. In-play
    green-outs are overwhelmingly forward-fill artifacts (a stale leg during a
    fast move), so they are NOT allowed to drive a VIABLE verdict. ``VIABLE``
    requires *plausible* PRE_MATCH green-outs (book at/above
    :data:`PLAUSIBLE_BOOK_FLOOR`) that are both common enough (plausible rate
    strictly above ``viable_min_rate``) AND carry a median margin after
    commission strictly above ``viable_min_median_margin``. The margin bar
    matters because a green-out this scan can only *price*, not *fill*: with no
    liquidity data (SP-384) and an as-of forward-fill that can pair a fresh leg
    with a stale one, a sub-threshold median edge is indistinguishable from noise
    and is not a tradeable strategy. The defaults (rate > 1%, median margin >
    0.5%) encode "a real, repeatable edge with room to survive costs and
    uncertainty", not "any positive flicker".

    Args:
        scan: A ``GREEN_OUT_COLUMNS`` frame from :func:`green_out_scan`.
        commission: The commission the scan margins were computed at (recorded
            on the summary).
        viable_min_rate: The plausible pre-match green-out rate that must be
            strictly exceeded for a ``VIABLE`` verdict. Defaults to ``0.01``.
        viable_min_median_margin: The median pre-match margin after commission
            that must be strictly exceeded for a ``VIABLE`` verdict. Defaults to
            ``0.005`` (0.5%).

    Returns:
        The :class:`GreenOutSummary`.
    """
    # The liquidity caveat is always true on the 0.7 dataset (no size/volume
    # captured -- SP-384), so it rides on every summary.
    liquidity_note = (
        "Liquidity not assessed: the dataset carries best prices only, with no "
        "available-size or matched-volume data (SP-384). A flagged green-out is a "
        "PRICED opportunity; how much could actually be matched is unknown."
    )

    if scan.empty:
        return GreenOutSummary(
            commission=commission,
            verdict="NOT_VIABLE",
            notes=[liquidity_note, "No complete book instants to scan."],
        )

    n_instants = int(len(scan))
    flagged = scan[scan["is_green_out"]]
    n_green_out = int(len(flagged))
    green_out_rate = n_green_out / n_instants if n_instants else None

    n_matches_total = int(scan["market_id"].nunique())
    n_matches_with_green_out = int(flagged["market_id"].nunique()) if n_green_out else 0

    if n_green_out:
        margins = flagged["profit_after_commission"].astype(float)
        max_profit = float(margins.max())
        median_profit = float(margins.median())
        max_run = _longest_green_out_run(scan)
    else:
        max_profit = None
        median_profit = None
        max_run = 0

    # Per-phase breakdown (PRE_MATCH / IN_PLAY) so the verdict and the writeup
    # can separate trustworthy pre-match green-outs from in-play artifacts.
    phase_breakdown = {
        phase: _phase_stats(scan[scan["match_phase"] == phase])
        for phase in ("PRE_MATCH", "IN_PLAY")
        if (scan["match_phase"] == phase).any()
    }

    # Phase-aware, conservative verdict: driven by PLAUSIBLE PRE_MATCH green-outs
    # only. In-play artifacts (and absurd sub-floor books) cannot make it VIABLE,
    # and the median pre-match edge must clear a meaningful bar -- a tiny priced
    # edge with no liquidity behind it is not a tradeable strategy.
    pre = phase_breakdown.get("PRE_MATCH")
    viable = (
        pre is not None
        and pre.plausible_rate is not None
        and pre.plausible_rate > viable_min_rate
        and pre.median_profit_after_commission is not None
        and pre.median_profit_after_commission > viable_min_median_margin
    )

    notes = [liquidity_note]
    if n_green_out:
        notes.append(
            "Flagged green-outs are candidates only: the book is reconstructed by "
            "as-of forward-fill across independently-timed legs, so a sub-1.0 book "
            "may pair a fresh price with a stale one. In-play instants are "
            "especially prone to this, so the verdict is based on pre-match only."
        )

    return GreenOutSummary(
        commission=commission,
        n_instants=n_instants,
        n_green_out_instants=n_green_out,
        green_out_rate=green_out_rate,
        n_matches_total=n_matches_total,
        n_matches_with_green_out=n_matches_with_green_out,
        max_profit_after_commission=max_profit,
        median_profit_after_commission=median_profit,
        max_run_instants=max_run,
        verdict="VIABLE" if viable else "NOT_VIABLE",
        phase_breakdown=phase_breakdown,
        notes=notes,
    )


def _phase_stats(phase_scan: pd.DataFrame) -> PhaseStats:
    """Compute :class:`PhaseStats` for one phase's slice of the scan frame.

    Separates the trustworthy ``plausible`` green-outs (book at/above
    :data:`PLAUSIBLE_BOOK_FLOOR`) from obvious artifacts so the per-phase picture
    and the verdict are not inflated by impossible sub-floor books. An empty
    slice yields a zero-count :class:`PhaseStats` with null rates.

    Args:
        phase_scan: The ``GREEN_OUT_COLUMNS`` rows for a single match phase.

    Returns:
        The :class:`PhaseStats` for that phase.
    """
    n_instants = int(len(phase_scan))
    if n_instants == 0:
        return PhaseStats()

    flagged = phase_scan[phase_scan["is_green_out"]]
    n_green = int(len(flagged))
    if n_green == 0:
        return PhaseStats(n_instants=n_instants, green_out_rate=0.0, plausible_rate=0.0)

    margins = flagged["profit_after_commission"].astype(float)
    books = flagged["book"].astype(float)
    plausible = flagged[books >= PLAUSIBLE_BOOK_FLOOR]
    n_plausible = int(len(plausible))

    return PhaseStats(
        n_instants=n_instants,
        n_green_out_instants=n_green,
        green_out_rate=n_green / n_instants,
        median_profit_after_commission=float(margins.median()),
        max_profit_after_commission=float(margins.max()),
        min_book=float(books.min()),
        n_plausible=n_plausible,
        plausible_rate=n_plausible / n_instants,
    )


def _longest_green_out_run(scan: pd.DataFrame) -> int:
    """Longest consecutive run of green-out instants within a single match.

    A crude persistence/duration proxy: the scan is already ordered by
    ``(market_id, timestamp)``, so within each match this counts the longest
    unbroken streak of ``is_green_out`` rows. It is measured in *instants*, not
    wall-clock time, because the capture has no fixed cadence -- a long run still
    signals an opportunity that persisted across consecutive captures rather
    than a lone flicker.

    Args:
        scan: A ``GREEN_OUT_COLUMNS`` frame ordered by (market_id, timestamp).

    Returns:
        The longest consecutive green-out run length, in instants (0 if none).
    """
    longest = 0
    for _market_id, group in scan.groupby("market_id", sort=False):
        current = 0
        for is_green in group["is_green_out"]:
            if bool(is_green):
                current += 1
                longest = max(longest, current)
            else:
                current = 0
    return longest
