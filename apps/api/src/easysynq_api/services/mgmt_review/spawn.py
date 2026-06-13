"""Outputs → work (S-mr-1, clause 9.3 §s5). At release, ``spawn_mr_actions`` mints one ``MR_ACTION``
task per ``ACTION`` review_output so the action is tracked to closure, and flips the review's
``close_state`` to ``ActionsTracked``.

The tasks hang off ONE ``MGMT_REVIEW`` workflow_instance (the container the 0050
``management_review`` definition was seeded for). NO standalone task is possible —
``task.instance_id`` is NOT-NULL with a RESTRICT FK — so the instance is created + flushed FIRST,
then each task is hand-built and added (the S5 direct-insert precedent in
``services/workflow/service.py:instantiate_approval``; this BYPASSES the declarative engine's
``_materialize_stage`` — the engine resolves a role/context pool and a wall-clock SLA, neither of
which fits an owner-pinned, due-date-anchored action task).

The task carries BOTH ``assignee_user_id=owner`` AND ``candidate_pool=[str(owner)]`` —
``wf_engine.decide`` overwrites ``assignee_user_id`` with the actor on decide, so the pool is what
keeps the task owner-discoverable in ``list_user_tasks`` (which ORs assignee with candidate-pool
containment)."""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._mgmt_review_enums import ManagementReviewCloseState, ReviewOutputType
from ...db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.documented_information import DocumentedInformation
from ...db.models.management_review import ManagementReview
from ...db.models.review_output import ReviewOutput
from ...db.models.workflow import Task, WorkflowInstance
from ...problems import ProblemException
from ..workflow import repository as wf_repo

_DEF_KEY = "management_review"
_ACTION_STAGE_KEY = "action"


def _action_due_at(due_date: datetime.date | None) -> datetime.datetime | None:
    """Org-local midnight of the action's ``due_date`` (the review.py:180 recipe) — NOT now+hours.
    A due_date is operator-set in org-tz dates (R8); anchoring on org-midnight keeps the due signal
    consistent with the org-tz day boundary. None ``due_date`` → None ``due_at`` (acceptable: an
    open-ended action is undated, never wall-clock-now)."""
    if due_date is None:
        return None
    tz = ZoneInfo(get_settings().easysynq_org_timezone)
    return datetime.datetime.combine(due_date, datetime.time(0, 0), tzinfo=tz)


async def spawn_mr_actions(
    session: AsyncSession,
    actor: AppUser,
    doc: DocumentedInformation,
    review: ManagementReview,
    outputs: Sequence[ReviewOutput],
) -> list[Task]:
    """Spawn one ``MR_ACTION`` task per ``ACTION`` output (with an owner) on a single
    ``MGMT_REVIEW`` container instance; stamp ``output.spawned_task_id``; flip
    ``review.close_state = ActionsTracked``. Adds rows WITHOUT committing — the release endpoint
    owns the txn (it called ``release()`` first, which committed the cutover in its own session).
    Returns the spawned tasks (possibly empty: a review with only DECISION/IMPROVEMENT outputs
    tracks nothing but still closes — ActionsTracked is the post-release rest state regardless)."""
    definition = await wf_repo.effective_definition(
        session, review.org_id, _DEF_KEY, WorkflowSubjectType.MGMT_REVIEW
    )
    if definition is None:  # pragma: no cover — the 0050 seed guarantees it for the org
        raise ProblemException(
            status=500,
            code="internal_error",
            title="No effective management_review workflow definition",
        )

    instance = WorkflowInstance(
        org_id=review.org_id,
        definition_id=definition.id,
        definition_version=definition.version,
        subject_type=WorkflowSubjectType.MGMT_REVIEW,
        subject_id=review.id,
        current_state="OPEN",
        revision=0,
    )
    session.add(instance)
    await session.flush()  # populate instance.id for each task's FK

    spawned: list[Task] = []
    for output in outputs:
        if output.output_type is not ReviewOutputType.ACTION or output.owner_user_id is None:
            continue
        task = Task(
            org_id=review.org_id,
            instance_id=instance.id,
            # A UNIQUE stage_key per action — NOT a shared "action" stage. The engine's
            # distinct-approver guard (engine.py:442) 409s an actor who already has a positive
            # outcome on the same (instance, stage_key); with a shared key an owner of TWO actions
            # could never complete the second → the review could never close. Each action is an
            # independent owner-pinned task, not a quorum, so it gets its own stage.
            stage_key=f"{_ACTION_STAGE_KEY}:{output.id}",
            type=TaskType.MR_ACTION,
            assignee_user_id=output.owner_user_id,
            candidate_pool=[str(output.owner_user_id)],
            action_expected="complete",
            state=TaskState.PENDING,
            due_at=_action_due_at(output.due_date),
        )
        session.add(task)
        await session.flush()  # populate task.id for the spawned_task_id stamp
        output.spawned_task_id = task.id
        session.add(
            AuditEvent(
                org_id=review.org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.MGMT_REVIEW_ACTION_SPAWNED,
                object_type=AuditObjectType.document,
                object_id=doc.id,
                scope_ref=doc.identifier,
                after={"output_id": str(output.id), "task_id": str(task.id)},
            )
        )
        spawned.append(task)

    review.close_state = ManagementReviewCloseState.ActionsTracked
    return spawned
