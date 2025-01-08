from uuid import uuid4
from output.log import Output as Log
import psycopg2
from psycopg2 import sql
from contextlib import contextmanager

class DBOutputException(Exception):
    pass

class DBOutputConnection:
    def __init__(self):
        self.conn = None
        self.run_id = None

    def open_connection(self, connection_string: dict):
        try:
            self.conn = psycopg2.connect(database=connection_string["db_name"],
                                         host=connection_string["host"],
                                         user=connection_string["db_user"],
                                         password=connection_string["db_pwd"],
                                         port=connection_string["port"])
            self.conn.autocommit = True
            self.run_id = str(uuid4())
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to open database connection")

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
                    'INSERT INTO bf.log_file(id, "timestamp", message) VALUES (%s, NOW(), %s)',
                    (self.run_id, msg)
                )
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to write log to database")

    def db_write_object_id(self, object_type, object_name, object_id):
        try:
            with self.get_cursor() as cursor:
                # Check if the record exists
                cursor.execute(
                    'SELECT object_id FROM bf.betfair_object_ids WHERE object_type = %s AND object_name = %s',
                    (object_type, object_name)
                )
                result = cursor.fetchone()

                if result:
                    # Record exists
                    existing_object_id = result[0]
                    Log.log_info(f"Existing object ID: {existing_object_id}, Provided object ID: {object_id}")
                    # print(
                    #     f"Existing object ID: {existing_object_id}, Provided object ID: {object_id}")  # Added print statement for debugging
                    #
                    # print(
                    #     f"Existing object ID: {type(existing_object_id)}, Provided object ID: {type(object_id)}")  # Added print statement for debugging

                    if str(existing_object_id) == str(object_id):
                        # Object ID matches, no changes needed
                        Log.log_info(f"No changes needed for {object_type}, {object_name}")
                        # print(
                        #     f"No changes needed for {object_type}, {object_name}")  # Added print statement for debugging
                    else:
                        # Object ID does not match, update the record
                        cursor.execute(
                            'UPDATE bf.betfair_object_ids SET object_id = %s, last_updated = NOW() WHERE object_type = %s AND object_name = %s',
                            (object_id, object_type, object_name)
                        )
                        Log.log_info(f"Updated object ID for {object_type}, {object_name}")
                        # print(
                        #     f"Updated object ID for {object_type}, {object_name}")  # Added print statement for debugging
                else:
                    # Record does not exist, insert a new record
                    cursor.execute(
                        'INSERT INTO bf.betfair_object_ids (object_type, object_name, object_id, last_updated) VALUES (%s, %s, %s, NOW())',
                        (object_type, object_name, object_id)
                    )
                    Log.log_info(f"Inserted new object ID for {object_type}, {object_name}")
                    # print(
                    #     f"Inserted new object ID for {object_type}, {object_name}")  # Added print statement for debugging

        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            raise DBOutputException("Failed to write object ID to database")

    def db_read(self, sql_query):
        try:
            with self.get_cursor() as cursor:
                cursor.execute(sql_query)
                value = cursor.fetchall()
                Log.log_info(f"Query executed: {sql_query}, Result: {value}")
                #print(f"Query executed: {sql_query}, Result: {value}")  # Added print statement for debugging
                return value
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            print(f"Error: {error}")  # Added print statement for debugging
            raise DBOutputException("Failed to read from database")

    def db_delete(self, table, condition):
        try:
            with self.get_cursor() as cursor:
                query = f"DELETE FROM {table} WHERE {condition}"
                cursor.execute(query)
                Log.log_info(f"Executed DELETE query: {query}")
                # print(f"Executed DELETE query: {query}")  # Added print statement for debugging
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)
            print(f"Error: {error}")  # Added print statement for debugging
            raise DBOutputException("Failed to delete from database")

    def close(self):
        if self.conn:
            self.conn.close()