"""The OUT-OF-BAND operator alert channel (Batch 11, review 2026-07-22 finding 2).

Everything else in this package is an *in-DB* path: resolve recipients from ``app_user``, render a
``notification_template`` row, insert a ``notification`` + a ``notification_email`` outbox row, let
the drain send it. That path is correct for every failure the application can observe *while the
database is up* — and structurally incapable of reporting the one failure that matters most. If
PostgreSQL is down or unreachable, ``run_scheduled_backups`` cannot read ``backup_policy``, there is
no session to resolve admins, no template to render, no outbox to insert into, and no audit row to
append. The nightly job simply raises into a worker log nobody reads, for weeks.

So this module is deliberately **DB-free**. It takes ``Settings`` and a payload, and nothing else —
no ``AsyncSession`` parameter, no model import, no ORM. Do not add one: a session parameter here
would silently re-couple the alarm to the thing it is meant to survive.

Three channels, each opt-in via ``OPS_ALERT_CHANNELS`` (comma-separated, empty ⇒ none):

* ``syslog``  — the host's own journal/collector. The most air-gap-friendly option under D1 and the
  only one that needs no network egress at all.
* ``smtp``    — a fixed operator mailbox over the same relay the notification outbox uses, but with
  no recipient resolution and no outbox row (the relay is a separate host from PostgreSQL).
* ``webhook`` — an off-host receiver the org controls (POST ``application/json``).

Contract: **``send_operator_alert`` never raises and never blocks the caller's failure handling.**
Each channel is isolated, so an unreachable webhook cannot suppress the syslog line. Every outcome
is logged, so the container log remains a complete record even with no channel configured.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import logging.handlers
import socket
from typing import Any

from ...config import Settings

logger = logging.getLogger("easysynq.notifications.ops_channel")

_VALID_CHANNELS = frozenset({"syslog", "smtp", "webhook"})

# Per-channel outcome tokens returned by send_operator_alert (test-observable, log-friendly).
SENT = "sent"
SKIPPED = "skipped"  # channel selected but not configured (e.g. smtp with no ops_alert_smtp_to)
FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class OperatorAlert:
    """One operator-facing alarm. ``detail`` carries operational metadata ONLY — never document or
    record content (an operator mailbox / syslog collector / webhook receiver sits outside the QMS
    permission model, and admins hold no ``document.*``; the ``system.email_delivery_failed``
    precedent, spec §5/§6)."""

    event: str  # the notification event key this mirrors, e.g. "system.backup_failed"
    severity: str  # "critical" | "error"
    summary: str  # one line, safe to use as an email subject / syslog message prefix
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)
    org_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": "easysynq",
            "event": self.event,
            "severity": self.severity,
            "summary": self.summary,
            "org_id": self.org_id,
            "detail": self.detail,
        }

    def as_text(self) -> str:
        """Plain-text rendering for the syslog + email bodies. Deliberately not templated through
        ``render.py``: that reads ``notification_template`` from the database."""
        lines = [self.summary, "", f"event:    {self.event}", f"severity: {self.severity}"]
        if self.org_id:
            lines.append(f"org:      {self.org_id}")
        for key, value in self.detail.items():
            lines.append(f"{key}: {value}")
        lines.append("")
        lines.append(
            "This is an EasySynQ out-of-band operator alert. It is sent independently of the "
            "in-app notification system so it survives a database outage."
        )
        return "\n".join(lines)


def selected_channels(settings: Settings) -> list[str]:
    """Parse ``OPS_ALERT_CHANNELS``. Unknown names are dropped with a warning rather than raising —
    a typo in an operator's env must not take the alert path down with it."""
    out: list[str] = []
    for raw in settings.ops_alert_channels.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name not in _VALID_CHANNELS:
            logger.warning(
                "ops_alert.unknown_channel",
                extra={"extra_fields": {"channel": name, "valid": sorted(_VALID_CHANNELS)}},
            )
            continue
        if name not in out:
            out.append(name)
    return out


def _syslog_address(raw: str) -> str | tuple[str, int]:
    """A unix socket path, or ``host:port`` for a UDP collector.

    ⚠ The two forms differ in how honestly they can report. A unix socket is connection-oriented
    enough that an absent/dead socket surfaces as ``failed`` (verified: a missing path reports
    ``failed``, a live one reports ``sent`` and the bytes arrive). ``host:port`` is UDP —
    fire-and-forget — so a closed collector port still reports ``sent``: the datagram was handed to
    the kernel and nothing comes back. Treat ``sent`` on the UDP form as "emitted", not "delivered",
    and prefer the mounted-socket form (or a second channel) where confirmation matters. TCP syslog
    would close this gap and is the v1.x option; it needs its own socktype knob.
    """
    if raw.startswith("/"):
        return raw
    host, _, port = raw.rpartition(":")
    if host and port.isdigit():
        return (host, int(port))
    return raw


_SEVERITY_LEVEL = {"critical": logging.CRITICAL, "error": logging.ERROR}


def _emit_syslog_checked(settings: Settings, alert: OperatorAlert) -> None:
    """Blocking — always called through ``asyncio.to_thread``. A handler is built and closed per
    alert: these fire at most a few times a day, and a long-lived handler over a socket that may
    have gone away is a worse failure mode than the negligible setup cost.

    ⚠ ``SysLogHandler.emit`` routes EVERY exception into ``handleError``, which by default prints to
    stderr and returns — so a dead collector is indistinguishable from a delivered record and the
    channel would report ``sent``. Since ``logging.raiseExceptions`` is a module-wide global we must
    not toggle, substitute a handler-level ``handleError`` that records the failure and re-raise it
    here: an alert channel that silently no-ops is precisely the bug this batch exists to fix."""
    failures: list[BaseException] = []
    address = _syslog_address(settings.ops_alert_syslog_address)
    handler = logging.handlers.SysLogHandler(
        address=address,
        facility=logging.handlers.SysLogHandler.LOG_DAEMON,
        socktype=socket.SOCK_DGRAM if isinstance(address, tuple) else None,
    )
    try:
        handler.handleError = lambda record: failures.append(  # type: ignore[method-assign]
            OSError("syslog handler reported a delivery error")
        )
        handler.setFormatter(logging.Formatter("easysynq[ops-alert]: %(message)s"))
        record = logging.LogRecord(
            name="easysynq.ops_alert",
            level=_SEVERITY_LEVEL.get(alert.severity, logging.ERROR),
            pathname=__file__,
            lineno=0,
            # Syslog is line-oriented: one summary line + a compact JSON tail, so one alert is one
            # record rather than a dozen orphaned continuation lines.
            msg="%s %s",
            args=(alert.summary, json.dumps(alert.as_dict(), default=str, sort_keys=True)),
            exc_info=None,
        )
        handler.emit(record)
    finally:
        handler.close()
    if failures:
        raise failures[0]


async def _send_smtp(settings: Settings, alert: OperatorAlert) -> str:
    if not (settings.smtp_host and settings.ops_alert_smtp_to):
        return SKIPPED
    # Imported here, not at module scope: this module must stay importable (and the other two
    # channels usable) on a host where the mail extra is unavailable.
    from email.message import EmailMessage

    import aiosmtplib

    email = EmailMessage()
    email["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_address}>"
    email["To"] = settings.ops_alert_smtp_to.replace("\r", " ").replace("\n", " ")
    email["Subject"] = f"[EasySynQ] {alert.severity.upper()}: {alert.summary}".replace(
        "\r", " "
    ).replace("\n", " ")
    email.set_content(alert.as_text())  # text/plain — same posture as mail.py
    await aiosmtplib.send(
        email,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username or None,
        password=settings.smtp_password or None,
        start_tls=settings.smtp_use_tls,
        timeout=settings.ops_alert_timeout_seconds,
    )
    return SENT


async def _send_webhook(settings: Settings, alert: OperatorAlert) -> str:
    if not settings.ops_alert_webhook_url:
        return SKIPPED
    import httpx

    headers = {"content-type": "application/json"}
    if settings.ops_alert_webhook_token:
        headers["authorization"] = f"Bearer {settings.ops_alert_webhook_token}"
    async with httpx.AsyncClient(timeout=settings.ops_alert_timeout_seconds) as client:
        response = await client.post(
            settings.ops_alert_webhook_url, json=alert.as_dict(), headers=headers
        )
        response.raise_for_status()
    return SENT


async def _send_syslog(settings: Settings, alert: OperatorAlert) -> str:
    if not settings.ops_alert_syslog_address:
        return SKIPPED
    await asyncio.to_thread(_emit_syslog_checked, settings, alert)
    return SENT


async def send_operator_alert(settings: Settings, alert: OperatorAlert) -> dict[str, str]:
    """Fan the alert out to every configured channel. NEVER raises — the caller is already handling
    a failure, and an alert-delivery problem must not mask it or abort the remaining work.

    Returns ``{channel: outcome}`` for observability/tests. With no channel configured the alert
    still lands in the application log at ERROR, so the container log is never worse off than
    before this module existed.
    """
    channels = selected_channels(settings)
    fields = {"event": alert.event, "severity": alert.severity, "org_id": alert.org_id}
    if not channels:
        logger.error(
            "ops_alert.no_channel_configured",
            extra={"extra_fields": {**fields, "summary": alert.summary, "detail": alert.detail}},
        )
        return {}

    senders = {"syslog": _send_syslog, "smtp": _send_smtp, "webhook": _send_webhook}
    results: dict[str, str] = {}
    for name in channels:
        try:
            results[name] = await senders[name](settings, alert)
        except Exception:  # one dead channel must not suppress the others
            results[name] = FAILED
            logger.warning(
                "ops_alert.channel_failed",
                exc_info=True,
                extra={"extra_fields": {**fields, "channel": name}},
            )
    logger.error(
        "ops_alert.dispatched",
        extra={"extra_fields": {**fields, "summary": alert.summary, "results": results}},
    )
    return results
