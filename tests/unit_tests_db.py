import unittest
from unittest.mock import patch, MagicMock
from output.dboutput import DBOutputConnection, DBOutputException

class TestDBOutputConnection(unittest.TestCase):

    @patch('output.dboutput.psycopg2.connect')
    def setUp(self, mock_connect):
        # Mock the database connection
        self.mock_conn = MagicMock()
        mock_connect.return_value = self.mock_conn
        self.db_output = DBOutputConnection()
        self.db_output.open_connection({
            "db_name": "test_db",
            "host": "localhost",
            "db_user": "test_user",
            "db_pwd": "test_pwd",
            "port": "5432"
        })

    @patch('output.dboutput.DBOutputConnection.get_cursor')
    def test_db_write_target(self, mock_get_cursor):
        # Mock the cursor
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        # Simulate the SELECT query returning no results
        mock_cursor.fetchone.return_value = None

        # Call the method
        self.db_output.db_write_target("test_target_id", "test_event_id", "test_market_id", "2025-02-13 00:00:00", "unit_test")

        # Check if the insert query was executed
        mock_cursor.execute.assert_any_call(
            'SELECT target_id FROM bf.target WHERE target_id = %s',
            ("test_target_id",)
        )
        mock_cursor.execute.assert_any_call(
            'INSERT INTO bf.target (target_id, event_id, market_id, start_time, status) VALUES (%s, %s, %s, %s, %s)',
            ("test_target_id", "test_event_id", "test_market_id", "2025-02-13 00:00:00", "unit_test")
        )

    @patch('output.dboutput.DBOutputConnection.get_cursor')
    def test_db_delete(self, mock_get_cursor):
        # Mock the cursor
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        # Call the method
        self.db_output.db_delete("bf.target", "status = 'unit_test'")

        # Check if the delete query was executed
        mock_cursor.execute.assert_called_with("DELETE FROM bf.target WHERE status = 'unit_test'")

    def tearDown(self):
        self.db_output.close()

if __name__ == '__main__':
    unittest.main()