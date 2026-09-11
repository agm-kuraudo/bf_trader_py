"""Pure-logic quality checks for post-event data-quality verification (SP-332).

This module intentionally contains no I/O: no ``os``, no ``psycopg2``, no file
or network access. It holds only pure functions and data definitions so the
quality-evaluation logic can be property-tested deterministically (see the
post-event-data-quality-verification design, "Pure logic in ``logic/``, I/O in
``scripts/``"). It mirrors the style of ``logic/deploy_checks.py``.

All quality thresholds live here in one documented place -- the
``QualityThresholds`` dataclass and the ``default_max_gaps()`` helper -- so both
the tests (Req 3.7, 5.5) and the Confluence note (Req 10.3) reference a single
source. The Coverage and Useful thresholds are anchored to the *intended*
``IN_PLAY`` 5s cadence, not the coarser cadence currently observed, so matches
captured at the observed ~900s cadence are expected to fail Coverage/Useful
(the SP-343 defect this feature detects but does not fix).

This module is scaffolding: the dataclasses, threshold source, and Cadence_Tier
interval map are defined here; the decision-function bodies are implemented in
later tasks.
"""

import ast
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# --- Cadence_Tier interval map (Req 3.1, 3.6) ---------------------------------
#
# The named update-frequency tiers selected by time-to-event, each mapping to a
# fixed polling interval in seconds (SP-328 definitions). ``IN_PLAY`` is the
# intended dense in-play cadence; the coarser tiers apply pre-match by
# time-to-event.
CADENCE_TIER_INTERVALS_S: dict[str, int] = {
    "IN_PLAY": 5,
    "LESS_THAN_3H": 300,
    "LESS_THAN_6H": 900,
    "LESS_THAN_12H": 3600,
    "MORE_THAN_12H": 14400,
}


def default_max_gaps() -> dict[str, int]:
    """Return the calibrated maximum acceptable no-row gap (seconds) per tier.

    Anchored to the intended tier intervals with generous headroom (see the
    design's Threshold Calibration): an order of magnitude above the intended
    ``IN_PLAY`` 5s interval so brief scheduling jitter is tolerated but real
    in-play dropouts are caught, and 1.5x-3x the pre-match tier intervals.
    Because these are anchored to the intended cadence, matches captured at the
    observed ~900s cadence exceed the ``IN_PLAY`` gap limit and correctly fail
    Coverage (the SP-343 defect).

    Validates: Requirements 3.5, 3.7, 10.3

    Returns:
        A fresh dict mapping each Cadence_Tier name to its maximum acceptable
        gap in seconds.
    """
    return {
        "IN_PLAY": 60,
        "LESS_THAN_3H": 900,
        "LESS_THAN_6H": 2700,
        "LESS_THAN_12H": 7200,
        "MORE_THAN_12H": 21600,
    }


@dataclass(frozen=True)
class QualityThresholds:
    """The single documented source of the calibrated quality thresholds.

    Every quality decision reads its threshold from an instance of this frozen
    dataclass, so a revision updates the tests and the Confluence note through
    one edit (Req 3.7, 5.5, 10.3). The Coverage/Useful values are anchored to
    the *intended* ``IN_PLAY`` 5s cadence, not the observed ~900s cadence, so
    current-season captures are expected to be flagged deficient until the
    in-play polling defect (SP-343) is resolved.

    Attributes:
        look_back_hours: Look_Back_Window duration in hours (previous-day
            default, Req 1.2). Configurable 1-168h at the wrapper.
        coverage_shortfall_ratio: Coverage fails if the actual count is less
            than ``(1 - ratio)`` of the Expected_Sample_Count (0.40 -> fail if
            actual < 60% of expected).
        inplay_interval_s: The intended ``IN_PLAY`` cadence in seconds, the
            basis of the Expected_Sample_Count in-play volume (Req 3.6).
        inplay_duration_s: The intended in-play span in seconds (~105 min) used
            when computing the expected in-play sample volume.
        max_gap_s: Per-Cadence_Tier maximum acceptable no-row gap in seconds
            (see ``default_max_gaps``).
        null_price_ratio: Consistency fails when the proportion of rows with
            neither a back nor a lay price exceeds this (0.50 -> >50%).
        min_samples_per_market: Minimum associated Market_Table rows for a
            Completed_Match to be analysis-useful (Useful dimension).
        prematch_window_s: The pre-match window before ``start_time`` within
            which at least one row must fall for the Useful lifecycle span.
        settlement_grace_s: Grace added to ``start_time`` for the at/after
            settlement lifecycle-span requirement (0 -> at/after ``start_time``).
    """

    look_back_hours: int = 24
    coverage_shortfall_ratio: float = 0.40
    inplay_interval_s: int = 5
    inplay_duration_s: int = 105 * 60
    max_gap_s: dict[str, int] = field(default_factory=default_max_gaps)
    null_price_ratio: float = 0.50
    min_samples_per_market: int = 200
    prematch_window_s: int = 3 * 3600
    settlement_grace_s: int = 0


@dataclass
class DimensionOutcome:
    """The evaluated outcome of a single Quality_Dimension for one match.

    Attributes:
        outcome: One of ``"PASS"``, ``"FAIL"``, or ``"NOT_EVALUATED"``.
        reason: A human-readable failure/not-evaluated reason, populated on
            ``"FAIL"`` / ``"NOT_EVALUATED"`` and ``None`` on ``"PASS"``.
        evidence: The recorded evidence used to determine the outcome (counts,
            timestamps, gap bounds, affected identifiers, etc.).
    """

    outcome: str
    reason: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class MatchQualityResult:
    """The single per-match Quality_Check result (Req 6.1).

    Records a discrete outcome for each of the four Quality_Dimensions plus the
    overall roll-up. Maps directly to one ``bf.quality_match_result`` row: the
    four ``DimensionOutcome`` values to the four ``*_outcome`` columns, their
    reasons/evidence folded into the ``evidence`` ``jsonb`` column.

    Attributes:
        target_id: Identity of the Completed_Match's Target.
        market_id: The Target's ``market_id`` (``None`` when absent).
        present: The Present-dimension outcome.
        coverage: The Coverage-dimension outcome.
        consistency: The Consistency-dimension outcome.
        useful: The Useful-dimension outcome.
        overall: ``"PASS"`` only when all four dimensions pass, else ``"FAIL"``.
    """

    target_id: str
    market_id: str | None
    present: DimensionOutcome
    coverage: DimensionOutcome
    consistency: DimensionOutcome
    useful: DimensionOutcome
    overall: str


# --- Odds_Value parser (Req 4.1, 4.2, 9.5, 9.6) -------------------------------


def _is_price_size_ladder(value: object) -> bool:
    """True iff ``value`` is a list of ``{price, size}`` mapping entries.

    An empty list is a valid (empty) ladder. Each entry must be a mapping that
    contains both a ``price`` and a ``size`` key; extra keys are tolerated so
    the parser stays forgiving of any additional Betfair fields.
    """
    if not isinstance(value, list):
        return False
    for entry in value:
        if not isinstance(entry, dict):
            return False
        if "price" not in entry or "size" not in entry:
            return False
    return True


def parse_odds(raw: str) -> dict | None:
    """Parse a stored Odds_Value into a dict, or ``None`` on any failure.

    The stored form is a Python dict ``repr`` (``str(dict)``), not JSON, so
    ``ast.literal_eval`` is used -- the same approach ``analyse_service.py``
    already applies to the ``odds`` column. A successful parse guarantees both
    ``availableToBack`` and ``availableToLay`` map to lists of ``{price, size}``
    entries; the optional ``tradedVolume`` key is preserved untouched for the
    round-trip (Req 9.6).

    Any failure -- a value that does not evaluate to a dict, a missing ladder
    key, or a ladder that is not a list of ``{price, size}`` entries -- returns
    ``None`` and leaves no partially parsed result (Req 4.2, 9.5).

    Validates: Requirements 4.1, 4.2, 9.5

    Args:
        raw: The stored Odds_Value string (a Python dict ``repr``).

    Returns:
        The parsed dict on success, or ``None`` on any parse/validation failure.
    """
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None

    if not isinstance(parsed, dict):
        return None

    if "availableToBack" not in parsed or "availableToLay" not in parsed:
        return None

    if not _is_price_size_ladder(parsed["availableToBack"]):
        return None
    if not _is_price_size_ladder(parsed["availableToLay"]):
        return None

    return parsed


def serialize_odds(parsed: dict) -> str:
    """Re-serialize a parsed Odds_Value back to its stored string form.

    The round-trip partner of :func:`parse_odds`: the stored form is a Python
    dict ``repr``, so serialization is ``str(dict)``. For any valid stored
    Odds_Value ``v``, ``serialize_odds(parse_odds(v)) == v``, preserving all
    keys including the optional ``tradedVolume`` (Req 9.6).

    Validates: Requirements 9.6

    Args:
        parsed: A parsed Odds_Value dict (as returned by :func:`parse_odds`).

    Returns:
        The dict rendered as its stored ``str(dict)`` string form.
    """
    return str(parsed)


def back_prices(parsed: dict) -> list[float]:
    """Return the ``price`` values from the ``availableToBack`` ladder.

    Assumes ``parsed`` came from :func:`parse_odds`, so ``availableToBack`` is a
    list of ``{price, size}`` entries.

    Args:
        parsed: A parsed Odds_Value dict.

    Returns:
        The list of back ``price`` values in ladder order (empty if none).
    """
    return [entry["price"] for entry in parsed["availableToBack"]]


def lay_prices(parsed: dict) -> list[float]:
    """Return the ``price`` values from the ``availableToLay`` ladder.

    Assumes ``parsed`` came from :func:`parse_odds`, so ``availableToLay`` is a
    list of ``{price, size}`` entries.

    Args:
        parsed: A parsed Odds_Value dict.

    Returns:
        The list of lay ``price`` values in ladder order (empty if none).
    """
    return [entry["price"] for entry in parsed["availableToLay"]]


def has_any_price(parsed: dict) -> bool:
    """True if the parsed value has at least one back OR lay price.

    Used by the Consistency null-price check: a row with neither a back nor a
    lay price contributes to the both-empty-price proportion (Req 4.3).

    Args:
        parsed: A parsed Odds_Value dict.

    Returns:
        ``True`` when either ladder has at least one price, else ``False``.
    """
    return bool(back_prices(parsed)) or bool(lay_prices(parsed))


# --- Expected sampling & coverage (Req 3) -------------------------------------


# Pre-match Cadence_Tier bands by time-to-event, expressed as the [lower, upper)
# seconds-before-``start_time`` interval each tier covers. Ordered nearest-event
# first. The most-distant tier (``MORE_THAN_12H``) has no upper bound and absorbs
# all lead time earlier than 12h before the event.
#
# Each band maps a portion of the pre-match window to the tier whose polling
# interval applies while the event is that far away, mirroring the SP-328
# time-to-event tier selection (Req 3.1).
_PREMATCH_TIER_BANDS_S: list[tuple[str, int, int | None]] = [
    ("LESS_THAN_3H", 0, 3 * 3600),
    ("LESS_THAN_6H", 3 * 3600, 6 * 3600),
    ("LESS_THAN_12H", 6 * 3600, 12 * 3600),
    ("MORE_THAN_12H", 12 * 3600, None),
]


def expected_sample_count(
    start_time: datetime,
    prematch_start: datetime,
    inplay_end: datetime,
    tier_intervals: dict[str, int],
    inplay_interval_s: int,
) -> int:
    """Sum expected sampling intervals per Cadence_Tier across the lifecycle.

    Computes the Expected_Sample_Count for one Completed_Match by summing, for
    each Cadence_Tier active across ``[prematch_start, inplay_end]``, the number
    of sampling intervals expected in that tier's active duration (Req 3.1).

    The lifecycle is split into a pre-match period ``[prematch_start,
    start_time]`` and an in-play period ``[start_time, inplay_end]``:

    - **Pre-match** is partitioned by time-to-event into the coarse tier bands
      (``LESS_THAN_3H`` .. ``MORE_THAN_12H``). The seconds of lead time falling
      in each band divided by that tier's interval gives the intervals expected
      while the event is that far away. Each tier's interval is read from
      ``tier_intervals`` (the Cadence_Tier interval map).
    - **In-play** uses the *intended* ``inplay_interval_s`` (5s), not the
      coarser observed cadence, so a match captured at the observed ~900s
      cadence produces far fewer rows than expected and is correctly flagged by
      the Coverage/Useful dimensions (the SP-343 defect, detect-only).

    Per-tier interval counts are floored (whole intervals), then summed. When
    the bounds are degenerate (non-positive durations), that period contributes
    zero.

    Validates: Requirements 3.1

    Args:
        start_time: The market start / kick-off. Boundary between the pre-match
            and in-play periods.
        prematch_start: The start of the pre-match capture period (the earliest
            point the expectation spans).
        inplay_end: The end of the in-play period (settlement side).
        tier_intervals: The Cadence_Tier interval map (seconds per tier), e.g.
            :data:`CADENCE_TIER_INTERVALS_S`.
        inplay_interval_s: The intended in-play sampling interval in seconds.

    Returns:
        The Expected_Sample_Count as a non-negative integer.
    """
    expected = 0

    # Pre-match: partition the lead time [prematch_start, start_time] by the
    # time-to-event tier bands and accrue intervals per band.
    prematch_lead_s = (start_time - prematch_start).total_seconds()
    if prematch_lead_s > 0:
        for tier, band_lo, band_hi in _PREMATCH_TIER_BANDS_S:
            interval = tier_intervals.get(tier)
            if not interval or interval <= 0:
                continue
            # Seconds of the lead time that fall within this band's
            # [band_lo, band_hi) distance-before-event window.
            upper = prematch_lead_s if band_hi is None else min(prematch_lead_s, band_hi)
            band_seconds = upper - band_lo
            if band_seconds > 0:
                expected += int(band_seconds // interval)

    # In-play: [start_time, inplay_end] at the intended dense cadence.
    inplay_s = (inplay_end - start_time).total_seconds()
    if inplay_s > 0 and inplay_interval_s > 0:
        expected += int(inplay_s // inplay_interval_s)

    return expected


def _largest_gap(
    timestamps: list[datetime],
) -> tuple[float, datetime, datetime] | None:
    """Return the largest contiguous inter-row gap among sorted timestamps.

    Args:
        timestamps: Row timestamps (any order; sorted internally).

    Returns:
        ``(gap_seconds, gap_start, gap_end)`` for the largest gap between two
        consecutive rows, or ``None`` when fewer than two timestamps are given.
    """
    if len(timestamps) < 2:
        return None
    ordered = sorted(timestamps)
    largest: tuple[float, datetime, datetime] | None = None
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        gap = (later - earlier).total_seconds()
        if largest is None or gap > largest[0]:
            largest = (gap, earlier, later)
    return largest


def coverage_result(
    actual_count: int,
    expected_count: int,
    row_timestamps: list[datetime],
    start_time: datetime,
    thresholds: QualityThresholds,
) -> dict:
    """Evaluate the Coverage dimension for one Completed_Match.

    Coverage passes only when all of the following hold (Req 3.2-3.6):

    - **Count-based shortfall (Req 3.2-3.4).** The shortfall is the amount by
      which ``actual_count`` falls below ``expected_count``. Coverage fails the
      count check when ``actual_count < (1 - coverage_shortfall_ratio) *
      expected_count`` -- i.e. the actual count is below the tolerated fraction
      of the Expected_Sample_Count. With the default ``0.40`` ratio, a match
      fails when it captured under 60% of the expected rows.
    - **Largest-gap detection (Req 3.5).** The largest contiguous no-row gap is
      detected from ``row_timestamps``. Gaps wholly outside the pre-match window
      (earlier than ``prematch_window_s`` before ``start_time``) are benign --
      the "monitor off overnight" case -- and are excluded before the gap is
      compared against the acceptable limit. The applicable ``max_gap_s`` limit
      is the ``IN_PLAY`` limit when the gap touches the in-play period (at/after
      ``start_time``), otherwise the tightest pre-match tier limit. Coverage
      fails when the largest evaluated gap exceeds that limit.
    - **In-play-empty special case (Req 3.6).** When the in-play period has no
      rows (no timestamp at or after ``start_time``) while the pre-match count
      check passes, Coverage fails: a match can have enough pre-match rows to
      clear the count check yet never capture the in-play period.

    The Expected_Sample_Count is anchored to the intended in-play cadence, so
    matches captured at the observed ~900s cadence fall short here as intended
    (the SP-343 defect this feature detects).

    Validates: Requirements 3.2, 3.3, 3.4, 3.5, 3.6

    Args:
        actual_count: The actual count of associated Market_Table rows.
        expected_count: The Expected_Sample_Count (see
            :func:`expected_sample_count`).
        row_timestamps: The timestamps of the associated Market_Table rows (any
            order).
        start_time: The market start / kick-off, dividing pre-match from
            in-play.
        thresholds: The calibrated :class:`QualityThresholds` (supplies
            ``coverage_shortfall_ratio``, ``max_gap_s``, and
            ``prematch_window_s``).

    Returns:
        A dict ``{'passed': bool, 'actual': int, 'expected': int, 'shortfall':
        int, 'largest_gap': (start_ts, end_ts) | None, 'inplay_empty': bool,
        'reasons': list[str]}``. ``largest_gap`` reports the bounds of the
        largest *evaluated* offending gap (or ``None`` when no evaluated gap
        exceeds its limit); ``shortfall`` is the count-based shortfall (>= 0).
    """
    reasons: list[str] = []

    # --- Count-based shortfall (Req 3.2-3.4) ---
    shortfall = max(0, expected_count - actual_count)
    tolerated_floor = (1.0 - thresholds.coverage_shortfall_ratio) * expected_count
    count_check_passes = actual_count >= tolerated_floor
    if not count_check_passes:
        reasons.append(
            f"count shortfall: actual {actual_count} below tolerated floor "
            f"{tolerated_floor:.1f} of expected {expected_count} "
            f"(shortfall {shortfall})"
        )

    # --- In-play-empty special case (Req 3.6) ---
    inplay_timestamps = [ts for ts in row_timestamps if ts >= start_time]
    inplay_empty = len(inplay_timestamps) == 0
    if inplay_empty and count_check_passes:
        reasons.append(
            "in-play period has no rows while the pre-match count check passes"
        )

    # --- Largest-gap detection, excluding benign out-of-window gaps (Req 3.5) ---
    prematch_window_start = start_time - timedelta(seconds=thresholds.prematch_window_s)
    # Only rows within the active window (pre-match window through in-play) count
    # toward gap detection; gaps wholly before the pre-match window are benign.
    in_window_timestamps = [ts for ts in row_timestamps if ts >= prematch_window_start]
    largest = _largest_gap(in_window_timestamps)
    largest_gap_bounds: tuple[datetime, datetime] | None = None
    if largest is not None:
        gap_s, gap_start, gap_end = largest
        # The gap is held to the IN_PLAY limit only when it lies within the
        # in-play period (its start is at/after ``start_time``). A gap that
        # begins before kick-off is a pre-match gap -- even if it ends at/after
        # kick-off -- and is held to the tightest pre-match tier limit (the
        # smallest non-IN_PLAY max gap).
        if gap_start >= start_time:
            limit = thresholds.max_gap_s.get("IN_PLAY", 0)
        else:
            prematch_limits = [
                v for k, v in thresholds.max_gap_s.items() if k != "IN_PLAY"
            ]
            limit = min(prematch_limits) if prematch_limits else 0
        if limit and gap_s > limit:
            largest_gap_bounds = (gap_start, gap_end)
            reasons.append(
                f"largest no-row gap {gap_s:.0f}s exceeds limit {limit}s "
                f"between {gap_start.isoformat()} and {gap_end.isoformat()}"
            )

    passed = len(reasons) == 0
    return {
        "passed": passed,
        "actual": actual_count,
        "expected": expected_count,
        "shortfall": shortfall,
        "largest_gap": largest_gap_bounds,
        "inplay_empty": inplay_empty,
        "reasons": reasons,
    }


# --- Present (Req 2) ----------------------------------------------------------


def present_result(market_id: str | None, row_count: int) -> dict:
    """Evaluate the Present dimension for one Kicked_Off_Target.

    A Kicked_Off_Target passes the Present dimension only when it has a usable
    ``market_id`` AND at least one captured Market_Table row:

    - Fail when ``market_id`` is absent (``None`` or empty/whitespace), recording
      an indication that the ``market_id`` is absent (Req 2.4).
    - Fail when ``row_count == 0``, recording the affected identifiers (Req 2.2).
    - Pass when ``market_id`` is present AND ``row_count >= 1`` (Req 2.3).

    The absent-``market_id`` check takes precedence: with no ``market_id`` the
    row count is meaningless, so the returned evidence flags the missing
    identifier regardless of the count. The affected identifiers are recorded in
    the evidence dict so the wrapper can fold them into the durable record.

    Validates: Requirements 2.2, 2.3, 2.4

    Args:
        market_id: The Target's ``market_id`` (``None``/empty when absent).
        row_count: The count of Market_Table rows sharing that ``market_id``
            (an integer >= 0, per Req 2.1).

    Returns:
        A dict ``{'passed': bool, 'reason': str | None, 'evidence': dict}``.
        ``reason`` is ``None`` on pass and a human-readable explanation on fail.
        ``evidence`` always carries ``market_id`` and ``row_count`` plus a
        ``market_id_absent`` flag.
    """
    market_id_absent = market_id is None or (
        isinstance(market_id, str) and market_id.strip() == ""
    )
    evidence = {
        "market_id": market_id,
        "row_count": row_count,
        "market_id_absent": market_id_absent,
    }

    if market_id_absent:
        return {
            "passed": False,
            "reason": "market_id is absent",
            "evidence": evidence,
        }

    if row_count == 0:
        return {
            "passed": False,
            "reason": "no Market_Table rows captured (row_count == 0)",
            "evidence": evidence,
        }

    return {"passed": True, "reason": None, "evidence": evidence}


# --- Consistency (Req 4) ------------------------------------------------------


def consistency_result(
    odds_values: list[str],
    runner_ids_in_rows: list[str],
    declared_runner_ids: list[str] | None,
    rows_in_storage_order: list[tuple[str, datetime]],
    dedup_keys: list[tuple[str, str, str]],
    null_price_threshold: float,
) -> dict:
    """Evaluate the Consistency dimension for one Completed_Match.

    Runs every Consistency sub-check over the match's associated rows and rolls
    them into an overall ``passed`` that is ``True`` only when *all* sub-checks
    hold. Each sub-check contributes its own evidence sub-dict so the wrapper can
    fold the detail into the durable record. The sub-checks are:

    - **Parseable odds (Req 4.1, 4.2).** Every ``odds_values`` entry must parse
      via :func:`parse_odds`. Fails when any value is unparseable, recording the
      count of unparseable Odds_Values.
    - **Null-price proportion (Req 4.3).** Among the *parseable* rows, the
      proportion whose Odds_Value has neither a back nor a lay price (see
      :func:`has_any_price`) must not exceed ``null_price_threshold``. Fails when
      the proportion exceeds the threshold, recording the both-empty count. The
      proportion is taken over parseable rows only, since an unparseable value
      has no meaningful ladder and is already caught by the parse sub-check.
    - **Runner-count match (Req 4.4).** The number of distinct ``runner_id``
      values across the rows must equal the number of declared runner
      identifiers. Fails when the two counts differ.
    - **Non-decreasing timestamps (Req 4.5).** For each ``runner_id``, the
      ``timestamp`` values must be non-decreasing in row-storage order. Fails
      when a later-stored row carries an earlier ``timestamp`` than a row stored
      before it for the same runner.
    - **No duplicate keys (Req 4.6).** No two rows may share the same
      ``(market_id, runner_id, timestamp)``. Fails when any such duplicate
      exists, recording the count of duplicate rows.
    - **Declared runner ids present (Req 4.7).** When ``declared_runner_ids`` is
      ``None`` (the Target's ``runner_ids`` column is absent or could not be
      parsed into a list of identifiers), the match always fails Consistency and
      the runner-count sub-check is short-circuited to a failure.

    Validates: Requirements 4.1, 4.3, 4.4, 4.5, 4.6, 4.7

    Args:
        odds_values: The raw stored Odds_Value strings for the match's rows.
        runner_ids_in_rows: The ``runner_id`` of each associated row (used for
            the distinct-runner count).
        declared_runner_ids: The runner identifiers declared on the Target
            (``None`` when the ``runner_ids`` column is absent/unparseable).
        rows_in_storage_order: ``(runner_id, timestamp)`` for each row in
            row-storage order (``ctid`` order), for the non-decreasing check.
        dedup_keys: ``(market_id, runner_id, timestamp)`` for each row, for the
            duplicate check.
        null_price_threshold: The maximum tolerated proportion of both-empty
            rows before the null-price sub-check fails
            (``QualityThresholds.null_price_ratio``).

    Returns:
        A dict ``{'passed': bool, 'reasons': list[str], <sub-check>: {...}}``
        carrying, per sub-check, ``passed`` plus its recorded evidence.
    """
    reasons: list[str] = []

    # --- Parseable odds (Req 4.1, 4.2) ---
    parsed_values: list[dict] = []
    unparseable_count = 0
    for raw in odds_values:
        parsed = parse_odds(raw)
        if parsed is None:
            unparseable_count += 1
        else:
            parsed_values.append(parsed)
    parse_passed = unparseable_count == 0
    if not parse_passed:
        reasons.append(f"{unparseable_count} unparseable Odds_Value(s)")
    parse_check = {"passed": parse_passed, "unparseable_count": unparseable_count}

    # --- Null-price proportion (Req 4.3) ---
    # Evaluated over parseable rows only: an unparseable value has no ladder to
    # inspect and is already accounted for by the parse sub-check.
    both_empty_count = sum(
        1 for parsed in parsed_values if not has_any_price(parsed)
    )
    parseable_total = len(parsed_values)
    null_price_proportion = (
        both_empty_count / parseable_total if parseable_total else 0.0
    )
    null_price_passed = null_price_proportion <= null_price_threshold
    if not null_price_passed:
        reasons.append(
            f"both-empty-price proportion {null_price_proportion:.3f} exceeds "
            f"threshold {null_price_threshold} "
            f"({both_empty_count}/{parseable_total} rows)"
        )
    null_price_check = {
        "passed": null_price_passed,
        "both_empty_count": both_empty_count,
        "parseable_row_count": parseable_total,
        "proportion": null_price_proportion,
        "threshold": null_price_threshold,
    }

    # --- Runner-count match (Req 4.4) / declared runner ids present (Req 4.7) ---
    distinct_runner_count = len(set(runner_ids_in_rows))
    if declared_runner_ids is None:
        runner_count_passed = False
        declared_runner_count: int | None = None
        reasons.append(
            "declared runner_ids absent or unparseable (declared runner count "
            "unavailable)"
        )
    else:
        declared_runner_count = len(declared_runner_ids)
        runner_count_passed = distinct_runner_count == declared_runner_count
        if not runner_count_passed:
            reasons.append(
                f"distinct runner count {distinct_runner_count} does not equal "
                f"declared runner count {declared_runner_count}"
            )
    runner_count_check = {
        "passed": runner_count_passed,
        "distinct_runner_count": distinct_runner_count,
        "declared_runner_count": declared_runner_count,
    }

    # --- Non-decreasing timestamps per runner (Req 4.5) ---
    last_ts_by_runner: dict[str, datetime] = {}
    ordering_violations = 0
    for runner_id, timestamp in rows_in_storage_order:
        previous = last_ts_by_runner.get(runner_id)
        if previous is not None and timestamp < previous:
            ordering_violations += 1
        last_ts_by_runner[runner_id] = timestamp
    ordering_passed = ordering_violations == 0
    if not ordering_passed:
        reasons.append(
            f"{ordering_violations} out-of-order timestamp(s) in storage order "
            f"for one or more runners"
        )
    ordering_check = {
        "passed": ordering_passed,
        "violations": ordering_violations,
    }

    # --- No duplicate (market_id, runner_id, timestamp) keys (Req 4.6) ---
    seen: set[tuple[str, str, str]] = set()
    duplicate_count = 0
    for key in dedup_keys:
        if key in seen:
            duplicate_count += 1
        else:
            seen.add(key)
    dedup_passed = duplicate_count == 0
    if not dedup_passed:
        reasons.append(
            f"{duplicate_count} duplicate (market_id, runner_id, timestamp) row(s)"
        )
    dedup_check = {"passed": dedup_passed, "duplicate_count": duplicate_count}

    passed = (
        parse_passed
        and null_price_passed
        and runner_count_passed
        and ordering_passed
        and dedup_passed
    )
    return {
        "passed": passed,
        "reasons": reasons,
        "parse": parse_check,
        "null_price": null_price_check,
        "runner_count": runner_count_check,
        "ordering": ordering_check,
        "duplicates": dedup_check,
    }


# --- Useful (Req 5) -----------------------------------------------------------


def useful_result(
    row_count: int,
    earliest_ts: datetime | None,
    latest_ts: datetime | None,
    start_time: datetime,
    settlement_ts: datetime,
    thresholds: QualityThresholds,
) -> dict:
    """Evaluate the Useful dimension for one Completed_Match.

    The Useful dimension asks whether a Completed_Match has enough resolution
    *and* enough lifecycle span to be analysis-ready. It produces a single
    boolean that passes only when **both** hold (Req 5.1-5.4):

    - **Sufficient resolution (Req 5.1).** ``row_count`` must be at least
      ``thresholds.min_samples_per_market``. Below that, the capture is too thin
      to analyse, recorded as an insufficient-resolution failure carrying the
      actual count and the required minimum.
    - **Lifecycle span (Req 5.2, 5.3).** The captured rows must span from the
      pre-match window through settlement:

      - a **pre-match-window** row: at least one row timestamped within
        ``thresholds.prematch_window_s`` before ``start_time`` -- i.e. the
        earliest row falls in ``[start_time - prematch_window_s, start_time]``
        (``earliest_ts <= start_time`` and ``earliest_ts >= start_time -
        prematch_window_s``); and
      - an **at/after-settlement** row: at least one row timestamped at or after
        the settlement boundary ``settlement_ts + thresholds.settlement_grace_s``
        (i.e. ``latest_ts >= settlement_ts + settlement_grace_s``).

    The earliest and latest row timestamps used in this determination are
    recorded in the evidence regardless of the outcome (Req 5.2), and on failure
    the reason names which lifecycle portion is absent -- pre-match window,
    settlement, or both (Req 5.3). When there are no rows (``earliest_ts`` /
    ``latest_ts`` are ``None``) neither span portion can be present, so both are
    reported absent.

    Validates: Requirements 5.1, 5.2, 5.3, 5.4

    Args:
        row_count: The count of associated Market_Table rows for the match.
        earliest_ts: The earliest associated row timestamp (``None`` when there
            are no rows).
        latest_ts: The latest associated row timestamp (``None`` when there are
            no rows).
        start_time: The market start / kick-off, anchoring the pre-match window.
        settlement_ts: The match settlement timestamp, anchoring the at/after
            settlement boundary.
        thresholds: The calibrated :class:`QualityThresholds` (supplies
            ``min_samples_per_market``, ``prematch_window_s``, and
            ``settlement_grace_s``).

    Returns:
        A dict ``{'passed': bool, 'reason': str | None, 'evidence': dict}``.
        ``reason`` is ``None`` on pass and a human-readable explanation on fail.
        ``evidence`` always carries ``row_count``, ``min_samples`` (the required
        threshold), ``earliest_ts``, ``latest_ts``, the two lifecycle boundaries
        (``prematch_window_start`` and ``settlement_boundary``), and the three
        determined flags (``resolution_ok``, ``prematch_present``,
        ``settlement_present``).
    """
    # --- Sufficient resolution (Req 5.1) ---
    resolution_ok = row_count >= thresholds.min_samples_per_market

    # --- Lifecycle-span boundaries (Req 5.2) ---
    prematch_window_start = start_time - timedelta(
        seconds=thresholds.prematch_window_s
    )
    settlement_boundary = settlement_ts + timedelta(
        seconds=thresholds.settlement_grace_s
    )

    # With no rows, neither lifecycle portion can be present.
    if earliest_ts is None or latest_ts is None:
        prematch_present = False
        settlement_present = False
    else:
        # A pre-match-window row exists iff the earliest row falls within
        # [start_time - prematch_window_s, start_time].
        prematch_present = prematch_window_start <= earliest_ts <= start_time
        # An at/after-settlement row exists iff the latest row is at or after
        # the settlement boundary.
        settlement_present = latest_ts >= settlement_boundary

    evidence = {
        "row_count": row_count,
        "min_samples": thresholds.min_samples_per_market,
        "earliest_ts": earliest_ts,
        "latest_ts": latest_ts,
        "prematch_window_start": prematch_window_start,
        "settlement_boundary": settlement_boundary,
        "resolution_ok": resolution_ok,
        "prematch_present": prematch_present,
        "settlement_present": settlement_present,
    }

    # --- Roll up: pass requires resolution AND both lifecycle portions ---
    passed = resolution_ok and prematch_present and settlement_present
    if passed:
        return {"passed": True, "reason": None, "evidence": evidence}

    reasons: list[str] = []
    if not resolution_ok:
        reasons.append(
            f"insufficient resolution: row_count {row_count} below minimum "
            f"{thresholds.min_samples_per_market}"
        )

    # Name which lifecycle portion is absent (Req 5.3).
    missing_portions: list[str] = []
    if not prematch_present:
        missing_portions.append("pre-match window")
    if not settlement_present:
        missing_portions.append("settlement")
    if missing_portions:
        reasons.append(
            "lifecycle span incomplete: missing " + " and ".join(missing_portions)
        )

    return {
        "passed": False,
        "reason": "; ".join(reasons),
        "evidence": evidence,
    }


# --- Per-match aggregation (Req 6) --------------------------------------------


# Sentinel outcome strings for a DimensionOutcome (Req 6.1).
_PASS = "PASS"
_FAIL = "FAIL"
_NOT_EVALUATED = "NOT_EVALUATED"


def _dimension_reason(result: dict) -> str | None:
    """Extract a human-readable reason from a dimension result dict.

    The four dimension functions carry their failure detail in slightly
    different shapes: :func:`present_result` and :func:`useful_result` use a
    single ``reason`` string, while :func:`coverage_result` and
    :func:`consistency_result` accumulate a ``reasons`` list. This normalizes
    both into one reason string (the ``reasons`` list joined on ``"; "``), or
    ``None`` when no reason is recorded.
    """
    reason = result.get("reason")
    if reason:
        return reason
    reasons = result.get("reasons")
    if reasons:
        return "; ".join(reasons)
    return None


def _dimension_evidence(result: dict) -> dict:
    """Extract the recorded evidence from a dimension result dict (Req 6.4).

    :func:`present_result` and :func:`useful_result` nest their evidence under
    an ``evidence`` key; :func:`coverage_result` and :func:`consistency_result`
    return their counts/sub-check detail as top-level keys of the result dict.
    This returns the nested ``evidence`` dict when present, otherwise the whole
    result dict minus the control keys (``passed``/``reason``/``reasons``) so
    the recorded counts, timestamps, and sub-check evidence are carried into the
    ``DimensionOutcome`` regardless of dimension shape.
    """
    evidence = result.get("evidence")
    if isinstance(evidence, dict):
        return evidence
    return {
        key: value
        for key, value in result.items()
        if key not in ("passed", "reason", "reasons")
    }


def _to_outcome(result: dict | None) -> DimensionOutcome:
    """Normalize one dimension result dict into a :class:`DimensionOutcome`.

    Maps a dimension result to a discrete PASS/FAIL/NOT_EVALUATED outcome
    (Req 6.1):

    - ``None`` (the dimension was not evaluated) or a dict explicitly marked
      ``{'evaluated': False}`` -> ``NOT_EVALUATED``, carrying any reason/evidence
      the caller recorded so the durable record can explain why (Req 6.5). The
      wrapper (task 9.2) passes ``None`` for a dimension it could not evaluate.
    - a result whose ``passed`` is truthy -> ``PASS`` (no reason).
    - otherwise -> ``FAIL``, carrying the dimension's failure reason and the
      recorded evidence (Req 6.4).
    """
    if result is None:
        return DimensionOutcome(
            outcome=_NOT_EVALUATED,
            reason="dimension could not be evaluated",
            evidence={},
        )
    if result.get("evaluated") is False:
        return DimensionOutcome(
            outcome=_NOT_EVALUATED,
            reason=_dimension_reason(result) or "dimension could not be evaluated",
            evidence=_dimension_evidence(result),
        )
    if result.get("passed"):
        return DimensionOutcome(outcome=_PASS, reason=None, evidence={})
    return DimensionOutcome(
        outcome=_FAIL,
        reason=_dimension_reason(result),
        evidence=_dimension_evidence(result),
    )


def aggregate_match(
    target_id: str,
    market_id: str | None,
    present: dict | None,
    coverage: dict | None,
    consistency: dict | None,
    useful: dict | None,
) -> MatchQualityResult:
    """Roll four dimension results into one ``MatchQualityResult`` (Req 6.1).

    Produces exactly one per-match result recording a discrete outcome for each
    of the four Quality_Dimensions plus the overall roll-up (Req 6.1):

    - Each dimension dict is normalized to a :class:`DimensionOutcome` of
      ``PASS``, ``FAIL``, or ``NOT_EVALUATED`` via :func:`_to_outcome`. A
      dimension passed to ``None`` (or a dict explicitly marked
      ``{'evaluated': False}``) is recorded as ``NOT_EVALUATED`` (Req 6.5); the
      wrapper (task 9.2) uses ``None`` for a dimension it could not evaluate.
    - ``overall`` is ``PASS`` only when all four dimensions pass, and ``FAIL``
      if any dimension fails *or* is unevaluated (Req 6.2, 6.3, 6.5).
    - Every failed or unevaluated dimension carries its failure reason and the
      recorded evidence into its ``DimensionOutcome`` (Req 6.4, 6.5); passing
      dimensions carry no reason.

    Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5

    Args:
        target_id: Identity of the Completed_Match's Target.
        market_id: The Target's ``market_id`` (``None`` when absent).
        present: The Present-dimension result dict (as returned by
            :func:`present_result`), or ``None`` if it could not be evaluated.
        coverage: The Coverage-dimension result dict (as returned by
            :func:`coverage_result`), or ``None`` if it could not be evaluated.
        consistency: The Consistency-dimension result dict (as returned by
            :func:`consistency_result`), or ``None`` if it could not be
            evaluated.
        useful: The Useful-dimension result dict (as returned by
            :func:`useful_result`), or ``None`` if it could not be evaluated.

    Returns:
        A :class:`MatchQualityResult` with the four ``DimensionOutcome`` fields
        and the ``overall`` roll-up.
    """
    present_outcome = _to_outcome(present)
    coverage_outcome = _to_outcome(coverage)
    consistency_outcome = _to_outcome(consistency)
    useful_outcome = _to_outcome(useful)

    all_pass = all(
        outcome.outcome == _PASS
        for outcome in (
            present_outcome,
            coverage_outcome,
            consistency_outcome,
            useful_outcome,
        )
    )
    overall = _PASS if all_pass else _FAIL

    return MatchQualityResult(
        target_id=target_id,
        market_id=market_id,
        present=present_outcome,
        coverage=coverage_outcome,
        consistency=consistency_outcome,
        useful=useful_outcome,
        overall=overall,
    )


# --- Look-back window & selection helpers (Req 1) -----------------------------


def default_look_back_window(now: datetime) -> tuple[datetime, datetime]:
    """Return the previous-calendar-day Look_Back_Window for ``now``.

    The default Look_Back_Window is the whole calendar day preceding the
    Quality_Check run date: from ``00:00:00`` to ``23:59:59`` (local time of the
    day before ``now``'s date), inclusive at both ends (Req 1.2). The window is
    derived purely from ``now``'s date -- its time-of-day is irrelevant -- and
    any ``tzinfo`` on ``now`` is carried onto both bounds so the window stays in
    the same reference frame as the run clock.

    The end bound uses ``23:59:59`` (whole-second granularity) to match the
    requirement's stated ``23:59:59`` boundary; selection against this window is
    inclusive (see :func:`is_verifiable`).

    Validates: Requirements 1.2

    Args:
        now: The Quality_Check run start time. Only its date is used.

    Returns:
        A ``(start, end)`` tuple of datetimes spanning the previous calendar
        day, preserving ``now``'s ``tzinfo`` on both bounds.
    """
    previous_day = now.date() - timedelta(days=1)
    start = datetime(
        previous_day.year,
        previous_day.month,
        previous_day.day,
        0,
        0,
        0,
        tzinfo=now.tzinfo,
    )
    end = datetime(
        previous_day.year,
        previous_day.month,
        previous_day.day,
        23,
        59,
        59,
        tzinfo=now.tzinfo,
    )
    return start, end


def is_verifiable(
    status: str, start_time: datetime, window: tuple[datetime, datetime]
) -> bool:
    """True iff the Target is settled AND its ``start_time`` is in the window.

    A Target is verifiable for a Quality_Check run only when both hold:

    - its Target_Status is ``CLOSED`` or ``EXPIRED`` (settled) -- ``IDENTIFIED``
      and ``OPEN`` targets are excluded (Req 1.1, 1.3); and
    - its ``start_time`` falls within the Look_Back_Window ``[start, end]``,
      inclusive of both bounds (Req 1.1).

    Status matching is exact and case-sensitive against the stored uppercase
    Target_Status values.

    Validates: Requirements 1.1, 1.3

    Args:
        status: The Target's Target_Status string.
        start_time: The Target's ``start_time``.
        window: A ``(start, end)`` Look_Back_Window tuple, as produced by
            :func:`default_look_back_window`.

    Returns:
        ``True`` when the Target is settled and in-window, else ``False``.
    """
    if status not in ("CLOSED", "EXPIRED"):
        return False
    window_start, window_end = window
    return window_start <= start_time <= window_end
