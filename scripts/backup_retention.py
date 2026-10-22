"""Pure keep-last-3 retention selection for Pi Postgres backups (SP-363).

Implements the Retention_Step selection logic from the pi-postgres-backup-automation
design: given the Dump_Artifacts on the Backup_Share (`/home/mypi/bf_backups`),
decide which ones to DELETE so that only the newest 3 remain.

This module is intentionally PURE: it performs no filesystem deletes and no
process calls. It only inspects filenames and their mtimes and returns the set of
filenames to delete. Keeping it pure makes the keep-last-3 ordering unit-testable
and property-testable in isolation (see tasks 1.2-1.5); the thin delete-executor
that actually removes files lives separately (task 2.1).

A Dump_Artifact is named ``bf_trader_<UTC-timestamp>.dump`` where the timestamp is
a fixed-width, lexicographically sortable UTC representation ``YYYYMMDDThhmmssZ``
(e.g. ``bf_trader_20260115T030000Z.dump``). Selection:

* Only files matching that exact pattern are ever considered; unrelated files on
  the share are never selected for deletion (Req 3.5).
* Artifacts are ordered DESCENDING by their embedded UTC timestamp (Req 3.1).
* When the embedded timestamp cannot be parsed, the artifact is ordered by its
  file mtime instead (Req 3.3).
* Ties (equal timestamp AND mtime) are broken by DESCENDING lexicographic
  filename order, so the selection is deterministic (Req 3.4).
* When 3 or fewer matching artifacts exist, zero deletions are returned (Req 3.6).
"""

import os
import re
import sys
from datetime import UTC, datetime

# The number of most-recent Dump_Artifacts to retain on the Backup_Share.
KEEP_COUNT = 3

# Default Backup_Share path on the Pi (SD-card root, OFF the USB disk). The
# delete-executor operates on this directory unless an explicit path is given.
DEFAULT_BACKUP_SHARE = "/home/mypi/bf_backups"

# A Dump_Artifact is ``bf_trader_<UTC-timestamp>.dump`` with the timestamp in the
# fixed-width, lexicographically sortable form ``YYYYMMDDThhmmssZ``. The pattern
# is anchored and specific so it never matches unrelated files on the share.
_DUMP_NAME_RE = re.compile(r"^bf_trader_(\d{8}T\d{6}Z)\.dump$")
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def is_dump_artifact(filename: str) -> bool:
    """Return True if ``filename`` matches the Dump_Artifact naming pattern.

    Only ``bf_trader_<UTC-timestamp>.dump`` names (with a fixed-width
    ``YYYYMMDDThhmmssZ`` timestamp) match; any other file on the Backup_Share is
    treated as unrelated and is never selected for deletion.

    Validates: Requirements 3.5, 1.2

    Args:
        filename: A bare filename (no directory component) on the Backup_Share.

    Returns:
        True when ``filename`` is a Dump_Artifact name, False otherwise.
    """
    return _DUMP_NAME_RE.match(filename) is not None


def parse_embedded_timestamp(filename: str) -> datetime | None:
    """Return the embedded UTC timestamp of a Dump_Artifact, or None.

    Parses the ``YYYYMMDDThhmmssZ`` component of a ``bf_trader_<ts>.dump`` name
    into a timezone-aware UTC datetime. Returns None when ``filename`` is not a
    Dump_Artifact or the embedded timestamp is not a valid calendar date/time
    (e.g. ``bf_trader_20260230T000000Z.dump``), so callers fall back to mtime.

    Validates: Requirements 1.2, 3.3

    Args:
        filename: A bare filename on the Backup_Share.

    Returns:
        The parsed timezone-aware UTC datetime, or None if it cannot be parsed.
    """
    match = _DUMP_NAME_RE.match(filename)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group(1), _TIMESTAMP_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


def select_dumps_to_delete(
    artifacts: list[tuple[str, float]],
    keep: int = KEEP_COUNT,
) -> set[str]:
    """Select which Dump_Artifacts to delete to leave the newest ``keep``.

    Given the files on the Backup_Share as ``(filename, mtime)`` pairs, returns
    the set of filenames to DELETE so that only the ``keep`` most recent
    Dump_Artifacts remain. The function is pure: it deletes nothing and only
    inspects the names and mtimes it is given.

    Non-matching files (anything not matching ``bf_trader_<UTC-timestamp>.dump``)
    are ignored entirely and are never returned for deletion (Req 3.5). Matching
    artifacts are ordered newest-first by embedded UTC timestamp (Req 3.1),
    falling back to file mtime when the timestamp cannot be parsed (Req 3.3), and
    ties are broken by descending lexicographic filename order so the result is
    deterministic (Req 3.4). When ``keep`` or fewer matching artifacts exist, the
    returned set is empty (Req 3.6).

    Validates: Requirements 3.1, 3.3, 3.4, 3.5, 3.6, 1.2

    Args:
        artifacts: The files on the Backup_Share as ``(filename, mtime)`` pairs,
            where ``mtime`` is the file modification time in epoch seconds. Only
            the bare filename is used for pattern matching and ordering.
        keep: The number of most-recent Dump_Artifacts to retain (default 3).

    Returns:
        The set of Dump_Artifact filenames to delete. Empty when there are
        ``keep`` or fewer matching artifacts.
    """
    matching = [(name, mtime) for name, mtime in artifacts if is_dump_artifact(name)]

    if len(matching) <= keep:
        return set()

    ordered = sorted(matching, key=_ordering_key, reverse=True)
    return {name for name, _ in ordered[keep:]}


def _ordering_key(artifact: tuple[str, float]) -> tuple[datetime, float, str]:
    """Build the newest-first sort key for a matching Dump_Artifact.

    The key orders by embedded UTC timestamp first, falling back to file mtime
    when the timestamp cannot be parsed (Req 3.3), then by filename to break ties
    deterministically (Req 3.4). The whole key is sorted DESCENDING by the caller,
    which yields: newest timestamp first, then newest mtime, then descending
    lexicographic filename.

    Args:
        artifact: A ``(filename, mtime)`` pair for a matching Dump_Artifact.

    Returns:
        A ``(timestamp, mtime, filename)`` tuple suitable for a reverse sort.
    """
    name, mtime = artifact
    embedded = parse_embedded_timestamp(name)
    # When the embedded timestamp is unparseable, order purely by mtime by using
    # a minimal timestamp so parsed-timestamp artifacts always sort ahead of
    # unparseable ones of the same era; mtime then discriminates among them.
    timestamp = embedded if embedded is not None else datetime.min.replace(tzinfo=UTC)
    return (timestamp, mtime, name)


def prune_backup_share(
    backup_share: str = DEFAULT_BACKUP_SHARE,
    keep: int = KEEP_COUNT,
) -> dict:
    """Delete old Dump_Artifacts on a real Backup_Share, keeping the newest ``keep``.

    This is the thin IO executor wrapped around the pure ``select_dumps_to_delete``
    selection. It lists ``backup_share``, builds ``(filename, mtime)`` pairs for
    the entries it finds, asks the pure function which Dump_Artifacts to delete,
    and then removes exactly those files from disk. The pure selection stays pure;
    all filesystem access lives here.

    Deletion is best-effort and never aborts the preserved set: every selected
    file is attempted, and if one delete raises, the error is recorded and the
    remaining deletions still proceed. The retained set (everything the pure
    function did NOT select) is therefore always left intact, and any artifact
    that failed to delete is surfaced by name so the caller can report it (Req 3.7).

    Validates: Requirements 3.7, 3.1, 3.5, 3.6

    Args:
        backup_share: Path to the Backup_Share directory to prune. Defaults to the
            Pi's off-USB share ``/home/mypi/bf_backups``.
        keep: The number of most-recent Dump_Artifacts to retain (default 3).

    Returns:
        A result dict:
            deleted (list[str]): filenames successfully deleted (sorted).
            failed (list[tuple[str, str]]): ``(filename, error_message)`` pairs for
                selected files that could not be deleted (sorted by filename).
            selected (list[str]): all filenames the pure function chose to delete.
            error (str | None): operator-facing summary when any delete failed, or
                when the share could not be listed; None on a clean run.
    """
    result = {
        "deleted": [],
        "failed": [],
        "selected": [],
        "error": None,
    }

    try:
        entries = os.listdir(backup_share)
    except OSError as error:
        result["error"] = f"Could not list Backup_Share '{backup_share}': {error}"
        return result

    artifacts: list[tuple[str, float]] = []
    for name in entries:
        full_path = os.path.join(backup_share, name)
        try:
            if not os.path.isfile(full_path):
                continue
            mtime = os.path.getmtime(full_path)
        except OSError:
            # Could not stat this entry (e.g. it vanished); it cannot be a
            # selectable Dump_Artifact we can act on, so skip it.
            continue
        artifacts.append((name, mtime))

    to_delete = select_dumps_to_delete(artifacts, keep=keep)
    result["selected"] = sorted(to_delete)

    for name in result["selected"]:
        full_path = os.path.join(backup_share, name)
        try:
            os.remove(full_path)
            result["deleted"].append(name)
        except OSError as error:
            # Record the failure and keep going so one stuck file never prevents
            # the other old dumps from being pruned, and never touches the
            # retained set (Req 3.7).
            result["failed"].append((name, str(error)))

    result["deleted"].sort()
    result["failed"].sort(key=lambda item: item[0])

    if result["failed"]:
        failed_names = ", ".join(name for name, _ in result["failed"])
        result["error"] = f"Failed to delete {len(result['failed'])} Dump_Artifact(s): {failed_names}"

    return result


def main() -> int:
    """CLI entry point so the Pi backup script can invoke retention via venv Python.

    Prunes the Backup_Share (first CLI arg, or ``DEFAULT_BACKUP_SHARE``) to the
    newest ``KEEP_COUNT`` Dump_Artifacts. Prints what was deleted/preserved to
    stdout, prints any delete failures to stderr naming the offending artifacts,
    and returns a non-zero exit code when the share could not be listed or any
    selected delete failed — without having aborted the other deletions (Req 3.7).

    Returns:
        0 on a clean run; 1 when any delete failed or the share was unreadable.
    """
    backup_share = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BACKUP_SHARE
    result = prune_backup_share(backup_share)

    print(f"Retention run against Backup_Share: {backup_share}")
    print(f"  selected for deletion : {len(result['selected'])}")
    print(f"  deleted               : {len(result['deleted'])}")
    for name in result["deleted"]:
        print(f"    - {name}")

    if result["error"] is not None:
        print(result["error"], file=sys.stderr)
        for name, message in result["failed"]:
            print(f"  FAILED to delete {name}: {message}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
