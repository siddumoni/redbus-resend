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

    IMPORTANT: the Resend SDK doesn't always raise an exception for every
    kind of failure - a call can return normally with a response that
    doesn't actually contain a sent email ID (e.g. certain account/domain
    restriction errors). Previously this function returned whatever came
    back without checking, so main.py's run summary could report a watch
    as "emailed" even when nothing was actually delivered. Now we
    explicitly check for an "id" in the response and raise if it's
    missing, so failures are impossible to silently swallow.
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

    # A successful send always includes an "id". If it's missing, the API
    # call technically "succeeded" (no exception) but nothing was actually
    # queued for delivery - treat that as a failure too, don't let it pass
    # silently.
    email_id = None
    if isinstance(result, dict):
        email_id = result.get("id")

    if not email_id:
        raise NotifierError(
            f"Resend call returned without an email ID - likely not actually sent. "
            f"Full response: {result}"
        )

    print(f"[notifier] Email sent successfully, Resend ID: {email_id}")
    return result
