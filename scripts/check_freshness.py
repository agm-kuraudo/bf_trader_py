"""Recurring data-freshness check for Betfair capture (SP-328, Req 5).

Confirms captured odds are actually landing in the Data_Store. Intended to be
scheduled by Rundeck at a fixed interval no greater than 15 minutes (Req 5.1).

Behaviour:

* Queries MAX("timestamp") from bf.market_table (the freshness signal).
* Reports the most recent record timestamp and elapsed seconds since it, using
  the pure logic.deploy_checks.freshness function (Req 5.2).
* Raises a STALL alert when elapsed exceeds the 15-minute threshold (Req 5.3),
  or when the store contains no odds records at all (Req 5.5).
* On an unreachable store, raises an UNREACHABLE alert and retains the timestamp
  of the last successful check in a small state file (Req 5.4).

Reuses the same .env/psycopg2 approach as verify_db.py. The pure freshness
decision is cross-platform and property-tested (Property 1); this thin wrapper
does the I/O and is expected to run against my_postgres on the Pi (Linux/ARM).
"""

import json
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from logic.deploy_checks import expected_freshness_threshold, freshness, validate_env
from output.log import Output as Log

REQUIRED_DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]
CONNECT_TIMEOUT_S = 10

# Cadence-aware freshness (refines the literal 15-min figure in Req 5.1/5.3):
# the stall threshold is derived from the TIGHTEST active capture cadence rather
# than a fixed 15 minutes, because targets far from kick-off legitimately update
# only every ~4 hours (MORE_THAN_12H tier = 14400s). A flat 15-min threshold
# fired constant false stalls when nothing was near kick-off. See the design
# doc "Freshness threshold decision".
#
# GRACE_S is added on top of the tightest cadence to allow for scheduling jitter
# and run duration. DEFAULT_THRESHOLD_S is a fallback if target frequencies are
# present but invalid. When there are NO active targets, no staleness alert is
# raised (nothing should be landing).
GRACE_S = 5 * 60          # 5 minutes of slack over the expected cadence
DEFAULT_THRESHOLD_S = 15 * 60
# Target statuses that mean "capture should be actively polling this target".
ACTIVE_TARGET_STATUSES = ("OPEN", "IDENTIFIED")

# The data source this check covers (named in alerts, Req 5.3).
DATA_SOURCE = "bf.market_table"

# Where the last successful check timestamp is retained across runs (Req 5.4),
# so an unreachable run can still report when the store was last seen healthy.
STATE_FILE = os.path.join(os.path.dirname(__file__), ".freshness_state.json")


def _read_db_config(env_path: str = None) -> dict:
    """Read the required DB keys from .env via DotenvLoader (see verify_db.py)."""
    loader = DotenvLoader(env_path)
    config = {}
    for key in REQUIRED_DB_KEYS:
        try:
            config[key] = loader.get_secret(key)
        except ConfigurationException:
            config[key] = ""
    return config


def _load_last_successful_check(state_path: str = STATE_FILE) -> str | None:
    """Return the ISO timestamp of the last successful check, or None."""
    try:
        with open(state_path, encoding="utf-8") as fh:
            return json.load(fh).get("last_successful_check")
    except (OSError, ValueError):
        return None


def _save_last_successful_check(when: datetime, state_path: str = STATE_FILE) -> None:
    """Persist the timestamp of a successful check (best-effort)."""
    try:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump({"last_successful_check": when.isoformat()}, fh)
    except OSError as error:
        Log.log_warning(f"Could not persist freshness state: {error}")


def check_freshness(env_path: str = None, now: datetime = None,
                    threshold_s: float = None, state_path: str = STATE_FILE) -> dict:
    """Check whether captured odds are fresh; raise alerts on stall/unreachable.

    The stall threshold is CADENCE-AWARE (refines the literal 15-min figure in
    Req 5.1/5.3): unless an explicit ``threshold_s`` is supplied (mainly for
    tests), it is derived from the tightest ``update_frequency`` among active
    targets plus ``GRACE_S``. When no targets are active, no staleness alert is
    raised (nothing should be landing).

    Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5

    Args:
        env_path: Optional explicit .env path (mainly for tests).
        now: Current time (timezone-aware). Defaults to datetime.now(UTC).
        threshold_s: Explicit freshness threshold in seconds. When None (default)
            the threshold is derived from the active capture cadence.
        state_path: Path to the last-successful-check state file.

    Returns:
        A dict:
            reachable (bool)
            last_record_ts (str | None): ISO timestamp of most recent odds row.
            elapsed_s (float | None): seconds since last record (None if none).
            stalled (bool): True when stale, empty, or unreachable.
            alert (str | None): operator-facing alert message, or None if fresh.
            data_source (str): the source this check covers.
            last_successful_check (str | None): retained from a prior healthy run
                (only meaningful on the unreachable path, Req 5.4).
            error (str | None)
    """
    if now is None:
        now = datetime.now(UTC)

    result = {
        "reachable": False,
        "last_record_ts": None,
        "elapsed_s": None,
        "stalled": True,
        "alert": None,
        "data_source": DATA_SOURCE,
        "last_successful_check": None,
        "threshold_s": None,
        "active_targets": None,
        "idle": False,
        "error": None,
    }

    # Config check: do not connect if required keys are missing.
    try:
        config = _read_db_config(env_path)
    except ConfigurationException as error:
        result["error"] = f"Could not load .env configuration: {error}"
        result["alert"] = f"STALL/UNREACHABLE ({DATA_SOURCE}): {result['error']}"
        return result

    missing = validate_env(config, REQUIRED_DB_KEYS)
    if missing:
        result["error"] = "Missing required DB connection details in .env: " + ", ".join(missing)
        result["alert"] = f"UNREACHABLE ({DATA_SOURCE}): {result['error']}"
        return result

    # Reachability + query. On failure -> unreachable alert, retain last check.
    conn = None
    try:
        conn = psycopg2.connect(
            host=config["DB_HOST"],
            port=config["DB_PORT"],
            dbname=config["DB_NAME"],
            user=config["DB_USER"],
            password=config["DB_PWD"],
            connect_timeout=CONNECT_TIMEOUT_S,
        )
        conn.autocommit = True
        result["reachable"] = True
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(error)
        last_ok = _load_last_successful_check(state_path)
        result["last_successful_check"] = last_ok
        result["error"] = f"Data store unreachable within {CONNECT_TIMEOUT_S}s: {error}"
        result["alert"] = (
            f"UNREACHABLE ({DATA_SOURCE}): store not reachable; "
            f"last successful check: {last_ok or 'never'}"
        )
        return result

    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT MAX("timestamp") FROM bf.market_table')
            last_record_ts = cursor.fetchone()[0]
            # Active targets' cadence drives the expected freshness (Req 5.1/5.3
            # refinement): the tightest update_frequency is the soonest a new
            # odds record should be expected.
            cursor.execute(
                "SELECT update_frequency FROM bf.target WHERE status = ANY(%s)",
                (list(ACTIVE_TARGET_STATUSES),),
            )
            active_frequencies = [row[0] for row in cursor.fetchall() if row[0] is not None]
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(error)
        result["error"] = f"Freshness query failed: {error}"
        result["alert"] = f"UNREACHABLE ({DATA_SOURCE}): {result['error']}"
        return result
    finally:
        conn.close()

    # We reached and queried the store successfully -> record this check.
    _save_last_successful_check(now, state_path)

    # Derive the cadence-aware threshold unless one was explicitly supplied.
    effective_threshold = threshold_s
    if effective_threshold is None:
        effective_threshold = expected_freshness_threshold(
            active_frequencies, grace_s=GRACE_S, default_s=DEFAULT_THRESHOLD_S
        )

    result["threshold_s"] = effective_threshold
    result["active_targets"] = len(active_frequencies)

    # No active targets -> nothing SHOULD be landing, so no staleness/empty alert
    # (idle is not a stall). Still report the elapsed time for visibility.
    if effective_threshold is None:
        if last_record_ts is not None:
            result["elapsed_s"] = (now - last_record_ts).total_seconds()
        result["last_record_ts"] = last_record_ts.isoformat() if last_record_ts is not None else None
        result["stalled"] = False
        result["alert"] = None
        result["idle"] = True
        return result

    result["idle"] = False

    # Pure freshness decision (Property 1) against the cadence-aware threshold.
    decision = freshness(now, last_record_ts, effective_threshold)
    result["last_record_ts"] = last_record_ts.isoformat() if last_record_ts is not None else None
    result["elapsed_s"] = decision["elapsed_s"]
    result["stalled"] = decision["stalled"]

    if last_record_ts is None:
        # Active targets exist but no odds have ever landed (Req 5.5).
        result["alert"] = (
            f"STALL ({DATA_SOURCE}): no captured odds present despite "
            f"{len(active_frequencies)} active target(s)."
        )
    elif decision["stalled"]:
        # Elapsed exceeds the expected-cadence threshold (Req 5.3).
        mins = decision["elapsed_s"] / 60.0
        result["alert"] = (
            f"STALL ({DATA_SOURCE}): last odds record was {decision['elapsed_s']:.0f}s "
            f"({mins:.1f} min) ago, exceeding the expected-cadence threshold of "
            f"{effective_threshold / 60:.0f} min (tightest active cadence + grace)."
        )
    # else: fresh -> alert stays None.

    return result


def main() -> int:
    """CLI entry: print the freshness result and return an exit code.

    Returns non-zero when a stall/unreachable alert is raised, so the Rundeck
    freshness job surfaces the failure in its run status.
    """
    result = check_freshness()

    print("Data freshness check result:")
    print(f"  data_source           : {result['data_source']}")
    print(f"  reachable             : {result['reachable']}")
    print(f"  last_record_ts        : {result['last_record_ts']}")
    print(f"  elapsed_s             : {result['elapsed_s']}")
    print(f"  stalled               : {result['stalled']}")
    print(f"  threshold_s           : {result['threshold_s']}")
    print(f"  active_targets        : {result['active_targets']}")
    print(f"  idle                  : {result['idle']}")
    print(f"  last_successful_check : {result['last_successful_check']}")
    print(f"  alert                 : {result['alert']}")
    print(f"  error                 : {result['error']}")

    if result["alert"]:
        print(result["alert"], file=sys.stderr)
        return 1
    if result.get("idle"):
        print("Idle: no active targets, so no odds are expected right now (not a stall).")
        return 0
    if result["elapsed_s"] is not None:
        print(f"Fresh: last odds record {result['elapsed_s']:.0f}s ago (within the expected-cadence threshold).")
    else:
        print("Fresh: within the expected-cadence threshold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
