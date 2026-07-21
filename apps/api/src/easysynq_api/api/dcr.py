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

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrSourceLinkType, DcrState
from ..db.models._vault_enums import ChangeSignificance
from ..db.models.app_user import AppUser
from ..db.models.dcr import Dcr
from ..db.models.dcr_stage_event import DcrStageEvent
from ..db.models.document_type import DocumentType
from ..db.models.document_version import DocumentVersion
from ..db.models.documented_information import DocumentedInformation
from ..db.models.impact_assessment import ImpactAssessment
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, gather_grants, get_authz_audit_sink, require
from ..services.authz.repository import gather_sod_constraints, get_allow_approver_release
from ..services.authz.resource import resource_from_doc
from ..services.capa import raise_dcr_from_capa
from ..services.dcr import (
    annotate_impact,
    assess_dcr,
    cancel_dcr,
    close_dcr,
    implement_dcr,
    patch_dcr,
    raise_dcr,
    route_dcr,
)
from ..services.dcr import repository as dcr_repo
from ..services.vault import (
    SignatureEventSink,
    VaultAuditSink,
    get_vault_audit_sink,
    get_vault_signature_sink,
)
from ..services.vault import repository as vault_repo
from ..services.vault.release_scope import enrich_release_sod_scope
from ..tasks.lifecycle import release_due_versions

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


class ImpactAnnotate(BaseModel):
    # Keyed by ImpactDimension value (e.g. {"affected_processes": "Diego to re-validate"}).
    annotations: dict[str, str]


class DcrImplement(BaseModel):
    # CREATE: the out-of-band-authored Approved version this DCR releases (required for CREATE).
    resulting_version_id: uuid.UUID | None = None
    # RETIRE: override the doc 05 §7.3 obsoletion-safety block (coverage gap) with a recorded note.
    force_retire: bool = False
    override_justification: str | None = None


class DcrFromCapa(BaseModel):
    change_type: DcrChangeType
    change_significance: ChangeSignificance
    reason_text: str = Field(min_length=1, max_length=4000)
    target_document_id: uuid.UUID | None = None  # required for a REVISE/RETIRE DCR
    reason_class: DcrReasonClass = DcrReasonClass.capa
    proposed_effective_from: datetime.datetime | None = None


# --- serializers ------------------------------------------------------------------------------


def _dcr(
    d: Dcr,
    *,
    target_identifier: str | None = None,
    target_title: str | None = None,
) -> dict[str, Any]:
    # target_identifier/target_title (critique #5): the target Document's human identity so the
    # register names the target instead of a bare "Document". Resolved on list + detail; null for a
    # CREATE DCR (no target) or an unresolvable id. Other _dcr callers (raise/create) pass neither →
    # both surface as null, which is correct (the target identity isn't yet meaningful there).
    return {
        "id": str(d.id),
        "identifier": d.identifier,
        "target_document_id": str(d.target_document_id) if d.target_document_id else None,
        "target_identifier": target_identifier,
        "target_title": target_title,
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


def _impact(ia: ImpactAssessment) -> dict[str, Any]:
    return {
        "id": str(ia.id),
        "dimension": ia.dimension.value,
        "auto_populated": ia.auto_populated,
        "requester_annotation": ia.requester_annotation,
        "created_at": ia.created_at.isoformat(),
        "updated_at": ia.updated_at.isoformat() if ia.updated_at else None,
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
    # #333: full scope tuple via the shared helper (adds kind; framework_id/process_ids/lifecycle
    # were already inline) so a kind-scoped changeRequest DENY at DOC_CLASS scope isn't dropped.
    return resource_from_doc(
        doc,
        document_level=level,
        process_ids=frozenset(str(p.id) for _link, p in links),
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
_dcr_route = require("changeRequest.route", async_scope_resolver=_dcr_scope)
_dcr_implement = require("changeRequest.implement", async_scope_resolver=_dcr_scope)


async def _underlying_control_allowed(
    session: AsyncSession, caller: AppUser, dcr: Dcr, now: datetime.datetime
) -> bool:
    """The caller's PDP answer for the underlying document control the implement DRIVES (RETIRE →
    document.obsolete; REVISE → document.release + SoD-2). True for CREATE (no SPA implement
    affordance → not AND-ed). The audit-free PDP twin of ``_enforce_underlying_document_control``;
    both read ``_underlying_control_target`` so the capability cannot drift from the enforcement."""
    target = await _underlying_control_target(session, dcr)
    if target is None:
        return True  # CREATE / defensive — no underlying probe to AND
    key, scope = target
    sod = await gather_sod_constraints(session, caller.org_id)
    allow_approver_release = await get_allow_approver_release(session, caller.org_id)
    ctx = RequestContext(
        now=now, actor_user_id=str(caller.id), allow_approver_release=allow_approver_release
    )
    grants = await gather_grants(session, caller.id, caller.org_id, key)
    return authorize(grants, key, scope, ctx, sig_hook=True, sod=sod).allow


async def _dcr_capabilities(session: AsyncSession, caller: AppUser, dcr: Dcr) -> dict[str, bool]:
    """The caller's PROCESS-scoped lifecycle affordances on this DCR (detail-only; the
    _mr_capabilities / _objective_capabilities precedent). One scope resolved from the target doc
    (SYSTEM fallback for a CREATE/unknown target), four changeRequest.* probes, and the honest
    ``implement`` = changeRequest.implement AND the underlying document.release/obsolete SoD-2
    answer so the SPA Implement button never show-then-403s. FE derives Edit/Cancel from these."""
    now = datetime.datetime.now(datetime.UTC)
    scope = await _dcr_doc_scope(session, dcr.target_document_id)
    ctx = RequestContext(now=now, actor_user_id=str(caller.id))

    async def _probe(key: str) -> bool:
        grants = await gather_grants(session, caller.id, caller.org_id, key)
        return authorize(grants, key, scope, ctx).allow

    implement_cr = await _probe("changeRequest.implement")
    return {
        "assess": await _probe("changeRequest.assess"),
        "route": await _probe("changeRequest.route"),
        "implement": implement_cr and await _underlying_control_allowed(session, caller, dcr, now),
        "close": await _probe("changeRequest.close"),
    }


async def _underlying_control_target(
    session: AsyncSession, dcr: Dcr
) -> tuple[str, ResourceContext] | None:
    """The vault-control (permission_key, scope) a DCR implement DRIVES, for RETIRE and REVISE — the
    SINGLE source of truth shared by the implement enforcement AND the detail-only ``implement``
    capability so they cannot drift. RETIRE → ``document.obsolete`` on the target; REVISE →
    ``document.release`` with the SoD-2 overlay over the target's latest Approved version. CREATE
    returns None: its release scope depends on the body's ``resulting_version_id`` (resolved only
    at implement time) and CREATE-implement has no SPA affordance, so the capability does not AND
    it."""
    if dcr.change_type is DcrChangeType.RETIRE:
        return "document.obsolete", await _dcr_doc_scope(session, dcr.target_document_id)
    if dcr.change_type is DcrChangeType.CREATE:
        return None
    if (
        dcr.target_document_id is None
    ):  # defensive — the create-iff-no-target CHECK guarantees a target
        return None
    base = await _dcr_doc_scope(session, dcr.target_document_id)
    scope = await enrich_release_sod_scope(session, base, dcr.target_document_id, None)
    return "document.release", scope


async def _enforce_underlying_document_control(
    session: AsyncSession,
    authz_sink: AuthzAuditSink,
    request: Request,
    caller: AppUser,
    dcr: Dcr,
    body: DcrImplement,
) -> None:
    """Enforce the vault-control permission the DCR implement DRIVES, IN ADDITION to the
    ``changeRequest.implement`` dependency gate (R40 S-dcr-5 addendum — no DCR side-door past
    document control). RETIRE → ``document.obsolete``; REVISE/CREATE → ``document.release`` with the
    SoD-2 overlay over the promoted version. This is the only path that fires the seeded SoD-2
    (author≠releaser). RETIRE/REVISE share ``_underlying_control_target`` with the capability probe;
    CREATE is special-cased here because its scope needs the request body's resulting_version_id."""
    if dcr.change_type is DcrChangeType.CREATE:
        if body.resulting_version_id is None:
            raise ProblemException(
                status=422,
                code="validation_error",
                title="resulting_version_id is required for a CREATE DCR implement",
            )
        version = await session.get(DocumentVersion, body.resulting_version_id)
        if version is None or version.org_id != caller.org_id:
            raise ProblemException(status=404, code="not_found", title="Version not found")
        base = await _dcr_doc_scope(session, version.document_id)
        scope = await enrich_release_sod_scope(
            session, base, version.document_id, body.resulting_version_id
        )
        await enforce(
            session, authz_sink, request, caller, "document.release", scope, sig_hook=True
        )
        return
    target = await _underlying_control_target(session, dcr)
    if target is None:  # defensive — a REVISE/RETIRE with no target (the CHECK guarantees one)
        raise ProblemException(status=404, code="not_found", title="Document not found")
    key, scope = target
    await enforce(session, authz_sink, request, caller, key, scope, sig_hook=True)


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
    return {
        "data": [
            _dcr(d, target_identifier=target_ident, target_title=target_t)
            for d, target_ident, target_t in rows
        ]
    }


@router.get("/dcrs/{dcr_id}")
async def get_dcr_endpoint(
    dcr_id: uuid.UUID,
    caller: AppUser = Depends(_dcr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    dcr = await dcr_repo.get_dcr(session, dcr_id)
    if dcr is None or dcr.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="DCR not found")
    target_identifier: str | None = None
    target_title: str | None = None
    if dcr.target_document_id is not None:
        target_di = await session.get(DocumentedInformation, dcr.target_document_id)
        if target_di is not None:
            target_identifier = target_di.identifier
            target_title = target_di.title
    out = _dcr(dcr, target_identifier=target_identifier, target_title=target_title)
    out["stage_events"] = [
        _stage_event(e) for e in await dcr_repo.list_dcr_stage_events(session, dcr_id)
    ]
    out["capabilities"] = await _dcr_capabilities(session, caller, dcr)
    # ui-4: surface the resulting version's parent document so the SPA can deep-link a CREATE DCR's
    # new document (there is no top-level version→document route, and _dcr can't expose it).
    # Detail-only; derived from the existing document_version.document_id FK (no migration).
    # CREATE → the new doc; REVISE → == target_document_id; RETIRE / pre-implement → None.
    resulting_document_id: str | None = None
    if dcr.resulting_version_id is not None:
        rv = await session.get(DocumentVersion, dcr.resulting_version_id)
        if rv is not None:
            resulting_document_id = str(rv.document_id)
    out["resulting_document_id"] = resulting_document_id
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


@router.post("/dcrs/{dcr_id}/assess")
async def assess_dcr_endpoint(
    dcr_id: uuid.UUID,
    caller: AppUser = Depends(_dcr_assess),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Open → Assessed (gate ``changeRequest.assess``). Auto-populates the seven doc 05 §5.3 impact
    dimensions from the target document's where-used. Returns the DCR + the impact rows."""
    dcr = await assess_dcr(session, caller, dcr_id)
    rows = [_impact(ia) for ia in await dcr_repo.list_impact_assessments(session, dcr_id)]
    out = _dcr(dcr)
    out["impact_assessment"] = rows
    return out


@router.post("/dcrs/{dcr_id}/route")
async def route_dcr_endpoint(
    dcr_id: uuid.UUID,
    caller: AppUser = Depends(_dcr_route),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Assessed → Routed → InApproval (gate ``changeRequest.route``). Instantiates the
    significance-routed ``dcr_approval`` workflow (MAJOR → Process Owner → QMS Owner; MINOR → QMS
    Owner) and returns the DCR + the opened approval instance. Approvers decide via
    ``POST /tasks/{id}/decision``; an empty routed role pool → 409 ``dcr_no_approvers``."""
    dcr, instance = await route_dcr(session, caller, dcr_id)
    out = _dcr(dcr)
    out["approval_instance"] = {"id": str(instance.id), "current_state": instance.current_state}
    return out


@router.post("/dcrs/{dcr_id}/implement")
async def implement_dcr_endpoint(
    dcr_id: uuid.UUID,
    body: DcrImplement,
    request: Request,
    caller: AppUser = Depends(_dcr_implement),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    vault_sink: VaultAuditSink = Depends(get_vault_audit_sink),
    sig_sink: SignatureEventSink = Depends(get_vault_signature_sink),
) -> dict[str, Any]:
    """Approved → Implemented (gate ``changeRequest.implement`` + the underlying
    ``document.release`` / ``document.obsolete`` enforced in-handler with SoD-2). REVISE releases
    the target's approved revision; CREATE releases the authored ``resulting_version_id``; RETIRE
    obsoletes the target behind the §7.3 gate (``force_retire`` + a note clears a coverage gap)."""
    dcr = await dcr_repo.get_dcr(session, dcr_id)
    if dcr is None or dcr.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="DCR not found")
    await _enforce_underlying_document_control(session, authz_sink, request, caller, dcr, body)
    dcr = await implement_dcr(
        session,
        caller,
        dcr_id,
        sink=vault_sink,
        sig_sink=sig_sink,
        resulting_version_id=body.resulting_version_id,
        force_retire=body.force_retire,
        override_justification=body.override_justification,
    )
    # REVISE/CREATE scheduled the cutover (effective_from set); trigger the release_due sweep now
    # for promptness — the 5-min Beat sweep is the self-healing backstop if this enqueue is lost.
    if dcr.change_type is not DcrChangeType.RETIRE:
        release_due_versions.delay()
    return _dcr(dcr)


@router.post("/dcrs/{dcr_id}/close")
async def close_dcr_endpoint(
    dcr_id: uuid.UUID,
    caller: AppUser = Depends(_dcr_close),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Implemented → Closed (gate ``changeRequest.close``). 409 ``dcr_effectivity_pending`` until
    the change has taken effect (the resulting version Effective / the target Obsolete)."""
    dcr = await close_dcr(session, caller, dcr_id)
    return _dcr(dcr)


@router.post("/capas/{capa_id}/raise-dcr")
async def raise_dcr_from_capa_endpoint(
    capa_id: uuid.UUID,
    body: DcrFromCapa,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Spawn a DCR from a CAPA corrective action (doc 02 Cl 10.2 / doc 05 §5.1 — closes the §10→§7.5
    loop; the DCR's ``source_link`` = the capa_id). Gate ``changeRequest.create`` (the DCR-domain
    key, scope from the target document — the ``POST /dcrs`` precedent). 1:N — a CAPA may spawn
    child DCRs; an ``Idempotency-Key`` makes a retry return the same DCR (201 new / 200 replay)."""
    scope = await _dcr_doc_scope(session, body.target_document_id)
    await enforce(session, authz_sink, request, caller, "changeRequest.create", scope)
    dcr, created = await raise_dcr_from_capa(
        session,
        caller,
        capa_id,
        change_type=body.change_type,
        change_significance=body.change_significance,
        reason_text=body.reason_text,
        target_document_id=body.target_document_id,
        reason_class=body.reason_class,
        proposed_effective_from=body.proposed_effective_from,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        content=_dcr(dcr),
    )


@router.get("/dcrs/{dcr_id}/impact")
async def get_dcr_impact_endpoint(
    dcr_id: uuid.UUID,
    caller: AppUser = Depends(_dcr_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The DCR's auto-populated impact_assessment rows (gate ``changeRequest.read``)."""
    dcr = await dcr_repo.get_dcr(session, dcr_id)
    if dcr is None or dcr.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="DCR not found")
    rows = [_impact(ia) for ia in await dcr_repo.list_impact_assessments(session, dcr_id)]
    return {"data": rows}


@router.put("/dcrs/{dcr_id}/impact")
async def put_dcr_impact_endpoint(
    dcr_id: uuid.UUID,
    body: ImpactAnnotate,
    caller: AppUser = Depends(_dcr_assess),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Annotate the impact dimensions (gate ``changeRequest.assess``). ``annotations`` is keyed by
    ImpactDimension value; the auto_populated facts are untouched."""
    await annotate_impact(session, caller, dcr_id, body.annotations)
    rows = [_impact(ia) for ia in await dcr_repo.list_impact_assessments(session, dcr_id)]
    return {"data": rows}
