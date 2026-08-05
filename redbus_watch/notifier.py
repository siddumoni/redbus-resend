"""
Thin wrapper around the Resend API - same pattern as bms-resend.
Kept separate from email_template.py so the HTML-building logic can be
unit-tested without any network calls or API keys.
"""

import os
import resend


class NotifierError(RuntimeError):
    pass


def send_email(subject: str, html: str, to: str = None) -> dict:
    """
    Sends an email via Resend.
    Expects RESEND_API_KEY, RESEND_FROM, and (unless `to` is passed) RESEND_TO
    to be set as environment variables - same convention as bms-resend.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    from_addr = os.environ.get("RESEND_FROM")
    to_addr = to or os.environ.get("RESEND_TO")

    missing = [name for name, val in [
        ("RESEND_API_KEY", api_key), ("RESEND_FROM", from_addr), ("RESEND_TO", to_addr)
    ] if not val]
    if missing:
        raise NotifierError(f"Missing required env var(s) for sending email: {', '.join(missing)}")

    resend.api_key = api_key

    try:
        result = resend.Emails.send({
            "from": from_addr,
            "to": to_addr,
            "subject": subject,
            "html": html,
        })
    except Exception as e:  # resend raises its own exception types; keep this broad but logged
        raise NotifierError(f"Resend send failed: {e}") from e

    return result
