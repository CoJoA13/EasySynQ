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


async def lock_instance_for_update(
    session: AsyncSession, instance_id: uuid.UUID
) -> WorkflowInstance | None:
    """SELECT … FOR UPDATE the instance row — the multi-stage engine's serialization point (sibling
    quorum approvers serialize here so quorum-count + advance + sibling-skip + next-stage
    materialization is atomic). Distinct from ``get_instance`` (an unlocked ``session.get`` the
    byte-identical DOCUMENT path keeps using)."""
    return (
        await session.execute(
            select(WorkflowInstance).where(WorkflowInstance.id == instance_id).with_for_update()
        )
    ).scalar_one_or_none()


async def all_stages(session: AsyncSession, definition_id: uuid.UUID) -> dict[str, WorkflowStage]:
    """Every stage of a definition, keyed by ``stage.key`` (the engine's stage graph)."""
    rows = (
        (
            await session.execute(
                select(WorkflowStage).where(WorkflowStage.definition_id == definition_id)
            )
        )
        .scalars()
        .all()
    )
    return {s.key: s for s in rows}


async def stage_tasks(session: AsyncSession, instance_id: uuid.UUID, stage_key: str) -> list[Task]:
    """All tasks materialized for one stage entry (any state) — siblings for quorum + auto-skip."""
    return list(
        (
            await session.execute(
                select(Task)
                .where(Task.instance_id == instance_id, Task.stage_key == stage_key)
                .order_by(Task.id)
            )
        )
        .scalars()
        .all()
    )


async def stage_outcomes(
    session: AsyncSession, instance_id: uuid.UUID, stage_key: str
) -> list[TaskOutcome]:
    """The recorded outcomes of a stage's DONE tasks (for the distinct-approver quorum count)."""
    return list(
        (
            await session.execute(
                select(TaskOutcome)
                .join(Task, Task.id == TaskOutcome.task_id)
                .where(Task.instance_id == instance_id, Task.stage_key == stage_key)
            )
        )
        .scalars()
        .all()
    )


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
