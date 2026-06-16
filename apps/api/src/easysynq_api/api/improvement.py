"""The Improvement Initiatives surface (slice S-improvement-1; doc 02 Cl 10.3, doc 14 §9,
decisions-register R46).

Two additive CONTENT-domain keys (R46, seeded in 0052): ``improvement.read`` (GET) and
``improvement.manage`` (POST / PATCH / transition). Both PROCESS finest-scope. An initiative is an
own-table mutable-state workflow object (R22/R46), not a ``documented_information`` row.

``improvement.manage``'s scope is resolved from the initiative's ``process_id`` (the
``_objective_scope`` precedent); a PROCESS-scoped grant matches once owner-assignment binds, riding
SYSTEM overrides meanwhile. The create has no path id → SYSTEM (an ad-hoc raise; the in-handler
``enforce`` reads the body's optional ``process_id`` so a PROCESS-scoped grant matches). Reads gate
at SYSTEM + an org-scoped query then **row-filter** (the records/CAPA-list precedent) — never a hard
403; the FULL ``ResourceContext`` (process_ids) is populated or a PROCESS-scoped grant mis-denies
(the S-pack-1 R28 lesson).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models._improvement_enums import ImprovementSource, ImprovementStage
from ..db.models.app_user import AppUser
from ..db.models.improvement_initiative import ImprovementInitiative
from ..db.models.improvement_initiative_stage_event import ImprovementInitiativeStageEvent
from ..db.session import get_session
from ..domain.authz import RequestContext, ResourceContext, authorize
from ..problems import ProblemException
from ..services.authz import AuthzAuditSink, enforce, gather_grants, get_authz_audit_sink, require
from ..services.improvement import (
    create_initiative,
    transition_initiative,
    update_initiative,
)
from ..services.improvement import repository as improvement_repo

router = APIRouter(prefix="/api/v1", tags=["improvement"])


# --- request bodies ---------------------------------------------------------------------------


class InitiativeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    target_outcome: str | None = Field(default=None, max_length=4000)
    process_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None


class InitiativePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=8000)
    target_outcome: str | None = Field(default=None, max_length=4000)
    owner_user_id: uuid.UUID | None = None
    process_id: uuid.UUID | None = None


class InitiativeTransition(BaseModel):
    to_state: ImprovementStage
    comment: str | None = Field(default=None, max_length=4000)
    # Folded into the sealed stage_event.payload on a Closed move (the lightweight 10.3 evidence).
    outcome: str | None = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def _require_comment_on_terminal(self) -> InitiativeTransition:
        # A Cancelled / Closed move requires a comment (the spec §5 gate — a terminal decision is
        # always explained).
        if self.to_state in (ImprovementStage.Closed, ImprovementStage.Cancelled) and not (
            self.comment and self.comment.strip()
        ):
            raise ValueError(f"a {self.to_state.value} transition requires a comment")
        return self


# --- serializers ------------------------------------------------------------------------------


def _initiative(i: ImprovementInitiative) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "identifier": i.identifier,
        "title": i.title,
        "description": i.description,
        "target_outcome": i.target_outcome,
        "source": i.source.value,
        "source_link_id": str(i.source_link_id) if i.source_link_id else None,
        "process_id": str(i.process_id) if i.process_id else None,
        "owner_user_id": str(i.owner_user_id) if i.owner_user_id else None,
        "stage": i.stage.value,
        "opened_at": i.opened_at.isoformat(),
        "closed_at": i.closed_at.isoformat() if i.closed_at else None,
        "created_by": str(i.created_by),
        "created_at": i.created_at.isoformat(),
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


def _stage_event(e: ImprovementInitiativeStageEvent) -> dict[str, Any]:
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


def _scope_for(process_id: uuid.UUID | None) -> ResourceContext:
    """PROCESS-scoped target for an initiative's manage write (SYSTEM fallback when unscoped — a
    SYSTEM grant/override always matches; the ``_objective_scope`` precedent)."""
    if process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(process_id)}))


async def _initiative_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve a path-id initiative's authz scope from its ``process_id`` (SYSTEM fallback for a bad
    id / an unscoped initiative)."""
    raw = request.path_params.get("initiative_id")
    if not raw:
        return ResourceContext.system()
    try:
        initiative_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    initiative = await improvement_repo.get_initiative(session, initiative_id)
    if initiative is None:
        return ResourceContext.system()  # the service raises the real 404
    return _scope_for(initiative.process_id)


# Single-resource reads gate at the initiative's PROCESS scope so a PROCESS-scoped grant (R46 grants
# Process Owner improvement.read PROCESS-scoped) is reachable; a bare SYSTEM gate would fail-closed
# mis-DENY it (the S-pack-1 R28 lesson). The LIST is auth-only (get_current_user) + the row-filter
# below — never a hard 403 — the api/records.py filter-not-403 precedent (doc 15 §9.3).
_read_scoped = require("improvement.read", async_scope_resolver=_initiative_scope)
_manage = require("improvement.manage", async_scope_resolver=_initiative_scope)


async def _load(
    session: AsyncSession, caller: AppUser, initiative_id: uuid.UUID
) -> ImprovementInitiative:
    initiative = await improvement_repo.get_initiative(session, initiative_id)
    if initiative is None or initiative.org_id != caller.org_id:
        raise ProblemException(
            status=404, code="not_found", title="Improvement initiative not found"
        )
    return initiative


# --- endpoints --------------------------------------------------------------------------------


@router.post("/improvement-initiatives", status_code=status.HTTP_201_CREATED)
async def create_initiative_endpoint(
    body: InitiativeCreate,
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    """Manually raise an improvement initiative (gate ``improvement.manage``; ``source=manual``).
    The scope is resolved from the body's optional ``process_id`` (a path-only dependency cannot see
    the body) so a PROCESS-scoped grant matches; SYSTEM for an unscoped raise."""
    scope = _scope_for(body.process_id)
    await enforce(session, authz_sink, request, caller, "improvement.manage", scope)
    initiative = await create_initiative(
        session,
        caller,
        title=body.title,
        description=body.description,
        target_outcome=body.target_outcome,
        source=ImprovementSource.manual,
        process_id=body.process_id,
        owner_user_id=body.owner_user_id,
    )
    return _initiative(initiative)


@router.get("/improvement-initiatives")
async def list_initiatives_endpoint(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    stage: ImprovementStage | None = None,
    source: ImprovementSource | None = None,
    owner_user_id: uuid.UUID | None = None,
    process_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    rows = await improvement_repo.list_initiatives(
        session,
        caller.org_id,
        stage=stage,
        source=source,
        owner_user_id=owner_user_id,
        process_id=process_id,
    )
    # Filter-not-403 (doc 15 §9.3): drop rows the caller may not improvement.read. The FULL
    # ResourceContext (process_ids) is populated so a PROCESS-scoped grant authorizes correctly (the
    # S-pack-1 R28 lesson — a bare SYSTEM context would fail-closed mis-DENY it).
    grants = await gather_grants(session, caller.id, caller.org_id, "improvement.read")
    ctx = RequestContext(now=datetime.datetime.now(datetime.UTC))
    visible = [
        i
        for i in rows
        if authorize(grants, "improvement.read", _scope_for(i.process_id), ctx).allow
    ]
    return {"data": [_initiative(i) for i in visible]}


@router.get("/improvement-initiatives/{initiative_id}")
async def get_initiative_endpoint(
    initiative_id: uuid.UUID,
    caller: AppUser = Depends(_read_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    initiative = await _load(session, caller, initiative_id)
    return _initiative(initiative)


@router.get("/improvement-initiatives/{initiative_id}/stage-events")
async def list_stage_events_endpoint(
    initiative_id: uuid.UUID,
    caller: AppUser = Depends(_read_scoped),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The append-only stage-event trail (oldest → newest; gate ``improvement.read``)."""
    await _load(session, caller, initiative_id)
    events = await improvement_repo.list_stage_events(session, initiative_id)
    return {"data": [_stage_event(e) for e in events]}


@router.patch("/improvement-initiatives/{initiative_id}")
async def patch_initiative_endpoint(
    initiative_id: uuid.UUID,
    body: InitiativePatch,
    request: Request,
    caller: AppUser = Depends(_manage),
    session: AsyncSession = Depends(get_session),
    authz_sink: AuthzAuditSink = Depends(get_authz_audit_sink),
) -> dict[str, Any]:
    """Edit an initiative's mutable metadata (gate ``improvement.manage``); never the ``stage``.
    The ``_manage`` dep authorized the CURRENT process; a ``process_id`` reassignment ALSO requires
    ``improvement.manage`` on the TARGET process (else a manager of A could move an initiative into
    B they cannot manage — the Codex P2). Mirrors create's body-scope enforce."""
    if body.process_id is not None:
        await enforce(
            session, authz_sink, request, caller, "improvement.manage", _scope_for(body.process_id)
        )
    initiative = await update_initiative(
        session,
        caller,
        initiative_id,
        title=body.title,
        description=body.description,
        target_outcome=body.target_outcome,
        owner_user_id=body.owner_user_id,
        process_id=body.process_id,
    )
    return _initiative(initiative)


@router.post("/improvement-initiatives/{initiative_id}/transition")
async def transition_initiative_endpoint(
    initiative_id: uuid.UUID,
    body: InitiativeTransition,
    caller: AppUser = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Move an initiative along the FSM (gate ``improvement.manage``). Covers InProgress / Completed
    / Closed / Cancelled; FSM-guarded (409 ``improvement_transition_invalid``). A Cancelled / Closed
    move requires a comment; ``outcome`` (on a Closed move) folds into the sealed stage_event."""
    initiative = await transition_initiative(
        session,
        caller,
        initiative_id,
        to_state=body.to_state,
        comment=body.comment,
        outcome=body.outcome,
    )
    return _initiative(initiative)
