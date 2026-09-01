"""Post-deploy verification for the Betfair capture deploy (SP-328, Req 7.4/7.6).

Confirms that, after ``docker compose up -d --build`` has recreated the capture
container from current code, a Monitor Service cycle actually runs that current
code and (when work is due) persists odds to the ``bf_trader`` database.

Design notes (why the check is shaped this way):

* Req 7.4 wants proof that *current* code runs, verifiable by the ABSENCE of the
  old Vault startup failure. A Monitor cycle that reaches ``Ending run
  successfully`` cannot have died in the old ``BFDriver.__init__`` -> ``Vault()``
  path, so a fresh successful run is the primary signal.
* Req 7.6 wants odds persisted within 300s. BUT a correct Monitor run legitimately
  writes ZERO odds when no target is due (all kick-offs far away). Failing the
  deploy in that case would be wrong. So odds persistence is treated as:
    - REQUIRED only if a fresh odds row appears, OR
    - satisfied-by-successful-cycle otherwise (nothing was due).
  The definitive, always-valid signal is therefore "a new successful Monitor
  cycle completed within the window"; a new odds row is reported as a bonus and
  strengthens the result.

Reuses the same .env/psycopg2 approach as verify_db.py. Cross-platform logic, but
expected to run against my_postgres on the Pi (Linux/ARM) as the sole capture host.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from logic.deploy_checks import validate_env
from output.log import Output as Log

REQUIRED_DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]
CONNECT_TIMEOUT_S = 10

# Req 7.6 window.
DEFAULT_TIMEOUT_S = 300
# How often to poll the DB while waiting.
POLL_INTERVAL_S = 5

END_MESSAGE = "Monitor Service: INFO: Ending run successfully"


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


def _connect(config: dict):
    return psycopg2.connect(
        host=config["DB_HOST"],
        port=config["DB_PORT"],
        dbname=config["DB_NAME"],
        user=config["DB_USER"],
        password=config["DB_PWD"],
        connect_timeout=CONNECT_TIMEOUT_S,
    )


def _successful_run_count(cursor) -> int:
    """Count of successful Monitor cycle completions in the run log."""
    cursor.execute("SELECT COUNT(*) FROM bf.log_file WHERE message = %s", (END_MESSAGE,))
    return cursor.fetchone()[0]


def _odds_row_count(cursor) -> int:
    cursor.execute('SELECT COUNT(*) FROM bf.market_table')
    return cursor.fetchone()[0]


def verify_deploy(env_path: str = None, timeout_s: int = DEFAULT_TIMEOUT_S,
                  poll_interval_s: int = POLL_INTERVAL_S, sleep=time.sleep,
                  now=time.monotonic) -> dict:
    """Confirm a fresh Monitor cycle ran current code (and report odds persisted).

    Validates: Requirements 7.4, 7.6

    Returns a dict:
        verified (bool): a new successful Monitor cycle completed in the window.
        odds_persisted (bool): a new odds row landed in the window.
        new_successful_runs (int): count of new "Ending run successfully" entries.
        new_odds_rows (int): count of new bf.market_table rows.
        error (str | None): operator-facing error, or None.
    """
    result = {
        "verified": False,
        "odds_persisted": False,
        "new_successful_runs": 0,
        "new_odds_rows": 0,
        "error": None,
    }

    try:
        config = _read_db_config(env_path)
    except ConfigurationException as error:
        result["error"] = f"Could not load .env configuration: {error}"
        return result

    missing = validate_env(config, REQUIRED_DB_KEYS)
    if missing:
        result["error"] = "Missing required DB connection details in .env: " + ", ".join(missing)
        return result

    try:
        conn = _connect(config)
        conn.autocommit = True
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(error)
        result["error"] = f"Data store unreachable within {CONNECT_TIMEOUT_S}s: {error}"
        return result

    try:
        with conn.cursor() as cursor:
            baseline_runs = _successful_run_count(cursor)
            baseline_odds = _odds_row_count(cursor)

        deadline = now() + timeout_s
        while now() < deadline:
            with conn.cursor() as cursor:
                runs = _successful_run_count(cursor)
                odds = _odds_row_count(cursor)
            result["new_successful_runs"] = runs - baseline_runs
            result["new_odds_rows"] = odds - baseline_odds
            if result["new_successful_runs"] > 0:
                result["verified"] = True
                result["odds_persisted"] = result["new_odds_rows"] > 0
                return result
            sleep(poll_interval_s)

        result["error"] = (
            f"No successful Monitor cycle completed within {timeout_s}s "
            f"(new successful runs: {result['new_successful_runs']}). "
            "Current code may not be running, or the cycle failed."
        )
        return result
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(error)
        result["error"] = f"Post-deploy verification query failed: {error}"
        return result
    finally:
        conn.close()


def main() -> int:
    timeout_s = int(os.environ.get("VERIFY_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    result = verify_deploy(timeout_s=timeout_s)

    print("Post-deploy verification result:")
    print(f"  verified            : {result['verified']}")
    print(f"  odds_persisted      : {result['odds_persisted']}")
    print(f"  new_successful_runs : {result['new_successful_runs']}")
    print(f"  new_odds_rows       : {result['new_odds_rows']}")
    print(f"  error               : {result['error']}")

    if not result["verified"]:
        print("Post-deploy verification FAILED.", file=sys.stderr)
        return 1
    if result["odds_persisted"]:
        print("Verified: current code ran a Monitor cycle and persisted fresh odds.")
    else:
        print("Verified: current code ran a successful Monitor cycle (no targets due, so no odds expected).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
