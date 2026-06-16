"""The Improvement Initiative service — manual create + lifecycle transitions + metadata edit
(slice S-improvement-1; doc 02 Cl 10.3, doc 14 §9, decisions-register R46).

Per **R46** an improvement initiative is a mutable-state workflow object (NOT a record):
``create_initiative`` creates it at ``Open`` with a genesis ``improvement_initiative_stage_event``
(from=NULL→Open) and an ``INITIATIVE_RAISED`` audit; every later stage move appends one append-only
``improvement_initiative_stage_event`` and emits ``INITIATIVE_TRANSITIONED`` in the SAME transaction
(the ``dcr``/``capa`` service atomicity pattern). An initiative id is NOT a record id, so its events
key on ``audit_object_type='improvement_initiative'`` (the ``dcr`` ``_emit_dcr`` precedent).

The ``_commit=False`` seam (the ``dcr``/``capa`` precedent) lets a caller open an initiative
atomically inside a larger transaction — used by slice-2's OFI-finding / MR-output spawn endpoints.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._improvement_enums import ImprovementSource, ImprovementStage
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.improvement_initiative import ImprovementInitiative
from ...db.models.improvement_initiative_stage_event import ImprovementInitiativeStageEvent
from ...db.models.process import Process
from ...domain.authz import ResourceContext
from ...domain.improvement import transition_allowed
from ...domain.vault import format_identifier
from ...logging import request_id_var
from ...problems import ProblemException
from ..vault import repository as vault_repo
from . import repository as repo

_IMP_PREFIX = "IMP"  # IMP-{YYYY}-{NNNN}: per-(org, "IMP", year) counter; 4-digit SEQ.

# The terminal stages whose transition sets closed_at (the "filed"/withdrawn moment).
_CLOSING_STATES = (ImprovementStage.Closed, ImprovementStage.Cancelled)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _emit(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    initiative: ImprovementInitiative,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append an initiative ``audit_event`` (object_type=improvement_initiative,
    scope_ref=identifier) BEFORE commit (the ``_emit_dcr`` pattern). An initiative is an own table —
    its id is not a record id, so it cannot reuse ``object_type=record``."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.improvement_initiative,
            object_id=initiative.id,
            scope_ref=initiative.identifier,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


def _not_found(what: str) -> ProblemException:
    return ProblemException(status=404, code="not_found", title=f"{what} not found")


def _conflict(code: str, title: str) -> ProblemException:
    return ProblemException(status=409, code=code, title=title)


def _validation_error(field: str, code: str, message: str) -> ProblemException:
    return ProblemException(
        status=422,
        code="validation_error",
        title=message,
        errors=[{"field": field, "code": code, "message": message}],
    )


async def _improvement_scope(
    session: AsyncSession, initiative: ImprovementInitiative
) -> ResourceContext:
    """The PROCESS-scoped authz target for an initiative (the ``_objective_scope`` / ``_capa``
    precedent): ``process_ids={process_id}`` when set, else SYSTEM (a SYSTEM grant/override always
    matches). Used by the listing row-filter and any path-id write resolver."""
    if initiative.process_id is None:
        return ResourceContext.system()
    return ResourceContext(process_ids=frozenset({str(initiative.process_id)}))


async def create_initiative(
    session: AsyncSession,
    actor: AppUser,
    *,
    title: str,
    description: str | None = None,
    target_outcome: str | None = None,
    source: ImprovementSource = ImprovementSource.manual,
    source_link_id: uuid.UUID | None = None,
    spawn_idempotency_key: str | None = None,
    process_id: uuid.UUID | None = None,
    owner_user_id: uuid.UUID | None = None,
    _commit: bool = True,
) -> ImprovementInitiative:
    """Raise an initiative at ``Open`` (POST /improvement-initiatives). Allocates an
    ``IMP-{YYYY}-{NNNN}`` identifier, writes the genesis stage event + the ``INITIATIVE_RAISED``
    audit, all in one transaction. ``source`` defaults to ``manual`` (the slice-1 standalone raise);
    slice-2 spawns pass ``OFI``/``review`` + ``source_link_id`` + ``spawn_idempotency_key``. The
    ``_commit=False`` seam lets a spawn compose this inside its own transaction."""
    if process_id is not None:
        proc = await session.get(Process, process_id)
        if proc is None or proc.org_id != actor.org_id:
            raise _validation_error(
                "process_id",
                "unknown_process",
                "Unknown process_id (must be a process in your organization)",
            )
    # Validate owner like process_id (the Codex P2): an unknown UUID would otherwise fall through to
    # a raw FK violation / 500, and an owner must belong to this org (FK only targets app_user.id).
    if owner_user_id is not None:
        owner = await session.get(AppUser, owner_user_id)
        if owner is None or owner.org_id != actor.org_id:
            raise _validation_error(
                "owner_user_id",
                "unknown_owner",
                "Unknown owner_user_id (must be a user in your organization)",
            )
    year = _now().year
    seq = await vault_repo.allocate_seq(session, actor.org_id, _IMP_PREFIX, str(year))
    initiative = ImprovementInitiative(
        org_id=actor.org_id,
        identifier=format_identifier(_IMP_PREFIX, seq, str(year), pad=4),
        title=title,
        description=description,
        target_outcome=target_outcome,
        source=source,
        source_link_id=source_link_id,
        spawn_idempotency_key=spawn_idempotency_key,
        process_id=process_id,
        owner_user_id=owner_user_id,
        stage=ImprovementStage.Open,
        created_by=actor.id,
    )
    session.add(initiative)
    await session.flush()  # materialize initiative.id for the genesis stage-event FK
    session.add(
        ImprovementInitiativeStageEvent(
            org_id=actor.org_id,
            initiative_id=initiative.id,
            from_state=None,  # genesis — no predecessor
            to_state=ImprovementStage.Open,
            actor_id=actor.id,
            payload={"source": source.value},
        )
    )
    _emit(
        session,
        actor,
        EventType.INITIATIVE_RAISED,
        initiative,
        after={
            "identifier": initiative.identifier,
            "title": title,
            "source": source.value,
            "stage": ImprovementStage.Open.value,
        },
    )
    if _commit:
        await session.commit()
        await session.refresh(initiative)
    return initiative


async def update_initiative(
    session: AsyncSession,
    actor: AppUser,
    initiative_id: uuid.UUID,
    *,
    title: str | None = None,
    description: str | None = None,
    target_outcome: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    process_id: uuid.UUID | None = None,
) -> ImprovementInitiative:
    """Edit an initiative's mutable metadata (PATCH /improvement-initiatives/{id}); never the
    ``stage`` (that is the transition endpoint). Emits ``INITIATIVE_UPDATED`` only when something
    actually changed. ``None`` means "unchanged" (this slice does not clear a field)."""
    initiative = await repo.get_initiative(session, initiative_id, for_update=True)
    if initiative is None or initiative.org_id != actor.org_id:
        raise _not_found("Improvement initiative")
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if title is not None and title != initiative.title:
        before["title"], after["title"] = initiative.title, title
        initiative.title = title
    if description is not None and description != initiative.description:
        before["description"], after["description"] = initiative.description, description
        initiative.description = description
    if target_outcome is not None and target_outcome != initiative.target_outcome:
        before["target_outcome"] = initiative.target_outcome
        after["target_outcome"] = target_outcome
        initiative.target_outcome = target_outcome
    if owner_user_id is not None and owner_user_id != initiative.owner_user_id:
        owner = await session.get(AppUser, owner_user_id)
        if owner is None or owner.org_id != actor.org_id:
            raise _validation_error(
                "owner_user_id",
                "unknown_owner",
                "Unknown owner_user_id (must be a user in your organization)",
            )
        before["owner_user_id"] = (
            str(initiative.owner_user_id) if initiative.owner_user_id else None
        )
        after["owner_user_id"] = str(owner_user_id)
        initiative.owner_user_id = owner_user_id
    if process_id is not None and process_id != initiative.process_id:
        proc = await session.get(Process, process_id)
        if proc is None or proc.org_id != actor.org_id:
            raise _validation_error(
                "process_id",
                "unknown_process",
                "Unknown process_id (must be a process in your organization)",
            )
        before["process_id"] = str(initiative.process_id) if initiative.process_id else None
        after["process_id"] = str(process_id)
        initiative.process_id = process_id
    if after:
        _emit(session, actor, EventType.INITIATIVE_UPDATED, initiative, before=before, after=after)
    await session.commit()
    await session.refresh(initiative)
    return initiative


async def transition_initiative(
    session: AsyncSession,
    actor: AppUser,
    initiative_id: uuid.UUID,
    *,
    to_state: ImprovementStage,
    comment: str | None = None,
    outcome: str | None = None,
) -> ImprovementInitiative:
    """Move an initiative along the FSM (POST /improvement-initiatives/{id}/transition). The single
    move endpoint covers InProgress / Completed / Closed / Cancelled. FOR UPDATE + populate_existing
    (the S-drift-1 stale-identity-map trap) → FSM 409-guard ``improvement_transition_invalid`` →
    append the append-only stage event → flip ``stage`` (+ set ``closed_at`` on Closed/Cancelled) →
    emit ``INITIATIVE_TRANSITIONED``, all in one transaction. On a Closed move ``outcome`` (when
    present) is folded into the sealed stage_event ``payload`` — the lightweight 10.3 evidence."""
    initiative = await repo.get_initiative(session, initiative_id, for_update=True)
    if initiative is None or initiative.org_id != actor.org_id:
        raise _not_found("Improvement initiative")
    before = initiative.stage
    if not transition_allowed(before, to_state):
        raise _conflict(
            "improvement_transition_invalid",
            f"An improvement initiative in {before.value} cannot move to {to_state.value}",
        )
    payload: dict[str, Any] | None = None
    if to_state is ImprovementStage.Closed and outcome is not None:
        payload = {"outcome": outcome}
    session.add(
        ImprovementInitiativeStageEvent(
            org_id=actor.org_id,
            initiative_id=initiative.id,
            from_state=before,
            to_state=to_state,
            actor_id=actor.id,
            comment=comment,
            payload=payload,
        )
    )
    initiative.stage = to_state
    if to_state in _CLOSING_STATES:
        initiative.closed_at = _now()
    _emit(
        session,
        actor,
        EventType.INITIATIVE_TRANSITIONED,
        initiative,
        before={"stage": before.value},
        after={"stage": to_state.value},
    )
    await session.commit()
    await session.refresh(initiative)
    return initiative
