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
from logic.deploy_checks import freshness, validate_env
from output.log import Output as Log

REQUIRED_DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]
CONNECT_TIMEOUT_S = 10

# Freshness threshold: 15 minutes (Req 5.3). Matches the max capture cadence.
THRESHOLD_S = 15 * 60

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
                    threshold_s: float = THRESHOLD_S, state_path: str = STATE_FILE) -> dict:
    """Check whether captured odds are fresh; raise alerts on stall/unreachable.

    Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5

    Args:
        env_path: Optional explicit .env path (mainly for tests).
        now: Current time (timezone-aware). Defaults to datetime.now(UTC).
        threshold_s: Freshness threshold in seconds (default 900 = 15 min).
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
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(error)
        result["error"] = f"Freshness query failed: {error}"
        result["alert"] = f"UNREACHABLE ({DATA_SOURCE}): {result['error']}"
        return result
    finally:
        conn.close()

    # We reached and queried the store successfully -> record this check.
    _save_last_successful_check(now, state_path)

    # Pure freshness decision (Property 1).
    decision = freshness(now, last_record_ts, threshold_s)
    result["last_record_ts"] = last_record_ts.isoformat() if last_record_ts is not None else None
    result["elapsed_s"] = decision["elapsed_s"]
    result["stalled"] = decision["stalled"]

    if last_record_ts is None:
        # No records at all (Req 5.5).
        result["alert"] = f"STALL ({DATA_SOURCE}): no captured odds present in the data store."
    elif decision["stalled"]:
        # Elapsed exceeds threshold (Req 5.3).
        mins = decision["elapsed_s"] / 60.0
        result["alert"] = (
            f"STALL ({DATA_SOURCE}): last odds record was {decision['elapsed_s']:.0f}s "
            f"({mins:.1f} min) ago, exceeding the {threshold_s / 60:.0f}-min threshold."
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
    print(f"  last_successful_check : {result['last_successful_check']}")
    print(f"  alert                 : {result['alert']}")
    print(f"  error                 : {result['error']}")

    if result["alert"]:
        print(result["alert"], file=sys.stderr)
        return 1
    print(f"Fresh: last odds record {result['elapsed_s']:.0f}s ago (within threshold).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
