"""The Management Review surface (S-mr-1; clause 9.3).

Rides the seeded ``mgmtReview.read``/``create``/``record_outputs`` keys (all SYSTEM finest-scope →
the default ``_system_scope`` resolver, no async resolver). create + release enforce IMPERATIVELY
(``enforce(...)``): create has no path id (the raise-on-body precedent) and release rides the
existing ``document.release`` key over a SoD-2-enriched scope (author/approver ≠ releaser — the OBJ
release_endpoint posture). The MR is a kind=DOCUMENT subtype: its approval instance carries
``subject_type=DOCUMENT`` (instantiate_approval hardcodes it).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._mgmt_review_enums import ReviewOutputType
from ..db.models._workflow_enums import WorkflowSubjectType
from ..db.models.app_user import AppUser
from ..db.models.document_type import DocumentType
from ..db.models.documented_information import DocumentedInformation
from ..db.models.management_review import ManagementReview
from ..db.models.review_input import ReviewInput
from ..db.models.review_output import ReviewOutput
from ..db.models.workflow import Task, WorkflowInstance
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..problems import ProblemException
from ..services.authz import (
    AuthzAuditSink,
    enforce,
    get_authz_audit_sink,
    require,
)
from ..services.mgmt_review import (
    add_output,
    compile_inputs,
    create_review,
    delete_output,
    get_review_doc,
    list_inputs,
    list_outputs,
    list_reviews,
    release_review,
    spawn_mr_actions,
    submit_review_for_review,
    update_output,
    update_review_meta,
)
from ..services.mgmt_review import repository as mr_repo
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
)
from ..services.vault.release_scope import enrich_release_sod_scope
from ..services.workflow import repository as wf_repo

router = APIRouter(prefix="/api/v1", tags=["management-reviews"])


# --- request bodies ---
class ReviewCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    period_label: str | None = Field(default=None, max_length=200)
    review_date: datetime.date | None = None


class OutputCreate(BaseModel):
    output_type: ReviewOutputType
    description: str = Field(min_length=1, max_length=4000)
    owner_user_id: uuid.UUID | None = None
    due_date: datetime.date | None = None


class OutputUpdate(BaseModel):
    """Partial output edit — omitted ≠ null (``model_fields_set``; the objectives PATCH rule)."""

    output_type: ReviewOutputType | None = None
    description: str | None = Field(default=None, min_length=1, max_length=4000)
    owner_user_id: uuid.UUID | None = None
    due_date: datetime.date | None = None


class ReviewMetaUpdate(BaseModel):
    """Edit the meta the freeze reads into the minutes (Draft-only). Omitted ≠ null."""

    period_label: str | None = Field(default=None, max_length=200)
    review_date: datetime.date | None = None
    attendees: list[dict[str, Any]] | None = None


class ReviewSubmitBody(BaseModel):
    change_reason: str | None = Field(default=None, max_length=500)


# --- serializers ---
def _mgmt_review(
    mr: ManagementReview, *, identifier: str, title: str, current_state: Any
) -> dict[str, Any]:
    return {
        "id": str(mr.id),
        "identifier": identifier,
        "title": title,
        "current_state": (
            current_state.value if hasattr(current_state, "value") else str(current_state)
        ),
        "period_label": mr.period_label,
        "review_date": mr.review_date.isoformat() if mr.review_date is not None else None,
        "attendees": mr.attendees,
        "close_state": mr.close_state.value if mr.close_state is not None else None,
        "closed_at": mr.closed_at.isoformat() if mr.closed_at is not None else None,
        "created_at": mr.created_at.isoformat(),
    }


def _review_input(ri: ReviewInput) -> dict[str, Any]:
    return {
        "id": str(ri.id),
        "management_review_id": str(ri.management_review_id),
        "input_type": ri.input_type.value,
        "available": ri.available,
        "source_ref": ri.source_ref,
        "position": ri.position,
    }


def _review_output(ro: ReviewOutput) -> dict[str, Any]:
    return {
        "id": str(ro.id),
        "management_review_id": str(ro.management_review_id),
        "output_type": ro.output_type.value,
        "description": ro.description,
        "owner_user_id": str(ro.owner_user_id) if ro.owner_user_id is not None else None,
        "due_date": ro.due_date.isoformat() if ro.due_date is not None else None,
        "spawned_task_id": str(ro.spawned_task_id) if ro.spawned_task_id is not None else None,
    }


def _approval_task(t: Task) -> dict[str, Any]:
    """Field-equivalent to api/workflow.py's ``_task`` (the document-approval shape — no
    subject_type/subject_id). Copied from api/objectives.py:227-241."""
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


def _approval_instance(i: WorkflowInstance, tasks: list[Task]) -> dict[str, Any]:
    """Copied from api/objectives.py:244-257 — the GET-approval shape (the FE WorkflowInstance
    type)."""
    return {
        "id": str(i.id),
        "definition_id": str(i.definition_id),
        "definition_version": i.definition_version,
        "subject_type": i.subject_type.value,
        "subject_id": str(i.subject_id),
        "current_state": i.current_state,
        "started_at": i.started_at.isoformat() if i.started_at else None,
        "revision": i.revision,
        "tasks": [_approval_task(t) for t in tasks],
    }


# --- scope helpers ---
async def _release_scope(session: AsyncSession, doc: DocumentedInformation) -> ResourceContext:
    """Release scope = the MR's document scope + the SoD-2 inputs for the version the cutover will
    promote. Copied from api/objectives.py:317-334 (_objective_release_scope)."""
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    base = ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        lifecycle_state=doc.current_state.value,
    )
    return await enrich_release_sod_scope(session, base, doc.id, None)


# create + release enforce IMPERATIVELY (enforce(...)) inside the handler, so they need no
# `require(...)` dependency; reads/outputs gate via these dependencies.
_mr_read = require("mgmtReview.read")
_mr_outputs = require("mgmtReview.record_outputs")


async def _load_review(
    session: AsyncSession, caller: AppUser, review_id: uuid.UUID
) -> tuple[ManagementReview, DocumentedInformation]:
    """Load the MR base doc + satellite, 404 if it isn't an MR in the caller's org."""
    pair = await get_review_doc(session, review_id)
    if pair is None:
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    mr, doc = pair
    if doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    return mr, doc


# --- endpoints ---
# NOTE: every literal sub-path is declared BEFORE /management-reviews/{review_id} so the literals
# aren't shadowed by the {review_id} str-convertor (the S-pack-2 lesson).


@router.post("/management-reviews", status_code=status.HTTP_201_CREATED)
async def create_review_endpoint(
    body: ReviewCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # No path id at create → enforce mgmtReview.create imperatively at SYSTEM scope (s8).
    await enforce(
        session, authz_sink, request, caller, "mgmtReview.create", ResourceContext.system()
    )
    mr = await create_review(
        session,
        vault_sink,
        caller,
        title=body.title,
        period_label=body.period_label,
        review_date=body.review_date,
    )
    row = await mr_repo.get_review_row(session, mr.id)
    if row is None:  # pragma: no cover — just created, cannot be absent
        raise ProblemException(
            status=500, code="internal_error", title="Review row not found after create"
        )
    _mr, ident, title, state = row
    return _mgmt_review(mr, identifier=ident, title=title, current_state=state)


@router.get("/management-reviews")
async def list_reviews_endpoint(
    caller: AppUser = Depends(_mr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await list_reviews(session, caller.org_id)
    return {
        "data": [_mgmt_review(mr, identifier=i, title=t, current_state=s) for mr, i, t, s in rows]
    }


@router.get("/management-reviews/{review_id}")
async def get_review_endpoint(
    review_id: uuid.UUID,
    caller: AppUser = Depends(_mr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    mr, _doc = await _load_review(session, caller, review_id)
    row = await mr_repo.get_review_row(session, review_id)
    if row is None:  # pragma: no cover — the satellite exists, so the base must too
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    _mr, ident, title, state = row
    out = _mgmt_review(mr, identifier=ident, title=title, current_state=state)
    out["inputs"] = [_review_input(ri) for ri in await list_inputs(session, review_id)]
    out["outputs"] = [_review_output(ro) for ro in await list_outputs(session, review_id)]
    return out


@router.post("/management-reviews/{review_id}/compile-inputs")
async def compile_inputs_endpoint(
    review_id: uuid.UUID,
    caller: AppUser = Depends(_mr_outputs),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """(Re)compile the six 9.3.2 sourced reads → ``review_input`` rows (Draft-only). TRIGGERED by a
    ``mgmtReview.record_outputs`` holder (the dependency gate), but each sourced read is evaluated
    under the review OWNER's grants (the MR document's ``owner_user_id``, PDP-checked per key,
    fail-closed → a gap row, never a 403 — F3). Returns the refreshed detail."""
    mr, doc = await _load_review(session, caller, review_id)
    # The owner is the MR document's owner (the convening QM, set at create_document) — load it as
    # an AppUser; the compiler gates each read on its grants (F3 trap 1), NOT the caller's.
    owner = await session.get(AppUser, doc.owner_user_id)
    if owner is None:  # pragma: no cover — owner_user_id is a RESTRICT FK to a live app_user
        raise ProblemException(
            status=409, code="conflict", title="Management Review has no resolvable owner"
        )
    await compile_inputs(session, mr, owner)
    row = await mr_repo.get_review_row(session, review_id)
    if row is None:  # pragma: no cover — just mutated it, cannot be absent
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    mr2, ident, title, state = row
    out = _mgmt_review(mr2, identifier=ident, title=title, current_state=state)
    out["inputs"] = [_review_input(ri) for ri in await list_inputs(session, review_id)]
    out["outputs"] = [_review_output(ro) for ro in await list_outputs(session, review_id)]
    return out


# Phase 6: POST /management-reviews/{review_id}/close (mgmtReview.record_outputs) — the close gate
# (spawned MR_ACTION tasks must be DONE) lands here. Omitted now (Phase 5 spawns the tasks first).


@router.post("/management-reviews/{review_id}/outputs", status_code=status.HTTP_201_CREATED)
async def add_output_endpoint(
    review_id: uuid.UUID,
    body: OutputCreate,
    caller: AppUser = Depends(_mr_outputs),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ro = await add_output(
        session,
        caller,
        review_id=review_id,
        output_type=body.output_type,
        description=body.description,
        owner_user_id=body.owner_user_id,
        due_date=body.due_date,
    )
    return _review_output(ro)


@router.patch("/management-reviews/{review_id}/outputs/{output_id}")
async def update_output_endpoint(
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    body: OutputUpdate,
    caller: AppUser = Depends(_mr_outputs),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    ro = await update_output(
        session,
        caller,
        review_id=review_id,
        output_id=output_id,
        fields=body.model_fields_set,
        output_type=body.output_type,
        description=body.description,
        owner_user_id=body.owner_user_id,
        due_date=body.due_date,
    )
    return _review_output(ro)


@router.delete(
    "/management-reviews/{review_id}/outputs/{output_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_output_endpoint(
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    caller: AppUser = Depends(_mr_outputs),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await delete_output(session, caller, review_id=review_id, output_id=output_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/management-reviews/{review_id}")
async def update_review_meta_endpoint(
    review_id: uuid.UUID,
    body: ReviewMetaUpdate,
    caller: AppUser = Depends(_mr_outputs),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    mr = await update_review_meta(
        session,
        caller,
        review_id=review_id,
        fields=body.model_fields_set,
        period_label=body.period_label,
        review_date=body.review_date,
        attendees=body.attendees,
    )
    row = await mr_repo.get_review_row(session, review_id)
    if row is None:  # pragma: no cover — just mutated it, cannot be absent
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    _mr, ident, title, state = row
    return _mgmt_review(mr, identifier=ident, title=title, current_state=state)


@router.get("/management-reviews/{review_id}/approval")
async def get_review_approval_endpoint(
    review_id: uuid.UUID,
    caller: AppUser = Depends(_mr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any] | None:
    """The MR's current approval cycle — the latest workflow instance + tasks, or ``null`` before
    submit. The MR approval instance carries ``subject_type=DOCUMENT`` (instantiate_approval
    hardcodes it), so query ``WorkflowSubjectType.DOCUMENT`` (the OBJ approval precedent)."""
    mr = await session.get(ManagementReview, review_id)
    if mr is None or mr.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    instance = await wf_repo.latest_instance_for_subject(
        session, caller.org_id, WorkflowSubjectType.DOCUMENT, review_id
    )
    if instance is None:
        return None
    tasks = await wf_repo.list_instance_tasks(session, instance.id)
    return _approval_instance(instance, tasks)


@router.post("/management-reviews/{review_id}/submit-review")
async def submit_review_endpoint(
    review_id: uuid.UUID,
    body: ReviewSubmitBody | None = None,
    caller: AppUser = Depends(_mr_outputs),
    session: AsyncSession = Depends(get_session),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
) -> dict[str, Any]:
    # FOR UPDATE + populate_existing serializes concurrent submits and dodges the stale-identity-map
    # trap (the S-drift-1 trap; a stale satellite would freeze yesterday's minutes).
    doc = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == review_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    mr = (
        await session.execute(
            select(ManagementReview)
            .where(ManagementReview.id == review_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if doc is None or mr is None or doc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    await submit_review_for_review(
        session,
        vault_sink,
        caller,
        doc,
        mr,
        change_reason=body.change_reason if body is not None else None,
    )
    row = await mr_repo.get_review_row(session, review_id)
    if row is None:  # pragma: no cover — just mutated it, cannot be absent
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    mr2, ident, title, state = row
    return _mgmt_review(mr2, identifier=ident, title=title, current_state=state)


@router.post("/management-reviews/{review_id}/release")
async def release_review_endpoint(
    review_id: uuid.UUID,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    # Enforce document.release imperatively over the SoD-2-enriched scope (author/approver ≠
    # releaser), then the shared release() runs the INV-1 SERIALIZABLE cutover → Effective → the
    # 9.3 ★ flips (the OBJ release_endpoint posture).
    _mr, doc = await _load_review(session, caller, review_id)
    resource = await _release_scope(session, doc)
    await enforce(session, authz_sink, request, caller, "document.release", resource, sig_hook=True)
    caller_id = caller.id  # capture BEFORE expire_all (the expired `caller` would lazy-refresh)
    await release_review(caller, review_id, vault_sink, sig_sink)
    # release() committed in its own SERIALIZABLE session; expire this session's identity map so the
    # re-reads refresh from the DB.
    session.expire_all()
    # Phase 5: spawn one MR_ACTION task per ACTION output (tracked to closure) on a MGMT_REVIEW
    # container instance + flip close_state=ActionsTracked. Re-load the satellite + base doc +
    # outputs + the actor FRESH (the release commit + expire_all invalidated the pre-release rows;
    # accessing an expired instance's attrs would lazy-refresh off-greenlet → MissingGreenlet).
    pair = await get_review_doc(session, review_id)
    outputs = await list_outputs(session, review_id)
    actor = await session.get(AppUser, caller_id)
    if pair is None or actor is None:  # pragma: no cover — just-released doc + just-authed caller
        raise ProblemException(
            status=500,
            code="internal_error",
            title="Management review not found after release",
        )
    mr_after, doc_after = pair
    await spawn_mr_actions(session, actor, doc_after, mr_after, outputs)
    await session.commit()
    row = await mr_repo.get_review_row(session, review_id)
    if row is None:  # pragma: no cover — the doc was just released, it cannot be absent
        raise ProblemException(status=404, code="not_found", title="Management Review not found")
    mr2, ident, title, state = row
    return _mgmt_review(mr2, identifier=ident, title=title, current_state=state)
