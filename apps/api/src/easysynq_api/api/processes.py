"""Process IA — the ISO 9001 Clause 4.4 process graph + authoring (slice S9c, doc 15 §8.4).

``GET /processes`` / ``/processes/{id}`` / ``/processes/map`` read the process landscape (gate
``process.read``, **default SYSTEM scope** — the ``GET /clauses`` shape: QMS Owner / Internal
Auditor see the org-wide map; per-process read-filtering for PROCESS-scoped owners is deferred — the
seeded PROCESS grants carry an unsubstituted ``:assignment_process`` placeholder that matches no
concrete process yet). Authoring — ``POST /processes`` (``process.create``, SYSTEM, **seeded but
held by no role** → grant via override until the role UI, the ``document.export`` precedent),
``PATCH /processes/{id}`` (``process.manage`` + ``_process_scope``; confirms ``SEED→ACTIVE``), and
the edge sub-resource — mutates the graph. ``org_role``/``supplier`` are FK targets only in S9c (no
authoring endpoint); the document↔process ``process_link`` lives under ``/documents`` (the
clause-mappings precedent). Every mutation writes an in-txn ``audit_event``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models._clause_enums import PdcaPhase
from ..db.models._process_enums import ProcessState
from ..db.models.app_user import AppUser
from ..db.models.audit_event import AuditEvent
from ..db.models.org_role import OrgRole
from ..db.models.org_role_assignment import OrgRoleAssignment
from ..db.models.process import Process
from ..db.models.process_edge import ProcessEdge
from ..db.session import get_session
from ..domain.authz import ResourceContext
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.authz import require
from ..services.owner_assignment import assign_process_owner, revoke_process_owner
from ..services.vault import repository as repo

router = APIRouter(prefix="/api/v1", tags=["processes"])


# --- bodies + serializers ---------------------------------------------------------------


class ProcessCreate(BaseModel):
    name: str
    pdca_phase: PdcaPhase
    parent_id: uuid.UUID | None = None
    criteria: str | None = None
    is_outsourced: bool = False
    owner_org_role_id: uuid.UUID | None = None
    outsourced_supplier_id: uuid.UUID | None = None


class ProcessUpdate(BaseModel):
    name: str | None = None
    pdca_phase: PdcaPhase | None = None
    criteria: str | None = None
    excluded: bool | None = None
    owner_org_role_id: uuid.UUID | None = None
    state: ProcessState | None = None


class EdgeCreate(BaseModel):
    to_process_id: uuid.UUID
    io_label: str | None = None


class OwnerAssignCreate(BaseModel):
    user_id: uuid.UUID
    # Optional: the RACI org_role to bind. Defaults to the org's generic "Process Owner" org_role
    # (resolve-or-created by the service); the per-process specificity rides the binding process_id.
    org_role_id: uuid.UUID | None = None


def _process(p: Process) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "org_id": str(p.org_id),
        "name": p.name,
        "parent_id": str(p.parent_id) if p.parent_id else None,
        "owner_org_role_id": str(p.owner_org_role_id) if p.owner_org_role_id else None,
        "pdca_phase": p.pdca_phase.value,
        "criteria": p.criteria,
        "state": p.state.value,
        "excluded": p.excluded,
        "is_outsourced": p.is_outsourced,
        "outsourced_supplier_id": (
            str(p.outsourced_supplier_id) if p.outsourced_supplier_id else None
        ),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _edge(e: ProcessEdge) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "from_process_id": str(e.from_process_id),
        "to_process_id": str(e.to_process_id),
        "io_label": e.io_label,
    }


def _owner(a: OrgRoleAssignment, org_role_name: str) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "process_id": str(a.process_id) if a.process_id else None,
        "user_id": str(a.user_id),
        "org_role_id": str(a.org_role_id),
        "org_role_name": org_role_name,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# --- helpers ----------------------------------------------------------------------------


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _validation_error(field: str, code: str, message: str) -> ProblemException:
    return ProblemException(
        status=422,
        code="validation_error",
        title=message,
        errors=[{"field": field, "code": code, "message": message}],
    )


def _emit_process_event(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    process_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a process ``audit_event`` (object_type=process) BEFORE commit, so the mutation + its
    audit row commit atomically (mirrors ``documents._emit_clause_event``). Hashes stay NULL — the
    S6 linker stamps them off the hot path."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.process,
            object_id=process_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


async def _process_scope(request: Request, session: AsyncSession) -> ResourceContext:
    """Resolve a process's PROCESS authz scope from the path id (SYSTEM grants always match; a
    concrete PROCESS override matches once owner-assignment writes real bindings — deferred)."""
    raw = request.path_params.get("process_id")
    if not raw:
        return ResourceContext.system()
    try:
        process_id = uuid.UUID(str(raw))
    except ValueError:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(process_id)}))


async def _load_process(session: AsyncSession, caller: AppUser, process_id: uuid.UUID) -> Process:
    proc = await repo.get_process(session, process_id)
    if proc is None or proc.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Process not found")
    return proc


async def _load_user(session: AsyncSession, caller: AppUser, user_id: uuid.UUID) -> AppUser:
    # Org-scoped (D1 single-org today; keeps the surface tenant-safe). A cross-org target reads as
    # not-found (no existence leak — the authz._get_user precedent).
    user = await session.get(AppUser, user_id)
    if user is None or user.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="User not found")
    return user


async def _require_org_process(
    session: AsyncSession, caller: AppUser, process_id: uuid.UUID, field: str
) -> Process:
    proc = await repo.get_process(session, process_id)
    if proc is None or proc.org_id != caller.org_id:
        raise _validation_error(field, "not_found", "referenced process does not exist")
    return proc


_read = require("process.read")  # default SYSTEM scope (GET /clauses shape)
_create = require("process.create")  # SYSTEM; seeded-but-ungranted → override-until-UI
_manage = require("process.manage", async_scope_resolver=_process_scope)
# S-owner-assignment-1: bind/unbind a process owner. The seeded process.assign_owner key (PROCESS
# finest-scope, content/QMS tier) gets its first require() consumer — a SYSTEM override matches in
# v1, a concrete PROCESS grant once owner-assignment binds it.
_assign_owner = require("process.assign_owner", async_scope_resolver=_process_scope)


# --- reads ------------------------------------------------------------------------------


@router.get("/processes")
async def list_processes_endpoint(
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    return [_process(p) for p in await repo.list_processes(session, caller.org_id)]


@router.get("/processes/map")
async def process_map_endpoint(
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    nodes = [_process(p) for p in await repo.list_processes(session, caller.org_id)]
    edges = [_edge(e) for e in await repo.list_process_edges(session, caller.org_id)]
    return {"nodes": nodes, "edges": edges}


@router.get("/processes/{process_id}")
async def get_process_endpoint(
    process_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return _process(await _load_process(session, caller, process_id))


# --- authoring --------------------------------------------------------------------------


@router.post("/processes", status_code=status.HTTP_201_CREATED)
async def create_process_endpoint(
    body: ProcessCreate,
    caller: AppUser = Depends(_create),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if body.parent_id is not None:
        await _require_org_process(session, caller, body.parent_id, "parent_id")
    if body.owner_org_role_id is not None:
        role = await repo.get_org_role(session, body.owner_org_role_id)
        if role is None or role.org_id != caller.org_id:
            raise _validation_error("owner_org_role_id", "not_found", "org_role does not exist")
    if body.outsourced_supplier_id is not None:
        supplier = await repo.get_supplier(session, body.outsourced_supplier_id)
        if supplier is None or supplier.org_id != caller.org_id:
            raise _validation_error(
                "outsourced_supplier_id", "not_found", "supplier does not exist"
            )
    if body.is_outsourced and body.outsourced_supplier_id is None:
        raise _validation_error(
            "outsourced_supplier_id", "required", "an outsourced process must name a supplier"
        )
    if await repo.get_process_by_name(session, caller.org_id, body.name) is not None:
        raise ProblemException(status=409, code="conflict", title="Process name already in use")

    proc = Process(
        org_id=caller.org_id,
        name=body.name,
        parent_id=body.parent_id,
        owner_org_role_id=body.owner_org_role_id,
        pdca_phase=body.pdca_phase,
        criteria=body.criteria,
        state=ProcessState.SEED,
        is_outsourced=body.is_outsourced,
        outsourced_supplier_id=body.outsourced_supplier_id,
        created_by=caller.id,
    )
    session.add(proc)
    try:
        await session.flush()  # the UNIQUE(org_id, name) backstop for a concurrent dup create
    except IntegrityError:
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Process name already in use"
        ) from None
    _emit_process_event(
        session, caller, EventType.PROCESS_CREATED, proc.id, after={"name": proc.name}
    )
    await session.commit()
    return _process(proc)


@router.patch("/processes/{process_id}")
async def update_process_endpoint(
    process_id: uuid.UUID,
    body: ProcessUpdate,
    caller: AppUser = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    proc = await _load_process(session, caller, process_id)
    fields = body.model_fields_set

    state_changed = False
    if "state" in fields and body.state is not None and body.state is not proc.state:
        # The only legal transition is SEED→ACTIVE (Mara confirms the wizard-seeded node); any other
        # (e.g. ACTIVE→SEED) is rejected — a one-way ratchet.
        if not (proc.state is ProcessState.SEED and body.state is ProcessState.ACTIVE):
            raise ProblemException(
                status=409,
                code="invalid_state_transition",
                title=f"cannot transition process from {proc.state.value} to {body.state.value}",
            )
        state_changed = True

    # Explicit null on a non-nullable column → 422 (not a 500 on flush). criteria/owner_org_role_id
    # are nullable, so a null there is a legitimate "unset".
    for attr in ("name", "pdca_phase", "excluded"):
        if attr in fields and getattr(body, attr) is None:
            raise _validation_error(attr, "required", f"{attr} cannot be null")

    if "owner_org_role_id" in fields and body.owner_org_role_id is not None:
        role = await repo.get_org_role(session, body.owner_org_role_id)
        if role is None or role.org_id != caller.org_id:
            raise _validation_error("owner_org_role_id", "not_found", "org_role does not exist")
    if "name" in fields and body.name is not None and body.name != proc.name:
        if await repo.get_process_by_name(session, caller.org_id, body.name) is not None:
            raise ProblemException(status=409, code="conflict", title="Process name already in use")

    before = {"name": proc.name, "state": proc.state.value}
    for attr in ("name", "criteria", "excluded", "owner_org_role_id", "pdca_phase"):
        if attr in fields:
            setattr(proc, attr, getattr(body, attr))
    if state_changed and body.state is not None:
        proc.state = body.state

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ProblemException(
            status=409, code="conflict", title="Process name already in use"
        ) from None

    after = {"name": proc.name, "state": proc.state.value}
    if state_changed:
        _emit_process_event(
            session, caller, EventType.PROCESS_STATE_CHANGED, proc.id, before=before, after=after
        )
    elif fields:
        _emit_process_event(
            session, caller, EventType.PROCESS_UPDATED, proc.id, before=before, after=after
        )
    await session.commit()
    return _process(proc)


@router.post("/processes/{process_id}/edges", status_code=status.HTTP_201_CREATED)
async def add_edge_endpoint(
    process_id: uuid.UUID,
    body: EdgeCreate,
    caller: AppUser = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _load_process(session, caller, process_id)  # 404 + org guard on the "from" node
    if body.to_process_id == process_id:
        raise ProblemException(
            status=409, code="conflict", title="A process edge cannot loop to itself"
        )
    await _require_org_process(session, caller, body.to_process_id, "to_process_id")
    if await repo.get_process_edge_pair(session, process_id, body.to_process_id) is not None:
        raise ProblemException(status=409, code="conflict", title="Edge already exists")

    edge = ProcessEdge(
        org_id=caller.org_id,
        from_process_id=process_id,
        to_process_id=body.to_process_id,
        io_label=body.io_label,
        created_by=caller.id,
    )
    session.add(edge)
    try:
        await session.flush()  # the UNIQUE/CHECK backstop for a concurrent dup/self-loop
    except IntegrityError:
        await session.rollback()
        raise ProblemException(status=409, code="conflict", title="Edge already exists") from None
    _emit_process_event(
        session,
        caller,
        EventType.PROCESS_EDGE_ADDED,
        process_id,
        after={"to_process_id": str(body.to_process_id), "io_label": body.io_label},
    )
    await session.commit()
    return _edge(edge)


@router.delete("/processes/{process_id}/edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_edge_endpoint(
    process_id: uuid.UUID,
    edge_id: uuid.UUID,
    caller: AppUser = Depends(_manage),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _load_process(session, caller, process_id)  # 404 + org guard on the "from" node
    edge = await repo.get_process_edge(session, edge_id)
    if edge is None or edge.from_process_id != process_id or edge.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Edge not found")
    await session.delete(edge)
    _emit_process_event(
        session,
        caller,
        EventType.PROCESS_EDGE_REMOVED,
        process_id,
        before={"to_process_id": str(edge.to_process_id), "io_label": edge.io_label},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- owner-assignment (process.assign_owner) --------------------------------------------


@router.get("/processes/{process_id}/owners")
async def list_process_owners_endpoint(
    process_id: uuid.UUID,
    caller: AppUser = Depends(_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """The process's recorded owners (the org_role_assignment RACI rows). Gated process.read — the
    same lens as the process roster/map (doc 15 §8.4)."""
    await _load_process(session, caller, process_id)
    rows = (
        await session.execute(
            select(OrgRoleAssignment, OrgRole.name)
            .join(OrgRole, OrgRole.id == OrgRoleAssignment.org_role_id)
            .where(
                OrgRoleAssignment.org_id == caller.org_id,
                OrgRoleAssignment.process_id == process_id,
            )
            .order_by(OrgRoleAssignment.created_at)
        )
    ).all()
    return [_owner(a, name) for a, name in rows]


@router.post("/processes/{process_id}/owner", status_code=status.HTTP_201_CREATED)
async def assign_process_owner_endpoint(
    process_id: uuid.UUID,
    body: OwnerAssignCreate,
    caller: AppUser = Depends(_assign_owner),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Bind a user as the accountable owner of this process — recording the RACI fact AND minting
    the concrete PROCESS-scoped Process-Owner grant (substituting the :assignment_process
    placeholder). Idempotent. Gated process.assign_owner at the process's PROCESS scope."""
    proc = await _load_process(session, caller, process_id)
    user = await _load_user(session, caller, body.user_id)
    return await assign_process_owner(
        session, actor=caller, process=proc, user=user, org_role_id=body.org_role_id
    )


@router.delete("/processes/{process_id}/owner/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_process_owner_endpoint(
    process_id: uuid.UUID,
    user_id: uuid.UUID,
    caller: AppUser = Depends(_assign_owner),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Unbind a user as owner of this process — removing the RACI row AND narrowing the
    Process-Owner grant's process_ids (dropping it when the last owned process goes)."""
    proc = await _load_process(session, caller, process_id)
    user = await _load_user(session, caller, user_id)
    await revoke_process_owner(session, actor=caller, process=proc, user=user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
