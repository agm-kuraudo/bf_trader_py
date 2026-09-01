"""Confirm the PostgreSQL data store is reachable and schema-ready (SP-328, Req 1).

This script is the "Data Storage Prerequisite" check from the
season-background-data-capture design. It:

1. Reads the DB connection details from the project ``.env`` via ``DotenvLoader``.
2. Validates that the required DB keys are present and non-empty (``validate_env``);
   if any are missing it surfaces which without attempting to connect (Req 1.3).
3. Opens a psycopg2 connection with a **10-second** timeout, treating a
   failure/timeout as the store being unreachable (Req 1.2, 1.7).
4. Confirms the four required capture tables exist in schema ``bf`` and creates
   ONLY the absent ones from the ``build/sql/create_database.sql`` DDL, leaving
   existing tables and their data unchanged (Req 1.4, 1.5).

The design keeps this deliberately thin: a direct psycopg2 connection is used
(rather than ``DBOutputConnection.open_connection``) purely because a 10-second
``connect_timeout`` is required and the shared helper does not set one.

Platform note: pure-logic checks (``validate_env`` / ``missing_tables``) are
cross-platform, but this script is expected to run against the ``my_postgres``
container on the always-on Raspberry Pi 500 (Linux/ARM), the sole capture host.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import psycopg2

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from logic.deploy_checks import missing_tables, validate_env
from output.log import Output as Log

# Required DB connection keys read from .env (Req 1.2, 1.3).
REQUIRED_DB_KEYS = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]

# Connection timeout in seconds (Req 1.2, 1.7).
CONNECT_TIMEOUT_S = 10

# The schema captured odds live in.
CAPTURE_SCHEMA = "bf"

# Bare table names (without schema) required for odds capture (Req 1.4).
REQUIRED_TABLES = {"target", "market_table", "log_file", "betfair_object_ids"}

# Bare-table-name -> exact CREATE TABLE DDL from build/sql/create_database.sql.
# Only the four bf.* capture tables are included; the CREATE DATABASE / \c lines
# from the SQL file are intentionally excluded because the database already
# exists. Each statement uses CREATE TABLE IF NOT EXISTS so it is safe, but we
# only ever execute the entries for tables reported absent by missing_tables.
TABLE_DDL = {
    "betfair_object_ids": """
        CREATE TABLE IF NOT EXISTS bf.betfair_object_ids
        (
            object_type text COLLATE pg_catalog."default",
            object_name text COLLATE pg_catalog."default",
            object_id integer,
            last_updated timestamp with time zone
        )
        TABLESPACE pg_default;
        ALTER TABLE IF EXISTS bf.betfair_object_ids
            OWNER to postgres;
    """,
    "log_file": """
        CREATE TABLE IF NOT EXISTS bf.log_file
        (
            id uuid NOT NULL,
            "timestamp" timestamp with time zone,
            message text COLLATE pg_catalog."default"
        )
        TABLESPACE pg_default;
        ALTER TABLE IF EXISTS bf.log_file
            OWNER to postgres;
    """,
    "market_table": """
        CREATE TABLE IF NOT EXISTS bf.market_table
        (
            "timestamp" timestamp with time zone,
            market_id text COLLATE pg_catalog."default",
            runner_id text COLLATE pg_catalog."default",
            odds text COLLATE pg_catalog."default"
        )
        TABLESPACE pg_default;
        ALTER TABLE IF EXISTS bf.market_table
            OWNER to postgres;
    """,
    "target": """
        CREATE TABLE IF NOT EXISTS bf.target
        (
            target_id text COLLATE pg_catalog."default",
            event_id text COLLATE pg_catalog."default",
            market_id text COLLATE pg_catalog."default",
            runner_ids text COLLATE pg_catalog."default",
            start_time timestamp with time zone,
            status text COLLATE pg_catalog."default",
            update_frequency integer,
            last_updated timestamp with time zone,
            notes text COLLATE pg_catalog."default"
        )
        TABLESPACE pg_default;
        ALTER TABLE IF EXISTS bf.target
            OWNER to postgres;
    """,
}


def _read_db_config(env_path: str = None) -> dict:
    """Read the required DB keys from ``.env`` via ``DotenvLoader``.

    Missing or empty keys are returned as empty strings rather than raising, so
    ``validate_env`` can report the full set of offending keys at once (Req 1.3)
    instead of failing on the first one.

    Args:
        env_path: Optional explicit path to the ``.env`` file. Defaults to the
            project root ``.env`` resolved by ``DotenvLoader``.

    Returns:
        A dict mapping each key in ``REQUIRED_DB_KEYS`` to its value (or "").
    """
    loader = DotenvLoader(env_path)
    config = {}
    for key in REQUIRED_DB_KEYS:
        try:
            config[key] = loader.get_secret(key)
        except ConfigurationException:
            # Absent/empty -> record as empty so validate_env reports it.
            config[key] = ""
    return config


def _present_tables(cursor) -> set:
    """Return the set of bare table names present in the ``bf`` schema."""
    cursor.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
        (CAPTURE_SCHEMA,),
    )
    return {row[0] for row in cursor.fetchall()}


def verify_db(env_path: str = None) -> dict:
    """Confirm the data store is reachable and schema-ready.

    Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.7

    Args:
        env_path: Optional explicit path to the ``.env`` file (mainly for tests).

    Returns:
        A dict with the contract:
            ``reachable`` (bool): True if a connection was established within the
                10-second timeout.
            ``missing_config`` (list): Required DB keys absent/empty in ``.env``.
            ``missing_tables`` (list): Required tables found absent (before
                creation).
            ``created_tables`` (list): Tables this run created.
            ``error`` (str | None): An operator-facing error message, or None.
    """
    result = {
        "reachable": False,
        "missing_config": [],
        "missing_tables": [],
        "created_tables": [],
        "error": None,
    }

    # --- Config check (Req 1.3): do not connect if required keys are missing. ---
    try:
        config = _read_db_config(env_path)
    except ConfigurationException as error:
        # .env file itself is missing/unreadable.
        result["missing_config"] = list(REQUIRED_DB_KEYS)
        result["error"] = f"Could not load .env configuration: {error}"
        return result

    missing_config = validate_env(config, REQUIRED_DB_KEYS)
    if missing_config:
        result["missing_config"] = missing_config
        result["error"] = "Missing required DB connection details in .env: " + ", ".join(missing_config)
        return result

    # --- Reachability check (Req 1.2, 1.7): 10-second connection timeout. ---
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
        result["error"] = f"Data store unreachable within {CONNECT_TIMEOUT_S}s: {error}"
        return result

    # --- Schema readiness (Req 1.4, 1.5): create only absent tables. ---
    try:
        with conn.cursor() as cursor:
            # Ensure the schema exists before creating any tables.
            cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {CAPTURE_SCHEMA}")

            present = _present_tables(cursor)
            absent = missing_tables(present, REQUIRED_TABLES)
            result["missing_tables"] = sorted(absent)

            created = []
            for table in sorted(absent):
                cursor.execute(TABLE_DDL[table])
                created.append(table)
                Log.log_info(f"Created missing capture table: {CAPTURE_SCHEMA}.{table}")
            result["created_tables"] = created
    except (Exception, psycopg2.DatabaseError) as error:
        Log.log_error(error)
        result["error"] = f"Failed to confirm/create capture tables: {error}"
    finally:
        conn.close()

    return result


def main() -> int:
    """CLI entry: print the verification result and return an exit code.

    Returns a non-zero exit code when the store is unreachable or required
    config is missing (Req 1.7), so a scheduler/deploy step can gate on it.
    """
    result = verify_db()

    print("Data store verification result:")
    print(f"  reachable      : {result['reachable']}")
    print(f"  missing_config : {result['missing_config']}")
    print(f"  missing_tables : {result['missing_tables']}")
    print(f"  created_tables : {result['created_tables']}")
    print(f"  error          : {result['error']}")

    if result["missing_config"]:
        print("Capture NOT stood up: required DB connection details are missing.", file=sys.stderr)
        return 2
    if not result["reachable"]:
        print("Capture NOT stood up: data store is unreachable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
