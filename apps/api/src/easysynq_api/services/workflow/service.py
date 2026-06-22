"""The approval-workflow use-case layer (slice S5).

``instantiate_approval`` opens the ``document_approval`` instance + the single APPROVE task when a
document is submitted for review. ``decide`` is the canonical approval/review trigger
(``POST /tasks/{id}/decision``): under a ``SELECT … FOR UPDATE`` task lock it dispatches to the
reused vault lifecycle FSM (``approve`` / ``request_changes``), emits the ``signature_event``
(approve only), writes the ``task_outcome``, flips the task to DONE, appends the ``audit_event``,
and commits — all in **one transaction** (in-txn audit, S6). Idempotency: a repeat decision replays
recorded outcome when the ``Idempotency-Key`` matches, else 409 (``UNIQUE(task_outcome.task_id)``
backstops it).
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._signature_enums import SignatureMeaning
from ...db.models._workflow_enums import TaskOutcomeKind, TaskState, TaskType, WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.signature_event import SignatureEvent as SignatureEventRow
from ...db.models.workflow import Task, TaskOutcome, WorkflowInstance
from ...problems import ProblemException
from ..vault import lifecycle
from ..vault import repository as vault_repo
from ..vault.audit import VaultAuditSink
from ..vault.signature import SignatureEvent, SignatureEventSink
from . import repository as wf_repo

_DEFINITION_KEY = "document_approval"
_APPROVE_OUTCOMES = {"approve"}
_REJECT_OUTCOMES = {"changes_requested", "reject"}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def instantiate_approval(
    session: AsyncSession, doc: Any, actor: AppUser
) -> tuple[WorkflowInstance, Task]:
    """Open the approval ``workflow_instance`` + one APPROVE ``task`` for a submitted document.
    Adds rows to the session WITHOUT committing — the submit-review handler commits the T2/T9
    transition and the instantiation together. An empty candidate pool → ``NEEDS_ATTENTION`` (the
    task is still created so a PEP-authorized approver can decide it; never a silent skip)."""
    definition = await wf_repo.effective_definition(
        session, doc.org_id, _DEFINITION_KEY, WorkflowSubjectType.DOCUMENT
    )
    if definition is None:
        raise ProblemException(
            status=500,
            code="internal_error",
            title="No effective document_approval workflow definition",
        )
    stage = await wf_repo.first_stage(session, definition.id)
    role_names = list((stage.assignees or {}).get("roles", [])) if stage else []
    candidates = await wf_repo.users_with_roles(session, doc.org_id, role_names)
    pool = [str(uid) for uid in candidates]

    instance = WorkflowInstance(
        org_id=doc.org_id,
        definition_id=definition.id,
        definition_version=definition.version,
        subject_type=WorkflowSubjectType.DOCUMENT,
        subject_id=doc.id,
        current_state="IN_APPROVAL" if pool else "NEEDS_ATTENTION",
        revision=0,
    )
    session.add(instance)
    await session.flush()  # populate instance.id for the task FK

    task = Task(
        org_id=doc.org_id,
        instance_id=instance.id,
        stage_key=stage.key if stage else "quality_approval",
        candidate_pool=pool or None,
        type=TaskType.APPROVE,
        action_expected="approve",
        state=TaskState.PENDING,
    )
    session.add(task)
    await session.flush()
    from ..notifications.dispatch import enqueue_task_notifications

    await enqueue_task_notifications(session, instance, [task])
    return instance, task


def _signature_payload(sig_row: SignatureEventRow | None) -> dict[str, Any] | None:
    if sig_row is None:
        return None
    return {
        "id": str(sig_row.id),
        "meaning": sig_row.meaning.value,
        "method": sig_row.method.value,
        "content_digest": sig_row.content_digest,
        "auth_context": sig_row.auth_context,
        "reauth_at": sig_row.reauth_at.isoformat() if sig_row.reauth_at else None,
        "crypto_signature": None,
    }


def _decision_response(
    task: Task, outcome: TaskOutcome, sig_row: SignatureEventRow | None
) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "instance_id": str(task.instance_id),
        "stage_key": task.stage_key,
        "outcome": outcome.outcome.value,
        "decided_at": outcome.decided_at.isoformat() if outcome.decided_at else None,
        "decided_by": str(outcome.decided_by),
        "signature_event": _signature_payload(sig_row),
        "comment": outcome.comment,
    }


async def _signature_for_replay(
    session: AsyncSession, task: Task, outcome: TaskOutcome
) -> SignatureEventRow | None:
    """Re-fetch the approval signature for an idempotent replay (only ``approve`` ever signs)."""
    if outcome.outcome is not TaskOutcomeKind.approve:
        return None
    instance = await wf_repo.get_instance(session, task.instance_id)
    if instance is None:
        return None
    version = await vault_repo.latest_version(session, instance.subject_id)
    if version is None:
        return None
    return (
        await session.execute(
            select(SignatureEventRow)
            .where(
                SignatureEventRow.signed_object_id == version.id,
                SignatureEventRow.meaning == SignatureMeaning.approval,
            )
            .order_by(SignatureEventRow.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def decide(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    effective_from: datetime.datetime | None,
    idempotency_key: str | None,
    sig_sink: SignatureEventSink,
    audit_sink: VaultAuditSink,
) -> dict[str, Any]:
    """The one-transaction decision core (see module docstring). Authorization + SoD are enforced by
    the caller (the decision endpoint) before this runs."""
    locked = (
        await session.execute(select(Task).where(Task.id == task.id).with_for_update())
    ).scalar_one()

    if locked.state is TaskState.DONE:
        existing = await wf_repo.get_outcome(session, locked.id)
        if (
            existing is not None
            and idempotency_key is not None
            and locked.client_token == idempotency_key
        ):
            return _decision_response(
                locked, existing, await _signature_for_replay(session, locked, existing)
            )
        raise ProblemException(
            status=409,
            code="conflict",
            title="Task already decided",
            detail="this task already has a recorded decision",
        )
    if locked.state is not TaskState.PENDING:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Task not decidable",
            detail=f"task state is {locked.state.value}",
        )

    instance = await wf_repo.get_instance(session, locked.instance_id)
    if instance is None:
        raise ProblemException(status=404, code="not_found", title="Workflow instance not found")
    doc = await vault_repo.get_document(session, instance.subject_id)
    if doc is None or doc.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Subject document not found")

    sig_row: SignatureEventRow | None = None
    if outcome in _APPROVE_OUTCOMES:
        result = await lifecycle.approve(session, actor, doc, effective_from=effective_from)
        sig_row = sig_sink.record(
            session,
            SignatureEvent(
                org_id=doc.org_id,
                signer_user_id=actor.id,
                signed_object_id=result.version.id,
                meaning="approval",
                content_digest=result.version.source_blob_sha256,
                intent=comment,
                auth_context={"acr": "SESSION"},
            ),
        )
        instance.current_state = "APPROVED"
    elif outcome in _REJECT_OUTCOMES:
        # request_changes 422s on a blank comment — the reviewer must say why (doc 04 T3).
        result = await lifecycle.request_changes(session, actor, doc, comment=comment or "")
        instance.current_state = "REJECTED_TO_DRAFT"
    else:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Unsupported outcome",
            detail=f"{outcome} is not valid for an APPROVE task (use approve/changes_requested)",
        )

    decision_outcome = TaskOutcome(
        task_id=locked.id,
        outcome=TaskOutcomeKind(outcome),
        comment=comment,
        decided_at=_now(),
        decided_by=actor.id,
    )
    session.add(decision_outcome)
    locked.state = TaskState.DONE
    locked.assignee_user_id = actor.id
    locked.client_token = idempotency_key

    lifecycle.audit_transition(session, audit_sink, result, actor)
    await (
        session.commit()
    )  # task_outcome + signature_event + FSM mutation + audit commit atomically
    return _decision_response(locked, decision_outcome, sig_row)
