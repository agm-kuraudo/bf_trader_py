"""Scan the Research_Dataset for green-out / cross-outcome arbitrage (SP-370).

Thin, I/O-owning wrapper over the pure ``logic/green_out.py`` core. It loads the
SP-368 Research_Dataset (Parquet), delegates ALL the green-out maths to the pure
core (``green_out_scan`` + ``summarise_green_out``), renders a book-distribution
chart to ``charts/``, and prints the quantified result plus the viable/not-viable
verdict the SP-370 Definition of Done asks for.

Following the SP-328 / SP-368 / SP-369 house pattern, this module owns every
side effect (reading the Parquet, matplotlib rendering, operator reporting, the
exit code) while all arbitrage maths and the verdict stay in the pure, property-
testable ``logic`` layer.

The headline numbers this prints are the raw material for the SP-370 writeup
(``docs/SP-370_green_out_scan.md``). The scan is price-only and does not assess
liquidity (the dataset has no size/volume -- SP-384); the core attaches that
caveat to every result and this script surfaces it.

NOTE (sketch): the chart-render body and summary formatting are implemented
below; the module mirrors ``scripts/explore_odds_behaviour.py``.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib

# Headless backend: runs on the Pi (or locally) with no display, writing PNGs.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)
import pandas as pd  # noqa: E402

from logic.green_out import (  # noqa: E402
    DEFAULT_COMMISSION,
    PLAUSIBLE_BOOK_FLOOR,
    GreenOutSummary,
    green_out_scan,
    summarise_green_out,
)
from output.log import Output as Log  # noqa: E402

CHARTS_DIR = "charts"

STATUS_OK = "OK"
STATUS_NO_DATASET = "NO_DATASET"
STATUS_EMPTY = "EMPTY"


@dataclass
class ScanResult:
    """The outcome of a :func:`scan` run: the summary plus run-summary state.

    Attributes:
        summary: The :class:`GreenOutSummary` from the pure core, or ``None``
            when no dataset was available.
        scan: The per-instant ``GREEN_OUT_COLUMNS`` frame (retained for the
            chart / further inspection), or ``None``.
        chart_paths: Chart files written to ``charts/``.
        status: One of :data:`STATUS_OK` / :data:`STATUS_NO_DATASET` /
            :data:`STATUS_EMPTY`.
        error: Operator-facing error, or ``None``.
    """

    summary: GreenOutSummary | None = None
    scan: pd.DataFrame | None = None
    chart_paths: list[str] = field(default_factory=list)
    status: str = STATUS_OK
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when a dataset was obtained (empty is still a reportable run)."""
        return self.status in (STATUS_OK, STATUS_EMPTY)


def load_dataset(parquet_path: str) -> pd.DataFrame | None:
    """Load the Research_Dataset from a Parquet artifact (see explore_odds_behaviour).

    Returns ``None`` (rather than raising) when the file is absent or unreadable,
    so the orchestration can report "no dataset" cleanly.
    """
    if not os.path.isfile(parquet_path):
        Log.log_error(f"Research_Dataset Parquet not found at {parquet_path}")
        return None
    try:
        return pd.read_parquet(parquet_path)
    except Exception as read_error:
        Log.log_error(f"Failed to read Research_Dataset Parquet at {parquet_path}: {read_error}")
        return None


def render_book_distribution(scan: pd.DataFrame, charts_dir: str) -> str:
    """Render the distribution of the back-all-three book, pre-match vs in-play.

    Plots a histogram of the reconstructed book with the green-out boundary
    (book = 1.0) marked, split by ``match_phase`` so the reader can see where the
    sub-1.0 mass sits (the SP-369 finding was that in-play produces extreme book
    excursions that are largely stale-leg artifacts). Writes
    ``green_out_book_distribution.png``. Returns the path, or "" when nothing to
    plot.

    Args:
        scan: A ``GREEN_OUT_COLUMNS`` frame from the pure core.
        charts_dir: Output directory for the PNG.

    Returns:
        The written chart path, or "".
    """
    if scan.empty:
        return ""

    os.makedirs(charts_dir, exist_ok=True)
    books = scan["book"].astype(float)

    fig, ax = plt.subplots(figsize=(10, 6))
    pre = books[scan["match_phase"] == "PRE_MATCH"]
    inplay = books[scan["match_phase"] == "IN_PLAY"]
    # Clip the x-range to a readable window; in-play can reach ~0.3 / ~1.8, which
    # would otherwise flatten the pre-match detail. The annotation records that.
    lo, hi = 0.9, 1.15
    if not pre.empty:
        ax.hist(pre.clip(lo, hi), bins=60, alpha=0.6, label=f"Pre-match (n={pre.size})")
    if not inplay.empty:
        ax.hist(inplay.clip(lo, hi), bins=60, alpha=0.5, label=f"In-play (n={inplay.size})")
    ax.axvline(1.0, linestyle="--", color="red", alpha=0.8, label="book = 1.0 (green-out boundary)")
    ax.set_xlabel(f"Back-all-three book (clipped to [{lo}, {hi}])")
    ax.set_ylabel("Count of instants")
    ax.set_title("Distribution of the back-all-three book")
    ax.legend()
    ax.grid(True)

    path = os.path.join(charts_dir, "green_out_book_distribution.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def scan(parquet_path: str, commission: float = DEFAULT_COMMISSION, charts_dir: str = CHARTS_DIR) -> ScanResult:
    """Orchestrate an SP-370 run: load dataset, scan, summarise, render, report.

    Loads the Research_Dataset, delegates the green-out maths and the verdict to
    the pure core, renders the book-distribution chart, and returns a result
    carrying the summary and chart paths. A missing/unreadable dataset is
    :data:`STATUS_NO_DATASET` (no charts); a dataset that loaded but produced no
    complete book instants is :data:`STATUS_EMPTY` (reported, not an error).

    Args:
        parquet_path: Path to the Research_Dataset Parquet artifact.
        commission: Betfair commission passed to the scan.
        charts_dir: Output directory for the chart.

    Returns:
        The :class:`ScanResult`.
    """
    dataset = load_dataset(parquet_path)
    if dataset is None:
        return ScanResult(status=STATUS_NO_DATASET, error=f"No Research_Dataset available at {parquet_path}.")

    scan_frame = green_out_scan(dataset, commission=commission)
    summary = summarise_green_out(scan_frame, commission=commission)

    if scan_frame.empty:
        return ScanResult(summary=summary, scan=scan_frame, chart_paths=[], status=STATUS_EMPTY)

    chart_paths = [p for p in (render_book_distribution(scan_frame, charts_dir),) if p]
    for path in chart_paths:
        Log.log_info(f"Wrote chart {path}")

    return ScanResult(summary=summary, scan=scan_frame, chart_paths=chart_paths, status=STATUS_OK)


def _summary_lines(result: ScanResult) -> list[str]:
    """Build the operator-facing headline summary for stdout + the writeup.

    Renders the SP-370 numbers (instants scanned, green-out count + rate, match
    reach, max/median margin after commission, longest run) and the
    viable/not-viable verdict into a few readable lines, plus the caveats.

    Args:
        result: The :func:`scan` result.

    Returns:
        The summary lines to print.
    """
    if result.status == STATUS_NO_DATASET:
        return [f"green-out[{result.status}]: {result.error or 'no dataset available'}"]

    summary = result.summary
    if summary is None or result.status == STATUS_EMPTY:
        return [f"green-out[{result.status}]: no complete book instants to scan."]

    def _pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.3f}%"

    def _margin(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.3f}%"

    lines = [
        f"green-out[{result.status}] verdict={summary.verdict} (commission={summary.commission * 100:.1f}%)",
        f"  all instants={summary.n_instants} green_outs={summary.n_green_out_instants} "
        f"rate={_pct(summary.green_out_rate)}",
        f"  matches_with_green_out={summary.n_matches_with_green_out}/{summary.n_matches_total} "
        f"longest_run={summary.max_run_instants} instants",
        f"  margin_after_commission (all): max={_margin(summary.max_profit_after_commission)} "
        f"median={_margin(summary.median_profit_after_commission)}",
    ]
    # Per-phase breakdown: the pre-match / in-play split is the honest view --
    # in-play green-outs are largely forward-fill artifacts.
    for phase in ("PRE_MATCH", "IN_PLAY"):
        ps = summary.phase_breakdown.get(phase)
        if ps is None:
            continue
        lines.append(
            f"  {phase}: instants={ps.n_instants} green_outs={ps.n_green_out_instants} "
            f"(plausible={ps.n_plausible}, book>={PLAUSIBLE_BOOK_FLOOR}) "
            f"rate={_pct(ps.green_out_rate)} plausible_rate={_pct(ps.plausible_rate)}"
        )
        lines.append(
            f"    margin: median={_margin(ps.median_profit_after_commission)} "
            f"max={_margin(ps.max_profit_after_commission)} min_book="
            f"{'n/a' if ps.min_book is None else f'{ps.min_book:.4f}'}"
        )
    for note in summary.notes:
        lines.append(f"  note: {note}")
    return lines


def main() -> int:
    """CLI entry: parse args, run the scan, print the summary + verdict, exit.

    Parses ``--parquet``, ``--commission``, and ``--charts-dir``, runs
    :func:`scan`, prints the headline summary + verdict, and returns a non-zero
    exit code only when no dataset could be obtained (an empty-but-loaded dataset
    exits zero -- "no green-outs" is a valid finding, not a failure).

    Returns:
        The process exit code: ``0`` on a completed run (including empty),
        non-zero when no dataset was available.
    """
    parser = argparse.ArgumentParser(
        description="Scan the SP-368 Research_Dataset for green-out / cross-outcome arbitrage (SP-370).",
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
