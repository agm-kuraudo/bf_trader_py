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
