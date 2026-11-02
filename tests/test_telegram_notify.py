"""Tests for the SP-395 Telegram failure-notification layer.

Covers the pure message formatter (property-based) and the send/CLI behaviour
with the Bot API and DotenvLoader stubbed out — no real network or .env needed.

The behavioural contracts asserted here mirror the SP-395 Definition of Done:
  * the message identifies the job name and that it failed, and includes the
    execution id / link when available (mobile-readable);
  * a configuration problem (missing token/chat id) never crashes the CLI;
  * a delivery failure never crashes the CLI;
  * the CLI always exits 0, so a notification can never change the outcome of
    the job that triggered it.
"""

import tempfile
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from logic import telegram_notify
from logic.telegram_notify import (
    CHAT_ID_KEY,
    TOKEN_KEY,
    format_failure_message,
    main,
    send_telegram_message,
)


def _loader_with(values: dict) -> DotenvLoader:
    """Build a real DotenvLoader backed by a temp .env with the given values."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False, encoding="utf-8")
    for k, v in values.items():
        f.write(f"{k}={v}\n")
    f.close()
    return DotenvLoader(f.name)


# --- format_failure_message: pure, property-tested ---------------------------


class TestFormatFailureMessage:
    """format_failure_message is pure and always produces a coherent message."""

    @given(
        job_name=st.text(min_size=1, max_size=60).filter(lambda s: s.strip() != ""),
        exec_id=st.text(max_size=20),
        exec_url=st.text(max_size=80),
    )
    @settings(max_examples=100)
    def test_always_marks_failure_and_names_job(self, job_name, exec_id, exec_url):
        """The message always says it FAILED and contains the (stripped) job name."""
        msg = format_failure_message(job_name, exec_id, exec_url)
        assert "FAILED" in msg
        assert job_name.strip() in msg

    def test_blank_job_name_uses_placeholder(self):
        """A blank/whitespace job name falls back to a neutral placeholder."""
        assert "(unknown job)" in format_failure_message("   ")
        assert "(unknown job)" in format_failure_message("")

    def test_exec_id_and_url_included_when_present(self):
        """Execution id and url appear in the message when supplied."""
        msg = format_failure_message("Monitor Service", "12345", "http://localhost:4440/exec/12345")
        assert "Monitor Service" in msg
        assert "12345" in msg
        assert "http://localhost:4440/exec/12345" in msg

    def test_exec_id_and_url_omitted_when_blank(self):
        """Blank execution id / url produce a single-line message, no empty lines."""
        msg = format_failure_message("Monitor Service", "", "")
        assert msg.count("\n") == 0
        assert "Monitor Service" in msg


# --- send_telegram_message: config + delivery behaviour ----------------------


class TestSendTelegramMessage:
    """send_telegram_message reads config via DotenvLoader and posts to the API."""

    def test_missing_token_raises_configuration_exception(self):
        """Missing token raises ConfigurationException naming the token key."""
        loader = _loader_with({CHAT_ID_KEY: "123"})  # token absent
        try:
            send_telegram_message("hi", loader=loader)
            raise AssertionError("expected ConfigurationException")
        except ConfigurationException as exc:
            assert TOKEN_KEY in str(exc)

    def test_missing_chat_id_raises_configuration_exception(self):
        """Missing chat id raises ConfigurationException naming the chat-id key."""
        loader = _loader_with({TOKEN_KEY: "abc"})  # chat id absent
        try:
            send_telegram_message("hi", loader=loader)
            raise AssertionError("expected ConfigurationException")
        except ConfigurationException as exc:
            assert CHAT_ID_KEY in str(exc)

    def test_posts_and_returns_true_on_success(self):
        """A 2xx from the Bot API yields True; token/chat id are passed through."""
        loader = _loader_with({TOKEN_KEY: "secret-token", CHAT_ID_KEY: "999"})
        with patch.object(telegram_notify, "_post_message", return_value=True) as post:
            assert send_telegram_message("boom", loader=loader) is True
        post.assert_called_once()
        args = post.call_args.args
        assert args[0] == "secret-token"
        assert args[1] == "999"
        assert args[2] == "boom"

    def test_returns_false_on_delivery_failure(self):
        """A delivery failure is reported as False, not raised."""
        loader = _loader_with({TOKEN_KEY: "secret-token", CHAT_ID_KEY: "999"})
        with patch.object(telegram_notify, "_post_message", return_value=False):
            assert send_telegram_message("boom", loader=loader) is False


# --- main (CLI): always exits 0 ----------------------------------------------


class TestMainAlwaysExitsZero:
    """The CLI must never fail the triggering job (SP-395 DoD)."""

    def test_exit_zero_on_successful_delivery(self):
        with patch.object(telegram_notify, "send_telegram_message", return_value=True):
            assert main(["Monitor Service", "42", "http://localhost:4440/exec/42"]) == 0

    def test_exit_zero_on_delivery_failure(self):
        with patch.object(telegram_notify, "send_telegram_message", return_value=False):
            assert main(["Monitor Service", "42", "http://localhost:4440/exec/42"]) == 0

    def test_exit_zero_on_configuration_exception(self):
        with patch.object(
            telegram_notify,
            "send_telegram_message",
            side_effect=ConfigurationException(f"Required key '{TOKEN_KEY}' is missing from .env"),
        ):
            assert main(["Monitor Service"]) == 0

    def test_exit_zero_on_unexpected_error(self):
        with patch.object(telegram_notify, "send_telegram_message", side_effect=RuntimeError("boom")):
            assert main(["Monitor Service"]) == 0

    def test_exit_zero_with_no_args(self):
        """Even with no job name at all, the CLI exits 0."""
        with patch.object(telegram_notify, "send_telegram_message", return_value=True):
            assert main([]) == 0

    def test_token_not_leaked_to_stderr_on_config_error(self, capsys):
        """A config error reports the key name, never a secret value."""
        with patch.object(
            telegram_notify,
            "send_telegram_message",
            side_effect=ConfigurationException(f"Required key '{TOKEN_KEY}' is empty in .env"),
        ):
            main(["Monitor Service"])
        err = capsys.readouterr().err
        assert TOKEN_KEY in err
