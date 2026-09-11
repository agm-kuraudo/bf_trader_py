from contextlib import contextmanager
from uuid import uuid4

import psycopg2

from output.log import Output as Log


class DBOutputException(Exception):
    pass


# Fixed bigint key for the SP-343 single-instance capture-run advisory lock.
# Arbitrary but stable; must be the same value across all runs so that
# concurrent runs contend for the same lock.
RUN_ADVISORY_LOCK_KEY = 4310343


class DBOutputConnection:
    def __init__(self):
        self.conn = None
        self.run_id = None

    def open_connection(self, connection_string: dict):
        try:
            self.conn = psycopg2.connect(
                database=connection_string["db_name"],
                host=connection_string["host"],
                user=connection_string["db_user"],
                password=connection_string["db_pwd"],
                port=connection_string["port"],
            )
            self.conn.autocommit = True
            self.run_id = str(uuid4())
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to open database connection") from error

    @contextmanager
    def get_cursor(self):
        cursor = self.conn.cursor()
        try:
            yield cursor
        finally:
            cursor.close()

    def db_write_log(self, msg):
        try:
            with self.get_cursor() as cursor:
                cursor.execute(
                    'INSERT INTO bf.log_file(id, "timestamp", message) VALUES (%s, NOW(), %s)', (self.run_id, msg)
                )
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to write log to database") from error

    def db_write_object_id(self, object_type, object_name, object_id):
        try:
            with self.get_cursor() as cursor:
                # Check if the record exists
                cursor.execute(
                    "SELECT object_id FROM bf.betfair_object_ids WHERE object_type = %s AND object_name = %s",
                    (object_type, object_name),
                )
                result = cursor.fetchone()

                if result:
                    # Record exists
                    existing_object_id = result[0]
                    Log.log_info(f"Existing object ID: {existing_object_id}, Provided object ID: {object_id}")

                    if str(existing_object_id) == str(object_id):
                        # Object ID matches, no changes needed
                        Log.log_info(f"No changes needed for {object_type}, {object_name}")
                    else:
                        # Object ID does not match, update the record
                        cursor.execute(
                            "UPDATE bf.betfair_object_ids SET object_id = %s, last_updated = NOW() WHERE object_type = %s AND object_name = %s",  # noqa: E501
                            (object_id, object_type, object_name),
                        )
                        Log.log_debug(f"Updated object ID for {object_type}, {object_name}")
                else:
                    # Record does not exist, insert a new record
                    cursor.execute(
                        "INSERT INTO bf.betfair_object_ids (object_type, object_name, object_id, last_updated) VALUES (%s, %s, %s, NOW())",  # noqa: E501
                        (object_type, object_name, object_id),
                    )
                    Log.log_debug(f"Inserted new object ID for {object_type}, {object_name}")

        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to write object ID to database") from error

    def db_write_target(
        self, target_id, event_id, market_id, runner_ids, start_time, status, update_frequency=None, notes="None"
    ):
        try:
            if update_frequency is None:
                update_frequency = 14400

            with self.get_cursor() as cursor:
                # Check if the record exists
                cursor.execute("SELECT target_id FROM bf.target WHERE target_id = %s", (target_id,))
                result = cursor.fetchone()

                if result:
                    # Record exists, no need to update
                    Log.log_debug(f"Target record already exists: {target_id}")
                else:
                    # Record does not exist, insert a new record
                    cursor.execute(
                        "INSERT INTO bf.target (target_id, event_id, market_id, runner_ids, start_time, status, update_frequency, last_updated, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)",  # noqa: E501
                        (target_id, event_id, market_id, runner_ids, start_time, status, update_frequency, notes),
                    )
                    Log.log_debug(f"Inserted new target record: {target_id}")

        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to write target to database") from error

    def db_read(self, sql_query):
        try:
            with self.get_cursor() as cursor:
                cursor.execute(sql_query)
                value = cursor.fetchall()
                Log.log_debug(f"Query executed: {sql_query}, Result: {value}")
                # print(f"Query executed: {sql_query}, Result: {value}")  # Added print statement for debugging
                return value
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            print(f"Error: {error}")  # Added print statement for debugging
            raise DBOutputException("Failed to read from database") from error

    def db_delete(self, table, condition):
        try:
            with self.get_cursor() as cursor:
                query = f"DELETE FROM {table} WHERE {condition}"
                cursor.execute(query)
                Log.log_debug(f"Executed DELETE query: {query}")
                # print(f"Executed DELETE query: {query}")  # Added print statement for debugging
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            print(f"Error: {error}")  # Added print statement for debugging
            raise DBOutputException("Failed to delete from database") from error

    def db_write(self, sql_command, params=None):
        """
        Execute a given SQL command and return True if successful, False if it fails.

        :param sql_command: The SQL command to execute.
        :param params: Optional parameters for the SQL command.
        :return: True if the command is executed successfully, False otherwise.
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute(sql_command, params)
                Log.log_debug(f"Executed SQL command: {sql_command}")
                return True
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            return False

    def try_acquire_run_lock(self) -> bool:
        """
        Attempt to acquire the single-instance capture-run advisory lock (SP-343).

        Uses ``pg_try_advisory_lock`` which is SESSION-scoped and NON-BLOCKING:
        it returns immediately with True if the lock was acquired, or False if
        another session already holds it. This is what lets a second concurrent
        run exit as a no-op instead of blocking.

        CRITICAL: this MUST be the SESSION-scoped ``pg_try_advisory_lock`` and NOT
        the transaction-scoped ``pg_advisory_xact_lock``. This connection runs with
        ``conn.autocommit = True``, so every statement commits its own implicit
        transaction immediately; a transaction-scoped lock would therefore release
        the instant this statement returns, giving zero protection. Do NOT
        "optimise" this to the xact variant.

        Session-scoped locks are tied to the connection/session, so they persist
        for the life of the run and auto-release when the connection closes or the
        process/container dies. That self-healing behaviour is what avoids the
        poisoned-lock failure mode the old count-based scheme (scripts/fix_lock.py)
        suffered from — no expiry/timeout logic is required.

        :return: True if this session acquired the lock, False otherwise.
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s);", (RUN_ADVISORY_LOCK_KEY,))
                return bool(cursor.fetchone()[0])
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to acquire run advisory lock") from error

    def release_run_lock(self) -> None:
        """
        Release the single-instance capture-run advisory lock (SP-343).

        Uses ``pg_advisory_unlock`` (SESSION-scoped, matching ``pg_try_advisory_lock``
        in :meth:`try_acquire_run_lock`). Call on normal exit before closing the
        connection. On crash this need not be called explicitly: closing the
        connection or the container dying drops the session, which auto-releases
        the session-scoped lock.
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s);", (RUN_ADVISORY_LOCK_KEY,))
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to release run advisory lock") from error

    def close(self):
        if self.conn:
            self.conn.close()
