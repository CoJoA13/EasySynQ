"""Email transport (spec §7). The api never sends — only the worker constructs SmtpMailSender; tests
inject FakeMailSender (the LoggingRenderSink vs GotenbergRenderSink split)."""

from __future__ import annotations

import dataclasses
from email.message import EmailMessage
from typing import Protocol

import aiosmtplib

from ...config import Settings


@dataclasses.dataclass(frozen=True)
class MailMessage:
    to: str
    subject: str
    body: str


class MailSender(Protocol):
    async def send(self, msg: MailMessage) -> None: ...


class SmtpMailSender:
    """Async STARTTLS sender. Raises on any SMTP/connection error (the drain owns the retry)."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    async def send(self, msg: MailMessage) -> None:
        email = EmailMessage()
        email["From"] = f"{self._s.smtp_from_name} <{self._s.smtp_from_address}>"
        email["To"] = msg.to
        email["Subject"] = msg.subject
        email.set_content(msg.body)
        await aiosmtplib.send(
            email,
            hostname=self._s.smtp_host,
            port=self._s.smtp_port,
            username=self._s.smtp_username or None,
            password=self._s.smtp_password or None,
            start_tls=self._s.smtp_use_tls,
        )


class FakeMailSender:
    """Test sender — records messages, or raises ``fail_with`` to exercise the failure path."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[MailMessage] = []
        self._fail_with = fail_with

    async def send(self, msg: MailMessage) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append(msg)
