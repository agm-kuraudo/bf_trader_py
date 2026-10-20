"""Pre-match drift trading simulation on the Research_Dataset (SP-371, option C).

Thin, I/O-owning wrapper over the pure ``logic/drift_trading.py`` core. It loads
the SP-368 Research_Dataset (Parquet), sweeps a grid of entry/exit windows under
both framings (momentum + mean-reversion), delegates ALL the P&L maths to the
pure core, renders a sensitivity chart, and prints the expected return / hit
rate per configuration plus the overall viable/not-viable verdict the SP-371
Definition of Done asks for.

Following the SP-328 / SP-368 / SP-369 / SP-370 house pattern, this owns every
side effect (reading the Parquet, matplotlib rendering, reporting, exit code)
while all simulation maths and the verdict stay in the pure, property-testable
``logic`` layer.

Option C scope: this is the best-price-fill "quick" version. The size-aware fill
model SP-371 ultimately wants needs captured liquidity (SP-384) and coherent
snapshots (SP-396), and is descoped to a follow-up; the fill limitation is
surfaced on every result. The headline numbers feed the SP-371 writeup
(``docs/SP-371_drift_trading.md``).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from logic.drift_trading import (  # noqa: E402
    DEFAULT_COMMISSION,
    MEAN_REVERSION,
    MOMENTUM,
    DriftTradeResult,
    simulate_drift_trades,
    summarise_drift_trades,
)
from output.log import Output as Log  # noqa: E402

CHARTS_DIR = "charts"

# The default entry/exit grid (hours before kick-off). Each (entry, exit) pair
# has entry EARLIER (more hours before) than exit. These span "enter a few days
# out, close nearer kick-off" through "enter and close within the final hours",
# which is the sensitivity-to-timing sweep SP-371 asks for.
DEFAULT_WINDOWS: tuple[tuple[float, float], ...] = (
    (72.0, 1.0),
    (48.0, 1.0),
    (24.0, 1.0),
    (12.0, 1.0),
    (6.0, 1.0),
    (24.0, 6.0),
    (6.0, 0.5),
)

STATUS_OK = "OK"
STATUS_NO_DATASET = "NO_DATASET"
STATUS_EMPTY = "EMPTY"


@dataclass
class DriftScanResult:
    """The outcome of a :func:`scan` run: per-config results plus run state.

    Attributes:
        results: One :class:`DriftTradeResult` per (framing, window) configuration.
        best: The configuration with the highest mean P&L per unit (the headline).
        chart_paths: Chart files written to ``charts/``.
        status: One of :data:`STATUS_OK` / :data:`STATUS_NO_DATASET` /
            :data:`STATUS_EMPTY`.
        error: Operator-facing error, or ``None``.
    """

    results: list[DriftTradeResult] = field(default_factory=list)
    best: DriftTradeResult | None = None
    chart_paths: list[str] = field(default_factory=list)
    status: str = STATUS_OK
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when a dataset was obtained (empty is still a reportable run)."""
        return self.status in (STATUS_OK, STATUS_EMPTY)

    @property
    def overall_verdict(self) -> str:
        """VIABLE if ANY configuration was viable, else NOT_VIABLE."""
        return "VIABLE" if any(r.verdict == "VIABLE" for r in self.results) else "NOT_VIABLE"


def load_dataset(parquet_path: str) -> pd.DataFrame | None:
    """Load the Research_Dataset from a Parquet artifact (see explore_odds_behaviour)."""
    if not os.path.isfile(parquet_path):
        Log.log_error(f"Research_Dataset Parquet not found at {parquet_path}")
        return None
    try:
        return pd.read_parquet(parquet_path)
    except Exception as read_error:
        Log.log_error(f"Failed to read Research_Dataset Parquet at {parquet_path}: {read_error}")
        return None


def render_sensitivity_chart(results: list[DriftTradeResult], charts_dir: str) -> str:
    """Render mean P&L per unit across the entry/exit grid, per framing.

    Plots the expected return (mean per-unit P&L after commission) for each
    configuration, grouped by framing, with the break-even line at 0. This is the
    "sensitivity to entry/exit timing" view SP-371 asks for. Writes
    ``drift_trading_sensitivity.png``. Returns the path, or "" when nothing to
    plot.

    Args:
        results: The per-config :class:`DriftTradeResult` list.
        charts_dir: Output directory for the PNG.

    Returns:
        The written chart path, or "".
    """
    plottable = [r for r in results if r.mean_profit_per_unit is not None]
    if not plottable:
        return ""

    os.makedirs(charts_dir, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 6))
    for framing in (MOMENTUM, MEAN_REVERSION):
        rows = [r for r in plottable if r.framing == framing]
        if not rows:
            continue
        labels = [f"{int(r.entry_hours_before)}h->{r.exit_hours_before}h" for r in rows]
        means = [r.mean_profit_per_unit * 100 for r in rows]  # percent
        ax.plot(labels, means, marker="o", label=framing)
    ax.axhline(0.0, linestyle="--", color="red", alpha=0.7, label="break-even")
    ax.set_xlabel("Entry -> exit window (hours before kick-off)")
    ax.set_ylabel("Mean P&L per unit after commission (%)")
    ax.set_title("Pre-match drift trading: expected return by timing & framing")
    ax.legend()
    ax.grid(True)
    fig.autofmt_xdate(rotation=30)

    path = os.path.join(charts_dir, "drift_trading_sensitivity.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def scan(
    parquet_path: str,
    commission: float = DEFAULT_COMMISSION,
    windows: tuple[tuple[float, float], ...] = DEFAULT_WINDOWS,
    charts_dir: str = CHARTS_DIR,
) -> DriftScanResult:
    """Orchestrate an SP-371 run: load, sweep windows x framings, summarise, report.

    Loads the Research_Dataset, runs the pure simulation + summary for every
    (framing, window) configuration, picks the best by mean P&L, renders the
    sensitivity chart, and returns the per-config results. A missing dataset is
    :data:`STATUS_NO_DATASET`; a dataset with no simulable pre-match trades at all
    is :data:`STATUS_EMPTY`.

    Args:
        parquet_path: Path to the Research_Dataset Parquet artifact.
        commission: Betfair commission passed to the sim.
        windows: The (entry_hours_before, exit_hours_before) grid to sweep.
        charts_dir: Output directory for the chart.

    Returns:
        The :class:`DriftScanResult`.
    """
    dataset = load_dataset(parquet_path)
    if dataset is None:
        return DriftScanResult(status=STATUS_NO_DATASET, error=f"No Research_Dataset available at {parquet_path}.")

    results: list[DriftTradeResult] = []
    for framing in (MOMENTUM, MEAN_REVERSION):
        for entry_h, exit_h in windows:
            trades = simulate_drift_trades(dataset, framing, entry_h, exit_h, commission=commission)
            results.append(summarise_drift_trades(trades, framing, entry_h, exit_h, commission=commission))

    if all(r.n_trades == 0 for r in results):
        return DriftScanResult(results=results, status=STATUS_EMPTY)

    # Best = highest mean P&L per unit among configs that produced trades.
    with_trades = [r for r in results if r.n_trades > 0 and r.mean_profit_per_unit is not None]
    best = max(with_trades, key=lambda r: r.mean_profit_per_unit) if with_trades else None

    chart_paths = [p for p in (render_sensitivity_chart(results, charts_dir),) if p]
    for path in chart_paths:
        Log.log_info(f"Wrote chart {path}")

    return DriftScanResult(results=results, best=best, chart_paths=chart_paths, status=STATUS_OK)


def _summary_lines(result: DriftScanResult) -> list[str]:
    """Build the operator-facing headline summary for stdout + the writeup."""
    if result.status == STATUS_NO_DATASET:
        return [f"drift-trading[{result.status}]: {result.error or 'no dataset available'}"]
    if result.status == STATUS_EMPTY or not result.results:
        return [f"drift-trading[{result.status}]: no simulable pre-match trades."]

    def _pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:+.3f}%"

    lines = [
        f"drift-trading[{result.status}] overall_verdict={result.overall_verdict} "
        f"(commission={result.results[0].commission * 100:.1f}%)",
    ]
    for framing in (MOMENTUM, MEAN_REVERSION):
        lines.append(f"  {framing}:")
        for r in result.results:
            if r.framing != framing:
                continue
            window = f"{int(r.entry_hours_before)}h->{r.exit_hours_before}h"
            lines.append(
                f"    {window}: n={r.n_trades} mean={_pct(r.mean_profit_per_unit)} "
                f"median={_pct(r.median_profit_per_unit)} "
                f"hit_rate={'n/a' if r.hit_rate is None else f'{r.hit_rate * 100:.1f}%'} "
                f"[{r.verdict}]"
            )
    if result.best is not None:
        b = result.best
        lines.append(
            f"  best: {b.framing} {int(b.entry_hours_before)}h->{b.exit_hours_before}h "
            f"mean={_pct(b.mean_profit_per_unit)} (n={b.n_trades})"
        )
    # The fill caveat (same on every result) once.
    if result.results and result.results[0].notes:
        lines.append(f"  note: {result.results[0].notes[0]}")
    return lines


def main() -> int:
    """CLI entry: parse args, run the sweep, print the summary + verdict, exit."""
    parser = argparse.ArgumentParser(
        description="Simulate pre-match drift trading on the SP-368 Research_Dataset (SP-371).",
    )
    parser.add_argument(
        "--parquet",
        dest="parquet_path",
        default="data/research_dataset.parquet",
        metavar="PATH",
        help="Path to the Research_Dataset Parquet artifact (default: data/research_dataset.parquet).",
    )
    parser.add_argument(
        "--commission",
        dest="commission",
        type=float,
        default=DEFAULT_COMMISSION,
        metavar="RATE",
        help=f"Betfair commission on net winnings, 0-1 (default: {DEFAULT_COMMISSION}).",
    )
    parser.add_argument(
        "--charts-dir",
        dest="charts_dir",
        default=CHARTS_DIR,
        metavar="DIR",
        help=f"Directory to write charts to (default: {CHARTS_DIR}).",
    )
    args = parser.parse_args()

    result = scan(
        parquet_path=args.parquet_path,
        commission=args.commission,
        charts_dir=args.charts_dir,
    )

    for line in _summary_lines(result):
        print(line)

    if not result.ok:
        if result.error:
            print(result.error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
