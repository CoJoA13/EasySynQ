"""The task / approval-workflow surface (slice S5, doc 15 §8.8).

``POST /tasks/{id}/decision`` is the canonical approval/review trigger: it derives the permission
from the (subject, outcome) — ``document.approve`` (sig-hook, SoD-1/2 gated) for approve,
``document.review`` for changes-requested — enforces it with a SoD-aware scope (the version's
immutable author), then runs the one-transaction :func:`decide`. ``GET /tasks`` is the self-scoped
My-Tasks inbox; ``GET /workflow-instances/{id}`` is gated by ``document.read`` on the subject. The
closed 96-key catalog has no ``task.*``/``workflow.*`` keys, so listing is self-scoped (doc 07).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._workflow_enums import TaskState, TaskType, WorkflowSubjectType
from ..db.models.app_user import AppUser
from ..db.models.document_type import DocumentType
from ..db.models.workflow import Task, WorkflowInstance
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..problems import ProblemException
from ..services.ack.decide import decide_doc_ack
from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink
from ..services.capa import decide_capa_action_plan
from ..services.dcr import decide_dcr_approval
from ..services.mgmt_review import decide_mr_task
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
)
from ..services.vault import repository as vault_repo
from ..services.vault.review import decide_periodic_review
from ..services.workflow import decide as decide_service
from ..services.workflow import repository as wf_repo

router = APIRouter(prefix="/api/v1", tags=["tasks"])


class Decision(BaseModel):
    outcome: str
    comment: str | None = None
    effective_from: datetime.datetime | None = None


# --- representations --------------------------------------------------------------------


# A DCR subject's "title" is its ``reason_text`` (up to 4000 chars) — cap it to a list-row label so
# a triage row stays legible and the payload stays lean. Real document/CAPA/MR titles are short.
_SUBJECT_TITLE_CAP = 160


def _short(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if len(s) <= _SUBJECT_TITLE_CAP else s[: _SUBJECT_TITLE_CAP - 1].rstrip() + "…"


def _task(
    t: Task,
    *,
    subject_type: str | None = None,
    subject_id: str | None = None,
    subject_identifier: str | None = None,
    subject_title: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(t.id),
        "instance_id": str(t.instance_id),
        "stage_key": t.stage_key,
        "type": t.type.value,
        "state": t.state.value,
        "assignee_user_id": str(t.assignee_user_id) if t.assignee_user_id else None,
        "candidate_pool": t.candidate_pool,
        "action_expected": t.action_expected,
        "due_at": t.due_at.isoformat() if t.due_at else None,
    }
    # Subject identity (critique #5): now on BOTH the list + detail so the inbox/rail are triageable
    # in place. self-scoped — the caller is the task's assignee/candidate, so the id+title are the
    # minimum context needed to triage one's own queue (not document content).
    if subject_type is not None:
        out["subject_type"] = subject_type
        out["subject_id"] = subject_id
        out["subject_identifier"] = subject_identifier
        out["subject_title"] = _short(subject_title)
    return out


def _instance(i: WorkflowInstance, tasks: list[Task] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(i.id),
        "definition_id": str(i.definition_id),
        "definition_version": i.definition_version,
        "subject_type": i.subject_type.value,
        "subject_id": str(i.subject_id),
        "current_state": i.current_state,
        "started_at": i.started_at.isoformat() if i.started_at else None,
        "revision": i.revision,
    }
    if tasks is not None:
        out["tasks"] = [_task(t) for t in tasks]
    return out


# --- helpers ----------------------------------------------------------------------------


# Map (subject DOCUMENT, outcome) → the in-catalog permission the decision enforces. Approve is the
# sig-hook action the SoD-1/2 gate applies to; changes-requested/reject is a plain review act.
_OUTCOME_PERMISSION: dict[str, tuple[str, bool]] = {
    "approve": ("document.approve", True),
    "changes_requested": ("document.review", False),
    "reject": ("document.review", False),
}


async def _decision_scope(session: AsyncSession, task: Task) -> ResourceContext:
    """Resolve the decision's authz scope incl. the SoD inputs — the version under decision and its
    immutable author (``document_version.author_user_id``)."""
    instance = await wf_repo.get_instance(session, task.instance_id)
    doc = await vault_repo.get_document(session, instance.subject_id) if instance else None
    if doc is None:
        return ResourceContext.system()
    version = await vault_repo.latest_version(session, doc.id)
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    return ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
        version_id=str(version.id) if version else None,
        author_user_id=str(version.author_user_id) if version else None,
    )


async def _own_task(session: AsyncSession, caller: AppUser, task_id: uuid.UUID) -> Task:
    """Load a task the caller may see (assignee or candidate); else 404 (sensitive collapse)."""
    task = await wf_repo.get_task(session, task_id)
    if task is None or task.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Task not found")
    pool = task.candidate_pool or []
    if task.assignee_user_id != caller.id and str(caller.id) not in pool:
        raise ProblemException(status=404, code="not_found", title="Task not found")
    return task


# --- tasks ------------------------------------------------------------------------------


@router.get("/tasks")
async def list_tasks_endpoint(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    assignee: str | None = None,
    state: str | None = None,
    type: str | None = None,
    instance_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    """My Tasks — tasks assigned to the caller or where the caller is a candidate (self-scoped)."""
    del assignee  # always self-scoped; ``assignee=me`` is the only meaningful value
    try:
        state_enum = TaskState(state) if state else None
        type_enum = TaskType(type) if type else None
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title="Invalid task filter"
        ) from exc
    rows = await wf_repo.list_user_tasks_with_subject(
        session,
        caller.id,
        caller.org_id,
        state=state_enum,
        task_type=type_enum,
        instance_id=instance_id,
    )
    return [
        _task(
            t,
            subject_type=subject_type.value if subject_type is not None else None,
            subject_id=str(subject_id) if subject_id is not None else None,
            subject_identifier=identifier,
            subject_title=title,
        )
        for t, subject_type, subject_id, identifier, title in rows
    ]


@router.get("/tasks/{task_id}")
async def get_task_endpoint(
    task_id: uuid.UUID,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    task = await _own_task(session, caller, task_id)
    instance = await wf_repo.get_instance(session, task.instance_id)
    identifier: str | None = None
    title: str | None = None
    if instance is not None:
        identifier, title = await wf_repo.subject_label(
            session, instance.subject_type, instance.subject_id
        )
    return _task(
        task,
        subject_type=instance.subject_type.value if instance else None,
        subject_id=str(instance.subject_id) if instance else None,
        subject_identifier=identifier,
        subject_title=title,
    )


@router.post("/tasks/{task_id}/decision")
async def decide_endpoint(
    task_id: uuid.UUID,
    body: Decision,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    task = await wf_repo.get_task(session, task_id)
    if task is None or task.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Task not found")
    # Dispatch on the subject type: the S5 DOCUMENT path is byte-identical; a CAPA action-plan
    # approval routes to the CAPA service, which OWNS its authorization (decide_capa_action_plan ->
    # _assert_capa_approver: task-ownership + live-role both 404-collapse, cross-stage 409;
    # no document permission key gates a CAPA approval; the role-resolved pool IS the authority,
    # self-scoped tasks doc 07) and writes the signature + signed ActionPlan stage on completion.
    instance = await wf_repo.get_instance(session, task.instance_id)
    if instance is not None and instance.subject_type is WorkflowSubjectType.CAPA:
        return await decide_capa_action_plan(
            session,
            task,
            caller,
            outcome=body.outcome,
            comment=body.comment,
            idempotency_key=idempotency_key,
            sig_sink=sig_sink,
        )
    # A DCR approval routes to the DCR service, which OWNS its authorization (decide_dcr_approval ->
    # _assert_dcr_approver: candidate-pool + live-role 404-collapse, cross-stage 409); no permission
    # key gates it (the role-resolved pool IS the authority); it writes the per-approver signatures.
    if instance is not None and instance.subject_type is WorkflowSubjectType.DCR:
        return await decide_dcr_approval(
            session,
            task,
            caller,
            outcome=body.outcome,
            comment=body.comment,
            idempotency_key=idempotency_key,
            sig_sink=sig_sink,
        )
    if instance is not None and instance.subject_type is WorkflowSubjectType.PERIODIC_REVIEW:
        return await decide_periodic_review(
            session,
            task,
            caller,
            outcome=body.outcome,
            comment=body.comment,
            idempotency_key=idempotency_key,
            sig_sink=sig_sink,
        )
    # An MR_ACTION (a clause-9.3 review output tracked to closure) routes to the mgmt-review
    # service, which OWNS its authorization (decide_mr_task: candidate-membership 404-collapses; no
    # document key gates it — the owner pinned at spawn IS the authority, self-scoped tasks doc 07).
    # No signature (an owner completing their own tracked action is not a sign-off).
    if instance is not None and instance.subject_type is WorkflowSubjectType.MGMT_REVIEW:
        return await decide_mr_task(
            session,
            task,
            caller,
            outcome=body.outcome,
            comment=body.comment,
            idempotency_key=idempotency_key,
        )
    # A DOC_ACK obligation routes to the ack service: candidate-membership 404-collapses, then
    # document.acknowledge is enforced at the document's scope (the key's first consumer); the
    # decision writes the immutable acknowledgement row + DOCUMENT_ACKNOWLEDGED — no signature
    # (R2/R43; sig_hook=false).
    if instance is not None and instance.subject_type is WorkflowSubjectType.DOC_ACK:
        return await decide_doc_ack(
            session,
            task,
            caller,
            outcome=body.outcome,
            comment=body.comment,
            idempotency_key=idempotency_key,
            request=request,
            authz_sink=authz_sink,
        )
    derived = _OUTCOME_PERMISSION.get(body.outcome)
    if derived is None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Unsupported outcome",
            detail=f"{body.outcome} is not valid for an APPROVE task",
        )
    permission_key, sig_hook = derived
    resource = await _decision_scope(session, task)
    # Enforce the derived permission (SoD-1/2 fire inside the PDP for document.approve/release).
    await enforce(session, authz_sink, request, caller, permission_key, resource, sig_hook=sig_hook)
    return await decide_service(
        session,
        task,
        caller,
        outcome=body.outcome,
        comment=body.comment,
        effective_from=body.effective_from,
        idempotency_key=idempotency_key,
        sig_sink=sig_sink,
        audit_sink=vault_sink,
    )


# --- workflow instances -----------------------------------------------------------------


@router.get("/workflow-instances/{instance_id}")
async def get_instance_endpoint(
    instance_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    expand: str | None = None,
) -> dict[str, Any]:
    instance = await wf_repo.get_instance(session, instance_id)
    if instance is None or instance.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Workflow instance not found")
    # Gate on the subject document's read permission (no task.*/workflow.* catalog key exists).
    doc = await vault_repo.get_document(session, instance.subject_id)
    level: str | None = None
    folder: str | None = None
    if doc is not None:
        folder = doc.folder_path
        if doc.document_type_id:
            dt = await session.get(DocumentType, doc.document_type_id)
            level = dt.document_level.value if dt else None
    resource = ResourceContext(
        artifact_id=str(instance.subject_id), folder_path=folder, document_level=level
    )
    await enforce(session, authz_sink, request, caller, "document.read", resource)
    tasks = await wf_repo.list_instance_tasks(session, instance.id) if expand == "tasks" else None
    return _instance(instance, tasks)


@router.get("/documents/{document_id}/approval", tags=["documents"])
async def get_document_approval_endpoint(
    document_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any] | None:
    """The document's current approval cycle (S-web-5) — the LATEST workflow instance for the
    document + its tasks, or ``null`` when it was never submitted. Gated ``document.read`` on the
    subject (the closed catalog has no ``task.*``/``workflow.*`` key — same gate as
    ``GET /workflow-instances/{id}``). Returns ``null`` (not 404) for a Draft with no cycle."""
    doc = await vault_repo.get_document(session, document_id)
    if doc is None or doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Document not found")
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    resource = ResourceContext(
        artifact_id=str(doc.id), folder_path=doc.folder_path, document_level=level
    )
    await enforce(session, authz_sink, request, caller, "document.read", resource)
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.DOCUMENT, doc.id
    )
    if instance is None:
        return None
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _instance(instance, tasks)
