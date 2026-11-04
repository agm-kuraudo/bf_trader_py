"""Cheap strategy checks on cached Betfair historic data (SP-401 / SP-491).

Run ``scripts/historic_extract.py`` first to build the cache. Every check is simulated on LAST TRADED PRICES, so all
results are upper bounds (no spread, queue or bet delay); commission is charged per market on net winnings.

Subcommands::

    python scripts/historic_checks.py flat         # flat-stake back/lay at close and T-24h; lay-early/back-late
    python scripts/historic_checks.py longshot     # lay long shots at fixed liability
    python scripts/historic_checks.py calibration  # is the as-of last traded price still calibrated in play?
    python scripts/historic_checks.py ltd          # profit target / stop loss / time exit grid (default: lay the draw)
    python scripts/historic_checks.py goal         # trade after a sudden price jump, with slippage and entry delay

The ``ltd`` grid runs two guards first: trade selection must not depend on the in-play series (look-ahead), and the
runner's last traded price must still be calibrated in play. Without calibration the grid is not printed.
"""

import argparse
import os
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.historic_data import (  # noqa: E402
    DEFAULT_STAKE,
    DRAW,
    OVER_25,
    LookAheadError,
    asof_calibration,
    assert_selection_ignores_future,
    build_trade,
    calibration_warnings,
    evaluate_grid,
    flat_stake_row,
    jump_profits,
    lay_early_back_late,
    lengthens,
    longshot_lay,
    mean_ci,
    post_jump_events,
    settle_hold,
    shortens,
)

TARGETS = (1, 2, 3, 5, 10, 20)
STOPS = (5, 10, 20, 30, 50, None)
# Wall-clock minutes since in-play start (including about 15 minutes of half-time): about 60/75/85 minutes of play.
TIME_EXITS = (None, 75, 90, 100)
DEFAULT_RUNNER = {"MO": DRAW, "OU25": OVER_25}


def load_cache(path: Path) -> list[dict]:
    """Load a cache written by ``historic_extract.py`` (a pickle: only open files you generated)."""
    with open(path, "rb") as handle:
        return pickle.load(handle)


# --------------------------------------------------------------------------------------------- flat / longshot
def cmd_flat(markets: list[dict], args: argparse.Namespace) -> int:
    mo = [m for m in markets if m["kind"] == "MO"]
    print(f"Match Odds markets in cache: {len(mo)}; flat stake GBP {args.stake:.0f}; commission from the files")
    for source, label in (("close", "at the close"), ("24", "at T-24h")):
        print(f"\n=== Hold-to-result flat-stake bets, price {label}")
        print(f"{'strategy':20s} {'n':>6s} {'gross':>10s} {'net':>10s} {'ROI on risk':>12s} {'net/bet (95%)':>16s}")
        for side in ("BACK", "LAY"):
            for which in ("favourite", "draw", "outsider", "home", "away"):
                row = flat_stake_row(mo, source, side, which, args.stake)
                print(
                    f"{side + ' ' + which:20s} {row['n']:6d} {row['gross']:10,.0f} {row['net']:10,.0f} "
                    f"{row['roi_on_risk']:12.1%} {row['net_per_bet']:+8.2f} +-{row['ci']:5.2f}"
                )
    print("\n=== Lay the favourite early, back it at the close (locks the result)")
    print(
        f"{'early':>6s} {'n':>6s} {'median move':>12s} {'%drifted':>9s} {'gross/trade (95%)':>20s} {'net/trade':>10s}"
    )
    for horizon in (72, 24, 6, 1):
        r = lay_early_back_late(mo, horizon, args.stake)
        print(
            f"{'T-' + str(horizon) + 'h':>6s} {r['n']:6d} {r['median_move']:+12.2%} {r['share_drifted']:9.1%} "
            f"{r['mean_gross']:+10.3f} +-{r['ci']:6.3f} {r['mean_net']:+10.3f}"
        )
    return 0


def cmd_longshot(markets: list[dict], args: argparse.Namespace) -> int:
    mo = [m for m in markets if m["kind"] == "MO"]
    print(f"Lay long shots at the close, fixed liability GBP {args.stake:.0f} (Match Odds, {len(mo)} markets)")
    print(f"{'min price':>9s} {'n':>7s} {'win%':>6s} {'implied%':>9s} {'net/bet (95%)':>16s} {'ROI on liability':>17s}")
    for lo in (6, 8, 10, 15, 20, 30):
        r = longshot_lay(mo, lo, args.stake)
        print(
            f"{'>= ' + str(lo):>9s} {r['n']:7d} {r['win_rate']:6.1%} {r['implied']:9.1%} "
            f"{r['net_per_bet']:+8.3f} +-{r['ci']:5.3f} {r['roi_on_liability']:17.2%}"
        )
    return 0


# --------------------------------------------------------------------------------------------- calibration
def print_calibration(markets: list[dict], kind: str, runner: str) -> list[str]:
    rows = asof_calibration(markets, kind, runner)
    print(f"\n=== As-of calibration: {kind} / {runner} (all in-play markets, nothing filtered on the future)")
    print("  wall-min       n  implied  realised    gap (realised - implied)  no sample yet  stale >10min")
    for r in rows:
        print(
            f"  {r['minute']:8g} {r['n']:7d}  {r['implied']:7.3f}  {r['realised']:8.3f}  "
            f"{r['gap']:+8.3f} +-{r['ci']:5.3f}            {r['no_sample_share']:7.1%}      {r['stale_share']:7.1%}"
        )
    return calibration_warnings(rows)


def cmd_calibration(markets: list[dict], args: argparse.Namespace) -> int:
    status = 0
    for kind, runner in (("MO", DRAW), ("OU25", OVER_25)):
        warnings = print_calibration(markets, kind, runner)
        for message in warnings:
            print(f"  WARNING {message}")
        status = status or (2 if warnings else 0)
    return status


# --------------------------------------------------------------------------------------------- ltd grid
def cmd_ltd(markets: list[dict], args: argparse.Namespace) -> int:
    kind, direction = args.market, args.direction
    runner = args.runner or DEFAULT_RUNNER[kind]
    subset = [m for m in markets if m["kind"] == kind]
    try:
        checked = assert_selection_ignores_future(subset, lambda m: build_trade(m, runner))
    except LookAheadError as error:
        print(f"LOOK-AHEAD GUARD FAILED: {error}")
        return 3
    print(f"look-ahead guard passed on {checked} markets (selection does not use the in-play series)")

    warnings = print_calibration(markets, kind, runner)
    trades = [t for t in (build_trade(m, runner) for m in subset) if t is not None]
    if args.max_entry is not None:
        trades = [t for t in trades if t.p0 <= args.max_entry]
    label = f"{direction} {runner} ({kind})" + (f", entry price <= {args.max_entry}" if args.max_entry else "")
    hold = [settle_hold(t, direction, args.stake) for t in trades]
    mean, ci = mean_ci(hold)
    print(f"\n##### {label}: {len(trades)} trades, stake GBP {args.stake:.0f}")
    print(f"hold to the result: net per trade {mean:+.2f} +-{ci:.2f}")
    if warnings and not args.force:
        for message in warnings:
            print(f"  WARNING {message}")
        print(
            "Exit grid NOT printed: the last traded price is not calibrated for this runner (use --force to override)."
        )
        return 2

    keys, values = evaluate_grid(trades, direction, TARGETS, STOPS, TIME_EXITS, args.stake)
    column = {key: i for i, key in enumerate(keys)}
    for mi in TIME_EXITS:
        title = "none (hold to the result)" if mi is None else f"wall-clock minute {mi}"
        print(f"\n  time exit: {title}   net per trade, rows = profit target, columns = stop loss")
        print("  target\\stop " + " ".join(f"{('none' if s is None else str(s)):>7s}" for s in STOPS))
        for tg in TARGETS:
            cells = [values[column[(tg, st, mi)]].mean() for st in STOPS]
            print(f"  {tg:>10d} " + " ".join(f"{v:+7.2f}" for v in cells))

    months = sorted({t.month for t in trades})
    train_months = set(months[: max(1, len(months) // 2)])
    is_train = np.array([t.month in train_months for t in trades])
    if is_train.all() or not is_train.any():
        return 0
    train_means = values[:, is_train].mean(axis=1)
    best = int(np.argmax(train_means))
    test_mean, test_ci = mean_ci(values[best, ~is_train])
    tg, st, mi = keys[best]
    print(
        f"\n  walk-forward: best cell on {min(train_months)}..{max(train_months)} = target {tg}, stop {st}, time {mi} "
        f"(train mean {train_means[best]:+.2f}); on the later months (n={int((~is_train).sum())}): "
        f"{test_mean:+.2f} +-{test_ci:.2f}"
    )
    print(
        f"  cells with a positive training mean: {int((train_means > 0).sum())}/{len(keys)} (chance alone gives some)"
    )
    return 0


# --------------------------------------------------------------------------------------------- post-jump
def cmd_goal(markets: list[dict], args: argparse.Namespace) -> int:
    specs = (
        (
            "MO draw lengthens >= 20% in one sample: BACK the draw, lay later",
            "MO",
            DRAW,
            lengthens(1.20),
            "BACK_THEN_LAY",
        ),
        (
            "MO draw shortens >= 17% (control): LAY the draw, back later",
            "MO",
            DRAW,
            shortens(1 / 1.20),
            "LAY_THEN_BACK",
        ),
    )
    for label, kind, runner, detector, direction in specs:
        print(f"\n=== {label}")
        print("  delay  horizon      n | net per trade at slippage per leg:  0%         1%         2%         3%")
        for delay in (0, 60, 120, 300):
            for horizon in (5, 10):
                events = post_jump_events(markets, kind, runner, detector, delay, horizon)
                if len(events) < 100:
                    continue
                cells = []
                for slip in (0.0, 0.01, 0.02, 0.03):
                    m, c = mean_ci(jump_profits(events, slip, direction, args.stake))
                    cells.append(f"{m:+6.2f}+-{c:4.2f}")
                print(
                    f"  {delay:4d}s  +{horizon:2d}min {len(events):6d} |                          " + "   ".join(cells)
                )
    print("\n(Both directions: the draw price tends to fall after a big move. Treat it as a pattern in last traded")
    print(" prices until real bid/ask history confirms it is tradable.)")
    return 0


COMMANDS = {
    "flat": cmd_flat,
    "longshot": cmd_longshot,
    "calibration": cmd_calibration,
    "ltd": cmd_ltd,
    "goal": cmd_goal,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--cache", type=Path, default=Path("data/historic/cache_basic.pkl"))
    parser.add_argument(
        "--stake", type=float, default=DEFAULT_STAKE, help="flat stake (lay stake, or liability for longshot)"
    )
    parser.add_argument("--market", choices=["MO", "OU25"], default="MO", help="ltd: market kind")
    parser.add_argument("--runner", default=None, help="ltd: runner name (default: The Draw / Over 2.5 Goals)")
    parser.add_argument("--direction", choices=["LAY", "BACK"], default="LAY", help="ltd: open with a lay or a back")
    parser.add_argument(
        "--max-entry", type=float, default=None, help="ltd: only enter when the pre-in-play price <= this"
    )
    parser.add_argument("--force", action="store_true", help="ltd: print the grid even if calibration fails")
    args = parser.parse_args(argv)

    markets = load_cache(args.cache)
    print(f"loaded {len(markets)} markets from {args.cache}")
    return COMMANDS[args.command](markets, args)


if __name__ == "__main__":
    sys.exit(main())
