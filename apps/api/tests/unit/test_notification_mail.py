"""The MailSender Protocol + the test fake (the LoggingRenderSink/GotenbergRenderSink split)."""

from __future__ import annotations

import pytest

from easysynq_api.services.notifications.mail import FakeMailSender, MailMessage

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
