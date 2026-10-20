"""Pure-logic pre-match drift trading simulation (SP-371, option C).

This module is the pure, I/O-free analysis core for SP-371: does pre-match odds
*drift* (the movement SP-369 found -- favourites lengthening before kick-off)
constitute a tradeable edge when you trade it in its self-contained form --
enter a position in the pre-match window, close it out BEFORE kick-off, and bank
the price difference regardless of who wins? P&L comes purely from the odds
moving, so no match results are needed (drift *sense 1* in the SP-367 plan;
*sense 2*, drift-predicts-winner held to settlement, is 0.8 and needs results).

Like the other SP-367 analysis cores (``logic/odds_behaviour.py``,
``logic/green_out.py``) it does no database, filesystem, or network access, so
every derivation is property-testable deterministically. The thin wrapper that
loads the Parquet dataset, calls in here, renders charts, and reports the verdict
lives in ``scripts/scan_drift_trading.py``.

## The trade (back/lay round trip, flat before kick-off)

To be flat before kick-off you do BOTH legs pre-match: a back and a lay on the
same selection, at two different times/prices. If you back £1 at odds ``b`` and
fully hedge with a lay at odds ``l`` (lay stake ``b/l``), the position is
"greened" -- the same profit whoever wins -- and that locked profit per £1 backed
is ``b/l - 1`` before commission (positive exactly when you BACKED HIGHER than you
LAID, ``b > l``). Betfair commission is charged on net winnings, so it scales a
positive profit: ``profit = (b/l - 1) * (1 - commission)`` when ``b > l``, and
``b/l - 1`` (a loss, untaxed) otherwise.

Two framings of how drift drives that:

* **Momentum** -- bet the drift CONTINUES. For a selection drifting OUT
  (price rising), you want to end up having backed at the higher (later) price
  and laid at the lower (earlier) price: **lay at entry, back at exit**. Then
  ``b = back_price(exit)``, ``l = lay_price(entry)``; profit if the price rose.
* **Mean-reversion** -- bet the drift REVERSES: the mirror trade (**back at
  entry, lay at exit**), profitable when the price falls back after entry.

Both are simulated from the captured ``best_back`` / ``best_lay`` at the entry
and exit instants.

## Honest scope / data limitation (SP-371 option C)

SP-371's scope asks to use captured available-to-back/lay **sizes** so simulated
fills reflect real liquidity. The SP-368 dataset carries only best prices -- no
size, no matched-volume (SP-384). So this sim assumes you are **matched in full
at the best price** at both entry and exit. That is OPTIMISTIC: real fills would
walk the ladder and often get a worse average price or only a partial match, so
true P&L would be no better, and usually worse, than this sim reports. The
size-aware version is deliberately DESCOPED to a follow-up pending SP-384
(volume/size capture) and SP-396 (coherent snapshots). Every result here carries
that caveat, as SP-369/SP-370 did with their gaps. The sim also uses the
best_back at entry and best_lay at exit from the SAME captured rows -- there is no
forward-fill here (each leg is a real observed price for that selection), so the
SP-370 stale-leg issue does not apply to a single selection's own series.

NOTE: data models, the module contract, and function docstrings are defined
here; behavioural bodies are implemented below.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Reuse the single Betfair commission default defined for the green-out scan so
# the two strategy analyses agree on the rate; it is a parameter everywhere.
from logic.green_out import DEFAULT_COMMISSION

__all__ = [
    "DEFAULT_COMMISSION",
    "MOMENTUM",
    "MEAN_REVERSION",
    "TRADE_COLUMNS",
    "DriftTradeResult",
    "round_trip_profit",
    "simulate_drift_trades",
    "summarise_drift_trades",
]

# The two framings the simulation tests.
MOMENTUM: str = "MOMENTUM"  # bet the pre-match drift continues
MEAN_REVERSION: str = "MEAN_REVERSION"  # bet the pre-match drift reverses

# Columns of the per-trade frame produced by ``simulate_drift_trades``: one row
# per simulated (market, runner) round trip for a given entry/exit window and
# framing.
TRADE_COLUMNS: tuple[str, ...] = (
    "market_id",
    "runner_id",
    "outcome_role",
    "framing",
    "entry_timestamp",
    "exit_timestamp",
    "back_price",
    "lay_price",
    "profit_per_unit",
)


@dataclass(frozen=True)
class DriftTradeResult:
    """Season-level quantification + verdict for a drift-trading configuration.

    Produced by ``summarise_drift_trades`` for ONE framing + entry/exit window.
    Carries the expected return, hit rate, and spread the SP-371 Definition of
    Done asks for, plus the viable/not-viable ``verdict`` and the commission the
    P&L was computed at. Rates are ``None`` when there were no trades to measure.

    Attributes:
        framing: ``MOMENTUM`` or ``MEAN_REVERSION``.
        commission: Commission rate the P&L was computed at.
        entry_hours_before: Hours before ``start_time`` the entry leg was taken.
        exit_hours_before: Hours before ``start_time`` the exit leg was taken.
        n_trades: Number of simulated round trips.
        mean_profit_per_unit: Mean P&L per unit backed, after commission (the
            "expected return").
        median_profit_per_unit: Median P&L per unit backed, after commission.
        hit_rate: Fraction of trades with a positive P&L.
        std_profit_per_unit: Standard deviation of per-unit P&L (dispersion).
        verdict: ``"VIABLE"`` or ``"NOT_VIABLE"``.
        notes: Operator-facing caveats (e.g. the best-price-fill limitation).
    """

    framing: str = MOMENTUM
    commission: float = DEFAULT_COMMISSION
    entry_hours_before: float | None = None
    exit_hours_before: float | None = None
    n_trades: int = 0
    mean_profit_per_unit: float | None = None
    median_profit_per_unit: float | None = None
    hit_rate: float | None = None
    std_profit_per_unit: float | None = None
    verdict: str = "NOT_VIABLE"
    notes: list[str] = field(default_factory=list)


def round_trip_profit(back_price: float, lay_price: float, commission: float = DEFAULT_COMMISSION) -> float:
    """P&L per unit backed of a fully-hedged back+lay round trip.

    Backing £1 at ``back_price`` and laying the hedge at ``lay_price`` locks in
    the same result whoever wins: a profit of ``back_price / lay_price - 1`` per
    £1 backed before commission (positive exactly when you backed higher than you
    laid). Betfair commission is charged on net winnings only, so it scales a
    POSITIVE profit and leaves a loss untaxed:

        profit = (back_price/lay_price - 1) * (1 - commission)   if back > lay
        profit =  back_price/lay_price - 1                        otherwise

    Returns ``0.0`` for a non-positive or missing price (no tradeable leg) rather
    than raising, so a null best price propagates as a flat trade.

    Args:
        back_price: The price the back leg is matched at.
        lay_price: The price the lay leg is matched at.
        commission: Betfair commission on net winnings.

    Returns:
        The locked-in P&L per unit backed, after commission.
    """
    if back_price is None or lay_price is None:
        return 0.0
    if pd.isna(back_price) or pd.isna(lay_price):
        return 0.0
    b = float(back_price)
    lay = float(lay_price)
    if b <= 0.0 or lay <= 0.0:
        return 0.0
    gross = b / lay - 1.0
    # Commission only applies to a net win; a loss is untaxed.
    return gross * (1.0 - commission) if gross > 0.0 else gross


def _leg_prices_at(series: pd.DataFrame, target_ts: pd.Timestamp) -> tuple[float | None, float | None]:
    """Best-back/best-lay at the series row closest at-or-before ``target_ts``.

    Picks the most-recent captured observation at or before ``target_ts`` for one
    selection's own pre-match series (an as-of pick within a single runner's
    series -- NOT the cross-runner forward-fill of SP-370, so no stale-leg issue).
    Returns ``(best_back, best_lay)`` for that row, or ``(None, None)`` when the
    selection has no observation at or before the target time.

    Args:
        series: One selection's pre-match rows, ordered by ``timestamp``.
        target_ts: The wall-clock instant to price the leg at.

    Returns:
        A ``(best_back, best_lay)`` pair, or ``(None, None)``.
    """
    at_or_before = series[series["timestamp"] <= target_ts]
    if at_or_before.empty:
        return None, None
    row = at_or_before.iloc[-1]
    back = row["best_back"]
    lay = row["best_lay"]
    return (None if pd.isna(back) else float(back), None if pd.isna(lay) else float(lay))


def simulate_drift_trades(
    dataset: pd.DataFrame,
    framing: str,
    entry_hours_before: float,
    exit_hours_before: float,
    commission: float = DEFAULT_COMMISSION,
) -> pd.DataFrame:
    """Simulate one pre-match drift round trip per selection for a config.

    For each ``(market_id, runner_id)`` with a known ``start_time``, prices an
    entry leg at ``entry_hours_before`` hours before kick-off and an exit leg at
    ``exit_hours_before`` hours before kick-off (``entry`` must be the earlier,
    i.e. the larger hours-before), using the selection's own pre-match best-back
    / best-lay observations (as-of at-or-before each target time). The round trip
    is assembled per ``framing``:

    * ``MOMENTUM`` -- lay at entry, back at exit (profit if the price drifts OUT:
      back high, lay low). ``back_price = best_back(exit)``,
      ``lay_price = best_lay(entry)``.
    * ``MEAN_REVERSION`` -- back at entry, lay at exit (profit if the price falls
      back). ``back_price = best_back(entry)``, ``lay_price = best_lay(exit)``.

    P&L per unit backed is ``round_trip_profit`` after commission (best-price
    fills; see the module docstring's data-limitation note). Selections lacking a
    usable price at either leg are dropped. Restricted to pre-match rows only
    (odds-only, flat before kick-off); no match results are used.

    The result carries exactly ``TRADE_COLUMNS`` and is ordered by
    ``(market_id, runner_id)``.

    Args:
        dataset: The SP-368 Research_Dataset frame.
        framing: ``MOMENTUM`` or ``MEAN_REVERSION``.
        entry_hours_before: Hours before ``start_time`` to take the entry leg.
        exit_hours_before: Hours before ``start_time`` to take the exit leg
            (must be smaller than ``entry_hours_before`` -- closer to kick-off).
        commission: Betfair commission on net winnings.

    Returns:
        A ``TRADE_COLUMNS`` DataFrame, one row per simulated round trip.
    """
    if framing not in (MOMENTUM, MEAN_REVERSION):
        raise ValueError(f"framing must be {MOMENTUM!r} or {MEAN_REVERSION!r}, got {framing!r}")
    # Entry must precede exit in wall-clock time: more hours before kick-off.
    if not entry_hours_before > exit_hours_before:
        raise ValueError("entry_hours_before must be greater than exit_hours_before (entry is earlier)")

    pre = dataset[dataset["match_phase"] == "PRE_MATCH"]
    if pre.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in TRADE_COLUMNS})

    records: list[dict] = []
    for (market_id, runner_id), group in pre.groupby(["market_id", "runner_id"], sort=True, dropna=False):
        start_time = group["start_time"].iloc[0]
        if pd.isna(start_time):
            continue
        ordered = group.sort_values("timestamp")

        entry_ts = start_time - pd.Timedelta(hours=entry_hours_before)
        exit_ts = start_time - pd.Timedelta(hours=exit_hours_before)
        entry_back, entry_lay = _leg_prices_at(ordered, entry_ts)
        exit_back, exit_lay = _leg_prices_at(ordered, exit_ts)

        if framing == MOMENTUM:
            # Lay at entry, back at exit: profit if the price drifted OUT.
            back_price, lay_price = exit_back, entry_lay
        else:
            # Back at entry, lay at exit: profit if the price came back IN.
            back_price, lay_price = entry_back, exit_lay

        if back_price is None or lay_price is None:
            continue

        records.append(
            {
                "market_id": market_id,
                "runner_id": runner_id,
                "outcome_role": group["outcome_role"].iloc[0],
                "framing": framing,
                "entry_timestamp": entry_ts,
                "exit_timestamp": exit_ts,
                "back_price": back_price,
                "lay_price": lay_price,
                "profit_per_unit": round_trip_profit(back_price, lay_price, commission),
            }
        )

    frame = pd.DataFrame.from_records(records, columns=list(TRADE_COLUMNS))
    if frame.empty:
        return frame
    return frame.sort_values(["market_id", "runner_id"]).reset_index(drop=True)


def summarise_drift_trades(
    trades: pd.DataFrame,
    framing: str,
    entry_hours_before: float,
    exit_hours_before: float,
    commission: float = DEFAULT_COMMISSION,
    viable_min_mean: float = 0.0,
) -> DriftTradeResult:
    """Quantify a per-trade frame and produce the SP-371 viable/not-viable verdict.

    Aggregates the per-trade P&L into the expected return (mean), median, hit
    rate (fraction profitable), and dispersion the SP-371 Definition of Done
    asks for. An empty frame yields a zero-trade result with null stats and a
    ``NOT_VIABLE`` verdict rather than raising.

    The verdict is conservative: ``VIABLE`` only when the mean P&L per unit after
    commission strictly exceeds ``viable_min_mean``. Because the fills are
    best-price (optimistic -- real fills would be worse, see the module
    docstring), a mean that is merely positive but tiny is NOT a tradeable edge;
    the default ``viable_min_mean`` of ``0.0`` is the floor, and callers (and the
    writeup) should read a near-zero mean as "no edge after realistic costs".

    Args:
        trades: A ``TRADE_COLUMNS`` frame from :func:`simulate_drift_trades`.
        framing: The framing the trades were simulated under (recorded).
        entry_hours_before: Entry window (recorded).
        exit_hours_before: Exit window (recorded).
        commission: Commission the P&L was computed at (recorded).
        viable_min_mean: Mean per-unit P&L that must be strictly exceeded for a
            ``VIABLE`` verdict. Defaults to ``0.0``.

    Returns:
        The :class:`DriftTradeResult`.
    """
    fill_note = (
        "Best-price fills only: the dataset has no available-size data (SP-384), "
        "so trades assume a full match at the best price at both legs. This is "
        "OPTIMISTIC -- real fills would walk the ladder and do no better, usually "
        "worse. Size-aware fills are descoped to a follow-up (SP-384/SP-396)."
    )

    if trades.empty:
        return DriftTradeResult(
            framing=framing,
            commission=commission,
            entry_hours_before=entry_hours_before,
            exit_hours_before=exit_hours_before,
            verdict="NOT_VIABLE",
            notes=[fill_note, "No simulated trades for this configuration."],
        )

    pnl = trades["profit_per_unit"].astype(float)
    n_trades = int(len(pnl))
    mean_pnl = float(pnl.mean())
    median_pnl = float(pnl.median())
    hit_rate = float((pnl > 0.0).mean())
    std_pnl = float(pnl.std(ddof=0))

    viable = mean_pnl > viable_min_mean

    return DriftTradeResult(
        framing=framing,
        commission=commission,
        entry_hours_before=entry_hours_before,
        exit_hours_before=exit_hours_before,
        n_trades=n_trades,
        mean_profit_per_unit=mean_pnl,
        median_profit_per_unit=median_pnl,
        hit_rate=hit_rate,
        std_profit_per_unit=std_pnl,
        verdict="VIABLE" if viable else "NOT_VIABLE",
        notes=[fill_note],
    )
