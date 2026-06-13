"""Decide an ``MR_ACTION`` task (S-mr-1, clause 9.3 §s5) — the new ``/tasks/{id}/decision`` dispatch
leg for the MGMT_REVIEW subject. Mirrors ``decide_periodic_review``'s self-scoped posture: the task
OWNS its authorization (404-collapse non-membership; NO ``document.*`` key gates it — the
CAPA/periodic precedent, doc 07 self-scoped tasks), then ``wf_engine.decide`` flips the task to DONE
(idempotency-replay included) and this service commits.

The only outcome is ``complete`` (``action_expected="complete"``): an MR action is done or not yet —
there is no reviewer-rejection of an owner's own tracked action. The close gate (Phase 6) only cares
the task reaches ``TaskState.DONE``; ``complete`` is in the engine's ``_POSITIVE`` set, so it does.

⚠ The spawned task's ``stage_key`` is a per-action ``"action:<output_id>"`` (NOT a shared "action"
stage, and NOT a stage of the seeded ``management_review`` definition, which has only ``prepare``).
The per-action key keeps the engine's distinct-approver guard (engine.py:442) from
spanning two actions owned by the same user. ``wf_engine.decide`` finds no stage for it, flips the
task to DONE (that mutation runs BEFORE the quorum block), and sets the CONTAINER instance to
NEEDS_ATTENTION — harmless here: the instance is a pure task container, never read for a state, and
the close gate keys off the task's DONE state, not the instance."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._workflow_enums import WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.workflow import Task
from ...problems import ProblemException
from ..workflow import engine as wf_engine
from ..workflow import repository as wf_repo

_ALLOWED_MR_ACTION_OUTCOMES = {"complete"}


async def decide_mr_task(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Decide an MR_ACTION task. Membership follows the sibling posture
    (``decide_periodic_review``/``_assert_capa_approver``): non-membership 404-COLLAPSES (never a
    403 that leaks another user's task). The owner pinned at spawn is the assignee + the sole
    candidate."""
    instance = await wf_repo.lock_instance_for_update(session, task.instance_id)
    if instance is None or instance.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Task not found")
    if instance.subject_type is not WorkflowSubjectType.MGMT_REVIEW:  # pragma: no cover
        raise ProblemException(status=404, code="not_found", title="Task not found")
    pool = [str(u) for u in (task.candidate_pool or [])]
    if task.assignee_user_id != actor.id and str(actor.id) not in pool:
        raise ProblemException(status=404, code="not_found", title="Task not found")
    if outcome not in _ALLOWED_MR_ACTION_OUTCOMES:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Management Review action accepts outcome complete",
        )

    result = await wf_engine.decide(
        session,
        task,
        actor,
        outcome=outcome,
        comment=comment,
        idempotency_key=idempotency_key,
        _commit=False,
    )
    await session.commit()
    return result
