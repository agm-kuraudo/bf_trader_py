"""Pure-logic checks for the season-background-data-capture deploy pipeline.

This module intentionally contains no I/O: no ``os``, no ``psycopg2``, no file
or network access. It holds only pure functions so the deploy/verify logic can
be property-tested deterministically (see the season-background-data-capture
design, "Pure logic extracted for testability").
"""

from datetime import datetime


def validate_env(values: dict, required: list) -> list[str]:
    """Return the required keys that are missing or effectively empty.

    A required key is reported when it is absent from ``values`` or when its
    value is ``None``, an empty string, or whitespace-only. The order of the
    ``required`` list is preserved in the result. The result is empty if and
    only if every required key is present with a non-empty value.

    Validates: Requirements 1.3, 7.8

    Args:
        values: Mapping of configuration keys to their values.
        required: Ordered list of keys that must be present and non-empty.

    Returns:
        The subset of ``required`` (in order) that is missing or empty.
    """
    missing: list[str] = []
    for key in required:
        if key not in values:
            missing.append(key)
            continue
        value = values[key]
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(key)
    return missing


def missing_tables(present: set, required: set) -> set:
    """Return the required tables that are not present (set difference).

    The result equals ``required - present`` and is empty if and only if
    ``required`` is a subset of ``present``. Only tables in this result should
    be created; tables already present are never re-created.

    Validates: Requirements 1.4, 1.5

    Args:
        present: Set of table names that already exist in the data store.
        required: Set of table names required for odds capture.

    Returns:
        The set of required table names absent from ``present``.
    """
    return required - present


def freshness(
    now: datetime,
    last_record_ts: datetime | None,
    threshold_s: float,
) -> dict:
    """Decide whether captured odds are stale and report elapsed time.

    Computes the elapsed time since the most recently stored odds record and
    whether that elapsed time exceeds the freshness threshold (a stall).

    When ``last_record_ts`` is provided, ``elapsed_s`` is
    ``(now - last_record_ts).total_seconds()``. Given ``last_record_ts <= now``
    this is ``>= 0``, and ``stalled`` is ``True`` when ``elapsed_s`` exceeds
    ``threshold_s``.

    When ``last_record_ts`` is ``None`` (the data store contains no odds
    records), there is no elapsed time to report, so ``elapsed_s`` is ``None``
    (the documented sentinel for "no records") and ``stalled`` is ``True`` --
    an empty store is always treated as a stall.

    Validates: Requirements 5.2, 5.3, 5.5

    Args:
        now: The current time (timezone-aware datetime).
        last_record_ts: Timestamp of the most recently stored odds record
            (timezone-aware datetime), or ``None`` when no records exist.
        threshold_s: The freshness threshold in seconds; elapsed time strictly
            greater than this is considered a stall.

    Returns:
        A dict with keys:
            ``elapsed_s``: float seconds since ``last_record_ts``, or ``None``
                when ``last_record_ts`` is ``None``.
            ``stalled``: bool, ``True`` when stale or when no records exist.
    """
    if last_record_ts is None:
        return {"elapsed_s": None, "stalled": True}

    elapsed_s = (now - last_record_ts).total_seconds()
    return {"elapsed_s": elapsed_s, "stalled": elapsed_s > threshold_s}


def expected_freshness_threshold(
    update_frequencies: list,
    grace_s: float,
    default_s: float,
) -> float | None:
    """Derive the freshness stall threshold from the active capture cadence.

    The capture cadence is tiered by time-to-event (IN_PLAY 5s ... MORE_THAN_12H
    14400s), so a fixed threshold cannot fit every situation: when the nearest
    match is days away, targets legitimately update only every ~4 hours, and a
    flat 15-minute threshold would fire constant false stalls. This function
    instead derives the threshold from the TIGHTEST active cadence -- the
    minimum ``update_frequency`` among currently-due targets -- plus a grace
    margin for scheduling jitter and run duration. The tightest cadence is used
    because that is the soonest a new odds record should be expected: if the
    fastest-updating target has not landed within its interval (+ grace), that
    is a genuine stall.

    When there are no active targets (``update_frequencies`` is empty), there is
    nothing that SHOULD be landing, so there is no meaningful stall: the function
    returns ``None`` to signal "do not raise a staleness alert".

    This refines the literal 15-minute figure in Req 5.1/5.3 into a
    cadence-aware threshold (design decision recorded in the design doc), so the
    check stays sharp near kick-off and quiet when everything is hours out.

    Args:
        update_frequencies: ``update_frequency`` (seconds) of each currently
            active/due target. Empty when no targets are due.
        grace_s: Extra seconds added on top of the tightest cadence to allow for
            scheduling jitter and run duration.
        default_s: Fallback threshold (seconds) if every provided frequency is
            non-positive/invalid but the list is non-empty.

    Returns:
        The threshold in seconds (tightest positive cadence + ``grace_s``), or
        ``None`` when there are no active targets (no staleness alert should be
        raised).
    """
    valid = [f for f in update_frequencies if isinstance(f, int | float) and f > 0]
    if not update_frequencies:
        return None
    if not valid:
        return default_s + grace_s
    return min(valid) + grace_s


# Ordered deploy steps for the SP-328 build/deploy pipeline (scripts/deploy.sh).
# The container is only ever replaced by the ``build_recreate`` step, so a
# failure at or before that step leaves the last known-good container running.
DEPLOY_STEPS = ("sync", "validate_env", "build_recreate", "verify")


def deploy_outcome(steps_results: list) -> dict:
    """Decide the deploy outcome from an ordered list of step results.

    Models the orchestration in ``scripts/deploy.sh``: the deploy runs the
    steps in the fixed order ``sync -> validate_env -> build_recreate ->
    verify``. Steps run one at a time; as soon as a step fails, no later step
    runs (short-circuit). The running capture container is only ever replaced
    by the ``build_recreate`` step, so the container is considered *changed*
    only when ``build_recreate`` both ran and succeeded. Any failure at or
    before ``build_recreate`` therefore leaves the last known-good container
    unchanged.

    This is the pure decision logic behind Property 5 (deploy atomicity); it
    contains no I/O so it can be property-tested deterministically.

    Validates: Requirements 7.7, 7.8, 7.9

    Args:
        steps_results: Ordered list of booleans, one per attempted step, in
            ``DEPLOY_STEPS`` order. ``True`` means the step succeeded, ``False``
            means it failed. The list may be shorter than ``DEPLOY_STEPS`` (only
            the steps that were actually attempted are included); it must not be
            longer, and no step after a failed step should be present.

    Returns:
        A dict with keys:
            ``failed_step``: name of the first failed step, or ``None`` when
                every attempted step succeeded.
            ``ran_steps``: list of step names that were attempted, in order.
            ``container_changed``: ``True`` only when ``build_recreate`` ran and
                succeeded; ``False`` otherwise (so any failure at or before
                ``build_recreate`` leaves the container unchanged).
            ``success``: ``True`` when all four steps ran and succeeded.
    """
    ran_steps: list[str] = []
    failed_step: str | None = None
    build_recreate_succeeded = False

    # strict=False: steps_results may be shorter than DEPLOY_STEPS when the
    # deploy short-circuited on an early failure (only attempted steps included).
    for name, succeeded in zip(DEPLOY_STEPS, steps_results, strict=False):
        ran_steps.append(name)
        if not succeeded:
            failed_step = name
            break
        if name == "build_recreate":
            build_recreate_succeeded = True

    success = failed_step is None and len(ran_steps) == len(DEPLOY_STEPS)

    return {
        "failed_step": failed_step,
        "ran_steps": ran_steps,
        "container_changed": build_recreate_succeeded,
        "success": success,
    }
