"""The MailSender Protocol + the test fake (the LoggingRenderSink/GotenbergRenderSink split)."""

from __future__ import annotations

import pytest

from easysynq_api.config import Settings
from easysynq_api.services.notifications.mail import (
    FakeMailSender,
    MailMessage,
    SmtpMailSender,
    _header_safe,
)

pytestmark = pytest.mark.unit


async def test_fake_records_sent() -> None:
    sender = FakeMailSender()
    await sender.send(MailMessage(to="a@x.test", subject="Hi", body="Body"))
    assert len(sender.sent) == 1
    assert sender.sent[0].to == "a@x.test"


async def test_fake_can_raise() -> None:
    sender = FakeMailSender(fail_with=RuntimeError("smtp down"))
    with pytest.raises(RuntimeError):
        await sender.send(MailMessage(to="a@x.test", subject="Hi", body="Body"))


def test_header_safe_folds_crlf() -> None:
    assert _header_safe("Line1\r\nInjected: x") == "Line1  Injected: x"
    assert _header_safe("a\rb\nc") == "a b c"
    assert _header_safe("clean") == "clean"


def test_build_message_subject_no_crlf() -> None:
    """_build_message must not raise even when subject contains CR/LF."""
    settings = Settings()
    sender = SmtpMailSender(settings)
    msg = MailMessage(
        to="recipient@example.com",
        subject="Line1\r\nInjected: x",
        body="Normal body\nwith newlines.",
    )
    built = sender._build_message(msg)
    subject = built["Subject"]
    assert "\r" not in subject, "Subject must not contain CR"
    assert "\n" not in subject, "Subject must not contain LF"


def test_build_message_to_no_crlf() -> None:
    """_build_message must fold CR/LF out of the To header."""
    settings = Settings()
    sender = SmtpMailSender(settings)
    msg = MailMessage(
        to="bad\r\nBcc: attacker@evil.test",
        subject="Normal subject",
        body="body",
    )
    built = sender._build_message(msg)
    to_header = built["To"]
    assert "\r" not in to_header, "To must not contain CR"
    assert "\n" not in to_header, "To must not contain LF"


def test_build_message_body_preserves_newlines() -> None:
    """Body newlines must NOT be stripped — only headers are single-line."""
    settings = Settings()
    sender = SmtpMailSender(settings)
    body = "Line one.\nLine two.\n"
    msg = MailMessage(to="r@example.com", subject="Subj", body=body)
    built = sender._build_message(msg)
    content = built.get_content()
    assert "Line one." in content
    assert "Line two." in content
