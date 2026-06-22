"""outbox_drain state machine (spec §4): SENT on success; transient → retry w/ lease + attempts++;
exhausted → FAILED + a system.email_delivery_failed in-app row to admins.
Count-before-send bounds resends."""

from __future__ import annotations

import datetime
import uuid
from typing import Any as AnyT

import pytest
from sqlalchemy import select

from easysynq_api.config import Settings, get_settings
from easysynq_api.db.models._notification_enums import NotificationEmailStatus
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.notification import (
    Notification,
    NotificationEmail,
    NotificationPreference,
)
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.constants import EVENT_EMAIL_DELIVERY_FAILED
from easysynq_api.services.notifications.drain import drain_once
from easysynq_api.services.notifications.mail import FakeMailSender

pytestmark = pytest.mark.integration

_T0 = datetime.datetime(2030, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _configured_settings() -> Settings:
    """Return settings with smtp_host set to a non-empty value.

    The testcontainer environment does not set SMTP_HOST, so get_settings() returns
    smtp_host="" which triggers the suppression path. Tests that exercise SENT/FAILED/retry
    paths need a configured transport so the drain proceeds past the suppression guard.
    """
    base = get_settings()
    return Settings(
        **{
            **base.model_dump(),
            "smtp_host": "test-smtp.local",
        }
    )


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _set_org_email_flag(org_id: uuid.UUID, *, enabled: bool) -> None:
    """The org email opt-in (default OFF). The drain's at-send eligibility re-check requires it ON
    for the eligible (SENT/retried/FAILED) paths; the suppressed/skipped cases don't need it."""
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is not None:
            cfg.notifications_email_enabled = enabled
            await s.commit()


async def _seed_user(org_id: uuid.UUID, salt: str) -> uuid.UUID:
    """Seed a minimal AppUser and return its id."""
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"drain-user-{salt}",
            display_name=f"Drain User {salt}",
            email=f"drain-user-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _seed_admin(org_id: uuid.UUID, salt: str) -> uuid.UUID:
    """Seed a System-Administrator-assigned user (needed for the FAILED emit path)."""
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"drain-admin-{salt}",
            display_name=f"Drain Admin {salt}",
            email=f"drain-admin-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.flush()
        role = (
            await s.execute(
                select(Role).where(Role.org_id == org_id, Role.name == "System Administrator")
            )
        ).scalar_one()
        s.add(
            RoleAssignment(
                org_id=org_id,
                user_id=user.id,
                role_id=role.id,
                bound_scope={"level": "SYSTEM"},
            )
        )
        await s.commit()
        return user.id


async def _seed_email_row(
    org_id: uuid.UUID,
    recipient_user_id: uuid.UUID,
    salt: str,
    *,
    attempts: int = 0,
    next_attempt_at: datetime.datetime | None = None,
) -> uuid.UUID:
    """Seed one Notification + one NotificationEmail(PENDING) and return the email row id."""
    async with get_sessionmaker()() as s:
        note = Notification(
            org_id=org_id,
            recipient_user_id=recipient_user_id,
            event_key="task.assigned",
            subject_type="DOCUMENT",
            subject_id=None,
            task_id=None,
            title=f"Test {salt}",
            body=f"Body {salt}",
            deep_link="/test",
        )
        s.add(note)
        await s.flush()
        email = NotificationEmail(
            org_id=org_id,
            notification_id=note.id,
            recipient_email=f"user-{salt}@example.com",
            subject=f"Subject {salt}",
            body=f"Body {salt}",
            attempts=attempts,
            next_attempt_at=next_attempt_at,
        )
        s.add(email)
        await s.commit()
        return email.id


async def _get_email(email_id: uuid.UUID) -> NotificationEmail:
    async with get_sessionmaker()() as s:
        row = await s.get(NotificationEmail, email_id)
        assert row is not None
        return row


# ---------------------------------------------------------------------------
# Case (a): a PENDING row with no next_attempt_at → SENT on success.
# ---------------------------------------------------------------------------


async def test_drain_sends_pending_row(app_under_test: AnyT) -> None:
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, salt)
    email_id = await _seed_email_row(org_id, user_id, salt)
    await _set_org_email_flag(org_id, enabled=True)  # at-send re-check needs the org flag ON

    sender = FakeMailSender()
    settings = _configured_settings()
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_T0)

    assert counts["sent"] >= 1
    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.SENT
    assert row.sent_at is not None
    assert row.attempts == 1
    assert len(sender.sent) >= 1
    assert any(m.to == f"user-{salt}@example.com" for m in sender.sent)


# ---------------------------------------------------------------------------
# Case (b): transient failure → PENDING; lease increments attempts BEFORE send.
# ---------------------------------------------------------------------------


async def test_drain_transient_failure_increments_attempts_and_stays_pending(
    app_under_test: AnyT,
) -> None:
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, salt)
    email_id = await _seed_email_row(org_id, user_id, salt)
    await _set_org_email_flag(org_id, enabled=True)  # at-send re-check needs the org flag ON

    # Sender that always raises (transient error).
    sender = FakeMailSender(fail_with=RuntimeError("smtp down"))
    settings = _configured_settings()
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_T0)

    assert counts["retried"] >= 1
    row = await _get_email(email_id)
    # Still PENDING (transient path, not exhausted).
    assert row.status == NotificationEmailStatus.PENDING
    # COUNT-BEFORE-SEND: attempts is 1 even though the send raised.
    assert row.attempts == 1
    assert row.last_error is not None
    # Backoff lease is set → a second drain at _T0 skips this row.
    assert row.next_attempt_at is not None
    assert row.next_attempt_at > _T0


# ---------------------------------------------------------------------------
# Case (c): exhausted → FAILED + system.email_delivery_failed emitted to admin.
# ---------------------------------------------------------------------------


async def test_drain_exhausted_marks_failed_and_emits_system_notification(
    app_under_test: AnyT,
) -> None:
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]

    # Seed a real user (notification FK) and a System Administrator (emit recipient).
    user_id = await _seed_user(org_id, salt)
    admin_id = await _seed_admin(org_id, salt)
    # The at-send re-check must pass so the drain reaches the attempts>=MAX → FAILED path.
    await _set_org_email_flag(org_id, enabled=True)

    settings = _configured_settings()
    max_attempts = settings.notification_max_send_attempts

    # Seed a row already at max_attempts (so the drain's first look sees attempts >= MAX).
    email_id = await _seed_email_row(org_id, user_id, salt, attempts=max_attempts)

    sender = FakeMailSender()  # healthy sender — emit_failure uses session, not sender
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_T0)

    assert counts["failed"] >= 1
    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.FAILED
    assert row.failed_at is not None

    # The system notification should have been emitted to the admin.
    async with get_sessionmaker()() as s:
        system_notes = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.org_id == org_id,
                        Notification.recipient_user_id == admin_id,
                        Notification.event_key == EVENT_EMAIL_DELIVERY_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(system_notes) >= 1


# ---------------------------------------------------------------------------
# Case (d): count-before-send invariant — attempts is incremented BEFORE send crash.
# ---------------------------------------------------------------------------


async def test_drain_attempts_incremented_before_send(app_under_test: AnyT) -> None:
    """Even when the send raises, attempts was already incremented (the count-before-send lease).
    This is already proven by case (b), but this test names the invariant explicitly."""
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, salt)
    email_id = await _seed_email_row(org_id, user_id, salt)
    await _set_org_email_flag(org_id, enabled=True)  # at-send re-check needs the org flag ON

    sender = FakeMailSender(fail_with=OSError("connection refused"))
    settings = _configured_settings()
    async with get_sessionmaker()() as session:
        await drain_once(session, sender, settings, now=_T0)

    row = await _get_email(email_id)
    assert row.attempts == 1, "attempts must be incremented before the SMTP call"
    assert row.status == NotificationEmailStatus.PENDING


# ---------------------------------------------------------------------------
# Case (e): a row whose next_attempt_at is in the future is skipped.
# ---------------------------------------------------------------------------


async def test_drain_skips_rows_with_future_next_attempt_at(app_under_test: AnyT) -> None:
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, salt)
    future = _T0 + datetime.timedelta(hours=1)
    email_id = await _seed_email_row(org_id, user_id, salt, next_attempt_at=future)

    sender = FakeMailSender()
    settings = _configured_settings()
    async with get_sessionmaker()() as session:
        await drain_once(session, sender, settings, now=_T0)

    row = await _get_email(email_id)
    # Row should be untouched (still PENDING, attempts=0).
    assert row.status == NotificationEmailStatus.PENDING
    assert row.attempts == 0
    # The sender should not have been called for this row.
    assert not any(m.to == f"user-{salt}@example.com" for m in sender.sent)


# ---------------------------------------------------------------------------
# Case (f): unconfigured SMTP (smtp_host="") → SUPPRESSED, no attempt, no noise.
# ---------------------------------------------------------------------------


async def test_drain_suppresses_when_smtp_unconfigured(app_under_test: AnyT) -> None:
    """If smtp_host is empty, drain marks the row SUPPRESSED without incrementing attempts
    and without emitting a system.email_delivery_failed admin notification."""
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, salt)
    # Seed an admin so _emit_failure would have a recipient (proves it is NOT called).
    admin_id = await _seed_admin(org_id, salt)
    email_id = await _seed_email_row(org_id, user_id, salt)

    sender = FakeMailSender()
    no_smtp = Settings(smtp_host="")
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, no_smtp, now=_T0)

    assert counts["suppressed"] >= 1
    assert counts["sent"] == 0
    assert counts["failed"] == 0

    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.SUPPRESSED
    assert row.attempts == 0, "suppressed rows must not increment attempts"
    assert sender.sent == [], "no email should be sent when smtp_host is empty"

    # No system.email_delivery_failed notification should have been emitted to the admin.
    async with get_sessionmaker()() as s:
        failure_notes = (
            (
                await s.execute(
                    select(Notification).where(
                        Notification.org_id == org_id,
                        Notification.recipient_user_id == admin_id,
                        Notification.event_key == EVENT_EMAIL_DELIVERY_FAILED,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert failure_notes == [], "no failure notification should be emitted for a suppressed row"


# ---------------------------------------------------------------------------
# Case (g): the at-send eligibility re-check (Fix B / Codex P2) — a PENDING row whose
# org flag is flipped OFF AFTER enqueue → SUPPRESSED on drain, no send, no failure emit.
# ---------------------------------------------------------------------------


async def test_drain_rechecks_org_flag_at_send_and_suppresses(app_under_test: AnyT) -> None:
    """A row enqueued while eligible, then the org email flag is disabled before the drain runs,
    must be SUPPRESSED at send time (an admin disabled email between enqueue and drain)."""
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, salt)
    email_id = await _seed_email_row(org_id, user_id, salt)

    # Org flag OFF at drain time (the at-send re-check evaluates the live SystemConfig).
    await _set_org_email_flag(org_id, enabled=False)

    sender = FakeMailSender()
    settings = _configured_settings()  # smtp configured → only the org flag is the disqualifier
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_T0)

    assert counts["suppressed"] >= 1
    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.SUPPRESSED
    assert row.attempts == 0, "a suppressed row must not increment attempts"
    assert not any(m.to == f"user-{salt}@example.com" for m in sender.sent)


# ---------------------------------------------------------------------------
# Case (h): the at-send re-check — a user who opted OUT after enqueue → SUPPRESSED.
# ---------------------------------------------------------------------------


async def test_drain_rechecks_user_opt_out_at_send_and_suppresses(app_under_test: AnyT) -> None:
    """A user with a NotificationPreference.email_enabled=False (opted out after enqueue) must be
    SUPPRESSED at send time even with the org flag ON + smtp configured."""
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, salt)
    email_id = await _seed_email_row(org_id, user_id, salt)
    await _set_org_email_flag(org_id, enabled=True)

    async with get_sessionmaker()() as s:
        s.add(NotificationPreference(user_id=user_id, email_enabled=False))
        await s.commit()

    sender = FakeMailSender()
    settings = _configured_settings()
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_T0)

    assert counts["suppressed"] >= 1
    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.SUPPRESSED
    assert row.attempts == 0
    assert not any(m.to == f"user-{salt}@example.com" for m in sender.sent)
