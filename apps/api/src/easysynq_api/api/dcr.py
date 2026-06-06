"""The Document Change Request (DCR) surface (slice S-dcr-1; doc 05 §5, doc 14 §7, doc 15 §8.7).

Permission keys are the seeded ``changeRequest.*`` catalog family (R5 normalizes doc 15's
``dcr.*`` onto it): ``changeRequest.create`` (POST /dcrs), ``changeRequest.read`` (GET),
``changeRequest.assess`` (PATCH — edit the request while Open), ``changeRequest.close`` (POST
/dcrs/{id}/cancel). No new keys.

``changeRequest.create`` is ``DOC_CLASS``-scoped; for a REVISE/RETIRE DCR the gate resolves the
target document's scope (artifact + folder + doc-class — the ``_document_scope_by_id`` precedent)
so a DOC_CLASS grant matches once owner-assignment binds; for a CREATE DCR there is no target yet
→ SYSTEM (an ad-hoc raise; a SYSTEM grant/override always matches). The PROCESS-scoped
``assess``/``close`` keys resolve the same target-document scope for path-id writes (an async
resolver); a concrete PROCESS grant matches once owner-assignment binds the placeholder, riding
SYSTEM overrides meanwhile (the family precedent). Reads gate at SYSTEM + an org-scoped query
(the CAPA/audits-list precedent).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrSourceLinkType, DcrState
from ..db.models._vault_enums import ChangeSignificance
from ..db.models.app_user import AppUser
from ..db.models.dcr import Dcr
from ..db.models.dcr_stage_event import DcrStageEvent
from ..db.models.document_type import DocumentType
from ..db.models.documented_information import DocumentedInformation
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, get_authz_audit_sink, require
from ..services.dcr import cancel_dcr, patch_dcr, raise_dcr
from ..services.dcr import repository as dcr_repo
from ..services.vault import repository as vault_repo

router = APIRouter(prefix="/api/v1", tags=["dcr"])


# --- request bodies ---------------------------------------------------------------------------


class DcrCreate(BaseModel):
    change_type: DcrChangeType
    change_significance: ChangeSignificance
    reason_class: DcrReasonClass
    reason_text: str = Field(min_length=1, max_length=4000)
    target_document_id: uuid.UUID | None = None
    source_link_type: DcrSourceLinkType | None = None
    source_link_id: uuid.UUID | None = None
    proposed_effective_from: datetime.datetime | None = None


class DcrPatch(BaseModel):
    reason_text: str | None = Field(default=None, min_length=1, max_length=4000)
    reason_class: DcrReasonClass | None = None
    change_significance: ChangeSignificance | None = None
    proposed_effective_from: datetime.datetime | None = None


class DcrCancel(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


# --- serializers ------------------------------------------------------------------------------


def _dcr(d: Dcr) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "identifier": d.identifier,
        "target_document_id": str(d.target_document_id) if d.target_document_id else None,
        "change_type": d.change_type.value,
        "change_significance": d.change_significance.value,
        "reason_class": d.reason_class.value,
        "reason_text": d.reason_text,
        "source_link_type": d.source_link_type.value if d.source_link_type else None,
        "source_link_id": str(d.source_link_id) if d.source_link_id else None,
        "proposed_effective_from": (
            d.proposed_effective_from.isoformat() if d.proposed_effective_from else None
        ),
        "resulting_version_id": str(d.resulting_version_id) if d.resulting_version_id else None,
        "state": d.state.value,
        "decision": d.decision,
        "created_by": str(d.created_by),
        "created_at": d.created_at.isoformat(),
    }


def _stage_event(e: DcrStageEvent) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "from_state": e.from_state.value if e.from_state else None,
        "to_state": e.to_state.value,
        "actor_id": str(e.actor_id) if e.actor_id else None,
        "comment": e.comment,
        "payload": e.payload,
        "occurred_at": e.occurred_at.isoformat(),
    }


# --- scope helpers ----------------------------------------------------------------------------


async def _dcr_doc_scope(session: AsyncSession, doc_id: uuid.UUID | None) -> ResourceContext:
    """Build the FULL ResourceContext for a DCR's target document (the records
    ``_form_capture_scope`` / S-pack-1 R28 lesson): artifact + folder + doc-class + framework +
    **process_ids** (from the document's process-links). The process_ids are load-bearing — the
    family's PROCESS-scoped ``changeRequest.assess``/``.close`` grants (and a Process-Owner
    ``changeRequest.create``) match ONLY against a populated ``process_ids`` (PDP _matches_scope); a
    bare artifact context would fail-closed mis-DENY them once owner-assignment binds the
    placeholder. SYSTEM fallback for a CREATE DCR (no target) or an unknown id (a SYSTEM
    grant/override always matches). The service is the org boundary, so it does not org-check."""
    if doc_id is None:
        return ResourceContext.system()
    doc = await session.get(DocumentedInformation, doc_id)
    if doc is None:
        return ResourceContext.system()  # the service raises the real 404/422
    level: str | None = None
    if doc.document_type_id:
        dt = await session.get(DocumentType, doc.document_type_id)
        level = dt.document_level.value if dt else None
    links = await vault_repo.list_process_links(session, doc.id)
    return ResourceContext(
        artifact_id=str(doc.id),
        folder_path=doc.folder_path,
        document_level=level,
        framework_id=str(doc.framework_id),
        process_ids=frozenset(str(p.id) for _link, p in links),
        lifecycle_state=doc.current_state.value,
    )


async def _dcr_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve a path-id DCR's authz scope from its target document (SYSTEM fallback for a CREATE
    DCR or a bad id)."""
    raw = request.path_params.get("dcr_id")
    if not raw:
        return ResourceContext.system()
    try:
        dcr_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    dcr = await dcr_repo.get_dcr(session, dcr_id)
    if dcr is None:
        return ResourceContext.system()
    return await _dcr_doc_scope(session, dcr.target_document_id)


_dcr_read = require("changeRequest.read")
_dcr_assess = require("changeRequest.assess", async_scope_resolver=_dcr_scope)
_dcr_close = require("changeRequest.close", async_scope_resolver=_dcr_scope)


# --- endpoints --------------------------------------------------------------------------------


@router.post("/dcrs", status_code=status.HTTP_201_CREATED)
async def create_dcr_endpoint(
    body: DcrCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    # changeRequest.create's scope is resolved from the target document — the FULL context (artifact
    # + folder + doc-class + process_ids) so a DOC_CLASS / FOLDER / PROCESS grant all match once
    # owner-assignment binds. A CREATE DCR has no target → SYSTEM. A path-only dependency cannot see
    # the body, hence the in-handler enforce (the records / capa-capture precedent).
    scope = await _dcr_doc_scope(session, body.target_document_id)
    await enforce(session, authz_sink, request, caller, "changeRequest.create", scope)
    dcr = await raise_dcr(
        session,
        caller,
        change_type=body.change_type,
        change_significance=body.change_significance,
        reason_class=body.reason_class,
        reason_text=body.reason_text,
        target_document_id=body.target_document_id,
        source_link_type=body.source_link_type,
        source_link_id=body.source_link_id,
        proposed_effective_from=body.proposed_effective_from,
    )
    return _dcr(dcr)


@router.get("/dcrs")
async def list_dcrs_endpoint(
    request: Request,
    caller: AppUser = Depends(_dcr_read),
    session: AsyncSession = Depends(get_session),
    state: DcrState | None = None,
    change_type: DcrChangeType | None = None,
    target_document_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    reason_class: DcrReasonClass | None = None,
) -> dict[str, Any]:
    rows = await dcr_repo.list_dcrs(
        session,
        caller.org_id,
        state=state,
        change_type=change_type,
        target_document_id=target_document_id,
        created_by=created_by,
        reason_class=reason_class,
    )
    return {"data": [_dcr(d) for d in rows]}


@router.get("/dcrs/{dcr_id}")
async def get_dcr_endpoint(
    dcr_id: uuid.UUID,
    caller: AppUser = Depends(_dcr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    dcr = await dcr_repo.get_dcr(session, dcr_id)
    if dcr is None or dcr.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="DCR not found")
    out = _dcr(dcr)
    out["stage_events"] = [
        _stage_event(e) for e in await dcr_repo.list_dcr_stage_events(session, dcr_id)
    ]
    return out


@router.patch("/dcrs/{dcr_id}")
async def patch_dcr_endpoint(
    dcr_id: uuid.UUID,
    body: DcrPatch,
    caller: AppUser = Depends(_dcr_assess),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Edit a DCR's request details while Open (gate ``changeRequest.assess``)."""
    dcr = await patch_dcr(
        session,
        caller,
        dcr_id,
        reason_text=body.reason_text,
        reason_class=body.reason_class,
        change_significance=body.change_significance,
        proposed_effective_from=body.proposed_effective_from,
    )
    return _dcr(dcr)


@router.post("/dcrs/{dcr_id}/cancel")
async def cancel_dcr_endpoint(
    dcr_id: uuid.UUID,
    body: DcrCancel,
    caller: AppUser = Depends(_dcr_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Withdraw a DCR while not yet approved (gate ``changeRequest.close``)."""
    dcr = await cancel_dcr(session, caller, dcr_id, comment=body.comment)
    return _dcr(dcr)
