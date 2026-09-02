"""
Unit tests for SP-302: Monitor Service error handling and timing configuration.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import inspect
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from api.call import Call
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from monitor_service import MonitorService, MonitorServiceException
from output.dboutput import DBOutputConnection
from output.log import Output as Log


class TestMonitorService(unittest.TestCase):
    Log.LOG_FILE = False

    def setUp(self):
        self.service = MonitorService()
        self.service.db_connection = MagicMock()

    @patch("monitor_service.BFDriver.get_token")
    def test_authenticate_and_get_token_success(self, mock_get_token):
        mock_get_token.return_value = True
        self.service.authenticate_and_get_token()
        # Success path no longer writes a log line to the database; it only logs
        # to the info log. Assert no failure log was written.
        self.service.db_connection.db_write_log.assert_not_called()

    @patch("monitor_service.BFDriver.get_token")
    def test_authenticate_and_get_token_failure(self, mock_get_token):
        mock_get_token.return_value = False
        with self.assertRaises(MonitorServiceException):
            self.service.authenticate_and_get_token()
        self.service.db_connection.db_write_log.assert_called_with(
            "Monitor Service: ERROR : Ending Run : Failed to retrieve token"
        )

    def test_get_targets_success(self):
        # get_targets now selects 9 columns:
        # target_id, event_id, market_id, runner_ids, start_time, status,
        # update_frequency, last_updated, notes
        self.service.db_connection.db_read.return_value = [
            (1, 2, 3, "1-2|3-4", "2025-03-10 12:00:00", "IDENTIFIED", 14400, "2025-03-10 11:00:00", "notes")
        ]
        targets = self.service.get_targets()
        self.assertEqual(len(targets), 1)
        self.service.db_connection.db_write_log.assert_not_called()

    def test_get_targets_failure(self):
        self.service.db_connection.db_read.side_effect = Exception("DB error")
        with self.assertRaises(MonitorServiceException):
            self.service.get_targets()

    @patch.object(Call, "call")
    def test_process_targets_success(self, mock_call):
        # update_odds_for_targets was renamed to process_targets. It reads the
        # market id from column [2] and calls the Betfair API per market.
        self.service.db_connection.db_read.return_value = [
            (1, 2, 3, "1-2|3-4", "2025-03-10 12:00:00", "IDENTIFIED", 14400, "2025-03-10 11:00:00", "notes")
        ]
        mock_call.return_value.json.return_value = {
            "result": [{"status": "OPEN", "runners": [{"selectionId": 1}, {"selectionId": 2}]}]
        }
        targets = self.service.process_targets(self.service.get_targets())
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0][1], "OPEN")
        self.assertEqual(targets[0][2], 2)

    @patch.object(Call, "call")
    def test_process_targets_failure(self, mock_call):
        self.service.db_connection.db_read.return_value = [
            (1, 2, 3, "1-2|3-4", "2025-03-10 12:00:00", "IDENTIFIED", 14400, "2025-03-10 11:00:00", "notes")
        ]
        mock_call.side_effect = Exception("API error")
        with self.assertRaises(MonitorServiceException):
            self.service.process_targets(self.service.get_targets())

    def test_update_target_status_success(self):
        targets = [(3, "OPEN", 2, [1, 2])]
        self.service.update_target_status(targets)
        self.service.db_connection.db_write.assert_called_with(
            "UPDATE bf.target SET status='OPEN' WHERE market_id='3';"
        )

    def test_update_target_status_failure(self):
        targets = [(3, "UNKNOWN", 2, [1, 2])]
        with self.assertRaises(MonitorServiceException):
            self.service.update_target_status(targets)

    def test_get_open_targets_success(self):
        self.service.db_connection.db_read.return_value = [(1, 2, 3, "1-2|3-4", "2025-03-10 12:00:00", "OPEN", "notes")]
        open_targets = self.service.get_open_targets()
        self.assertEqual(len(open_targets), 1)
        self.service.db_connection.db_write_log.assert_not_called()

    def test_get_open_targets_failure(self):
        self.service.db_connection.db_read.side_effect = Exception("DB error")
        with self.assertRaises(MonitorServiceException):
            self.service.get_open_targets()

    @patch.object(Call, "call")
    def test_update_runner_odds_success(self, mock_call):
        # update_runner_odds now consumes "processed" targets (as produced by
        # process_targets), not raw open-target rows. It iterates target[3] as a
        # list of selection ids and uses target[6] as the event start datetime.
        event_time = datetime.now(UTC) + timedelta(hours=1)
        processed_target = (3, "OPEN", 2, [1, 2], 14400, "2025-03-10 11:00:00", event_time)
        mock_call.return_value.json.return_value = {
            "result": [{"status": "OPEN", "runners": [{"ex": {"availableToBack": [{"price": 1.5, "size": 100}]}}]}]
        }
        self.service.update_runner_odds([processed_target])
        self.service.db_connection.db_write.assert_called()

    @patch.object(Call, "call")
    def test_update_runner_odds_failure(self, mock_call):
        event_time = datetime.now(UTC) + timedelta(hours=1)
        processed_target = (3, "OPEN", 2, [1, 2], 14400, "2025-03-10 11:00:00", event_time)
        mock_call.side_effect = Exception("API error")
        with self.assertRaises(MonitorServiceException):
            self.service.update_runner_odds([processed_target])


# ─── New tests for SP-302: Monitor Service error handling and timing ────────────


class TestConfigDefaults:
    """Test that all timing configuration defaults are correctly set."""

    def test_monitor_max_wait_seconds_default(self):
        assert DefaultStrategy.MONITOR_MAX_WAIT_SECONDS == 900

    def test_stale_target_hours_default(self):
        assert DefaultStrategy.STALE_TARGET_HOURS == 24

    def test_initial_update_frequency_default(self):
        assert DefaultStrategy.INITIAL_UPDATE_FREQUENCY == 14400

    def test_update_frequency_tiers_has_all_keys(self):
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        assert "IN_PLAY" in tiers
        assert "LESS_THAN_3H" in tiers
        assert "LESS_THAN_6H" in tiers
        assert "LESS_THAN_12H" in tiers
        assert "MORE_THAN_12H" in tiers

    def test_update_frequency_tiers_values_are_positive_integers(self):
        for _key, value in DefaultStrategy.UPDATE_FREQUENCY_TIERS.items():
            assert isinstance(value, int)
            assert value > 0

    def test_tiers_are_in_ascending_order(self):
        """Higher tiers (further from event) should have higher intervals."""
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        assert tiers["IN_PLAY"] <= tiers["LESS_THAN_3H"]
        assert tiers["LESS_THAN_3H"] <= tiers["LESS_THAN_6H"]
        assert tiers["LESS_THAN_6H"] <= tiers["LESS_THAN_12H"]
        assert tiers["LESS_THAN_12H"] <= tiers["MORE_THAN_12H"]


class TestDbWriteTargetParameter:
    """Test that db_write_target accepts update_frequency parameter."""

    def test_db_write_target_has_update_frequency_param(self):
        sig = inspect.signature(DBOutputConnection.db_write_target)
        assert "update_frequency" in sig.parameters

    def test_db_write_target_update_frequency_default_is_none(self):
        sig = inspect.signature(DBOutputConnection.db_write_target)
        param = sig.parameters["update_frequency"]
        assert param.default is None


class TestFromFileStrategyLoadsTimingKeys:
    """Test that FromFileStrategy loads timing keys from strategy.yaml."""

    def test_from_file_strategy_loads_without_error(self):
        """FromFileStrategy should load successfully with current strategy.yaml."""
        FromFileStrategy()
        # After loading, DefaultStrategy attributes should be set
        assert DefaultStrategy.UPDATE_FREQUENCY_TIERS is not None
        assert DefaultStrategy.INITIAL_UPDATE_FREQUENCY is not None
        assert DefaultStrategy.STALE_TARGET_HOURS is not None
        assert DefaultStrategy.MONITOR_MAX_WAIT_SECONDS is not None

    def test_from_file_strategy_loads_tier_values(self):
        """FromFileStrategy should load the tier values from YAML."""
        FromFileStrategy()
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        # These should match the values in config/strategy.yaml
        assert tiers["IN_PLAY"] == 5
        assert tiers["LESS_THAN_3H"] == 300
        assert tiers["LESS_THAN_6H"] == 900
        assert tiers["LESS_THAN_12H"] == 3600
        assert tiers["MORE_THAN_12H"] == 14400


if __name__ == "__main__":
    unittest.main()
