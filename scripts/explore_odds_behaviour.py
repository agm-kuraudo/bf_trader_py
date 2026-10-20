"""Explore odds & volume behaviour in the Research_Dataset (SP-369).

This is the thin, I/O-owning wrapper over the pure ``logic/odds_behaviour.py``
analysis core. It loads the SP-368 Research_Dataset (from a Parquet artifact, or
by building one on the fly from the captured ``bf.market_table`` via
``scripts/build_research_dataset.py``), delegates ALL characterisation maths to
the pure core (``profile_behaviour``), renders the summary charts to
``charts/``, and prints a run summary plus the headline numbers for the
Confluence writeup.

Following the SP-328 / SP-368 house pattern, this module owns every side effect
(reading the Parquet / DB, matplotlib rendering, writing PNGs, operator-facing
reporting and the exit code) while all shaping and statistics stay in the pure,
property-testable ``logic`` layer.

Deliverable (SP-369): the charts this emits plus the printed headline numbers
are the raw material for the short Confluence note ("how do the odds/volume
behave, and is anything here a candidate pattern?"). This script does NOT decide
strategy viability -- green-out is SP-370, drift P&L is SP-371.

Charts emitted (one set per analysed match, plus dataset-level summaries):
  * ``<market>_book_over_time.png`` -- Home/Draw/Away implied probabilities and
    their sum (the overround "book") over capture time. Sets up SP-370.
  * ``<market>_drift_pre_match.png`` -- each selection's best-back price over the
    pre-match window, so drift toward kick-off is visible.
  * ``drift_distribution.png`` -- dataset-wide histogram of relative pre-match
    drift, favourite vs field.
  * ``volume_over_time.png`` -- matched-volume increments with spikes flagged
    (where in-play data exists).

NOTE (sketch): this is the SP-369 scaffolding. The CLI, the load/build decision,
and the orchestration contract are laid out here; the chart-rendering bodies and
the headline-print formatting are filled in alongside the pure core as the
exploration proceeds. Rendering helpers that are not yet implemented raise
``NotImplementedError`` so an accidental early call is loud.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import matplotlib

# Headless backend: this runs on the Pi with no display, writing PNGs only.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)
import pandas as pd  # noqa: E402

from logic.odds_behaviour import BehaviourProfile, profile_behaviour  # noqa: E402
from output.log import Output as Log  # noqa: E402

# Where rendered charts are written (the existing MarketAnalyser convention).
CHARTS_DIR = "charts"

# Default matched-volume-increment threshold for flagging a "spike". Matched
# volume units are Betfair's (GBP matched). The default is a starting point for
# the exploration and is overridable on the CLI; the writeup should record the
# value actually used.
DEFAULT_VOLUME_SPIKE_THRESHOLD = 1000.0

# ``ExploreResult.status`` values. OK when the dataset loaded/built and the
# profile + charts were produced; the non-OK statuses drive the non-zero exit
# code so a scheduler/deploy step can gate on them.
STATUS_OK = "OK"
STATUS_NO_DATASET = "NO_DATASET"
STATUS_EMPTY = "EMPTY"


@dataclass
class ExploreResult:
    """The outcome of an :func:`explore` run: the profile plus run-summary state.

    Carries the pure-core :class:`BehaviourProfile` together with the chart
    paths written and the status the operator summary and process exit code are
    derived from. ``status`` is :data:`STATUS_OK` on a clean run;
    :data:`STATUS_NO_DATASET` when no Parquet/DB dataset could be obtained; and
    :data:`STATUS_EMPTY` when the dataset loaded but held no analysable rows (a
    valid, reportable outcome, not an error of this script).

    Attributes:
        profile: The characterisation bundle from the pure core, or ``None``
            when no dataset was available.
        chart_paths: The chart files written to ``charts/``.
        status: One of :data:`STATUS_OK` / :data:`STATUS_NO_DATASET` /
            :data:`STATUS_EMPTY`.
        error: An operator-facing error message, or ``None``.
    """

    profile: BehaviourProfile | None = None
    chart_paths: list[str] = field(default_factory=list)
    status: str = STATUS_OK
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when a dataset was obtained (empty is still a reportable run)."""
        return self.status in (STATUS_OK, STATUS_EMPTY)


def load_dataset(parquet_path: str) -> pd.DataFrame | None:
    """Load the Research_Dataset from a Parquet artifact.

    Reads the SP-368 artifact (``docs/research_dataset_schema.md``) with
    ``pd.read_parquet``. Returns ``None`` (rather than raising) when the file is
    absent or cannot be read, so the orchestration can fall back to building one
    from the DB or report "no dataset" cleanly.

    Args:
        parquet_path: Path to a persisted Research_Dataset Parquet file.

    Returns:
        The loaded DataFrame, or ``None`` when it could not be read.
    """
    if not os.path.isfile(parquet_path):
        Log.log_error(f"Research_Dataset Parquet not found at {parquet_path}")
        return None
    try:
        return pd.read_parquet(parquet_path)
    except Exception as read_error:
        Log.log_error(f"Failed to read Research_Dataset Parquet at {parquet_path}: {read_error}")
        return None


def _chart_name(market_id: str, suffix: str) -> str:
    """Build a filesystem-safe chart filename for a market (MarketAnalyser style)."""
    # Mirror MarketAnalyser: dots in market ids ("1.240323372") become hyphens so
    # the filename is clean.
    safe_market = str(market_id).replace(".", "-")
    return f"{safe_market}_{suffix}.png"


def _plot_values(series: pd.Series):
    """Convert a Series to a plain NumPy array matplotlib renders reliably.

    The SP-368 dataset uses pandas *nullable* extension dtypes -- ``Float64`` for
    prices/volume and ``datetime64[ns, UTC]`` for timestamps. matplotlib 3.8
    draws a nullable ``Float64`` Series as a BLANK line (no exception, no points)
    when it arrives via a groupby slice of a Parquet-reloaded frame, which is why
    the first Pi run produced empty per-match charts despite valid data. Casting
    to a plain NumPy array before plotting side-steps that: numeric columns
    become ``float64`` (pandas NA -> ``NaN``, which matplotlib skips as a gap),
    and datetime columns are handed over as datetime64 values matplotlib dates
    understand.

    Args:
        series: The column to plot (nullable or otherwise).

    Returns:
        A NumPy array suitable for ``ax.plot`` / ``ax.scatter``.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        # Hand matplotlib datetime64 values (tz-aware ok); it plots these as dates.
        return series.to_numpy(dtype="datetime64[ns]")
    # Numeric/other: coerce to float64 so NA becomes NaN (a plotted gap) and the
    # nullable extension array is dropped.
    return pd.to_numeric(series, errors="coerce").to_numpy(dtype="float64")


def render_book_chart(market_id: str, book: pd.DataFrame, charts_dir: str) -> str:
    """Render the implied-probability-book overlay for one match.

    Plots Home/Draw/Away implied probabilities and their sum (the overround
    "book") over capture time for ``market_id`` and writes
    ``<market>_book_over_time.png`` to ``charts_dir``. The horizontal line at
    book = 1.0 marks the green-out boundary SP-370 scans for. Returns the written
    path. A match with no book rows renders nothing and returns an empty string.

    Args:
        market_id: The match whose book to plot.
        book: The ``BOOK_COLUMNS`` frame restricted to (or filtered for) this
            match.
        charts_dir: Output directory for the PNG.

    Returns:
        The written chart path, or "" when nothing was plotted.
    """
    market_book = book[book["market_id"] == market_id].sort_values("timestamp")
    if market_book.empty:
        return ""

    fig, ax = plt.subplots(figsize=(10, 6))
    # Cast via _plot_values: a nullable Float64 Series renders blank in mpl 3.8.
    x = _plot_values(market_book["timestamp"])
    ax.plot(x, _plot_values(market_book["prob_home"]), label="Home P")
    ax.plot(x, _plot_values(market_book["prob_draw"]), label="Draw P")
    ax.plot(x, _plot_values(market_book["prob_away"]), label="Away P")
    ax.plot(x, _plot_values(market_book["book"]), label="Book (overround)", linewidth=2, color="black")
    # The green-out boundary SP-370 scans for: book < 1.0 means the three
    # outcomes imply < 100% in total.
    ax.axhline(1.0, linestyle="--", color="red", alpha=0.6, label="book = 1.0")
    ax.set_xlabel("Capture time")
    ax.set_ylabel("Implied probability")
    ax.set_title(f"Implied-probability book over time: {market_id}")
    ax.legend()
    ax.grid(True)
    fig.autofmt_xdate()

    path = os.path.join(charts_dir, _chart_name(market_id, "book_over_time"))
    fig.savefig(path)
    plt.close(fig)
    return path


def render_drift_chart(market_id: str, dataset: pd.DataFrame, charts_dir: str) -> str:
    """Render each selection's pre-match best-back price over time for one match.

    Generalises the existing per-runner ``MarketAnalyser.plot_data`` loop to the
    pre-match window and writes ``<market>_drift_pre_match.png`` so drift toward
    kick-off is visible per selection. Returns the written path, or "" when the
    match has no pre-match rows.

    Args:
        market_id: The match to plot.
        dataset: The full Research_Dataset (filtered to this match internally).
        charts_dir: Output directory for the PNG.

    Returns:
        The written chart path, or "".
    """
    pre = dataset[(dataset["market_id"] == market_id) & (dataset["match_phase"] == "PRE_MATCH")]
    if pre.empty:
        return ""

    fig, ax = plt.subplots(figsize=(10, 6))
    plotted = False
    # Generalises MarketAnalyser.plot_data: one best-back series per selection,
    # all on a single axes so the three outcomes are directly comparable.
    for runner_id, runner_group in pre.groupby("runner_id", sort=True):
        ordered = runner_group.sort_values("timestamp")
        series = ordered.dropna(subset=["best_back"])
        if series.empty:
            continue
        role = series["outcome_role"].iloc[0]
        label = f"{role} ({runner_id})" if pd.notna(role) else f"runner {runner_id}"
        # Cast via _plot_values: a nullable Float64 Series renders blank in mpl 3.8.
        ax.plot(_plot_values(series["timestamp"]), _plot_values(series["best_back"]), label=label)
        plotted = True

    if not plotted:
        plt.close(fig)
        return ""

    ax.set_xlabel("Capture time (pre-match)")
    ax.set_ylabel("Best back price")
    ax.set_title(f"Pre-match best-back drift: {market_id}")
    ax.legend()
    ax.grid(True)
    fig.autofmt_xdate()

    path = os.path.join(charts_dir, _chart_name(market_id, "drift_pre_match"))
    fig.savefig(path)
    plt.close(fig)
    return path


def render_drift_distribution(profile: BehaviourProfile, charts_dir: str) -> str:
    """Render the dataset-wide relative-drift distribution histogram.

    Plots the distribution of relative pre-match drift across all measured
    selections (favourite vs field) and writes ``drift_distribution.png``. This
    is the dataset-level "how much, how consistently" chart for the writeup.
    Returns the written path, or "" when there is nothing to plot.
    """
    drift = profile.drift
    if drift.empty:
        return ""

    rel = _plot_values(drift["rel_drift"])
    # Favourites = shortest first pre-match price within each match. The
    # histogram overlays favourite drift against the full field so "do
    # favourites behave differently?" is visible.
    favourite_idx = drift.groupby("market_id")["first_price"].idxmin()
    favourite_rel = _plot_values(drift.loc[favourite_idx, "rel_drift"])

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(rel, bins=30, alpha=0.5, label=f"All selections (n={len(rel)})")
    if favourite_rel.size:
        ax.hist(favourite_rel, bins=30, alpha=0.7, label=f"Favourites (n={favourite_rel.size})")
    ax.axvline(0.0, linestyle="--", color="black", alpha=0.6, label="no drift")
    ax.set_xlabel("Relative pre-match drift (last/first - 1)")
    ax.set_ylabel("Count")
    ax.set_title("Pre-match relative drift distribution")
    ax.legend()
    ax.grid(True)

    path = os.path.join(charts_dir, "drift_distribution.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def render_volume_chart(profile: BehaviourProfile, charts_dir: str) -> str:
    """Render matched-volume increments over time with spikes flagged.

    Plots the volume-delta series and marks flagged spikes, writing
    ``volume_over_time.png``. Where the dataset has in-play rows, the pre-match /
    in-play boundary is indicated so volume spikes can be related to kick-off.
    Returns the written path, or "".
    """
    volume = profile.volume
    series = volume.dropna(subset=["volume_delta"])
    if series.empty:
        # No usable matched-volume increments. On the current captured data this
        # is expected -- capture does not persist a volume figure (SP-384), so
        # matched_volume is null throughout and 0.7 analysis is price-only. The
        # explicit note is emitted by profile_behaviour; here we just skip.
        return ""

    fig, ax = plt.subplots(figsize=(10, 6))
    vx = _plot_values(series["timestamp"])
    ax.scatter(vx, _plot_values(series["volume_delta"]), s=8, alpha=0.4, label="volume delta")
    spikes = series[series["is_spike"]]
    if not spikes.empty:
        ax.scatter(
            _plot_values(spikes["timestamp"]),
            _plot_values(spikes["volume_delta"]),
            s=24,
            color="red",
            label="spike",
        )
    ax.set_xlabel("Capture time")
    ax.set_ylabel("Matched-volume increment")
    ax.set_title("Matched-volume increments over time (spikes flagged)")
    ax.legend()
    ax.grid(True)
    fig.autofmt_xdate()

    path = os.path.join(charts_dir, "volume_over_time.png")
    fig.savefig(path)
    plt.close(fig)
    return path


def render_charts(profile: BehaviourProfile, dataset: pd.DataFrame, charts_dir: str) -> list[str]:
    """Render the full SP-369 chart set and return the written paths.

    Ensures ``charts_dir`` exists, then renders the per-match book and drift
    charts plus the dataset-level drift-distribution and volume charts, skipping
    any that have nothing to plot. All matplotlib side effects are confined here
    (the pure core produced only frames).

    Args:
        profile: The pure-core characterisation bundle.
        dataset: The source Research_Dataset (for per-match price series).
        charts_dir: Output directory for the PNGs.

    Returns:
        The list of chart paths actually written.
    """
    os.makedirs(charts_dir, exist_ok=True)

    paths: list[str] = []

    # Per-match charts: book overlay + pre-match drift. Iterate the distinct
    # markets present in the dataset so every captured match gets its set.
    for market_id in sorted(dataset["market_id"].dropna().unique()):
        for path in (
            render_book_chart(market_id, profile.book, charts_dir),
            render_drift_chart(market_id, dataset, charts_dir),
        ):
            if path:
                paths.append(path)

    # Dataset-level summary charts.
    for path in (
        render_drift_distribution(profile, charts_dir),
        render_volume_chart(profile, charts_dir),
    ):
        if path:
            paths.append(path)

    return paths


def explore(parquet_path: str, volume_spike_threshold: float, charts_dir: str = CHARTS_DIR) -> ExploreResult:
    """Orchestrate an SP-369 run: load dataset, profile, render, report.

    Loads the Research_Dataset from ``parquet_path``, delegates all
    characterisation to the pure ``profile_behaviour`` core, renders the chart
    set, and returns a result carrying the profile and written chart paths plus
    the status the exit code derives from. A missing/unreadable dataset is
    reported as :data:`STATUS_NO_DATASET` with no charts; a dataset that loaded
    but held no analysable rows is :data:`STATUS_EMPTY` (reported, not an error).
    Performs no characterisation maths itself -- that all lives in the pure core.

    Args:
        parquet_path: Path to the Research_Dataset Parquet artifact.
        volume_spike_threshold: Threshold passed to the volume-spike derivation.
        charts_dir: Output directory for rendered charts.

    Returns:
        The :class:`ExploreResult`.
    """
    dataset = load_dataset(parquet_path)
    if dataset is None:
        error = f"No Research_Dataset available at {parquet_path}."
        return ExploreResult(profile=None, status=STATUS_NO_DATASET, error=error)

    # All characterisation maths lives in the pure core; this wrapper only does
    # I/O and rendering.
    profile = profile_behaviour(dataset, volume_spike_threshold)

    # An empty dataset (or one with no analysable rows) is a valid, reportable
    # finding, not an error of this script.
    if dataset.empty:
        return ExploreResult(profile=profile, chart_paths=[], status=STATUS_EMPTY)

    chart_paths = render_charts(profile, dataset, charts_dir)
    for path in chart_paths:
        Log.log_info(f"Wrote chart {path}")

    return ExploreResult(profile=profile, chart_paths=chart_paths, status=STATUS_OK)


def _summary_lines(result: ExploreResult) -> list[str]:
    """Build the operator-facing headline summary for stdout + the writeup.

    Renders the dataset-shape counts (matches, pre-match vs in-play rows) and the
    headline drift statistics (mean/median relative drift, shorten fraction,
    favourite drift) into a few readable lines -- the numbers that go straight
    into the Confluence note. Reports a clean "no analysable data" line for an
    empty run.

    Args:
        result: The :func:`explore` result.

    Returns:
        The summary lines to print.
    """
    if result.status == STATUS_NO_DATASET:
        return [f"odds-behaviour[{result.status}]: {result.error or 'no dataset available'}"]

    profile = result.profile
    if profile is None or result.status == STATUS_EMPTY:
        return [f"odds-behaviour[{result.status}]: dataset held no analysable rows."]

    def _fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:+.4f}"

    def _pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.1f}%"

    summary = profile.drift_summary
    lines = [
        f"odds-behaviour[{result.status}]: "
        f"matches={profile.n_matches} pre_match_rows={profile.n_pre_match_rows} "
        f"in_play_rows={profile.n_in_play_rows} charts={len(result.chart_paths)}",
        f"  drift: n_selections={summary.n_selections} "
        f"mean_rel={_fmt(summary.mean_rel_drift)} median_rel={_fmt(summary.median_rel_drift)} "
        f"shorten_fraction={_pct(summary.shorten_fraction)} "
        f"favourite_mean_rel={_fmt(summary.favourite_mean_rel_drift)}",
    ]
    for note in profile.notes:
        lines.append(f"  note: {note}")
    return lines


def main() -> int:
    """CLI entry: parse args, run the exploration, print the summary, exit.

    Parses ``--parquet`` (the Research_Dataset artifact path), ``--charts-dir``,
    and ``--volume-spike-threshold``, runs :func:`explore`, prints the headline
    summary, and returns a non-zero exit code only when no dataset could be
    obtained (an empty-but-loaded dataset exits zero -- it is a valid finding).

    Returns:
        The process exit code: ``0`` on a completed run (including empty),
        non-zero when no dataset was available.
    """
    parser = argparse.ArgumentParser(
        description="Explore odds & volume behaviour in the SP-368 Research_Dataset (SP-369).",
    )
    parser.add_argument(
        "--parquet",
        dest="parquet_path",
        default="research_dataset.parquet",
        metavar="PATH",
        help="Path to the Research_Dataset Parquet artifact (default: research_dataset.parquet).",
    )
    parser.add_argument(
        "--charts-dir",
        dest="charts_dir",
        default=CHARTS_DIR,
        metavar="DIR",
        help=f"Directory to write charts to (default: {CHARTS_DIR}).",
    )
    parser.add_argument(
        "--volume-spike-threshold",
        dest="volume_spike_threshold",
        type=float,
        default=DEFAULT_VOLUME_SPIKE_THRESHOLD,
        metavar="UNITS",
        help=(
            "Matched-volume increment above which an interval counts as a spike "
            f"(default: {DEFAULT_VOLUME_SPIKE_THRESHOLD})."
        ),
    )
    args = parser.parse_args()

    result = explore(
        parquet_path=args.parquet_path,
        volume_spike_threshold=args.volume_spike_threshold,
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
