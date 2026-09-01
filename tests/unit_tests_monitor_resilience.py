"""Unit tests for Monitor Service resilience behaviours (SP-328 Tasks 7.4, 7.5).

Targets the CURRENT monitor_service.py (not the older, partially-stale tests in
unit_tests_monitor_service.py). No live DB needed: the DB connection and the
Betfair driver are mocked.

Task 7.4 (E3, Req 2.5/2.6): per-target persist-and-continue. If one target
fails during the initial-odds fetch, the run records it and continues with the
remaining targets without terminating.

Task 7.5 (E4, Req 3.3): single-instance lock. When a previous invocation is
still "running" (start/finish counts unequal in bf.log_file), a second
invocation is skipped and the skip is recorded, rather than running concurrently.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest

from monitor_service import MonitorService, MonitorServiceException
from output.log import Output as Log

Log.LOG_FILE = False


# --- Task 7.4: per-target persist-and-continue (E3) -------------------------


class TestPersistAndContinue:
    def _service(self):
        svc = MonitorService()
        svc.db_connection = MagicMock()
        return svc

    def test_one_target_failure_does_not_stop_the_others(self):
        """A persist failure on one target is recorded; remaining targets still process."""
        svc = self._service()

        # Two targets both transition IDENTIFIED (raw[5]) -> OPEN (processed[1]),
        # so both are "newly opened" and each triggers update_runner_odds.
        raw_targets = [
            ("t1", "e1", "m1", "r1", "st1", "IDENTIFIED"),
            ("t2", "e2", "m2", "r2", "st2", "IDENTIFIED"),
        ]
        processed_targets = [
            ("m1", "OPEN", 2, [1, 2], 300, None, None),
            ("m2", "OPEN", 2, [3, 4], 300, None, None),
        ]

        calls = []

        def fake_update_runner_odds(target_list):
            market = target_list[0][0]
            calls.append(market)
            if market == "m1":
                raise Exception("simulated persist failure for m1")
            # m2 succeeds

        svc.update_runner_odds = MagicMock(side_effect=fake_update_runner_odds)

        # Should NOT raise despite m1 failing.
        svc.fetch_odds_for_new_targets(raw_targets, processed_targets)

        # Both targets were attempted -> it continued past the m1 failure.
        assert calls == ["m1", "m2"]
        assert svc.update_runner_odds.call_count == 2

    def test_all_targets_succeed(self):
        svc = self._service()
        raw_targets = [("t1", "e1", "m1", "r1", "st1", "IDENTIFIED")]
        processed_targets = [("m1", "OPEN", 2, [1, 2], 300, None, None)]
        svc.update_runner_odds = MagicMock()
        svc.fetch_odds_for_new_targets(raw_targets, processed_targets)
        svc.update_runner_odds.assert_called_once()

    def test_no_newly_opened_targets_is_a_noop(self):
        """Targets already OPEN in the DB (raw[5]=='OPEN') are not re-fetched."""
        svc = self._service()
        raw_targets = [("t1", "e1", "m1", "r1", "st1", "OPEN")]
        processed_targets = [("m1", "OPEN", 2, [1, 2], 300, None, None)]
        svc.update_runner_odds = MagicMock()
        svc.fetch_odds_for_new_targets(raw_targets, processed_targets)
        svc.update_runner_odds.assert_not_called()


# --- Task 7.5: single-instance lock (E4) ------------------------------------


class TestSingleInstanceLock:
    """The lock treats unequal start/finish counts as 'a run is in progress'.

    We drive run() with a mocked DB whose log-count query reports unequal
    start/finish, and patch sleep so the retry loop is instant. The second
    invocation must be skipped (never reach capture) and raise Failed to
    acquire lock after exhausting retries.
    """

    def _service_with_locked_db(self):
        svc = MonitorService()
        db = MagicMock()
        # get_local_db_details + open_connection are called before the lock check.
        # The lock query returns [(start_count, finish_count)] with start != finish.
        db.db_read.return_value = [(231, 230)]
        svc.db_connection = db
        return svc, db

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_second_invocation_is_skipped_and_recorded(self, mock_driver, mock_sleep):
        svc, db = self._service_with_locked_db()
        # Ensure the DB connection created inside run() is our mock.
        with patch("monitor_service.DBOutputConnection", return_value=db):
            with pytest.raises(MonitorServiceException) as exc:
                svc.run()

        # It gave up with the lock error rather than proceeding to capture.
        assert "Failed to acquire lock" in str(exc.value)
        # It retried (slept) rather than running immediately -> concurrency avoided.
        assert mock_sleep.called
        # "Starting run" must NOT have been written (we never acquired the lock),
        # i.e. no start log among the db_write_log calls.
        start_logged = any("Starting run" in str(call.args[0]) for call in db.db_write_log.call_args_list if call.args)
        assert start_logged is False

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_balanced_lock_allows_the_run_to_proceed_past_the_lock(self, mock_driver, mock_sleep):
        """When start == finish, the lock is free: run() proceeds past the lock
        (it will then do other work which we don't assert here)."""
        svc = MonitorService()
        db = MagicMock()
        db.db_read.return_value = [(231, 231)]  # balanced -> lock free
        # Make authenticate step fail fast so we don't exercise the whole cycle;
        # we only care that it got PAST the lock (i.e. wrote "Starting run").
        svc.db_connection = db
        with patch("monitor_service.DBOutputConnection", return_value=db):
            # get_token returns False -> authenticate raises, but only AFTER the
            # lock is acquired and "Starting run" is logged.
            mock_driver.return_value.get_token.return_value = False
            with pytest.raises(MonitorServiceException):
                svc.run()

        start_logged = any("Starting run" in str(call.args[0]) for call in db.db_write_log.call_args_list if call.args)
        assert start_logged is True
        # A balanced lock must not have triggered the retry sleep.
        assert mock_sleep.called is False


# --- Task 11.1: failure outcome recorded to the durable run log (Req 4.1/4.2/3.4) -


class TestRunFailureLogging:
    """A failed run records its failure outcome+reason to bf.log_file, and the
    exception still propagates. Records a failure 'Ending run' so a crash after
    'Starting run' does not permanently unbalance the single-instance lock."""

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_failure_writes_failure_record_and_reraises(self, mock_driver, mock_sleep):
        svc = MonitorService()
        db = MagicMock()
        db.db_read.return_value = [(5, 5)]  # balanced lock -> proceed past it
        # Make authentication blow up AFTER the connection is open and after
        # "Starting run" is written, so we hit the top-level except with a live
        # db_connection.
        mock_driver.return_value.get_token.side_effect = Exception("boom")

        with patch("monitor_service.DBOutputConnection", return_value=db):
            with pytest.raises(MonitorServiceException):
                svc.run()

        # A failure 'Ending run' record was written to the durable run log.
        failure_logged = any(
            "ERROR : Ending run with failure" in str(call.args[0])
            for call in db.db_write_log.call_args_list
            if call.args
        )
        assert failure_logged is True

        # And the balancing invariant holds: for this run, a start was logged and
        # a matching failure-end was logged (so the lock is not left unbalanced).
        start_logged = any("Starting run" in str(call.args[0]) for call in db.db_write_log.call_args_list if call.args)
        assert start_logged is True

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_failure_logging_never_masks_original_error(self, mock_driver, mock_sleep):
        """If writing the failure record ITSELF fails, the original exception
        still propagates (failure-logging is best-effort)."""
        svc = MonitorService()
        db = MagicMock()
        db.db_read.return_value = [(5, 5)]
        mock_driver.return_value.get_token.side_effect = Exception("boom")

        # First db_write_log ("Starting run") ok; the failure-record write raises.
        def write_log_side_effect(msg):
            if "Ending run with failure" in msg:
                raise Exception("db down during error handling")

        db.db_write_log.side_effect = write_log_side_effect

        with patch("monitor_service.DBOutputConnection", return_value=db):
            with pytest.raises(MonitorServiceException) as exc:
                svc.run()
        # Original failure reason preserved.
        assert "boom" in str(exc.value)
