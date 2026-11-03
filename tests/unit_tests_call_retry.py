"""Unit tests for SP-487: Call.call() retry-with-backoff on transient transport
errors.

Context: the Betfair monitor service aborted its entire run the first time a
single API call hit a transient DNS failure ("Temporary failure in name
resolution", surfacing as requests.exceptions.ConnectionError), despite having
run hundreds of healthy loops. The fix adds a bounded retry-with-backoff around
the HTTP request in api/call.py so a one-in-a-thousand transport blip becomes a
non-event, while real API responses and non-transport errors are NOT retried.

These tests drive the retry loop fast by injecting a no-op sleep and patching
requests.request; no network is used.

Conventions mirror tests/unit_tests_monitor_resilience.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest
import requests

from api.call import Call, CallException
from api.http_methods import Methods
from api.urls import Urls
from output.log import Output as Log

Log.LOG_FILE = False
Log.LOG_CONSOLE = False


def _call(**overrides):
    """A Call with a no-op sleep and small, fast retry settings unless overridden."""
    params = dict(retry_attempts=4, retry_backoff_base_s=0.0, sleep=MagicMock())
    params.update(overrides)
    return Call(**params)


class TestTransientRetrySucceeds:
    """A transient transport error that then recovers must NOT crash the call:
    the request is retried and the eventual successful Response is returned."""

    def test_retry_then_succeed_on_connection_error(self):
        sleep = MagicMock()
        c = _call(sleep=sleep)
        ok = MagicMock(name="Response", text="ok")

        # Fail transiently twice (DNS-style), then succeed on the third attempt.
        side_effects = [
            requests.exceptions.ConnectionError("Temporary failure in name resolution"),
            requests.exceptions.ConnectionError("Temporary failure in name resolution"),
            ok,
        ]
        with patch("api.call.requests.request", side_effect=side_effects) as req:
            result = c.call(Methods.POST, Urls.JSON_RPC_BET, {"x": 1})

        assert result is ok
        assert req.call_count == 3  # two failures + one success
        assert sleep.call_count == 2  # slept once before each retry

    def test_retry_then_succeed_on_timeout(self):
        c = _call()
        ok = MagicMock(name="Response", text="ok")
        with patch(
            "api.call.requests.request",
            side_effect=[requests.exceptions.Timeout("read timed out"), ok],
        ) as req:
            result = c.call(Methods.POST, Urls.JSON_RPC_BET, {})
        assert result is ok
        assert req.call_count == 2


class TestTransientRetryExhausted:
    """When every attempt hits a transient transport error, the call raises
    CallException after exactly retry_attempts tries (it does not loop forever,
    and the original transport error is chained)."""

    def test_exhaustion_raises_after_n_attempts(self):
        sleep = MagicMock()
        c = _call(retry_attempts=4, sleep=sleep)
        err = requests.exceptions.ConnectionError("Temporary failure in name resolution")
        with patch("api.call.requests.request", side_effect=err) as req:
            with pytest.raises(CallException) as exc:
                c.call(Methods.POST, Urls.JSON_RPC_BET, {})

        assert req.call_count == 4  # first try + 3 retries
        assert sleep.call_count == 3  # a backoff sleep before each retry, none after the last
        # Original transport error is preserved as the cause.
        assert isinstance(exc.value.__cause__, requests.exceptions.ConnectionError)

    def test_single_attempt_does_not_retry(self):
        """retry_attempts=1 means no retries: one try, then raise."""
        sleep = MagicMock()
        c = _call(retry_attempts=1, sleep=sleep)
        with patch(
            "api.call.requests.request",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ) as req:
            with pytest.raises(CallException):
                c.call(Methods.POST, Urls.JSON_RPC_BET, {})
        assert req.call_count == 1
        assert sleep.call_count == 0


class TestNonTransientNotRetried:
    """A non-transport error (e.g. a malformed URL) must NOT be retried: it
    raises CallException on the first attempt. This preserves the existing
    test_call contract in unit_tests_betfair_objects.py."""

    def test_bad_url_raises_immediately_without_retry(self):
        sleep = MagicMock()
        c = _call(sleep=sleep)
        # MissingSchema is a RequestException but NOT ConnectionError/Timeout.
        with patch(
            "api.call.requests.request",
            side_effect=requests.exceptions.MissingSchema("Invalid URL 'a.b.c'"),
        ) as req:
            with pytest.raises(CallException):
                c.call(Methods.GET, "a.b.c", {})
        assert req.call_count == 1  # no retry on a non-transient error
        assert sleep.call_count == 0


class TestRealResponseNotRetried:
    """Any real HTTP Response (including a 4xx/5xx status) is returned as-is and
    never retried: retry is only for transport failures, not API-level errors.
    The caller remains responsible for interpreting the response body/status."""

    def test_http_error_status_is_returned_not_retried(self):
        sleep = MagicMock()
        c = _call(sleep=sleep)
        resp = MagicMock(name="Response", status_code=400, text="bad request")
        with patch("api.call.requests.request", return_value=resp) as req:
            result = c.call(Methods.POST, Urls.JSON_RPC_BET, {})
        assert result is resp
        assert req.call_count == 1
        assert sleep.call_count == 0


class TestBackoffGrows:
    """The backoff between retries grows exponentially from the configured base."""

    def test_exponential_backoff_sequence(self):
        sleep = MagicMock()
        c = _call(retry_attempts=4, retry_backoff_base_s=0.5, sleep=sleep)
        with patch(
            "api.call.requests.request",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            with pytest.raises(CallException):
                c.call(Methods.POST, Urls.JSON_RPC_BET, {})
        # 0.5 * 2**0, 0.5 * 2**1, 0.5 * 2**2 -> 0.5, 1.0, 2.0
        actual = [args.args[0] for args in sleep.call_args_list]
        assert actual == [0.5, 1.0, 2.0]
