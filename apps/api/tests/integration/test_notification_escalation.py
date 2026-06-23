"""S-notify-4: escalation recipient resolver + non-swallowing emit helper.

Tests run against a real migrated PG16 via testcontainers. Mirrors the fixture
pattern from test_notification_dispatch.py.

Three tests:
1. resolve_escalation_recipients: assignee has manager_id → returns [manager_id].
2. resolve_escalation_recipients: assignee has no manager, org has a QMS Owner role-holder
   → returns that holder's id.
3. resolve_escalation_recipients: no manager + no QMS Owner → returns [].
4. emit_task_event(event_key="task.escalated"): creates a Notification row with the right
   event_key / recipient_user_id / task_id (non-swallowing — failure would propagate).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from easysynq_api.db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.notification import Notification
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.models.workflow import Task, WorkflowDefinition, WorkflowInstance
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.constants import EVENT_TASK_ESCALATED
from easysynq_api.services.notifications.escalation import (
    emit_task_event,
    resolve_escalation_recipients,
)
from easysynq_api.services.notifications.recipients import Recipient

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_user(
    org_id: uuid.UUID,
    *,
    display_name: str | None = None,
    email: str | None = "test@example.com",
    manager_id: uuid.UUID | None = None,
) -> uuid.UUID:
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-esc-{salt}",
            display_name=display_name or f"Escalation Test {salt}",
            email=email,
            status=UserStatus.ACTIVE,
            manager_id=manager_id,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _assign_role(org_id: uuid.UUID, user_id: uuid.UUID, role_name: str) -> None:
    """Assign an existing seeded role by name to user_id in org_id."""
    async with get_sessionmaker()() as s:
        role = (
            await s.execute(select(Role).where(Role.org_id == org_id, Role.name == role_name))
        ).scalar_one_or_none()
        if role is None:
            # Create the role if it doesn't exist (e.g. in a fresh test DB without seeds)
            role = Role(org_id=org_id, name=role_name, is_reserved=False)
            s.add(role)
            await s.flush()
        assignment = RoleAssignment(
            org_id=org_id,
            role_id=role.id,
            user_id=user_id,
            bound_scope=None,
        )
        s.add(assignment)
        await s.commit()


async def _seed_workflow_objects(
    org_id: uuid.UUID, assignee_user_id: uuid.UUID
) -> tuple[WorkflowInstance, Task]:
    key = f"esc_test_{uuid.uuid4().hex[:8]}"
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
            subject_id=uuid.uuid4(),  # phantom — resolve_subject degrades gracefully
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

        async with get_sessionmaker()() as s2:
            inst2 = await s2.get(WorkflowInstance, instance.id)
            task2 = await s2.get(Task, task.id)
            return inst2, task2  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_resolve_escalation_recipients_returns_manager(app_under_test: Any) -> None:
    """If the assignee has a manager_id, the resolver returns exactly [manager_id]."""
    org_id = await _default_org_id()
    manager_id = await _seed_user(org_id, display_name="Manager User")
    assignee_id = await _seed_user(org_id, display_name="Assignee User", manager_id=manager_id)

    _, task = await _seed_workflow_objects(org_id, assignee_id)

    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        result = await resolve_escalation_recipients(s, task_fresh)  # type: ignore[arg-type]

    assert result == [manager_id], f"Expected [manager_id], got {result}"


async def test_resolve_escalation_recipients_fallback_to_qms_owner(app_under_test: Any) -> None:
    """If assignee has no manager, the resolver falls back to QMS Owner role-holders."""
    org_id = await _default_org_id()
    # Assignee with no manager
    assignee_id = await _seed_user(org_id, display_name="No-Manager Assignee", manager_id=None)
    # A QMS Owner role-holder
    qm_user_id = await _seed_user(org_id, display_name="QMS Owner User")
    await _assign_role(org_id, qm_user_id, "QMS Owner")

    _, task = await _seed_workflow_objects(org_id, assignee_id)

    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        result = await resolve_escalation_recipients(s, task_fresh)  # type: ignore[arg-type]

    assert qm_user_id in result, f"Expected QMS Owner user in result, got {result}"


async def test_resolve_escalation_recipients_empty_when_no_manager_no_qm(
    app_under_test: Any,
) -> None:
    """If no manager and no QMS Owner holders, the resolver returns []."""
    # Use a fresh isolated org so there are no QMS Owner assignments to find
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"Isolated Org {salt}", short_code=f"ISO{salt[:5].upper()}")
        s.add(org)
        await s.commit()
        isolated_org_id = org.id

    assignee_id = await _seed_user(
        isolated_org_id, display_name="Isolated Assignee", manager_id=None
    )
    _, task = await _seed_workflow_objects(isolated_org_id, assignee_id)

    async with get_sessionmaker()() as s:
        task_fresh = await s.get(Task, task.id)
        result = await resolve_escalation_recipients(s, task_fresh)  # type: ignore[arg-type]

    assert result == [], f"Expected [], got {result}"


async def test_emit_task_event_creates_notification(app_under_test: Any) -> None:
    """emit_task_event creates a Notification row with the correct event_key/recipient/task_id."""
    org_id = await _default_org_id()
    recipient_user_id = await _seed_user(org_id, display_name="Emit Target User")
    assignee_id = await _seed_user(org_id, display_name="Assignee for Emit")
    instance, task = await _seed_workflow_objects(org_id, assignee_id)

    now = datetime.datetime.now(datetime.UTC)
    recipient = Recipient(
        user_id=recipient_user_id,
        email="emit-target@example.com",
        display_name="Emit Target User",
        first_name="Emit",
        email_enabled=True,
    )

    # Non-swallowing: if it raises, the test fails (that's the expected behavior).
    async with get_sessionmaker()() as s:
        inst_fresh = await s.get(WorkflowInstance, instance.id)
        task_fresh = await s.get(Task, task.id)
        await emit_task_event(
            s,
            instance=inst_fresh,
            task=task_fresh,
            recipient=recipient,
            event_key=EVENT_TASK_ESCALATED,
            now=now,
        )
        await s.commit()

    async with get_sessionmaker()() as s:
        notif = (
            await s.execute(
                select(Notification).where(
                    Notification.recipient_user_id == recipient_user_id,
                    Notification.task_id == task.id,
                    Notification.event_key == EVENT_TASK_ESCALATED,
                )
            )
        ).scalar_one_or_none()

    assert notif is not None, "Expected a Notification row but got None"
    assert notif.event_key == EVENT_TASK_ESCALATED
    assert notif.recipient_user_id == recipient_user_id
    assert notif.task_id == task.id
    # Verify that template variables were actually substituted — no unresolved {{ placeholder
    # remains. _substitute() leaves the literal {{name}} when the name is absent from the
    # VARIABLE_WHITELIST, so this assertion fails without the whitelist fix.
    assert "{{" not in notif.title, (
        f"Unsubstituted placeholder in notification title: {notif.title!r}"
    )
    assert "{{" not in notif.body, f"Unsubstituted placeholder in notification body: {notif.body!r}"
