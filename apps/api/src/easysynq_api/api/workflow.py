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
from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink
from ..services.capa import decide_capa_action_plan
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
)
from ..services.vault import repository as vault_repo
from ..services.workflow import decide as decide_service
from ..services.workflow import repository as wf_repo

router = APIRouter(prefix="/api/v1", tags=["tasks"])


class Decision(BaseModel):
    outcome: str
    comment: str | None = None
    effective_from: datetime.datetime | None = None


# --- representations --------------------------------------------------------------------


def _task(t: Task) -> dict[str, Any]:
    return {
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
    tasks = await wf_repo.list_user_tasks(
        session,
        caller.id,
        caller.org_id,
        state=state_enum,
        task_type=type_enum,
        instance_id=instance_id,
    )
    return [_task(t) for t in tasks]


@router.get("/tasks/{task_id}")
async def get_task_endpoint(
    task_id: uuid.UUID,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return _task(await _own_task(session, caller, task_id))


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
