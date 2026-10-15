"""Pure-logic Odds_Transform for the Betfair Research_Dataset (SP-368).

This module is the pure, I/O-free transform layer of the Dataset_Loader: given
raw captured rows (as plain Python values), it parses and shapes them into
Dataset_Records without touching the database, filesystem, or network. Like
``logic/quality_checks.py`` it contains no ``os``, no ``psycopg2``, and no file
or network access, so the shaping logic can be property-tested deterministically
(see the betfair-research-dataset design, "Pure core, thin wrapper").

It deliberately reuses the single existing odds-parse path -- ``parse_odds`` /
``serialize_odds`` from ``logic/quality_checks.py`` (SP-332) -- rather than
adding a second ``ast.literal_eval`` read of the ``odds`` column, which is what
Requirement 6.4 demands ("exactly one odds-parsing code path"). The new
per-side extraction (best-back / best-lay / depth), the Req 1.8 price/size
validation, Runner_Identity parsing, Outcome_Role assignment, Match_Phase
derivation, and record assembly live here and call into that shared core.

This module is scaffolding: the data models, the ``DEFAULT_DRAW_NAME`` constant,
and the ``MarketRow`` / ``TargetRow`` input aliases are defined here with their
docstrings; the behavioural function bodies are implemented in later tasks.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from logic.quality_checks import parse_odds, serialize_odds

__all__ = [
    "DEFAULT_DRAW_NAME",
    "DATASET_SCHEMA",
    "MarketRow",
    "TargetRow",
    "PriceLevel",
    "StructuredOdds",
    "RunnerIdentity",
    "OutcomeRoleAssignment",
    "DatasetRecord",
    "TransformResult",
    "parse_odds",
    "serialize_odds",
    "empty_dataset",
    "records_to_dataframe",
]

# --- Configured draw selection name (Req 2.5) ---------------------------------
#
# The Outcome_Role derivation keys off the configured draw-selection name,
# observed as the literal ``"The Draw"`` in captured MATCH_ODDS data (e.g. the
# ``58805-The Draw`` entry of a ``runner_ids`` string). A runner whose trimmed
# name case-sensitively equals this trimmed value is assigned the ``DRAW``
# Outcome_Role; HOME/AWAY are then assigned to the two non-draw runners in
# ``runner_ids`` order (see ``assign_outcome_roles``).
DEFAULT_DRAW_NAME: str = "The Draw"


# --- Dataset_Schema (Req 5.6) -------------------------------------------------
#
# The single source of truth for the Research_Dataset's tabular contract: the
# exact 12 columns, in this order, with their pandas dtypes. Both
# ``empty_dataset`` (the zero-record path, Req 5.2) and ``records_to_dataframe``
# (the populated path) build their frames from this one spec, and the schema
# documentation (``docs/research_dataset_schema.md``) describes the same set, so
# there is one place that fixes "exactly these 12 columns in this order/dtypes,
# no extras" (Req 5.6). The two timestamp columns use UTC-aware
# ``datetime64[ns, UTC]``; the three nullable price/volume columns use the
# pandas nullable ``Float64``; every id/name/role/phase column uses the pandas
# nullable ``string`` dtype.
DATASET_SCHEMA: tuple[tuple[str, str], ...] = (
    ("market_id", "string"),
    ("runner_id", "string"),
    ("runner_name", "string"),
    ("outcome_role", "string"),
    ("timestamp", "datetime64[ns, UTC]"),
    ("best_back", "Float64"),
    ("best_lay", "Float64"),
    ("matched_volume", "Float64"),
    ("match_phase", "string"),
    ("event_id", "string"),
    ("start_time", "datetime64[ns, UTC]"),
    ("target_id", "string"),
)


# --- Reader -> transform input aliases ----------------------------------------
#
# The thin Dataset_Reader (``scripts/build_research_dataset.py``) reads rows
# from the Data_Store and hands them to this pure transform as plain tuples
# mirroring the DB columns. These aliases name those shapes for the transform's
# signatures without coupling the pure layer to psycopg2.

# One row of ``bf.market_table``: ``(timestamp, market_id, runner_id, odds)``.
MarketRow = tuple[datetime, str, str, str]

# One row of ``bf.target``:
# ``(target_id, event_id, market_id, runner_ids, start_time, status,
#    last_updated, notes)``.
TargetRow = tuple[str, str, str, str, datetime, str, datetime, str]


# --- Parsed Odds_Value models (Req 1) -----------------------------------------


@dataclass(frozen=True)
class PriceLevel:
    """One ``{price, size}`` entry of a Price_Ladder within an Odds_Value.

    Attributes:
        price: The ladder entry's price.
        size: The amount available at that price.
    """

    price: float
    size: float


@dataclass(frozen=True)
class StructuredOdds:
    """A parsed Odds_Value exposing best prices, depth, and matched volume.

    Produced by ``structured_odds`` from one stored Odds_Value. ``best_back`` is
    the highest ``availableToBack`` price and ``best_lay`` the lowest
    ``availableToLay`` price (Req 1.3, 1.4), each ``None`` when its ladder is
    empty (Req 1.6). The ``*_depth`` tuples carry every non-best entry for that
    side, preserving price+size and ordered best-to-worst (Req 1.5), and are
    empty when the ladder has no entries beyond the best (or is empty).
    ``matched_volume`` is the Matched_Volume signal, ``None`` when the blob
    carries no volume figure (Req 1.2).

    Attributes:
        raw: The original stored Odds_Value string, retained for an exact
            round-trip via ``serialize_structured`` (Req 1.9, 1.10).
        best_back: Highest available-to-back price, or ``None`` if that ladder
            is empty.
        best_lay: Lowest available-to-lay price, or ``None`` if that ladder is
            empty.
        back_depth: The non-best back entries, best-to-worst.
        lay_depth: The non-best lay entries, best-to-worst.
        matched_volume: The Matched_Volume figure, or ``None`` when absent.
    """

    raw: str
    best_back: float | None
    best_lay: float | None
    back_depth: tuple[PriceLevel, ...]
    lay_depth: tuple[PriceLevel, ...]
    matched_volume: float | None


# --- Runner identity & outcome role models (Req 2) ----------------------------


@dataclass(frozen=True)
class RunnerIdentity:
    """A ``(runner_id, runner_name)`` pair for one selection in a Match.

    Parsed from one pipe-delimited entry of a Target_Row ``runner_ids`` string
    by ``parse_runner_ids`` (Req 2.1, 2.2).

    Attributes:
        runner_id: The selection id (text before the first hyphen of the
            ``runner_ids`` entry).
        runner_name: The selection name (text after the first hyphen, including
            any further hyphens).
    """

    runner_id: str
    runner_name: str


@dataclass(frozen=True)
class OutcomeRoleAssignment:
    """The Outcome_Role assignment for one Match's runners (Req 2.5-2.7).

    Produced by ``assign_outcome_roles``. ``roles`` maps each ``runner_id`` to
    its assigned role (``"HOME"``, ``"DRAW"``, ``"AWAY"``) or ``None`` when the
    role could not be determined. ``undetermined`` is ``True`` when the Match
    could not be fully resolved -- i.e. it did not have exactly three runners
    with exactly one draw (Req 2.7).

    Attributes:
        roles: Mapping of ``runner_id`` to ``"HOME"``/``"DRAW"``/``"AWAY"`` or
            ``None`` when undetermined.
        undetermined: ``True`` when the Match's role assignment is incomplete.
    """

    roles: dict[str, str | None] = field(default_factory=dict)
    undetermined: bool = False


# --- Record & result containers (Req 3, 5) ------------------------------------


@dataclass(frozen=True)
class DatasetRecord:
    """One row of the Research_Dataset: one runner observation at one instant.

    A single captured observation of one Runner in one Match at one
    Capture_Timestamp, carrying the 12 Dataset_Schema columns (Req 5.6). Any
    field other than the ``market_id`` / ``runner_id`` grouping keys and the
    ``timestamp`` is ``None`` where the corresponding value is absent or
    undetermined for that observation (Req 3.2, 3.3, 3.8).

    Attributes:
        market_id: The Match's Betfair market id (grouping key, never null).
        runner_id: The Runner/selection id (grouping key, never null).
        runner_name: The Runner name from the Target, ``None`` when the
            ``runner_id`` is absent from the Target (Req 3.8).
        outcome_role: ``"HOME"``/``"DRAW"``/``"AWAY"`` or ``None`` when the role
            is undetermined (Req 2.7, 3.8).
        timestamp: The Capture_Timestamp of the observation (never null).
        best_back: Best (highest) available-to-back price, ``None`` when the
            back ladder is empty (Req 1.6).
        best_lay: Best (lowest) available-to-lay price, ``None`` when the lay
            ladder is empty (Req 1.6).
        matched_volume: The Matched_Volume signal, ``None`` when no volume
            figure is present (Req 1.2).
        match_phase: ``"PRE_MATCH"``/``"IN_PLAY"`` or ``None`` when the Match
            ``start_time`` is unknown (Req 8.3).
        event_id: The Target ``event_id``, ``None`` when absent (Req 3.3).
        start_time: The Match kick-off, ``None`` when absent on the Target
            (Req 3.3).
        target_id: The Target identity, ``None`` when absent (Req 3.3).
    """

    market_id: str
    runner_id: str
    runner_name: str | None
    outcome_role: str | None
    timestamp: datetime
    best_back: float | None
    best_lay: float | None
    matched_volume: float | None
    match_phase: str | None
    event_id: str | None
    start_time: datetime | None
    target_id: str | None


@dataclass(frozen=True)
class TransformResult:
    """The output of ``build_records``: records plus run-summary counts.

    Carries the produced Dataset_Records together with the two counts the
    Dataset_Reader reports to the operator: how many Market_Rows were excluded
    as unparseable (Req 3.5) and how many produced records had an unresolved
    Runner_Identity -- a ``runner_id`` with no matching identity on the Target
    (Req 3.8).

    Attributes:
        records: The produced Dataset_Records, ordered per Req 3.6.
        unparseable_excluded: Count of Market_Rows excluded as unparseable.
        unresolved_runners: Count of produced records with a null identity.
    """

    records: tuple[DatasetRecord, ...] = ()
    unparseable_excluded: int = 0
    unresolved_runners: int = 0


# --- Odds parsing (reuses logic/quality_checks.py) ----------------------------


def _valid_number(value: object) -> bool:
    """True iff ``value`` is a usable, in-range price or size (Req 1.8).

    A ladder entry's price/size is valid only when it is a real number -- an
    ``int`` or ``float``, but NOT a ``bool`` (``bool`` is an ``int`` subclass
    and is never a legitimate price/size) -- that is finite (no ``NaN`` and no
    infinity) and non-negative. Anything else (strings, ``None``, ``NaN``,
    infinities, negatives) is out-of-range and makes the whole Odds_Value
    unparseable per Req 1.8.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    return number >= 0.0


def structured_odds(raw: str) -> StructuredOdds | None:
    """Parse one stored Odds_Value into best-back/best-lay/depth/volume.

    Delegates the raw ``str(dict)`` decode to ``quality_checks.parse_odds`` (the
    single odds-reading path, Req 6.4), then derives ``best_back`` (max back
    price), ``best_lay`` (min lay price), the best-to-worst ``back_depth`` /
    ``lay_depth`` of the non-best entries, and ``matched_volume``.

    Returns ``None`` without raising when the value cannot be decoded into a
    mapping with both ladders (Req 1.7) or any ladder entry has a non-numeric,
    negative, or out-of-range price or size (Req 1.8). An empty ladder yields a
    null best price and an empty depth for that side (Req 1.6).

    Args:
        raw: The stored Odds_Value string (a Python dict ``repr``).

    Returns:
        The parsed :class:`StructuredOdds`, or ``None`` on any parse/validation
        failure.
    """
    # Single odds-reading path (Req 6.4): delegate the raw str(dict) decode and
    # both-ladders structural check to quality_checks.parse_odds. A None here is
    # the Req 1.7 "cannot be decoded into a mapping with both ladders" case.
    parsed = parse_odds(raw)
    if parsed is None:
        return None

    back_ladder = parsed["availableToBack"]
    lay_ladder = parsed["availableToLay"]

    # Req 1.8: any non-numeric, negative, or out-of-range price or size in
    # either ladder makes the whole value unparseable -> null, never raising.
    for entry in back_ladder:
        if not _valid_number(entry["price"]) or not _valid_number(entry["size"]):
            return None
    for entry in lay_ladder:
        if not _valid_number(entry["price"]) or not _valid_number(entry["size"]):
            return None

    best_back, back_depth = _best_and_depth(back_ladder, best_is_max=True)
    best_lay, lay_depth = _best_and_depth(lay_ladder, best_is_max=False)

    return StructuredOdds(
        raw=raw,
        best_back=best_back,
        best_lay=best_lay,
        back_depth=back_depth,
        lay_depth=lay_depth,
        matched_volume=_matched_volume(parsed),
    )


def _best_and_depth(ladder: list, best_is_max: bool) -> tuple[float | None, tuple[PriceLevel, ...]]:
    """Return ``(best_price, non_best_depth)`` for one Price_Ladder.

    ``best_is_max`` selects the best-price rule: the highest price is best for
    the ``availableToBack`` side (Req 1.3) and the lowest for ``availableToLay``
    (Req 1.4). The returned depth is every entry other than the single best one,
    ordered best-to-worst (Req 1.5) -- descending price for back, ascending for
    lay. An empty ladder yields a null best price and an empty depth (Req 1.6).
    Prices and sizes are coerced to ``float`` so an integer blob value surfaces
    as a numeric level.

    Args:
        ladder: A validated list of ``{price, size}`` entries for one side.
        best_is_max: ``True`` to treat the highest price as best (back side),
            ``False`` to treat the lowest as best (lay side).

    Returns:
        A ``(best_price_or_None, depth_tuple)`` pair.
    """
    if not ladder:
        return None, ()

    # Order best-to-worst so the first entry is the best and the rest are the
    # Depth. Computing max/min explicitly (rather than trusting Betfair's
    # best-first order) means a mis-ordered blob still yields the true best.
    ordered = sorted(ladder, key=lambda entry: entry["price"], reverse=best_is_max)
    best_price = float(ordered[0]["price"])
    depth = tuple(PriceLevel(price=float(entry["price"]), size=float(entry["size"])) for entry in ordered[1:])
    return best_price, depth


def _matched_volume(parsed: dict) -> float | None:
    """Derive the Matched_Volume from a parsed Odds_Value (Req 1.2).

    Reads a scalar ``totalMatched`` figure when the blob carries one, otherwise
    falls back to summing the ``size`` values of a ``tradedVolume`` ladder when
    present, otherwise returns ``None``. This mirrors the design decision that
    the captured blob -- not a separate settlement feed -- is the only
    Matched_Volume source in the odds-only scope. A ``totalMatched`` or
    ``tradedVolume`` size that is not a usable number is treated as absent.

    Args:
        parsed: A parsed Odds_Value dict (from ``quality_checks.parse_odds``).

    Returns:
        The Matched_Volume as a ``float``, or ``None`` when no usable figure is
        present.
    """
    total_matched = parsed.get("totalMatched")
    if _valid_number(total_matched):
        return float(total_matched)

    traded_volume = parsed.get("tradedVolume")
    if isinstance(traded_volume, list) and traded_volume:
        total = 0.0
        for entry in traded_volume:
            if not isinstance(entry, dict):
                return None
            size = entry.get("size")
            if not _valid_number(size):
                return None
            total += float(size)
        return total

    return None


def serialize_structured(odds: StructuredOdds) -> str:
    """Serialize a :class:`StructuredOdds` back to its stored string form.

    The round-trip partner of :func:`structured_odds` via
    ``quality_checks.serialize_odds`` (Req 1.9): for any valid stored
    Odds_Value, parsing then serializing reproduces a value whose prices, sizes,
    entry order, and Matched_Volume equal the original (Req 1.10).

    Args:
        odds: A :class:`StructuredOdds` produced by :func:`structured_odds`.

    Returns:
        The Odds_Value rendered as its stored ``str(dict)`` string form.
    """
    # The round-trip (Req 1.9, 1.10) rides the single serialize path in
    # quality_checks. StructuredOdds.raw retains the original stored string, so
    # re-decoding it and serializing reproduces a value whose prices, sizes,
    # entry order, and Matched_Volume equal the original -- the derived
    # best/depth fields are a view over that same blob, not a second source.
    return serialize_odds(parse_odds(odds.raw))


# --- Runner identity & outcome role (Req 2) -----------------------------------


def parse_runner_ids(runner_ids: str | None) -> list[RunnerIdentity]:
    """Parse a Target ``runner_ids`` string into ordered Runner_Identity pairs.

    Splits the pipe-delimited ``"id-name|id-name"`` string into one
    :class:`RunnerIdentity` per entry, splitting each entry on the FIRST hyphen
    only so a name containing further hyphens is preserved intact (Req 2.1,
    2.2). Preserves left-to-right order, drops malformed entries (no hyphen,
    empty id, or empty name) while retaining the parseable ones (Req 2.3), and
    returns an empty list for an absent/empty/no-parseable input, never raising
    (Req 2.4).

    Args:
        runner_ids: The raw ``runner_ids`` column value, or ``None``.

    Returns:
        The ordered list of parsed :class:`RunnerIdentity` pairs (possibly
        empty).
    """
    # Absent/empty input -> empty sequence, no raise (Req 2.4).
    if not runner_ids:
        return []

    identities: list[RunnerIdentity] = []
    for entry in runner_ids.split("|"):
        # Split on the FIRST hyphen only so a name with further hyphens is kept
        # intact (Req 2.2). An entry with no hyphen has no separator and is
        # malformed (Req 2.3).
        runner_id, separator, runner_name = entry.partition("-")
        if not separator:
            continue
        # Drop entries with an empty id or empty name segment, retaining the
        # other parseable entries (Req 2.3).
        if not runner_id or not runner_name:
            continue
        identities.append(RunnerIdentity(runner_id=runner_id, runner_name=runner_name))

    return identities


def assign_outcome_roles(runners: list[RunnerIdentity], draw_name: str = DEFAULT_DRAW_NAME) -> OutcomeRoleAssignment:
    """Assign HOME/DRAW/AWAY Outcome_Roles to a Match's runners (Req 2.5-2.7).

    DRAW is the runner whose trimmed ``runner_name`` case-sensitively equals the
    trimmed ``draw_name`` (Req 2.5). When the Match has exactly three runners
    with exactly one draw, HOME and AWAY go to the first and second non-draw
    runner in ``runner_ids`` order (Req 2.6). Otherwise every undetermined
    runner receives a null role and the assignment is flagged undetermined
    (Req 2.7). Never raises.

    Args:
        runners: The Match's :class:`RunnerIdentity` values in ``runner_ids``
            order.
        draw_name: The configured draw selection name (defaults to
            :data:`DEFAULT_DRAW_NAME`).

    Returns:
        The :class:`OutcomeRoleAssignment` mapping each ``runner_id`` to its
        role (or ``None``) with the ``undetermined`` flag set accordingly.
    """
    # Start with every runner undetermined; the resolved branch overwrites.
    roles: dict[str, str | None] = {runner.runner_id: None for runner in runners}

    # DRAW = runner whose trimmed name case-sensitively equals the trimmed
    # configured draw_name (Req 2.5). Compare in order; track the non-draw
    # runners (also in order) for the HOME/AWAY assignment.
    trimmed_draw = draw_name.strip()
    draw_runners: list[RunnerIdentity] = []
    non_draw_runners: list[RunnerIdentity] = []
    for runner in runners:
        if runner.runner_name.strip() == trimmed_draw:
            draw_runners.append(runner)
        else:
            non_draw_runners.append(runner)

    # Resolve HOME/DRAW/AWAY only for the exactly-three-runners / exactly-one-
    # draw case (Req 2.6). Any other shape leaves every runner null and flags
    # the match undetermined (Req 2.7).
    if len(runners) == 3 and len(draw_runners) == 1 and len(non_draw_runners) == 2:
        roles[draw_runners[0].runner_id] = "DRAW"
        roles[non_draw_runners[0].runner_id] = "HOME"
        roles[non_draw_runners[1].runner_id] = "AWAY"
        return OutcomeRoleAssignment(roles=roles, undetermined=False)

    return OutcomeRoleAssignment(roles=roles, undetermined=True)


# --- Phase (Req 8) ------------------------------------------------------------


def match_phase(capture_ts: datetime | None, start_time: datetime | None) -> str | None:
    """Classify a record as pre-match or in-play from its two timestamps.

    Returns ``"PRE_MATCH"`` when ``capture_ts`` is strictly earlier than
    ``start_time`` (Req 8.1), ``"IN_PLAY"`` when it is equal to or later
    (Req 8.2), and ``None`` when either value is unknown (Req 8.3). Derived
    solely from the two timestamps, using no match results or settlement data
    (Req 8.4). Never raises.

    Args:
        capture_ts: The Capture_Timestamp of the observation, or ``None``.
        start_time: The Match ``start_time``, or ``None``.

    Returns:
        ``"PRE_MATCH"``, ``"IN_PLAY"``, or ``None``.
    """
    # Either timestamp unknown -> null phase, no raise (Req 8.3).
    if capture_ts is None or start_time is None:
        return None
    # Strictly before kick-off is pre-match (Req 8.1); at/after is in-play
    # (Req 8.2). Derived solely from the two timestamps (Req 8.4).
    if capture_ts < start_time:
        return "PRE_MATCH"
    return "IN_PLAY"


# --- Record assembly (the top-level pure entry point, Req 3) ------------------


def build_records(
    market_rows: Iterable[MarketRow],
    targets_by_market: Mapping[str, TargetRow],
    draw_name: str = DEFAULT_DRAW_NAME,
) -> TransformResult:
    """Produce the full set of Dataset_Records across all supplied matches.

    Pure: uses only its inputs, performing no database, filesystem, or network
    access (Req 3.7). Produces exactly one Dataset_Record per distinct
    ``(market_id, runner_id, capture_ts)`` that parses (Req 3.1); populates
    every schema field, nulling where the value is absent or undetermined
    (Req 3.2, 3.3). Excludes unparseable Market_Rows while counting them
    (Req 3.5); still emits a record with a null identity and null role for a
    ``runner_id`` absent from the Target while counting it (Req 3.8); and orders
    each runner's records by ascending ``capture_ts``, breaking ties by
    ascending ``runner_id`` so identical inputs yield identical output
    (Req 3.6).

    Args:
        market_rows: The Market_Rows to shape, as ``(timestamp, market_id,
            runner_id, odds)`` tuples.
        targets_by_market: The Target metadata keyed by ``market_id``.
        draw_name: The configured draw selection name (defaults to
            :data:`DEFAULT_DRAW_NAME`).

    Returns:
        A :class:`TransformResult` carrying the ordered records plus the
        unparseable-excluded and unresolved-runner counts.
    """
    # Per-market lookups derived once from each Target: runner_id -> runner_name
    # and runner_id -> outcome_role, plus the Target's own metadata (event_id,
    # start_time, target_id). A market with no Target yields empty lookups and
    # null metadata, so its rows still produce records with null identity
    # (counted as unresolved) and null Target fields (Req 3.3, 3.8).
    name_by_market: dict[str, dict[str, str]] = {}
    role_by_market: dict[str, dict[str, str | None]] = {}
    meta_by_market: dict[str, tuple[str | None, datetime | None, str | None]] = {}

    def _market_lookups(
        market_id: str,
    ) -> tuple[dict[str, str], dict[str, str | None], tuple[str | None, datetime | None, str | None]]:
        """Resolve (and memoise) the per-market name/role lookups + metadata."""
        cached = name_by_market.get(market_id)
        if cached is not None:
            return cached, role_by_market[market_id], meta_by_market[market_id]

        target = targets_by_market.get(market_id)
        if target is None:
            names: dict[str, str] = {}
            roles: dict[str, str | None] = {}
            meta: tuple[str | None, datetime | None, str | None] = (None, None, None)
        else:
            # TargetRow = (target_id, event_id, market_id, runner_ids,
            #              start_time, status, last_updated, notes).
            target_id, event_id = target[0], target[1]
            runner_ids, start_time = target[3], target[4]
            identities = parse_runner_ids(runner_ids)
            names = {identity.runner_id: identity.runner_name for identity in identities}
            roles = assign_outcome_roles(identities, draw_name).roles
            meta = (event_id, start_time, target_id)

        name_by_market[market_id] = names
        role_by_market[market_id] = roles
        meta_by_market[market_id] = meta
        return names, roles, meta

    records: list[DatasetRecord] = []
    unparseable_excluded = 0
    unresolved_runners = 0

    for timestamp, market_id, runner_id, odds in market_rows:
        # Exclude unparseable rows while counting them (Req 3.5); then continue
        # producing records for the remaining rows.
        odds = structured_odds(odds)
        if odds is None:
            unparseable_excluded += 1
            continue

        names, roles, (event_id, start_time, target_id) = _market_lookups(market_id)

        # A runner_id absent from the Target has null identity + null role and
        # is counted as unresolved (Req 3.8). Present runners take the Target's
        # name and the derived role (which may itself be null when the Match's
        # roles were undetermined, Req 2.7).
        if runner_id in names:
            runner_name: str | None = names[runner_id]
            outcome_role = roles.get(runner_id)
        else:
            runner_name = None
            outcome_role = None
            unresolved_runners += 1

        records.append(
            DatasetRecord(
                market_id=market_id,
                runner_id=runner_id,
                runner_name=runner_name,
                outcome_role=outcome_role,
                timestamp=timestamp,
                best_back=odds.best_back,
                best_lay=odds.best_lay,
                matched_volume=odds.matched_volume,
                match_phase=match_phase(timestamp, start_time),
                event_id=event_id,
                start_time=start_time,
                target_id=target_id,
            )
        )

    # Deterministic ordering (Req 3.6): group by market, then within each runner
    # order by ascending capture_ts, breaking ties by ascending runner_id. A
    # single stable sort on (market_id, timestamp, runner_id) satisfies the
    # per-runner ascending-timestamp / runner_id tie-break and yields identical
    # output for identical input.
    records.sort(key=lambda record: (record.market_id, record.timestamp, record.runner_id))

    return TransformResult(
        records=tuple(records),
        unparseable_excluded=unparseable_excluded,
        unresolved_runners=unresolved_runners,
    )


# --- DataFrame schema layer (Req 5) -------------------------------------------


def empty_dataset() -> pd.DataFrame:
    """Build the typed-but-empty Research_Dataset frame (Req 5.2, 5.6).

    Returns a zero-row :class:`pandas.DataFrame` carrying exactly the 12
    :data:`DATASET_SCHEMA` columns, in the declared order and with the declared
    pandas dtypes (nullable ``string`` / ``Float64`` and UTC-aware
    ``datetime64[ns, UTC]`` for the two timestamp columns). This is the shared
    definition used by the zero-record path (Req 5.2) and the schema
    documentation, so an empty run still carries the full typed schema and no
    additional columns (Req 5.6).

    Returns:
        An empty 12-column :class:`pandas.DataFrame` with the declared dtypes.
    """
    # Build each column as an empty, correctly-typed Series so the frame carries
    # the declared dtype even with zero rows (an empty object column would not).
    columns = {name: pd.Series([], dtype=dtype) for name, dtype in DATASET_SCHEMA}
    return pd.DataFrame(columns)


def records_to_dataframe(result: TransformResult) -> pd.DataFrame:
    """Assemble a TransformResult's records into the Research_Dataset frame.

    Turns the :class:`DatasetRecord` values of ``result`` into a
    :class:`pandas.DataFrame` carrying exactly the 12 :data:`DATASET_SCHEMA`
    columns, in the declared order and coerced to the declared dtypes, with no
    additional columns (Req 5.1, 5.6). When ``result`` has no records the typed
    empty frame from :func:`empty_dataset` is returned so the zero-record case
    still carries the full schema (Req 5.2). The two timestamp columns are
    coerced to UTC-aware ``datetime64[ns, UTC]``.

    Args:
        result: The :class:`TransformResult` produced by :func:`build_records`.

    Returns:
        A 12-column :class:`pandas.DataFrame` in the declared order/dtypes.
    """
    # Zero-record runs still carry the full typed schema (Req 5.2).
    if not result.records:
        return empty_dataset()

    # Project each record onto exactly the schema columns, in declared order, so
    # no additional columns can leak in (Req 5.6).
    data = {name: [getattr(record, name) for record in result.records] for name, _dtype in DATASET_SCHEMA}
    frame = pd.DataFrame(data, columns=[name for name, _dtype in DATASET_SCHEMA])

    # Coerce every column to its declared dtype. The two timestamp columns need
    # pd.to_datetime(..., utc=True) rather than a plain astype: pandas refuses a
    # naive -> datetime64[ns, UTC] astype, whereas to_datetime localises naive
    # values to UTC and converts offset-aware values to UTC (Req 5.2). The
    # remaining nullable string / Float64 columns carry Python None as NA.
    for name, dtype in DATASET_SCHEMA:
        if dtype == "datetime64[ns, UTC]":
            frame[name] = pd.to_datetime(frame[name], utc=True)
        else:
            frame[name] = frame[name].astype(dtype)

    return frame
