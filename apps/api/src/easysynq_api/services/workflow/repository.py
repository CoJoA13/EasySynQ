"""Workflow/task data access (slice S5).

Reads the effective ``workflow_definition`` + its stages, resolves a stage's role-named assignees to
concrete candidate user ids (stored on the task as a jsonb list, queried by My-Tasks containment),
and loads tasks/instances/outcomes for the decision flow.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from ...db.models.role import Role, RoleAssignment
from ...db.models.workflow import (
    Task,
    TaskOutcome,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowStage,
)


async def effective_definition(
    session: AsyncSession, org_id: uuid.UUID, key: str, subject_type: WorkflowSubjectType
) -> WorkflowDefinition | None:
    return (
        await session.execute(
            select(WorkflowDefinition).where(
                WorkflowDefinition.org_id == org_id,
                WorkflowDefinition.key == key,
                WorkflowDefinition.subject_type == subject_type,
                WorkflowDefinition.effective.is_(True),
            )
        )
    ).scalar_one_or_none()


async def first_stage(session: AsyncSession, definition_id: uuid.UUID) -> WorkflowStage | None:
    """The (single, in MVP) approval stage of a definition."""
    return (
        await session.execute(
            select(WorkflowStage)
            .where(WorkflowStage.definition_id == definition_id)
            .order_by(WorkflowStage.key)
            .limit(1)
        )
    ).scalar_one_or_none()


async def users_with_roles(
    session: AsyncSession, org_id: uuid.UUID, role_names: list[str]
) -> list[uuid.UUID]:
    """Concrete candidate user ids for a stage's role-named assignees (deterministic order)."""
    if not role_names:
        return []
    rows = (
        (
            await session.execute(
                select(RoleAssignment.user_id)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(RoleAssignment.org_id == org_id, Role.name.in_(role_names))
            )
        )
        .scalars()
        .all()
    )
    return list(dict.fromkeys(rows))


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> Task | None:
    return await session.get(Task, task_id)


async def get_instance(session: AsyncSession, instance_id: uuid.UUID) -> WorkflowInstance | None:
    return await session.get(WorkflowInstance, instance_id)


async def get_outcome(session: AsyncSession, task_id: uuid.UUID) -> TaskOutcome | None:
    return (
        await session.execute(select(TaskOutcome).where(TaskOutcome.task_id == task_id))
    ).scalar_one_or_none()


async def list_instance_tasks(session: AsyncSession, instance_id: uuid.UUID) -> list[Task]:
    return list(
        (
            await session.execute(
                select(Task).where(Task.instance_id == instance_id).order_by(Task.id)
            )
        )
        .scalars()
        .all()
    )


async def list_user_tasks(
    session: AsyncSession,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    state: TaskState | None = None,
    task_type: TaskType | None = None,
    instance_id: uuid.UUID | None = None,
) -> list[Task]:
    """My Tasks: tasks assigned to the caller OR where the caller is in the candidate pool."""
    stmt = (
        select(Task)
        .where(Task.org_id == org_id)
        .where(
            or_(
                Task.assignee_user_id == user_id,
                Task.candidate_pool.contains([str(user_id)]),
            )
        )
    )
    if state is not None:
        stmt = stmt.where(Task.state == state)
    if task_type is not None:
        stmt = stmt.where(Task.type == task_type)
    if instance_id is not None:
        stmt = stmt.where(Task.instance_id == instance_id)
    stmt = stmt.order_by(Task.id)
    return list((await session.execute(stmt)).scalars().all())
