from uuid import uuid4
from output.log import Output as Log
import psycopg2

class DBOutputException(Exception):
    pass

class DBOutputConnection:
    def __init__(self):
        self.cursor = None
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
            #self.cursor = self.conn.cursor()

        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)

    def db_write(self, msg):
        try:

            self.cursor = self.conn.cursor()

            self.cursor.execute( 'INSERT INTO bf.log_file(id, "timestamp", message) VALUES (%s, %s, %s)', (self.run_id, "NOW()", msg))

            self.cursor.close()
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)


    def db_read(self, sql):
        try:



            self.cursor.execute(sql)
            value = self.cursor.fetchall()
            #self.cursor.close()

            return value
        except (Exception, psycopg2.DatabaseError) as error:
            Log.log_error(error)

    def close(self):
        #self.cursor.close()
        self.conn.close()