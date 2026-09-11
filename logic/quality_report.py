"""Pure report formatters for post-event data-quality verification (SP-332).

This module is the presentation layer over the durable Quality_Report record
(``bf.quality_run`` + ``bf.quality_match_result``). It is pure: no ``os``, no
``psycopg2``, no file/network access, and no clock. Every formatter takes
already-fetched rows (plain data) and returns a string, so identical inputs
produce identical output and the formatting is unit- and property-testable per
the R9.1 pure-logic convention (mirrors the design's "Pure logic in ``logic/``,
I/O in ``scripts/``").

These functions contain **no** quality decisions and perform **no** evaluation:
they only present outcomes the daily check (``scripts/check_quality.py``) already
recorded. ``scripts/report_quality.py`` fetches the rows and hands them straight
to the matching formatter for ``--format text|markdown|csv``.

Row shapes (accepted as **mappings** -- a ``dict`` per row, e.g. from a
psycopg2 ``DictCursor``; this is the single documented, consistently handled
form):

``run_row`` -- one ``bf.quality_run`` row with keys:
    ``run_id``, ``run_started``, ``run_finished``, ``look_back_start``,
    ``look_back_end``, ``matches_verified``, ``matches_passed``,
    ``matches_failed``, ``overall_alert``, ``status``, ``notes``.

``match_rows`` -- a list of ``bf.quality_match_result`` rows, each with keys:
    ``run_id``, ``target_id``, ``market_id``, ``present_outcome``,
    ``coverage_outcome``, ``consistency_outcome``, ``useful_outcome``,
    ``overall_outcome``, ``evidence``.

Only the stdlib (``csv``, ``io``) is used; no new dependencies.
"""

import csv
import io
from collections.abc import Mapping, Sequence

# The per-match dimension columns in their documented order. Both the text and
# markdown tables and the CSV data rows follow this order so every surface reads
# the same way.
_MATCH_HEADERS = (
    "target_id",
    "market_id",
    "present",
    "coverage",
    "consistency",
    "useful",
    "overall",
)

# The keys read from each match row for the columns above (in the same order).
_MATCH_KEYS = (
    "target_id",
    "market_id",
    "present_outcome",
    "coverage_outcome",
    "consistency_outcome",
    "useful_outcome",
    "overall_outcome",
)


def _value(row: Mapping, key: str) -> str:
    """Return ``row[key]`` as a display string, empty for missing/``None``."""
    value = row.get(key)
    return "" if value is None else str(value)


def _match_cells(row: Mapping) -> list[str]:
    """Extract the documented per-match column values from one match row."""
    return [_value(row, key) for key in _MATCH_KEYS]


def format_report_text(run_row: Mapping, match_rows: Sequence[Mapping]) -> str:
    """Render a readable plain-text Quality_Report for stdout.

    Produces a run header (run_id, look-back window, verified/passed/failed
    counts, status, alert flag) followed by an aligned per-match table with one
    row per match. Every FAIL match appears in the table. Pure -- no I/O.

    Args:
        run_row: One ``bf.quality_run`` row (mapping).
        match_rows: The run's ``bf.quality_match_result`` rows (mappings).

    Returns:
        The formatted plain-text report.
    """
    lines: list[str] = []
    lines.append("Quality Report")
    lines.append("=" * 14)
    lines.append(f"Run ID:    {_value(run_row, 'run_id')}")
    lines.append(f"Window:    {_value(run_row, 'look_back_start')} -> {_value(run_row, 'look_back_end')}")
    lines.append(f"Started:   {_value(run_row, 'run_started')}")
    lines.append(f"Finished:  {_value(run_row, 'run_finished')}")
    lines.append(f"Status:    {_value(run_row, 'status')}")
    lines.append(
        f"Verified:  {_value(run_row, 'matches_verified')}   "
        f"Passed: {_value(run_row, 'matches_passed')}   "
        f"Failed: {_value(run_row, 'matches_failed')}"
    )
    alert = run_row.get("overall_alert")
    lines.append(f"Alert:     {'YES' if alert else 'no'}")
    lines.append("")

    # Build the aligned per-match table. Column widths are the max of the header
    # and every cell so the table lines up regardless of value lengths.
    rows = [_match_cells(row) for row in match_rows]
    widths = [len(header) for header in _MATCH_HEADERS]
    for cells in rows:
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))

    def _format_row(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines.append(_format_row(_MATCH_HEADERS))
    lines.append("  ".join("-" * width for width in widths))
    if rows:
        for cells in rows:
            lines.append(_format_row(cells))
    else:
        lines.append("(no matches verified)")

    return "\n".join(lines) + "\n"


def format_report_markdown(run_row: Mapping, match_rows: Sequence[Mapping]) -> str:
    """Render the Quality_Report as Markdown for Confluence/ticket paste.

    Produces a heading, a summary list for the run, and a Markdown table with one
    row per match (every match included). Same content as
    :func:`format_report_text`. Pure -- no I/O.

    Args:
        run_row: One ``bf.quality_run`` row (mapping).
        match_rows: The run's ``bf.quality_match_result`` rows (mappings).

    Returns:
        The formatted Markdown report.
    """
    alert = run_row.get("overall_alert")
    lines: list[str] = []
    lines.append(f"# Quality Report - run {_value(run_row, 'run_id')}")
    lines.append("")
    lines.append(f"- **Window:** {_value(run_row, 'look_back_start')} -> {_value(run_row, 'look_back_end')}")
    lines.append(f"- **Started:** {_value(run_row, 'run_started')}")
    lines.append(f"- **Finished:** {_value(run_row, 'run_finished')}")
    lines.append(f"- **Status:** {_value(run_row, 'status')}")
    lines.append(f"- **Verified:** {_value(run_row, 'matches_verified')}")
    lines.append(f"- **Passed:** {_value(run_row, 'matches_passed')}")
    lines.append(f"- **Failed:** {_value(run_row, 'matches_failed')}")
    lines.append(f"- **Alert:** {'YES' if alert else 'no'}")
    lines.append("")
    lines.append("| " + " | ".join(_MATCH_HEADERS) + " |")
    lines.append("| " + " | ".join("---" for _ in _MATCH_HEADERS) + " |")
    for row in match_rows:
        lines.append("| " + " | ".join(_match_cells(row)) + " |")

    return "\n".join(lines) + "\n"


def format_report_csv(match_rows: Sequence[Mapping]) -> str:
    """Render the per-match rows as CSV using the stdlib ``csv`` module.

    Emits a header row followed by exactly one data row per match, in the
    documented column order (target_id, market_id, present/coverage/consistency/
    useful/overall outcomes). The data-row count equals ``len(match_rows)``.
    Pure -- no I/O.

    Args:
        match_rows: The run's ``bf.quality_match_result`` rows (mappings).

    Returns:
        The CSV text (header + one row per match).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_MATCH_HEADERS)
    for row in match_rows:
        writer.writerow(_match_cells(row))
    return buffer.getvalue()
