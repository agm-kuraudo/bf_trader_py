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


# --- SP-330 Task 3.2: no single-instance lock (E4 retired) ------------------


class TestNoSingleInstanceLock:
    """SP-330 Task 3.2 - fix-property test (runs against FIXED code).

    Property 1: Expected Behavior - orphaned lock residue no longer blocks a run.

    This is the inversion of the Task 1 exploration test
    (TestBugConditionLockPoisoning): the same bug-condition input
    (isBugCondition(X) = (NOT another_instance_alive) AND has_orphaned_lock_residue,
    scoped to the Pi counterexample db_read -> [(231, 230)]) that USED to be
    blocked must now proceed past where the count-based lock used to sit.

    After the fix the lock query is gone, so run() flows directly from
    open_connection(...) to the 'Starting run' marker. We make BFDriver.get_token()
    return False so authenticate_and_get_token() raises AFTER 'Starting run' is
    written but BEFORE the capture loop / get_targets() - reaching the marker
    without running the full capture cycle, and making the residue value
    irrelevant to reaching the marker.

    Validates: Requirements 2.1, 2.2, 2.3
    """

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_orphaned_residue_no_longer_blocks_run(self, mock_driver, mock_sleep):
        svc = MonitorService()
        db = MagicMock()
        # Same orphaned residue as the Pi counterexample: 231 starts vs 230 ends,
        # no live instance. After the fix the lock query is gone, so this value
        # is never read for gating; it is retained to mirror the bug condition.
        db.db_read.return_value = [(231, 230)]
        # SP-343: the single-instance guard is now a Postgres session advisory lock.
        # Make acquisition succeed so the run proceeds past the guard.
        db.try_acquire_run_lock.return_value = True
        svc.db_connection = db

        # get_token() returns False -> authenticate_and_get_token() raises AFTER
        # 'Starting run' is written but BEFORE the capture loop.
        mock_driver.return_value.get_token.return_value = False

        with patch("monitor_service.DBOutputConnection", return_value=db):
            with pytest.raises(MonitorServiceException) as exc:
                svc.run()

        logged_messages = [str(call.args[0]) for call in db.db_write_log.call_args_list if call.args]

        # 2.1: 'Starting run' WAS written - the run got past where the lock used
        # to sit despite the orphaned residue.
        assert any("Monitor Service: INFO: Starting run" in msg for msg in logged_messages)
        # 2.2: the run did NOT abort on the poisoned lock - it failed later on
        # auth, so the message must NOT contain 'Failed to acquire lock'.
        assert "Failed to acquire lock" not in str(exc.value)
        # 2.3: no lock retry sleep, and the capture loop (which also sleeps) is
        # never reached because auth failed fast -> time.sleep was NOT called.
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
        db.try_acquire_run_lock.return_value = True  # SP-343: acquire the advisory lock
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
        db.try_acquire_run_lock.return_value = True  # SP-343: acquire the advisory lock
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


# --- SP-330 Task 2: preservation baseline - audit markers on success --------


class TestAuditMarkersOnSuccess:
    """SP-330 Task 2 - preservation baseline (runs against UNFIXED code).

    Property 2: Preservation - the audit log markers are unchanged.

    A run that reaches success writes BOTH 'Monitor Service: INFO: Starting run'
    at the start and 'Monitor Service: INFO: Ending run successfully' on
    successful completion (Req 3.1). These markers are retained for
    auditing/observability; the fix (Task 3) removes only the count-based gating
    that read the start/end counts, not the log lines themselves.

    After the Task 3.1 fix the count-based lock is gone, so run() no longer
    issues the lock query. The only db_read in the success path is now
    get_targets(); seeding it with an empty target list means there are no
    targets to update and the capture cycle completes immediately, reaching
    success cleanly. (Before the fix this test seeded a leading balanced
    count (5, 5) to satisfy the old gating; that read no longer happens.)

    Validates: Requirements 3.1, 3.2, 3.3
    """

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_success_writes_both_audit_markers(self, mock_driver, mock_sleep):
        svc = MonitorService()
        db = MagicMock()
        # After the fix the only db_read is get_targets() -> empty list, so there
        # are no targets to update and the capture cycle completes immediately.
        db.db_read.return_value = []
        db.try_acquire_run_lock.return_value = True  # SP-343: acquire the advisory lock
        svc.db_connection = db

        # Authentication succeeds so the run proceeds into (an empty) capture.
        mock_driver.return_value.get_token.return_value = True

        with patch("monitor_service.DBOutputConnection", return_value=db):
            # No exception: an empty capture cycle reaches success cleanly.
            svc.run()

        logged_messages = [str(call.args[0]) for call in db.db_write_log.call_args_list if call.args]

        # Req 3.1: the start marker is written.
        assert any("Monitor Service: INFO: Starting run" in msg for msg in logged_messages)
        # Req 3.1: the success end marker is written.
        assert any("Monitor Service: INFO: Ending run successfully" in msg for msg in logged_messages)
        # A clean success wrote no failure marker.
        assert not any("Ending run with failure" in msg for msg in logged_messages)


# --- SP-330 Task 1: bug condition exploration test (retired after the fix) --
#
# The Task 1 exploration test (TestBugConditionLockPoisoning ->
# test_orphaned_residue_blocks_run_on_unfixed_code) asserted the OLD, unfixed
# behaviour: that orphaned residue db_read -> [(231, 230)] raised
# 'Failed to acquire lock', slept on the retry loop, and did NOT write
# 'Starting run'. That behaviour no longer exists after the Task 3.1 fix
# (the count-based lock was removed from MonitorService.run()).
#
# Per the bug-condition methodology, that exploration assertion has been
# INVERTED into the fix-property assertion, which now lives in
# TestNoSingleInstanceLock.test_orphaned_residue_no_longer_blocks_run above:
# the same [(231, 230)] input now proves the bug is fixed (run proceeds,
# writes 'Starting run', never raises 'Failed to acquire lock', never sleeps).
# The exploration class is therefore removed rather than left asserting the
# removed behaviour.


# --- SP-330 Task 1: bug condition exploration test (retired after the fix) --

# ======================================================================================
# SP-343 Task 7: Logging preservation under the NEW run() loop (Property 2).
#
# The SP-343 fix rewrote run() to (a) acquire a Postgres session advisory lock as the
# single-instance guard and (b) drive an adaptive while-loop via decide_next_action.
# These tests prove the durable run-log markers are UNCHANGED by that rewrite:
#   - "Starting run" is still written at the start,
#   - "Ending run successfully" is still written on clean completion,
#   - "Ending run with failure" is still written on crash, and the original exception
#     still propagates (failure-logging never masks it),
# and additionally that when the advisory lock is NOT acquired the run exits as a no-op
# WITHOUT authenticating (single-instance guard).
#
# Mocking approach (mirrors the existing tests in this file):
#   - MonitorService() is constructed, its db_connection is a MagicMock `db`, and
#     monitor_service.DBOutputConnection is patched to return that same `db` so run()'s
#     internal open_connection(...) yields the mock.
#   - monitor_service.BFDriver is patched; get_token() controls auth success/failure.
#   - monitor_service.time.sleep is patched to a no-op (the loop never really sleeps).
#   - db.try_acquire_run_lock.return_value = True lets the run proceed past the guard;
#     setting it False exercises the no-op path.
#   - The loop is forced to EXIT after one pass by making db.db_read return [] (empty
#     targets => no due target and no active-or-imminent target => decide_next_action
#     returns "exit").
#
# Requirements traced: 3.4 (durable logging incl. on crash), and 3.1/3.2 (idle no-op).
# ======================================================================================


class TestSP343LoggingPreservation:
    """SP-343: durable run-log markers are unchanged by the new run() loop + lock."""

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_success_still_writes_start_and_success_markers(self, mock_driver, mock_sleep):
        """A clean run under the new loop still writes both the "Starting run" and
        "Ending run successfully" markers, and NO failure marker.

        **Validates: Requirements 3.4**
        """
        svc = MonitorService()
        db = MagicMock()
        # Empty targets -> loop reaches decide_next_action "exit" on the first pass.
        db.db_read.return_value = []
        db.try_acquire_run_lock.return_value = True
        svc.db_connection = db

        mock_driver.return_value.get_token.return_value = True

        with patch("monitor_service.DBOutputConnection", return_value=db):
            svc.run()

        logged = [str(call.args[0]) for call in db.db_write_log.call_args_list if call.args]
        assert any("Monitor Service: INFO: Starting run" in m for m in logged)
        assert any("Monitor Service: INFO: Ending run successfully" in m for m in logged)
        assert not any("Ending run with failure" in m for m in logged)
        # Single-instance guard was consulted and the lock released on the clean exit.
        db.try_acquire_run_lock.assert_called_once()
        db.release_run_lock.assert_called_once()

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_crash_still_writes_failure_marker_and_reraises(self, mock_driver, mock_sleep):
        """A crash under the new loop still writes the "Ending run with failure" marker
        to the durable run log AND re-raises MonitorServiceException.

        **Validates: Requirements 3.4**
        """
        svc = MonitorService()
        db = MagicMock()
        db.db_read.return_value = []
        db.try_acquire_run_lock.return_value = True
        # Auth blows up AFTER "Starting run" is written and after the lock is acquired.
        mock_driver.return_value.get_token.side_effect = Exception("boom")

        with patch("monitor_service.DBOutputConnection", return_value=db):
            with pytest.raises(MonitorServiceException) as exc:
                svc.run()

        logged = [str(call.args[0]) for call in db.db_write_log.call_args_list if call.args]
        assert any("Monitor Service: INFO: Starting run" in m for m in logged)
        assert any("ERROR : Ending run with failure" in m for m in logged)
        # Original failure reason preserved through the wrapping exception.
        assert "boom" in str(exc.value)

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_failure_logging_never_masks_original_error_under_new_loop(self, mock_driver, mock_sleep):
        """If writing the failure marker ITSELF fails, the original exception still
        propagates (failure-logging is best-effort) under the new loop.

        **Validates: Requirements 3.4**
        """
        svc = MonitorService()
        db = MagicMock()
        db.db_read.return_value = []
        db.try_acquire_run_lock.return_value = True
        mock_driver.return_value.get_token.side_effect = Exception("boom")

        def write_log_side_effect(msg):
            if "Ending run with failure" in msg:
                raise Exception("db down during error handling")

        db.db_write_log.side_effect = write_log_side_effect

        with patch("monitor_service.DBOutputConnection", return_value=db):
            with pytest.raises(MonitorServiceException) as exc:
                svc.run()
        assert "boom" in str(exc.value)

    @patch("monitor_service.time.sleep", return_value=None)
    @patch("monitor_service.BFDriver")
    def test_lock_not_acquired_exits_as_noop_without_authenticating(self, mock_driver, mock_sleep):
        """When the advisory lock is NOT acquired, run() logs the no-op marker, does NOT
        authenticate, and returns without raising (single-instance guard).

        **Validates: Requirements 3.1, 3.2**
        """
        svc = MonitorService()
        db = MagicMock()
        db.db_read.return_value = []
        db.try_acquire_run_lock.return_value = False  # another run holds the lock

        with patch("monitor_service.DBOutputConnection", return_value=db):
            with patch("monitor_service.Log.log_info") as mock_log_info:
                # No exception: a second concurrent run is a clean no-op.
                svc.run()

        # The single-instance guard was consulted.
        db.try_acquire_run_lock.assert_called_once()
        # It never authenticated (get_token not called) -> no capture work started.
        mock_driver.return_value.get_token.assert_not_called()
        # The no-op marker was logged (to the console log, as run() does for this path)
        # and the connection closed.
        info_messages = [str(call.args[0]) for call in mock_log_info.call_args_list if call.args]
        assert any("Another run active, exiting as no-op" in m for m in info_messages)
        db.close.assert_called_once()
        # A no-op run wrote no success/failure end marker to the durable run log.
        logged = [str(call.args[0]) for call in db.db_write_log.call_args_list if call.args]
        assert not any("Ending run successfully" in m for m in logged)
        assert not any("Ending run with failure" in m for m in logged)
