import unittest
from unittest.mock import MagicMock, patch

from output.dboutput import DBOutputConnection
from output.log import Output as Log


class TestDBOutputConnection(unittest.TestCase):
    Log.LOG_FILE = False

    @patch("output.dboutput.psycopg2.connect")
    def setUp(self, mock_connect):
        # Mock the database connection
        self.mock_conn = MagicMock()
        mock_connect.return_value = self.mock_conn
        self.db_output = DBOutputConnection()
        self.db_output.open_connection(
            {"db_name": "test_db", "host": "localhost", "db_user": "test_user", "db_pwd": "test_pwd", "port": "5432"}
        )

    @patch("output.dboutput.DBOutputConnection.get_cursor")
    def test_db_write_target(self, mock_get_cursor):
        # Mock the cursor
        mock_cursor = MagicMock()
        mock_get_cursor.return_value.__enter__.return_value = mock_cursor

        # Simulate the SELECT query returning no results
        mock_cursor.fetchone.return_value = None

        # Call the method
        self.db_output.db_write_target(
            "test_target_id",
            "test_event_id",
            "test_market_id",
            runner_ids="12|12",
            start_time="2025-02-13 00:00:00",
            status="unit_test",
            notes="str(target.my_market.description)",
        )

        # Check if the insert query was executed
        mock_cursor.execute.assert_any_call("SELECT target_id FROM bf.target WHERE target_id = %s", ("test_target_id",))
        mock_cursor.execute.assert_any_call(
            "INSERT INTO bf.target (target_id, event_id, market_id, runner_ids, start_time, status, notes) VALUES (%s, %s, %s, %s, %s, %s, %s)",  # noqa: E501
            (
                "test_target_id",
                "test_event_id",
                "test_market_id",
                "12|12",
                "2025-02-13 00:00:00",
                "unit_test",
                "str(target.my_market.description)",
            ),
        )

    @patch("output.dboutput.DBOutputConnection.get_cursor")
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


if __name__ == "__main__":
    unittest.main()
