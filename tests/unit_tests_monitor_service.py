import unittest
from unittest.mock import patch, MagicMock

from api.call import Call
from monitor_service import MonitorService, MonitorServiceException
from output.log import Output as Log

class TestMonitorService(unittest.TestCase):
    Log.LOG_FILE = False
    def setUp(self):
        self.service = MonitorService()
        self.service.db_connection = MagicMock()

    @patch('monitor_service.BFDriver.get_token')
    def test_authenticate_and_get_token_success(self, mock_get_token):
        mock_get_token.return_value = True
        self.service.authenticate_and_get_token()
        self.service.db_connection.db_write_log.assert_called_with("Token retrieved")

    @patch('monitor_service.BFDriver.get_token')
    def test_authenticate_and_get_token_failure(self, mock_get_token):
        mock_get_token.return_value = False
        with self.assertRaises(MonitorServiceException):
            self.service.authenticate_and_get_token()
        self.service.db_connection.db_write_log.assert_called_with("Failed to retrieve token")

    def test_get_targets_success(self):
        self.service.db_connection.db_read.return_value = [(1, 2, 3, '2025-03-10 12:00:00', 'IDENTIFIED')]
        targets = self.service.get_targets()
        self.assertEqual(len(targets), 1)
        self.service.db_connection.db_write_log.assert_not_called()

    def test_get_targets_failure(self):
        self.service.db_connection.db_read.side_effect = Exception("DB error")
        with self.assertRaises(MonitorServiceException):
            self.service.get_targets()

    @patch.object(Call, 'call')
    def test_update_odds_for_targets_success(self, mock_call):
        self.service.db_connection.db_read.return_value = [(1, 2, 3, '2025-03-10 12:00:00', 'IDENTIFIED')]
        mock_call.return_value.json.return_value = {
            "result": [{"status": "OPEN", "runners": [{"selectionId": 1}, {"selectionId": 2}]}]
        }
        targets = self.service.update_odds_for_targets(self.service.get_targets())
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0][1], "OPEN")
        self.assertEqual(targets[0][2], 2)

    @patch.object(Call, 'call')
    def test_update_odds_for_targets_failure(self, mock_call):
        self.service.db_connection.db_read.return_value = [(1, 2, 3, '2025-03-10 12:00:00', 'IDENTIFIED')]
        mock_call.side_effect = Exception("API error")
        with self.assertRaises(MonitorServiceException):
            self.service.update_odds_for_targets(self.service.get_targets())

    def test_update_target_status_success(self):
        targets = [(3, "OPEN", 2, [1, 2])]
        self.service.update_target_status(targets)
        self.service.db_connection.db_write.assert_called_with("UPDATE bf.target SET status='OPEN' WHERE market_id='3';")

    def test_update_target_status_failure(self):
        targets = [(3, "UNKNOWN", 2, [1, 2])]
        with self.assertRaises(MonitorServiceException):
            self.service.update_target_status(targets)

    def test_get_open_targets_success(self):
        self.service.db_connection.db_read.return_value = [(1, 2, 3, '1-2|3-4', '2025-03-10 12:00:00', 'OPEN', 'notes')]
        open_targets = self.service.get_open_targets()
        self.assertEqual(len(open_targets), 1)
        self.service.db_connection.db_write_log.assert_not_called()

    def test_get_open_targets_failure(self):
        self.service.db_connection.db_read.side_effect = Exception("DB error")
        with self.assertRaises(MonitorServiceException):
            self.service.get_open_targets()

    @patch.object(Call, 'call')
    def test_update_runner_odds_success(self, mock_call):
        self.service.db_connection.db_read.return_value = [(1, 2, 3, '1-2|3-4', '2025-03-10 12:00:00', 'OPEN', 'notes')]
        mock_call.return_value.json.return_value = {
            "result": [{"status": "OPEN", "runners": [{"ex": {"availableToBack": [{"price": 1.5, "size": 100}]}}]}]
        }
        self.service.update_runner_odds(self.service.get_open_targets())
        self.service.db_connection.db_write.assert_called()

    @patch.object(Call, 'call')
    def test_update_runner_odds_failure(self, mock_call):
        self.service.db_connection.db_read.return_value = [(1, 2, 3, '1-2|3-4', '2025-03-10 12:00:00', 'OPEN', 'notes')]
        mock_call.side_effect = Exception("API error")
        with self.assertRaises(MonitorServiceException):
            self.service.update_runner_odds(self.service.get_open_targets())

if __name__ == "__main__":
    unittest.main()