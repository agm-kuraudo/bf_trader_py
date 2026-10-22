"""Unit tests for SP-403 Part 2: the empty listRunnerBook result guard in
MonitorService.update_runner_odds.

If listRunnerBook returns an empty result for a runner, update_runner_odds must
log a warning naming the market and runner and continue to the next runner,
instead of indexing result[0] and crashing the whole run with IndexError ->
MonitorServiceException.

The real cause of the empty result was a corrupted (float-coerced) market id
built by populate_template (SP-403 Part 1, covered by template_tests.py and
test_property_template_substitution.py); this guard ensures a genuinely empty
result can never abort a run again (defense-in-depth).

Conventions mirror tests/unit_tests_monitor_resilience.py: MonitorService() with
a mocked db_connection and a mocked BFDriver (svc.BF), Log.LOG_FILE = False.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from monitor_service import MonitorService, MonitorServiceException
from output.log import Output as Log

Log.LOG_FILE = False


class TestEmptyRunnerBookGuard:
    """update_runner_odds skips an empty listRunnerBook result and continues."""

    def _service(self):
        svc = MonitorService()
        svc.db_connection = MagicMock()
        svc.db_connection.db_write.return_value = True
        svc.BF = MagicMock()
        svc.BF.request_body_obj.populate_template.return_value = {"stub": "body"}
        return svc

    def _resp(self, payload):
        resp = MagicMock()
        resp.json.return_value = payload
        return resp

    def _target(self, market="1.263913210", runners=("60298",), start=None):
        start = start or (datetime.now(UTC) + timedelta(hours=5))
        # (market, status, num_runners, runners, update_frequency, last_updated, event_start_time)
        return (market, "OPEN", len(runners), list(runners), 300, None, start)

    def test_empty_result_is_skipped_with_warning_no_raise(self):
        """An empty result logs a warning naming market+runner and does not raise."""
        svc = self._service()
        svc.BF.call_obj.call.return_value = self._resp({"result": []})

        with patch("monitor_service.Log.log_warning") as mock_warn:
            svc.update_runner_odds([self._target(market="1.263913210", runners=("60298",))])

        warnings = [str(c.args[0]) for c in mock_warn.call_args_list if c.args]
        assert any("1.263913210" in m and "60298" in m for m in warnings), warnings
        odds_writes = [
            c for c in svc.db_connection.db_write.call_args_list if c.args and "bf.market_table" in str(c.args[0])
        ]
        assert odds_writes == []

    def test_empty_runner_skipped_but_valid_runner_still_written(self):
        """A mix: the empty runner is skipped, the valid runner still writes odds."""
        svc = self._service()
        seq = [
            self._resp({"result": []}),
            self._resp({"result": [{"status": "OPEN", "runners": [{"ex": {"availableToBack": [{"price": 2.8}]}}]}]}),
        ]
        svc.BF.call_obj.call.side_effect = seq

        svc.update_runner_odds([self._target(market="1.263913210", runners=("60298", "48351"))])

        odds_writes = [
            c for c in svc.db_connection.db_write.call_args_list if c.args and "bf.market_table" in str(c.args[0])
        ]
        assert len(odds_writes) == 1
        assert odds_writes[0].args[1][1] == "48351"

    def test_missing_result_key_is_treated_as_empty(self):
        """A response with no 'result' key is falsy too - skip, no KeyError/IndexError."""
        svc = self._service()
        svc.BF.call_obj.call.return_value = self._resp({})
        with patch("monitor_service.Log.log_warning") as mock_warn:
            svc.update_runner_odds([self._target(runners=("60298",))])
        assert mock_warn.called

    def test_valid_result_writes_odds_and_updates_target(self):
        """Preservation: a valid result writes odds and updates last_updated/frequency."""
        svc = self._service()
        svc.BF.call_obj.call.return_value = self._resp(
            {"result": [{"status": "OPEN", "runners": [{"ex": {"availableToBack": [{"price": 2.5}]}}]}]}
        )
        svc.update_runner_odds([self._target(market="1.263913215", runners=("60298",))])

        writes = [str(c.args[0]) for c in svc.db_connection.db_write.call_args_list if c.args]
        assert any("bf.market_table" in w for w in writes), "odds row must be written"
        assert any("UPDATE bf.target" in w for w in writes), "target last_updated must be updated"

    def test_genuine_error_still_raises_monitor_exception(self):
        """Preservation: a genuine (non-empty-result) error still raises."""
        svc = self._service()
        svc.BF.call_obj.call.side_effect = Exception("network boom")
        with pytest.raises(MonitorServiceException):
            svc.update_runner_odds([self._target(runners=("60298",))])
