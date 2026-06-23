"""Task 6 — class-aware enqueue: daily/immediate/off + quiet-hours hold + DOC_ACK email on.
Task 7 — digest sweep: bundle due rows → one NotificationEmail(email_kind=DIGEST); idempotent.

Integration tests for the new dispatch.py behavior (S-notify-3a, spec §4/§5/§6):

1. ACTION_REQUIRED default (daily): after enqueue, the Notification row has digest_due_at set
   and there is NO NotificationEmail row for it.
2. A user with digest_mode_action_required=IMMEDIATE, no quiet hours, org email ON: exactly one
   NotificationEmail (PENDING, email_kind=single) is created, next_attempt_at is None.
3. Same IMMEDIATE user WITH quiet hours covering `now`: the NotificationEmail.next_attempt_at
   equals window_end(eff, now) — the email is held until quiet window ends.
4. Mode OFF: in-app row created, NO email.
5. DOC_ACK (subject_type=DOC_ACK) with digest_mode_action_required=IMMEDIATE: a NotificationEmail
   IS created (the slice-1 DOC_ACK exclusion is gone).
6. Sweep bundles a user's daily rows into ONE NotificationEmail(email_kind=DIGEST,
   notification_id IS NULL, recipient_user_id, item_count=N, PENDING); stamps digested_at.
7. A second sweep run for the same user is a no-op (idempotent — no new email).
8. Ineligible user (email_enabled=False between enqueue and sweep): rows stamped, no email.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from easysynq_api.config import Settings, get_settings
from easysynq_api.db.models._notification_enums import (
    NotificationDigestMode,
    NotificationEmailKind,
    NotificationEmailStatus,
)
from easysynq_api.db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.notification import (
    Notification,
    NotificationEmail,
    NotificationPreference,
)
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.models.workflow import Task, WorkflowDefinition, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications import dispatch
from easysynq_api.services.notifications.constants import EVENT_EMAIL_DELIVERY_FAILED
from easysynq_api.services.notifications.digest import sweep_digests
from easysynq_api.services.notifications.drain import drain_once
from easysynq_api.services.notifications.mail import FakeMailSender
from easysynq_api.services.notifications.preferences import effective_preferences
from easysynq_api.services.notifications.quiet import window_end

pytestmark = pytest.mark.integration

# A fixed "now" far in the past so quiet-window math is deterministic.
_NOW = datetime.datetime(2031, 1, 15, 14, 0, 0, tzinfo=datetime.UTC)  # 14:00 UTC Thursday

# Quiet window: 22:00-08:00 UTC (wraps midnight).  14:00 UTC is OUTSIDE (not quiet).
_QUIET_START = datetime.time(22, 0)
_QUIET_END = datetime.time(8, 0)

# A quiet window that COVERS _NOW: 13:00-15:00 UTC (14:00 UTC is inside).
_COVERING_START = datetime.time(13, 0)
_COVERING_END = datetime.time(15, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configured_settings() -> Settings:
    """Return settings with smtp_host set to a non-empty value.

    The testcontainer environment does not set SMTP_HOST so get_settings() returns smtp_host=""
    which triggers the suppress/skip path in bundle_user_digest. Sweep tests that expect an
    email to be created must pin this setting (the S-notify-1 test_notification_drain precedent).
    """
    base = get_settings()
    return Settings(**{**base.model_dump(), "smtp_host": "test-smtp.local"})


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _set_org_email_flag(org_id: uuid.UUID, *, enabled: bool) -> None:
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is not None:
            cfg.notifications_email_enabled = enabled
            await s.commit()


async def _seed_user(
    org_id: uuid.UUID, *, email: str | None = "digest-test@example.com"
) -> uuid.UUID:
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-digest-{salt}",
            display_name=f"Digest Test {salt}",
            email=email,
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _set_user_pref(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    mode: NotificationDigestMode | None = None,
    quiet_start: datetime.time | None = None,
    quiet_end: datetime.time | None = None,
    timezone: str = "UTC",
) -> None:
    """Upsert a NotificationPreference row for the user."""
    async with get_sessionmaker()() as s:
        pref = await s.get(NotificationPreference, user_id)
        if pref is None:
            pref = NotificationPreference(
                user_id=user_id,
            )
            s.add(pref)
        if mode is not None:
            pref.digest_mode_action_required = mode
        pref.quiet_start = quiet_start
        pref.quiet_end = quiet_end
        pref.timezone = timezone
        await s.commit()


async def _seed_workflow_objects(
    org_id: uuid.UUID,
    assignee_user_id: uuid.UUID,
    *,
    subject_type: WorkflowSubjectType = WorkflowSubjectType.DOCUMENT,
) -> tuple[WorkflowInstance, Task]:
    """Seed a minimal WorkflowDefinition + WorkflowInstance + Task for dispatch tests."""
    key = f"digest_test_{uuid.uuid4().hex[:8]}"
    async with get_sessionmaker()() as s:
        defn = WorkflowDefinition(
            org_id=org_id,
            key=key,
            version=1,
            effective=True,
            subject_type=subject_type,
            stages={"entry": "approve"},
        )
        s.add(defn)
        await s.flush()

        instance = WorkflowInstance(
            org_id=org_id,
            definition_id=defn.id,
            definition_version=1,
            subject_type=subject_type,
            subject_id=uuid.uuid4(),
            current_state="IN_APPROVAL",
        )
        s.add(instance)
        await s.flush()

        task = Task(
            org_id=org_id,
            instance_id=instance.id,
            stage_key="approve",
            assignee_user_id=assignee_user_id,
            type=TaskType.APPROVE,
            state=TaskState.PENDING,
        )
        s.add(task)
        await s.commit()

    # Reload in a fresh session
    async with get_sessionmaker()() as s2:
        inst2 = await s2.get(WorkflowInstance, instance.id)
        task2 = await s2.get(Task, task.id)
        return inst2, task2


async def _notif_for_task(task_id: uuid.UUID) -> Notification | None:
    async with get_sessionmaker()() as s:
        return (
            (await s.execute(select(Notification).where(Notification.task_id == task_id)))
            .scalars()
            .first()
        )


async def _email_count_for_task(task_id: uuid.UUID) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(NotificationEmail)
                .join(Notification, NotificationEmail.notification_id == Notification.id)
                .where(Notification.task_id == task_id)
            )
        ).scalar_one()


async def _email_for_task(task_id: uuid.UUID) -> NotificationEmail | None:
    async with get_sessionmaker()() as s:
        return (
            (
                await s.execute(
                    select(NotificationEmail)
                    .join(Notification, NotificationEmail.notification_id == Notification.id)
                    .where(Notification.task_id == task_id)
                )
            )
            .scalars()
            .first()
        )


# ---------------------------------------------------------------------------
# Fixture: restore org email flag after each test that enables it
# ---------------------------------------------------------------------------


@pytest.fixture()
async def org_email_on(app_under_test: Any) -> Any:  # type: ignore[misc]
    """Enable org email for the test, then restore it to False in teardown.

    Tests that call _set_org_email_flag(org_id, enabled=True) use this fixture
    instead of app_under_test so the flag is always reset, preventing cross-file
    ordering flakes in test_notification_config.py (which asserts the flag starts False).
    """
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=True)
    yield app_under_test
    await _set_org_email_flag(org_id, enabled=False)


# ---------------------------------------------------------------------------
# Test 1: ACTION_REQUIRED default (daily) — digest_due_at set, NO email row
# ---------------------------------------------------------------------------


async def test_daily_default_sets_digest_due_at_no_email(org_email_on: Any) -> None:
    """ACTION_REQUIRED defaults to DAILY: in-app row gets digest_due_at, no NotificationEmail."""
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="daily-default@example.com")
    # No preference row → defaults apply (action_required → DAILY)
    instance, task = await _seed_workflow_objects(org_id, user_id)

    async with get_sessionmaker()() as s:
        await dispatch.enqueue_task_notifications(s, instance, [task], now=_NOW)
        await s.commit()

    notif = await _notif_for_task(task.id)
    assert notif is not None, "Expected an in-app Notification row"
    assert notif.digest_due_at is not None, (
        "Expected digest_due_at to be set for daily-mode notification"
    )

    email_count = await _email_count_for_task(task.id)
    assert email_count == 0, (
        f"Expected 0 email rows (daily mode → no immediate email), got {email_count}"
    )


# ---------------------------------------------------------------------------
# Test 2: IMMEDIATE user, no quiet hours → email created, next_attempt_at is None
# ---------------------------------------------------------------------------


async def test_immediate_no_quiet_hours_creates_email(org_email_on: Any) -> None:
    """IMMEDIATE user with no quiet hours: one NotificationEmail created, next_attempt_at None."""
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="immediate-noq@example.com")
    await _set_user_pref(
        org_id,
        user_id,
        mode=NotificationDigestMode.IMMEDIATE,
        quiet_start=None,
        quiet_end=None,
    )
    instance, task = await _seed_workflow_objects(org_id, user_id)

    async with get_sessionmaker()() as s:
        await dispatch.enqueue_task_notifications(s, instance, [task], now=_NOW)
        await s.commit()

    notif = await _notif_for_task(task.id)
    assert notif is not None, "Expected an in-app Notification row"
    assert notif.digest_due_at is None, (
        "Expected digest_due_at to be None for immediate-mode notification"
    )

    email_count = await _email_count_for_task(task.id)
    assert email_count == 1, f"Expected 1 email row (immediate, no quiet), got {email_count}"

    email_row = await _email_for_task(task.id)
    assert email_row is not None
    assert email_row.next_attempt_at is None, (
        f"Expected next_attempt_at=None (not in quiet hours), got {email_row.next_attempt_at}"
    )
    assert email_row.email_kind == NotificationEmailKind.SINGLE, (
        f"Expected email_kind=SINGLE, got {email_row.email_kind}"
    )


# ---------------------------------------------------------------------------
# Test 3: IMMEDIATE user WITH quiet hours covering `now` → next_attempt_at = window_end
# ---------------------------------------------------------------------------


async def test_immediate_in_quiet_window_defers_to_window_end(org_email_on: Any) -> None:
    """IMMEDIATE user with quiet hours covering now: next_attempt_at = window_end."""
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="immediate-quiet@example.com")
    await _set_user_pref(
        org_id,
        user_id,
        mode=NotificationDigestMode.IMMEDIATE,
        quiet_start=_COVERING_START,
        quiet_end=_COVERING_END,
        timezone="UTC",
    )
    instance, task = await _seed_workflow_objects(org_id, user_id)

    async with get_sessionmaker()() as s:
        await dispatch.enqueue_task_notifications(s, instance, [task], now=_NOW)
        await s.commit()

    # Compute the expected window_end using the same pure helper
    async with get_sessionmaker()() as s:
        pref = await s.get(NotificationPreference, user_id)
    eff = effective_preferences(pref)
    expected_window_end = window_end(eff, _NOW)

    email_count = await _email_count_for_task(task.id)
    assert email_count == 1, f"Expected 1 email row (immediate, quiet-held), got {email_count}"

    email_row = await _email_for_task(task.id)
    assert email_row is not None
    assert email_row.next_attempt_at is not None, (
        "Expected next_attempt_at to be set (quiet hours hold)"
    )
    # Allow a 1-second tolerance for any sub-second rounding
    diff = abs((email_row.next_attempt_at - expected_window_end).total_seconds())
    assert diff < 2, (
        f"next_attempt_at {email_row.next_attempt_at!r} does not match "
        f"window_end {expected_window_end!r} (diff={diff}s)"
    )


# ---------------------------------------------------------------------------
# Test 4: Mode OFF → in-app row created, NO email
# ---------------------------------------------------------------------------


async def test_mode_off_no_email(org_email_on: Any) -> None:
    """Mode OFF: in-app Notification row is created but NO NotificationEmail."""
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="mode-off@example.com")
    await _set_user_pref(
        org_id,
        user_id,
        mode=NotificationDigestMode.OFF,
    )
    instance, task = await _seed_workflow_objects(org_id, user_id)

    async with get_sessionmaker()() as s:
        await dispatch.enqueue_task_notifications(s, instance, [task], now=_NOW)
        await s.commit()

    notif = await _notif_for_task(task.id)
    assert notif is not None, "Expected an in-app Notification row even with mode=OFF"
    assert notif.digest_due_at is None, (
        "Expected digest_due_at=None for OFF mode (not DAILY which sets it)"
    )

    email_count = await _email_count_for_task(task.id)
    assert email_count == 0, f"Expected 0 email rows (mode OFF), got {email_count}"


# ---------------------------------------------------------------------------
# Test 5: DOC_ACK subject_type with IMMEDIATE mode → email IS created
# ---------------------------------------------------------------------------


async def test_doc_ack_immediate_creates_email(org_email_on: Any) -> None:
    """DOC_ACK with digest_mode_action_required=IMMEDIATE: a NotificationEmail IS created.

    The slice-1 DOC_ACK email exclusion (_DOC_ACK suppression) is removed in slice 3a.
    """
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="docack-immediate@example.com")
    await _set_user_pref(
        org_id,
        user_id,
        mode=NotificationDigestMode.IMMEDIATE,
    )
    # Use DOC_ACK as the subject type so resolve_subject returns DOC_ACK kind
    instance, task = await _seed_workflow_objects(
        org_id, user_id, subject_type=WorkflowSubjectType.DOC_ACK
    )

    async with get_sessionmaker()() as s:
        await dispatch.enqueue_task_notifications(s, instance, [task], now=_NOW)
        await s.commit()

    notif = await _notif_for_task(task.id)
    assert notif is not None, "Expected an in-app Notification row for DOC_ACK"

    email_count = await _email_count_for_task(task.id)
    assert email_count == 1, (
        f"Expected 1 email row for DOC_ACK+IMMEDIATE (suppression removed), got {email_count}"
    )


# ---------------------------------------------------------------------------
# Helpers for sweep tests
# ---------------------------------------------------------------------------


async def _seed_daily_notification(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    digest_due_at: datetime.datetime,
    title: str = "Task: Review SOP-1",
    deep_link: str = "http://localhost/tasks",
) -> uuid.UUID:
    """Insert a Notification row already enrolled in a digest window (digest_due_at set)."""
    async with get_sessionmaker()() as s:
        note = Notification(
            org_id=org_id,
            recipient_user_id=user_id,
            event_key="task.assigned",
            subject_type="DOCUMENT",
            subject_id=uuid.uuid4(),
            title=title,
            body="",
            deep_link=deep_link,
            digest_due_at=digest_due_at,
        )
        s.add(note)
        await s.commit()
        return note.id


async def _digest_email_count_for_user(user_id: uuid.UUID) -> int:
    """Count NotificationEmail rows of kind=DIGEST for this recipient_user_id."""
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                sa.select(sa.func.count())
                .select_from(NotificationEmail)
                .where(
                    NotificationEmail.recipient_user_id == user_id,
                    NotificationEmail.email_kind == NotificationEmailKind.DIGEST,
                )
            )
        ).scalar_one()


async def _get_notification(note_id: uuid.UUID) -> Notification | None:
    async with get_sessionmaker()() as s:
        return await s.get(Notification, note_id)


# ---------------------------------------------------------------------------
# Test 6: sweep bundles daily rows into ONE DIGEST NotificationEmail
# ---------------------------------------------------------------------------

# "now" for sweep tests — past enough that digest_due_at is always in the past.
_SWEEP_NOW = datetime.datetime(2031, 2, 1, 9, 0, 0, tzinfo=datetime.UTC)
# digest_due_at is set one hour before _SWEEP_NOW so rows are clearly due.
_DUE_AT = _SWEEP_NOW - datetime.timedelta(hours=1)


async def test_sweep_bundles_daily_rows_into_one_digest_email(org_email_on: Any) -> None:
    """Sweep creates ONE DIGEST NotificationEmail for the user, stamps digested_at on all rows."""
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="sweep-bundle@example.com")

    note1_id = await _seed_daily_notification(
        org_id, user_id, digest_due_at=_DUE_AT, title="Approve SOP-1"
    )
    note2_id = await _seed_daily_notification(
        org_id, user_id, digest_due_at=_DUE_AT, title="Approve POL-2"
    )

    summary = await sweep_digests(get_sessionmaker(), _configured_settings(), _SWEEP_NOW)

    # At least 1 user processed, at least 1 email created (may be more from other tests,
    # but this user's rows must be included — assert on the user-specific email count).
    assert summary["users"] >= 1
    assert summary["emails"] >= 1

    digest_count = await _digest_email_count_for_user(user_id)
    assert digest_count == 1, f"Expected exactly 1 DIGEST email for user, got {digest_count}"

    # Check the email fields
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(
                select(NotificationEmail).where(
                    NotificationEmail.recipient_user_id == user_id,
                    NotificationEmail.email_kind == NotificationEmailKind.DIGEST,
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.notification_id is None, "Digest email must have notification_id IS NULL"
    assert row.item_count == 2, f"Expected item_count=2, got {row.item_count}"
    assert row.status == NotificationEmailStatus.PENDING  # type: ignore[attr-defined]

    # Both notifications stamped with digested_at
    n1 = await _get_notification(note1_id)
    n2 = await _get_notification(note2_id)
    assert n1 is not None and n1.digested_at is not None, "note1 must have digested_at set"
    assert n2 is not None and n2.digested_at is not None, "note2 must have digested_at set"


# ---------------------------------------------------------------------------
# Test 7: second sweep run is a no-op (idempotent)
# ---------------------------------------------------------------------------


async def test_sweep_is_idempotent(org_email_on: Any) -> None:
    """A second sweep for the same user finds no pending rows → no new email created."""
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="sweep-idempotent@example.com")

    await _seed_daily_notification(org_id, user_id, digest_due_at=_DUE_AT, title="Approve SOP-3")

    # First sweep
    summary1 = await sweep_digests(get_sessionmaker(), _configured_settings(), _SWEEP_NOW)
    assert summary1["emails"] >= 1

    email_count_after_first = await _digest_email_count_for_user(user_id)
    assert email_count_after_first == 1

    # Second sweep — rows already stamped; no new email
    summary2 = await sweep_digests(get_sessionmaker(), _configured_settings(), _SWEEP_NOW)
    _ = summary2  # may be 0 users/emails or include other users; only assert on this user

    email_count_after_second = await _digest_email_count_for_user(user_id)
    assert email_count_after_second == 1, (
        f"Second sweep must not create a new digest email; got {email_count_after_second}"
    )


# ---------------------------------------------------------------------------
# Test 8: ineligible user (email_enabled=False) — rows stamped, no email
# ---------------------------------------------------------------------------


async def test_sweep_ineligible_user_stamped_but_no_email(org_email_on: Any) -> None:
    """When the user has email_enabled=False at sweep time: rows get digested_at but no email."""
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="sweep-ineligible@example.com")

    note_id = await _seed_daily_notification(
        org_id, user_id, digest_due_at=_DUE_AT, title="Approve DOC-5"
    )

    # Disable email between enqueue and sweep
    async with get_sessionmaker()() as s:
        pref = NotificationPreference(user_id=user_id, email_enabled=False)
        s.add(pref)
        await s.commit()

    await sweep_digests(get_sessionmaker(), _configured_settings(), _SWEEP_NOW)

    # No digest email for this user
    digest_count = await _digest_email_count_for_user(user_id)
    assert digest_count == 0, f"Expected 0 DIGEST emails for ineligible user, got {digest_count}"

    # But the notification row must still be stamped
    note = await _get_notification(note_id)
    assert note is not None and note.digested_at is not None, (
        "digested_at must be set even when user is ineligible"
    )


# ---------------------------------------------------------------------------
# Helpers for digest-drain tests
# ---------------------------------------------------------------------------


async def _seed_admin(org_id: uuid.UUID, salt: str) -> uuid.UUID:
    """Seed a System-Administrator-assigned user (needed for the FAILED emit path)."""
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"digest-drain-admin-{salt}",
            display_name=f"Digest Drain Admin {salt}",
            email=f"digest-drain-admin-{salt}@example.com",
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


async def _seed_digest_email(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    recipient_email: str,
    *,
    attempts: int = 0,
) -> uuid.UUID:
    """Seed a PENDING NotificationEmail(email_kind=DIGEST, notification_id=NULL) directly."""
    async with get_sessionmaker()() as s:
        email = NotificationEmail(
            org_id=org_id,
            notification_id=None,
            recipient_user_id=user_id,
            recipient_email=recipient_email,
            subject="Your daily digest",
            body="You have 2 new tasks.",
            email_kind=NotificationEmailKind.DIGEST,
            item_count=2,
            attempts=attempts,
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
# Test 9 (digest_drain): a PENDING digest email drains to SENT — not SUPPRESSED
# ---------------------------------------------------------------------------


async def test_digest_drain_sends(org_email_on: Any) -> None:
    """A PENDING DIGEST NotificationEmail (notification_id=NULL, recipient_user_id set,
    org email ON, smtp configured) must drain to SENT via drain_once — NOT be suppressed."""
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]
    user_id = await _seed_user(org_id, email=f"digest-drain-{salt}@example.com")
    email_id = await _seed_digest_email(org_id, user_id, f"digest-drain-{salt}@example.com")

    sender = FakeMailSender()
    settings = _configured_settings()
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_SWEEP_NOW)

    assert counts["sent"] >= 1
    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.SENT, (
        f"Expected SENT, got {row.status} — digest row was wrongly suppressed"
    )
    assert row.sent_at is not None
    assert row.attempts == 1
    assert any(m.to == f"digest-drain-{salt}@example.com" for m in sender.sent)


# ---------------------------------------------------------------------------
# Test 10 (digest_drain): exhausted digest email → FAILED + admin notification with digest ref
# ---------------------------------------------------------------------------


async def test_digest_drain_failure_emits_to_admins(org_email_on: Any) -> None:
    """A digest email row at max_attempts → FAILED + system.email_delivery_failed emitted
    to org admins; the context notification_id must be 'digest:<id>', not None/NULL."""
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]

    user_id = await _seed_user(org_id, email=f"digest-exhaust-{salt}@example.com")
    admin_id = await _seed_admin(org_id, salt)

    settings = _configured_settings()
    max_attempts = settings.notification_max_send_attempts

    email_id = await _seed_digest_email(
        org_id, user_id, f"digest-exhaust-{salt}@example.com", attempts=max_attempts
    )

    sender = FakeMailSender()
    async with get_sessionmaker()() as session:
        counts = await drain_once(session, sender, settings, now=_SWEEP_NOW)

    assert counts["failed"] >= 1
    row = await _get_email(email_id)
    assert row.status == NotificationEmailStatus.FAILED
    assert row.failed_at is not None

    # A system.email_delivery_failed notification must have been emitted to the admin.
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
    assert len(system_notes) >= 1, (
        "_emit_failure returned early for a digest row — admin was not notified"
    )

    # The context notification_id must be 'digest:<id>', not a NULL or plain None string.
    note = system_notes[0]
    ctx = note.context or {}
    ref = ctx.get("notification_id", "")
    assert ref == f"digest:{email_id}", f"Expected notification_id='digest:{email_id}', got {ref!r}"


# ---------------------------------------------------------------------------
# Test 11 (FIX D): whitespace-only display_name does not raise IndexError
# ---------------------------------------------------------------------------


async def test_sweep_whitespace_display_name_uses_there_fallback(org_email_on: Any) -> None:
    """FIX D: a user with display_name='   ' (whitespace-only) must not raise IndexError.

    bundle_user_digest renders the digest with first_name='there' and stamps digested_at.
    The sweep must complete without exception and must create the digest email.
    """
    org_id = await _default_org_id()
    salt = uuid.uuid4().hex[:8]

    # Seed a user with a whitespace-only display_name directly in the DB.
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-ws-{salt}",
            display_name="   ",  # whitespace-only — triggers IndexError without the fix
            email=f"ws-display-{salt}@example.com",
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        user_id = user.id

    note_id = await _seed_daily_notification(
        org_id, user_id, digest_due_at=_DUE_AT, title="Review SOP-WS"
    )

    # sweep_digests must complete without raising.
    summary = await sweep_digests(get_sessionmaker(), _configured_settings(), _SWEEP_NOW)
    # At minimum this user's row was processed (summary["users"] includes them).
    assert summary["users"] >= 1

    # The digest email for this user must have been created.
    digest_count = await _digest_email_count_for_user(user_id)
    assert digest_count == 1, (
        f"Expected 1 DIGEST email for whitespace-display-name user, got {digest_count}"
    )

    # The notification row must be stamped.
    note = await _get_notification(note_id)
    assert note is not None and note.digested_at is not None, (
        "digested_at must be set for whitespace-display-name user's notification"
    )

    # Verify the rendered subject/body uses 'there', not an empty or errored string.
    async with get_sessionmaker()() as s:
        email_row = (
            await s.execute(
                select(NotificationEmail).where(
                    NotificationEmail.recipient_user_id == user_id,
                    NotificationEmail.email_kind == NotificationEmailKind.DIGEST,
                )
            )
        ).scalar_one_or_none()
    assert email_row is not None
    assert "there" in email_row.body, (
        f"Expected 'there' in digest body when display_name is whitespace; body={email_row.body!r}"
    )
