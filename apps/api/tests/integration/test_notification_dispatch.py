"""dispatch.enqueue_task_notifications — gates + the SAVEPOINT non-poisoning property (spec §4).

Tests run against a real migrated PG16 via testcontainers. The migration seeds the
``task.assigned`` template, so no manual template row is needed.

Three tests:
1. In-app + email rows are written when org email is enabled + user has an address + IMMEDIATE mode.
2. Email row is suppressed (in-app only) when the org flag is OFF.
3. SAVEPOINT non-poisoning: a render error rolls back only the savepoint; a sentinel row
   added to the same session BEFORE the call still commits.

S-notify-3a update: action_required now defaults to DAILY. Test 1 sets IMMEDIATE explicitly to
preserve the immediate-email assertion. Test 2 (org-flag-off) is unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from easysynq_api.db.models._notification_enums import NotificationDigestMode
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
from easysynq_api.services.notifications.recipients import resolve_recipients

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_user(org_id: uuid.UUID, *, email: str | None = "test@example.com") -> uuid.UUID:
    """Create a unique AppUser with an optional email address."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-notify-{salt}",
            display_name=f"Notify Test {salt}",
            email=email,
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _seed_workflow_objects(
    org_id: uuid.UUID, assignee_user_id: uuid.UUID
) -> tuple[WorkflowInstance, Task]:
    """Seed a minimal WorkflowDefinition + WorkflowInstance + Task for dispatch tests."""
    key = f"notify_test_{uuid.uuid4().hex[:8]}"
    async with get_sessionmaker()() as s:
        defn = WorkflowDefinition(
            org_id=org_id,
            key=key,
            version=1,
            effective=True,
            subject_type=WorkflowSubjectType.DOCUMENT,
            stages={"entry": "approve"},
        )
        s.add(defn)
        await s.flush()

        instance = WorkflowInstance(
            org_id=org_id,
            definition_id=defn.id,
            definition_version=1,
            subject_type=WorkflowSubjectType.DOCUMENT,
            subject_id=uuid.uuid4(),  # phantom id — resolve_subject degrades gracefully
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

        # Reload in new session so ORM attributes are fresh
        async with get_sessionmaker()() as s2:
            inst2 = await s2.get(WorkflowInstance, instance.id)
            task2 = await s2.get(Task, task.id)
            return inst2, task2


async def _set_org_email_flag(org_id: uuid.UUID, *, enabled: bool) -> None:
    async with get_sessionmaker()() as s:
        cfg = await s.get(SystemConfig, org_id)
        if cfg is not None:
            cfg.notifications_email_enabled = enabled
            await s.commit()


async def _set_immediate_mode(user_id: uuid.UUID) -> None:
    """Set digest_mode_action_required=IMMEDIATE so task.assigned emails fire right away.

    S-notify-3a: action_required defaults to DAILY; tests that need an immediate email
    must opt the user in explicitly.
    """
    async with get_sessionmaker()() as s:
        pref = await s.get(NotificationPreference, user_id)
        if pref is None:
            pref = NotificationPreference(user_id=user_id)
            s.add(pref)
        pref.digest_mode_action_required = NotificationDigestMode.IMMEDIATE
        await s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_enqueue_writes_in_app_and_email_when_enabled(app_under_test: Any) -> None:
    """When org email is on + user has an address + mode IMMEDIATE, both in-app and email rows.

    S-notify-3a: action_required defaults to DAILY; set IMMEDIATE explicitly to keep the
    immediate-email assertion valid.
    """
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=True)
    user_id = await _seed_user(org_id, email="recipient@example.com")
    await _set_immediate_mode(user_id)
    instance, task = await _seed_workflow_objects(org_id, user_id)

    async with get_sessionmaker()() as s:
        await dispatch.enqueue_task_notifications(s, instance, [task])
        await s.commit()

    async with get_sessionmaker()() as s:
        notif_count = (
            await s.execute(
                select(sa.func.count())
                .select_from(Notification)
                .where(
                    Notification.recipient_user_id == user_id,
                    Notification.task_id == task.id,
                )
            )
        ).scalar_one()
        email_count = (
            await s.execute(
                select(sa.func.count())
                .select_from(NotificationEmail)
                .join(Notification, NotificationEmail.notification_id == Notification.id)
                .where(Notification.task_id == task.id)
            )
        ).scalar_one()

    assert notif_count == 1, f"Expected 1 in-app notification, got {notif_count}"
    assert email_count == 1, f"Expected 1 email row, got {email_count}"


async def test_email_suppressed_when_org_flag_off(app_under_test: Any) -> None:
    """When org email flag is OFF, the notification row is created but no email row."""
    org_id = await _default_org_id()
    await _set_org_email_flag(org_id, enabled=False)
    user_id = await _seed_user(org_id, email="suppressed@example.com")
    instance, task = await _seed_workflow_objects(org_id, user_id)

    async with get_sessionmaker()() as s:
        await dispatch.enqueue_task_notifications(s, instance, [task])
        await s.commit()

    async with get_sessionmaker()() as s:
        notif_count = (
            await s.execute(
                select(sa.func.count())
                .select_from(Notification)
                .where(
                    Notification.recipient_user_id == user_id,
                    Notification.task_id == task.id,
                )
            )
        ).scalar_one()
        email_count = (
            await s.execute(
                select(sa.func.count())
                .select_from(NotificationEmail)
                .join(Notification, NotificationEmail.notification_id == Notification.id)
                .where(Notification.task_id == task.id)
            )
        ).scalar_one()

    assert notif_count == 1, f"Expected 1 in-app notification, got {notif_count}"
    assert email_count == 0, f"Expected 0 email rows (org email off), got {email_count}"


async def test_render_error_does_not_block_parent_txn(
    app_under_test: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A render error rolls back only the SAVEPOINT; a sentinel row added before the call persists.

    This is the load-bearing SAVEPOINT non-poisoning proof (spec §4): enqueue must NEVER raise,
    and the parent txn must be able to commit its own writes regardless of a notification failure.
    """
    org_id = await _default_org_id()
    user_id = await _seed_user(org_id, email="sentinel@example.com")
    instance, task = await _seed_workflow_objects(org_id, user_id)

    # Monkeypatch render to raise inside the SAVEPOINT.
    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated render failure")

    monkeypatch.setattr(dispatch, "render", _boom)

    # A sentinel notification added BEFORE enqueue — must survive.
    sentinel_id = uuid.uuid4()
    async with get_sessionmaker()() as s:
        sentinel = Notification(
            id=sentinel_id,
            org_id=org_id,
            recipient_user_id=user_id,
            event_key="sentinel.test",
            subject_type="SYSTEM",
            subject_id=None,
            task_id=None,
            title="sentinel",
            body="sentinel body",
            deep_link="/",
            template_id=None,
            template_version=None,
            context=None,
        )
        s.add(sentinel)
        # Call enqueue — must not raise even though render blows up.
        await dispatch.enqueue_task_notifications(s, instance, [task])
        await s.commit()  # parent txn must commit cleanly

    # Verify: sentinel persisted, but no notification row for the task (savepoint rolled back).
    async with get_sessionmaker()() as s:
        sentinel_exists = (
            await s.execute(select(Notification.id).where(Notification.id == sentinel_id))
        ).scalar_one_or_none()
        task_notif_count = (
            await s.execute(
                select(sa.func.count())
                .select_from(Notification)
                .where(Notification.task_id == task.id)
            )
        ).scalar_one()

    assert sentinel_exists is not None, "Sentinel row was lost — parent txn did not commit"
    assert task_notif_count == 0, (
        f"Expected 0 task notifications (savepoint rolled back), got {task_notif_count}"
    )


async def test_resolve_recipients_excludes_inactive_users(app_under_test: Any) -> None:
    """resolve_recipients (Fix C / Codex P2) must drop deactivated users from a candidate pool —
    a LOCKED/DISABLED/RETIRED user must never become a notification/email recipient."""
    org_id = await _default_org_id()
    active_id = await _seed_user(org_id, email="active@example.com")

    # Seed a DISABLED user directly.
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        disabled = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-disabled-{salt}",
            display_name=f"Disabled {salt}",
            email=f"disabled-{salt}@example.com",
            status=UserStatus.DISABLED,
        )
        s.add(disabled)
        await s.commit()
        disabled_id = disabled.id

    # A task with NO assignee but a candidate_pool of [active, disabled].
    key = f"notify_inactive_{uuid.uuid4().hex[:8]}"
    async with get_sessionmaker()() as s:
        defn = WorkflowDefinition(
            org_id=org_id,
            key=key,
            version=1,
            effective=True,
            subject_type=WorkflowSubjectType.DOCUMENT,
            stages={"entry": "approve"},
        )
        s.add(defn)
        await s.flush()
        instance = WorkflowInstance(
            org_id=org_id,
            definition_id=defn.id,
            definition_version=1,
            subject_type=WorkflowSubjectType.DOCUMENT,
            subject_id=uuid.uuid4(),
            current_state="IN_APPROVAL",
        )
        s.add(instance)
        await s.flush()
        task = Task(
            org_id=org_id,
            instance_id=instance.id,
            stage_key="approve",
            assignee_user_id=None,
            candidate_pool=[str(active_id), str(disabled_id)],
            type=TaskType.APPROVE,
            state=TaskState.PENDING,
        )
        s.add(task)
        await s.commit()
        task_id = task.id

    async with get_sessionmaker()() as s:
        task2 = await s.get(Task, task_id)
        assert task2 is not None
        recipients = await resolve_recipients(s, task2)

    recipient_ids = {r.user_id for r in recipients}
    assert active_id in recipient_ids, "the active user must be a recipient"
    assert disabled_id not in recipient_ids, "a DISABLED user must be excluded from recipients"


async def test_resolve_recipients_excludes_foreign_org_user(app_under_test: Any) -> None:
    """resolve_recipients (Fix P1 / Codex) must exclude users whose org_id differs from the task's
    org — a UUID from another tenant in the candidate_pool must never receive that tenant's
    notification.
    """
    org_id = await _default_org_id()

    # Seed a second Organisation (the "foreign" tenant).
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        foreign_org = Organization(
            legal_name=f"Foreign Org {salt}",
            short_code=f"FRG{salt[:5].upper()}",
        )
        s.add(foreign_org)
        await s.commit()
        foreign_org_id = foreign_org.id

    # A user who belongs to the FOREIGN org.
    foreign_user_id = await _seed_user(foreign_org_id, email=f"foreign-{salt}@other.test")
    # A user who belongs to the TASK's org.
    local_user_id = await _seed_user(org_id, email=f"local-{salt}@example.com")

    # Build a task in org_id whose candidate_pool contains BOTH users.
    key = f"notify_crossorg_{uuid.uuid4().hex[:8]}"
    async with get_sessionmaker()() as s:
        defn = WorkflowDefinition(
            org_id=org_id,
            key=key,
            version=1,
            effective=True,
            subject_type=WorkflowSubjectType.DOCUMENT,
            stages={"entry": "approve"},
        )
        s.add(defn)
        await s.flush()
        instance = WorkflowInstance(
            org_id=org_id,
            definition_id=defn.id,
            definition_version=1,
            subject_type=WorkflowSubjectType.DOCUMENT,
            subject_id=uuid.uuid4(),
            current_state="IN_APPROVAL",
        )
        s.add(instance)
        await s.flush()
        task = Task(
            org_id=org_id,
            instance_id=instance.id,
            stage_key="approve",
            assignee_user_id=None,
            candidate_pool=[str(local_user_id), str(foreign_user_id)],
            type=TaskType.APPROVE,
            state=TaskState.PENDING,
        )
        s.add(task)
        await s.commit()
        task_id = task.id

    async with get_sessionmaker()() as s:
        task2 = await s.get(Task, task_id)
        assert task2 is not None
        recipients = await resolve_recipients(s, task2)

    recipient_ids = {r.user_id for r in recipients}
    assert local_user_id in recipient_ids, "the same-org user must be a recipient"
    assert foreign_user_id not in recipient_ids, (
        "a user from a different org must be excluded from recipients"
    )
