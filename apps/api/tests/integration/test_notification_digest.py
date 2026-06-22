"""Task 6 — class-aware enqueue: daily/immediate/off + quiet-hours hold + DOC_ACK email on.

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
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from easysynq_api.db.models._notification_enums import (
    NotificationDigestMode,
    NotificationEmailKind,
)
from easysynq_api.db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.notification import (
    Notification,
    NotificationEmail,
    NotificationPreference,
)
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.system_config import SystemConfig
from easysynq_api.db.models.workflow import Task, WorkflowDefinition, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications import dispatch
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
# Test 1: ACTION_REQUIRED default (daily) — digest_due_at set, NO email row
# ---------------------------------------------------------------------------


async def test_daily_default_sets_digest_due_at_no_email(app_under_test: Any) -> None:
    """ACTION_REQUIRED defaults to DAILY: in-app row gets digest_due_at, no NotificationEmail."""
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=True)
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


async def test_immediate_no_quiet_hours_creates_email(app_under_test: Any) -> None:
    """IMMEDIATE user with no quiet hours: one NotificationEmail created, next_attempt_at None."""
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=True)
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


async def test_immediate_in_quiet_window_defers_to_window_end(app_under_test: Any) -> None:
    """IMMEDIATE user with quiet hours covering now: next_attempt_at = window_end."""
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=True)
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


async def test_mode_off_no_email(app_under_test: Any) -> None:
    """Mode OFF: in-app Notification row is created but NO NotificationEmail."""
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=True)
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

    email_count = await _email_count_for_task(task.id)
    assert email_count == 0, f"Expected 0 email rows (mode OFF), got {email_count}"


# ---------------------------------------------------------------------------
# Test 5: DOC_ACK subject_type with IMMEDIATE mode → email IS created
# ---------------------------------------------------------------------------


async def test_doc_ack_immediate_creates_email(app_under_test: Any) -> None:
    """DOC_ACK with digest_mode_action_required=IMMEDIATE: a NotificationEmail IS created.

    The slice-1 DOC_ACK email exclusion (_DOC_ACK suppression) is removed in slice 3a.
    """
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=True)
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
