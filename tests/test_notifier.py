"""
Tests for the notifier's response validation. Uses unittest.mock to
simulate different Resend API responses without making real network calls
or needing a real API key.
"""

import os
from unittest.mock import patch

import pytest

from redbus_watch.notifier import send_email, NotifierError


@pytest.fixture(autouse=True)
def resend_env():
    with patch.dict(os.environ, {
        "RESEND_API_KEY": "test_key",
        "RESEND_FROM": "alerts@example.com",
        "RESEND_TO": "me@example.com",
    }):
        yield


def test_send_email_succeeds_with_valid_id_response():
    with patch("resend.Emails.send", return_value={"id": "abc-123"}) as mock_send:
        result = send_email("Test Subject", "<p>hi</p>")
    assert result == {"id": "abc-123"}
    mock_send.assert_called_once()


def test_send_email_raises_when_response_missing_id():
    """
    This is the exact bug class that caused a watch to be reported as
    'emailed' in the run summary when nothing was actually sent - the
    Resend SDK call returned normally (no exception) but the response
    didn't actually represent a sent email.
    """
    with patch("resend.Emails.send", return_value={"error": "domain not verified"}):
        with pytest.raises(NotifierError, match="without an email ID"):
            send_email("Test Subject", "<p>hi</p>")


def test_send_email_raises_when_response_is_none():
    with patch("resend.Emails.send", return_value=None):
        with pytest.raises(NotifierError, match="without an email ID"):
            send_email("Test Subject", "<p>hi</p>")


def test_send_email_raises_when_response_is_empty_dict():
    with patch("resend.Emails.send", return_value={}):
        with pytest.raises(NotifierError, match="without an email ID"):
            send_email("Test Subject", "<p>hi</p>")


def test_send_email_raises_when_sdk_itself_throws():
    with patch("resend.Emails.send", side_effect=Exception("network error")):
        with pytest.raises(NotifierError, match="Resend send failed"):
            send_email("Test Subject", "<p>hi</p>")


def test_send_email_missing_env_vars_raises_before_calling_api():
    with patch.dict(os.environ, {"RESEND_API_KEY": ""}, clear=False):
        with patch("resend.Emails.send") as mock_send:
            with pytest.raises(NotifierError, match="RESEND_API_KEY"):
                send_email("Test Subject", "<p>hi</p>")
            mock_send.assert_not_called()
