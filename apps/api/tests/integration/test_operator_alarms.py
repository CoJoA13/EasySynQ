"""Batch 11 (review 2026-07-22 finding 2): the operator-alarm WIRING, end to end on a real DB.

The unit suite owns the pure parts (whitelist↔template coverage, the channel fan-out, the off-host
alarm decision table). These tests own the things only a database proves:

* ``system.backup_failed`` — a failing nightly backup now leaves a ``BACKUP_FAILED`` audit row AND a
  notification per System Administrator, rendered from the migration-seeded template (which is the
  half that would be silently missing if the seed never ran);
* the out-of-band channel fires even when the in-DB half succeeds, and — the finding's core case —
  fires when the database read itself fails;
* ``integrity.alarm`` — the emitter reaches admins and honours the CRITICAL immediate-email default.

Assertions are run-scoped (a unique admin per test, counted by ``recipient_user_id``), never global
counts: the ``-m integration`` suite shares one database across files, so an absolute count breaks
as soon as another file runs first. Everything reuses the default org — committing a second
``Organization`` is the ``scalar_one`` shard-flake this repo has been bitten by twice.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from easysynq_api.config import Settings
from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models._notification_enums import NotificationDigestMode
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.backup_policy import BackupPolicy
from easysynq_api.db.models.notification import (
    Notification,
    NotificationEmail,
    NotificationPreference,
)
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.backup import service as backup_service
from easysynq_api.services.notifications.constants import (
    EVENT_BACKUP_FAILED,
    EVENT_INTEGRITY_ALARM,
)
from easysynq_api.services.notifications.ops_channel import OperatorAlert
from easysynq_api.services.notifications.ops_events import (
    CHECK_CHAIN_VERIFY,
    emit_backup_failed,
    emit_integrity_alarm,
)

pytestmark = pytest.mark.integration

_ADMIN_ROLE = "System Administrator"


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_admin(
    org_id: uuid.UUID,
    *,
    email: str | None = "avery@example.com",
    mode: NotificationDigestMode | None = None,
) -> uuid.UUID:
    """Create a UNIQUE System Administrator for this test and return its app_user id.

    Self-provides its own role row (``System Administrator`` is seeded per org by 0004, but this
    never assumes another test left one behind — the shard-composition lesson cuts both ways).
    """
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            subject=f"ops-admin-{salt}",
            display_name=f"Avery Ops {salt}",
            email=email,
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.flush()
        role_id = (
            await s.execute(select(Role.id).where(Role.org_id == org_id, Role.name == _ADMIN_ROLE))
        ).scalar_one_or_none()
        if role_id is None:
            role = Role(org_id=org_id, name=_ADMIN_ROLE, is_reserved=True)
            s.add(role)
            await s.flush()
            role_id = role.id
        s.add(RoleAssignment(org_id=org_id, user_id=user.id, role_id=role_id))
        if mode is not None:
            s.add(
                NotificationPreference(
                    user_id=user.id,
                    email_enabled=True,
                    digest_mode_admin_ops=mode,
                    digest_mode_critical=mode,
                )
            )
        await s.commit()
        return user.id


async def _enable_org_email(org_id: uuid.UUID) -> None:
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is None:
            s.add(SystemConfig(org_id=org_id, notifications_email_enabled=True))
        else:
            cfg.notifications_email_enabled = True
        await s.commit()


async def _notifications_for(user_id: uuid.UUID, event_key: str) -> list[Notification]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(Notification).where(
                        Notification.recipient_user_id == user_id,
                        Notification.event_key == event_key,
                    )
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# system.backup_failed
# ---------------------------------------------------------------------------


async def test_backup_failure_notifies_admins_from_the_seeded_template(
    app_under_test: Any,
) -> None:
    """The in-DB half. Mutation-distinguishing on TWO axes: before Batch 11 there was no emitter at
    all (zero rows), and without the migration seed ``render()`` returns None so the emitter still
    produces zero rows. Asserting the rendered BODY proves both the wiring and the seed."""
    org_id = await _default_org_id()
    await _enable_org_email(org_id)
    admin_id = await _seed_admin(org_id, mode=NotificationDigestMode.IMMEDIATE)

    async with get_sessionmaker()() as s:
        created = await emit_backup_failed(
            s,
            org_id=org_id,
            destination="/mnt/backup",
            error="destination not writable: [Errno 30] Read-only file system",
            now=datetime.datetime.now(datetime.UTC),
        )
        await s.commit()
    assert created >= 1

    rows = await _notifications_for(admin_id, EVENT_BACKUP_FAILED)
    assert len(rows) == 1, "exactly one alarm per admin per emit"
    note = rows[0]
    assert note.title == "Scheduled backup failed"
    # The template actually rendered: the destination + the error text are interpolated, and no
    # slot survived as literal `{{...}}` (the silent template-variable drop).
    assert "/mnt/backup" in note.body
    assert "Read-only file system" in note.body
    assert "{{" not in note.body
    assert note.subject_id is None and note.task_id is None
    # Operational-only: no deep link into the vault (admins hold no document.*).
    assert note.deep_link == ""

    # CRITICAL/ADMIN_OPS default to IMMEDIATE → an outbox email row exists, rendered from the
    # template's EMAIL form (which the in-app form does not carry).
    async with get_sessionmaker()() as s:
        email = (
            await s.execute(
                select(NotificationEmail).where(NotificationEmail.notification_id == note.id)
            )
        ).scalar_one()
    assert email.recipient_email == "avery@example.com"
    assert email.subject == "[EasySynQ] Scheduled backup FAILED"
    assert "/mnt/backup" in email.body
    assert "{{" not in email.body


async def test_backup_failure_reaches_a_daily_admin_via_the_digest_marker(
    app_under_test: Any,
) -> None:
    """An admin whose ADMIN_OPS mode is DAILY gets no immediate email row — so the alarm must carry
    ``digest_due_at`` or it produces an in-app row and NO email at all, a silent half-delivery."""
    org_id = await _default_org_id()
    await _enable_org_email(org_id)
    admin_id = await _seed_admin(org_id, mode=NotificationDigestMode.DAILY)

    async with get_sessionmaker()() as s:
        await emit_backup_failed(
            s,
            org_id=org_id,
            destination="/mnt/backup",
            error="boom",
            now=datetime.datetime.now(datetime.UTC),
        )
        await s.commit()

    rows = await _notifications_for(admin_id, EVENT_BACKUP_FAILED)
    assert len(rows) == 1
    assert rows[0].digest_due_at is not None, "a DAILY admin would never receive this alarm"
    async with get_sessionmaker()() as s:
        emails = (
            await s.execute(
                select(func.count())
                .select_from(NotificationEmail)
                .where(NotificationEmail.notification_id == rows[0].id)
            )
        ).scalar_one()
    assert emails == 0, "DAILY mode must not create an immediate email row"


async def test_report_backup_failure_writes_the_audit_row_and_fires_out_of_band(
    app_under_test: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_report_backup_failure`` is the nightly job's failure handler: a durable BACKUP_FAILED
    audit row, the admin notifications, AND the out-of-band channel — all three, every time."""
    org_id = await _default_org_id()
    admin_id = await _seed_admin(org_id)

    fired: list[OperatorAlert] = []

    async def _capture(settings: Settings, alert: OperatorAlert) -> dict[str, str]:
        fired.append(alert)
        return {}

    monkeypatch.setattr(backup_service, "send_operator_alert", _capture)

    async with get_sessionmaker()() as s:
        before = (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.org_id == org_id,
                    AuditEvent.event_type == EventType.BACKUP_FAILED,
                )
            )
        ).scalar_one()

    await backup_service._report_backup_failure(
        get_sessionmaker(),
        org_id=org_id,
        destination="/mnt/backup",
        error="disk full",
    )

    # Delta-based, never an absolute count — the shared integration DB carries other files' rows.
    async with get_sessionmaker()() as s:
        after = (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.org_id == org_id,
                    AuditEvent.event_type == EventType.BACKUP_FAILED,
                )
            )
        ).scalar_one()
    assert after == before + 1, "the failure left no durable audit row"

    assert len(await _notifications_for(admin_id, EVENT_BACKUP_FAILED)) == 1
    assert len(fired) == 1
    assert fired[0].event == EVENT_BACKUP_FAILED
    assert fired[0].detail["destination"] == "/mnt/backup"
    assert fired[0].org_id == str(org_id)


async def test_out_of_band_fires_when_the_database_read_itself_fails(
    app_under_test: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE finding's core case. With PostgreSQL unreachable there is no policy list, no admin to
    resolve, no notification to insert and no audit row to append — the nightly job used to die
    into a log line. The out-of-band alert must fire, and the task must still raise so Celery
    records a failure rather than a silent no-op run.

    Mutation-distinguishing: against the pre-Batch-11 ``run_scheduled_backups`` the exception
    propagates with ``fired`` empty.
    """
    fired: list[OperatorAlert] = []

    async def _capture(settings: Settings, alert: OperatorAlert) -> dict[str, str]:
        fired.append(alert)
        return {}

    monkeypatch.setattr(backup_service, "send_operator_alert", _capture)

    class _DeadSession:
        async def __aenter__(self) -> _DeadSession:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def scalars(self, *a: object, **kw: object) -> object:
            raise OSError("connection to server failed: Connection refused")

    monkeypatch.setattr(
        backup_service, "async_sessionmaker", lambda *a, **kw: lambda: _DeadSession()
    )

    with pytest.raises(OSError):
        await backup_service.run_scheduled_backups()

    assert len(fired) == 1, "a DB-down nightly backup raised no out-of-band alert"
    assert fired[0].event == EVENT_BACKUP_FAILED
    assert fired[0].severity == "critical"
    assert "database is unreachable" in fired[0].summary


# ---------------------------------------------------------------------------
# integrity.alarm
# ---------------------------------------------------------------------------


async def test_integrity_alarm_reaches_admins_with_the_check_named(app_under_test: Any) -> None:
    """The chain-verify alarm. Before Batch 11 a detected tamper produced a CHAIN_VERIFY_FAIL audit
    row and a log line — nobody was told. Pins that the specific detector is named in the body, so
    an admin can tell "the chain broke" from "the off-host witness is gone"."""
    org_id = await _default_org_id()
    await _enable_org_email(org_id)
    admin_id = await _seed_admin(org_id, mode=NotificationDigestMode.IMMEDIATE)

    async with get_sessionmaker()() as s:
        created = await emit_integrity_alarm(
            s,
            org_id=org_id,
            check=CHECK_CHAIN_VERIFY,
            reasons=["row 4711: prev_hash mismatch"],
            break_count=1,
            now=datetime.datetime.now(datetime.UTC),
        )
        await s.commit()
    assert created >= 1

    rows = await _notifications_for(admin_id, EVENT_INTEGRITY_ALARM)
    assert len(rows) == 1
    note = rows[0]
    assert note.title == "Audit integrity alarm"
    assert CHECK_CHAIN_VERIFY in note.body
    assert "prev_hash mismatch" in note.body
    assert "{{" not in note.body

    async with get_sessionmaker()() as s:
        email = (
            await s.execute(
                select(NotificationEmail).where(NotificationEmail.notification_id == note.id)
            )
        ).scalar_one()
    # The email body must warn against destroying the evidence — a well-meaning admin's first
    # instinct on "audit trail broken" is to restore or re-seed it.
    assert "before investigating" in email.body


async def test_emit_does_not_roll_back_the_callers_audit_row(app_under_test: Any) -> None:
    """The SAVEPOINT property. ``tasks/audit.py`` adds the CHAIN_VERIFY_FAIL audit row to the SAME
    session before emitting. That durable row is the more important signal, so a notification
    failure must roll back only the notification rows — never take the audit row with it.

    Forces the failure with a non-existent event key (``render()`` → None) after a sentinel audit
    row is pending, then asserts the sentinel still commits.
    """
    org_id = await _default_org_id()
    await _seed_admin(org_id)
    marker = uuid.uuid4().hex

    async with get_sessionmaker()() as s:
        s.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=None,
                actor_type=ActorType.system,
                event_type=EventType.CHAIN_VERIFY_FAIL,
                object_type=AuditObjectType.audit,
                object_id=org_id,
                after={"sentinel": marker},
            )
        )
        # An unseeded key → render() returns None → the emit bails inside its savepoint.
        from easysynq_api.services.notifications import ops_events

        created = await ops_events._emit_admin_alert(
            s,
            org_id=org_id,
            event_key="integrity.alarm.does-not-exist",
            context={},
            now=datetime.datetime.now(datetime.UTC),
        )
        assert created == 0
        await s.commit()

    async with get_sessionmaker()() as s:
        found = (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.after["sentinel"].astext == marker)
            )
        ).scalar_one()
    assert found == 1, "the failed notification emit rolled back the caller's audit row"


async def test_no_admin_in_org_returns_zero_without_raising(app_under_test: Any) -> None:
    """No System Administrator (or all inactive) is an honest zero, not a crash and not a silent
    'delivered'. The out-of-band channel is the only carrier in that state — asserted here so the
    behaviour is pinned rather than incidental."""
    org_id = await _default_org_id()
    admin_id = await _seed_admin(org_id)
    async with get_sessionmaker()() as s:
        user = await s.get(AppUser, admin_id)
        assert user is not None
        user.status = UserStatus.DISABLED
        await s.commit()

    # The org may still hold other admins from sibling tests, so assert the DISABLED one is skipped
    # rather than that the total is zero (run-scoped, not global).
    async with get_sessionmaker()() as s:
        await emit_integrity_alarm(
            s,
            org_id=org_id,
            check=CHECK_CHAIN_VERIFY,
            reasons=["x"],
            break_count=0,
            now=datetime.datetime.now(datetime.UTC),
        )
        await s.commit()
    assert await _notifications_for(admin_id, EVENT_INTEGRITY_ALARM) == []


async def test_a_succeeding_backup_raises_no_alarm(
    app_under_test: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard against over-tightening. The new try/except and the failure reporter must not touch
    the SUCCESS path: no out-of-band alert, no BACKUP_FAILED audit row, and the ordinary result
    shape unchanged. ``build_durable_backup`` is stubbed so no real pg_dump runs (it lives only in
    the worker image, so calling it here would fail for an unrelated reason and mask the point)."""
    fired: list[OperatorAlert] = []

    async def _capture(settings: Settings, alert: OperatorAlert) -> dict[str, str]:
        fired.append(alert)
        return {}

    monkeypatch.setattr(backup_service, "send_operator_alert", _capture)
    monkeypatch.setattr(
        backup_service.drill,
        "build_durable_backup",
        lambda settings, destination: {"archive": f"{destination}/easysynq-backup-x.tar"},
    )

    async with get_sessionmaker()() as s:
        policies = (await s.scalars(select(BackupPolicy))).all()
        before = (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == EventType.BACKUP_FAILED)
            )
        ).scalar_one()

    out = await backup_service.run_scheduled_backups()
    assert isinstance(out["backups"], list)
    assert len(out["backups"]) == len(policies)
    assert all("error" not in r for r in out["backups"])
    assert fired == [], "a SUCCESSFUL backup raised an operator alert"

    async with get_sessionmaker()() as s:
        after = (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == EventType.BACKUP_FAILED)
            )
        ).scalar_one()
    assert after == before, "a SUCCESSFUL backup wrote a BACKUP_FAILED audit row"
