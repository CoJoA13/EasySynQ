"""The CAPA / NCR / Complaint use-case layer (slice S-capa-1; doc 02 Cl 8.7/10.2, doc 10 §6,
doc 14 §9/§14).

Two record subtypes + one own table:
- ``capa`` / ``complaint`` — ``kind=RECORD`` shared-PK subtypes written via
  ``capture_record(_commit=False)`` (the S-aud-1 ``create_audit`` precedent): the base
  ``documented_information(kind=RECORD)`` + ``record`` row + the family satellite all commit in ONE
  transaction. Their lifecycle/intake events reuse ``object_type=record`` (their ``.id`` IS a record
  id) so ``GET /documents/{id}/audit-events`` surfaces them.
- ``ncr`` — an own table (a working nonconformity, not a captured artifact); its events key on the
  reserved ``object_type=ncr`` (``_emit_ncr``), and it carries its own ``NCR-{SEQ}`` identifier.

The CAPA ``close_state`` FSM (``advance_capa_to_containment``) mirrors the disposition / audit
service: load the CAPA ``FOR UPDATE``, validate the transition (pure ``domain.capa``), append the
sealed ``capa_stage`` block, flip ``close_state``, emit, commit — atomically. S-capa-1 wires only
the ``Raised → Containment`` edge (``capa.update``); later stages land behind their own gates.

The complaint→CAPA spawn is idempotent: the complaint is held ``FOR UPDATE`` across the
check-then-spawn, and ``complaint.spawned_capa_id`` is the latch (a complaint spawns at most one
CAPA). A replay sees the latch set and returns the existing CAPA — committing first to release the
lock promptly (the ``expire_on_commit=False`` sessionmaker keeps the loaded CAPA usable
post-commit).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import (
    CapaCloseState,
    CapaSource,
    NcrDisposition,
    NcrSource,
    NcSeverity,
)
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.capa import Capa
from ...db.models.capa_stage import CapaStage
from ...db.models.complaint import Complaint
from ...db.models.ncr import Ncr
from ...domain.capa import allowed_targets, transition_allowed
from ...domain.vault import format_identifier
from ...logging import request_id_var
from ...problems import ProblemException
from ..records.service import capture_record, emit_record_event
from ..vault import repository as vault_repo
from . import repository as repo

_NCR_PREFIX = "NCR"  # {NCR}-{SEQ} identifier (own-table; the AUDPROG precedent, no area)


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


def _emit_ncr(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    ncr_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append an NCR ``audit_event`` (object_type=ncr) BEFORE commit (the ``emit_record_event`` /
    ``services.audits._emit`` pattern). NCR is an own table, so it cannot reuse ``record``."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.ncr,
            object_id=ncr_id,
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


async def _check_process(
    session: AsyncSession, actor: AppUser, process_id: uuid.UUID | None
) -> None:
    """Validate an optional process_id belongs to the actor's org (the create_audit_plan guard)."""
    if process_id is None:
        return
    from ...db.models.process import Process

    proc = await session.get(Process, process_id)
    if proc is None or proc.org_id != actor.org_id:
        raise _not_found("Process")


# --- Complaint (record subtype) ---------------------------------------------------------------


async def capture_complaint(
    session: AsyncSession,
    actor: AppUser,
    *,
    description: str,
    customer: str | None = None,
    received_at: datetime.datetime | None = None,
    channel: str | None = None,
    severity: NcSeverity | None = None,
) -> Complaint:
    """Capture a lightweight customer complaint (R16) as a ``record_type=COMPLAINT`` record + the
    ``complaint`` satellite, in one transaction (the ``create_audit`` precedent)."""
    title = f"Complaint — {customer}" if customer else "Customer complaint"
    record = await capture_record(
        session, actor, record_type="COMPLAINT", title=title, _commit=False
    )
    complaint = Complaint(
        id=record.id,
        org_id=actor.org_id,
        customer=customer,
        received_at=received_at,
        channel=channel,
        description=description,
        severity=severity,
        spawned_capa_id=None,
    )
    session.add(complaint)
    await session.flush()
    emit_record_event(
        session,
        actor,
        EventType.COMPLAINT_CAPTURED,
        complaint.id,
        after={
            "customer": customer,
            "channel": channel,
            "severity": severity.value if severity else None,
        },
    )
    await session.commit()
    await session.refresh(complaint)
    return complaint


async def spawn_capa_from_complaint(
    session: AsyncSession,
    actor: AppUser,
    complaint_id: uuid.UUID,
    *,
    severity: NcSeverity | None = None,
    process_id: uuid.UUID | None = None,
) -> tuple[Capa, bool]:
    """Idempotently spawn a CAPA from a complaint (R16, one-click spawn-to-CAPA). Returns
    ``(capa, created)`` — ``created`` is False on an idempotent replay (the complaint already
    spawned).

    The complaint row is held ``FOR UPDATE`` across check-then-spawn: the first caller wins the
    write of ``spawned_capa_id``; a concurrent caller blocks on the load, then sees the latch set
    and returns the existing CAPA. ``severity`` resolves from the request (preferred — late triage)
    else the complaint's; a non-null severity is required (the CAPA needs one). S-capa-1 makes no
    SLA on triage time and no audit of WHO triaged (the complaint is immutable; the resolved
    severity is committed at CAPA creation)."""
    complaint = await repo.get_complaint(session, complaint_id, for_update=True)
    if complaint is None or complaint.org_id != actor.org_id:
        raise _not_found("Complaint")

    if complaint.spawned_capa_id is not None:
        existing = await repo.get_capa(session, complaint.spawned_capa_id)
        await session.commit()  # release the FOR UPDATE lock promptly (no mutation on the replay)
        # Org-check the loaded CAPA too (defense-in-depth): every loaded row is org-checked even
        # though a complaint can only ever latch a same-org CAPA the spawn itself created.
        if existing is None or existing.org_id != actor.org_id:
            raise _not_found("CAPA")
        return existing, False

    resolved_severity = severity or complaint.severity
    if resolved_severity is None:
        raise _validation_error(
            "severity", "required", "A severity is required to spawn a CAPA from this complaint"
        )
    await _check_process(session, actor, process_id)

    record = await capture_record(
        session, actor, record_type="CAPA", title="CAPA (from complaint)", _commit=False
    )
    capa = Capa(
        id=record.id,
        org_id=actor.org_id,
        origin_finding_id=None,
        source=CapaSource.complaint,
        severity=resolved_severity,
        process_id=process_id,
        close_state=CapaCloseState.Raised,
        cycle_marker=0,
    )
    session.add(capa)
    await session.flush()
    session.add(
        CapaStage(
            org_id=actor.org_id,
            capa_id=capa.id,
            stage=CapaCloseState.Raised,
            content_block={
                "source": CapaSource.complaint.value,
                "complaint_id": str(complaint.id),
                "description": complaint.description,
                "severity": resolved_severity.value,
            },
            cycle_marker=0,
            created_by=actor.id,
        )
    )
    complaint.spawned_capa_id = capa.id
    emit_record_event(
        session,
        actor,
        EventType.CAPA_RAISED,
        capa.id,
        after={"source": CapaSource.complaint.value, "severity": resolved_severity.value},
    )
    emit_record_event(
        session,
        actor,
        EventType.COMPLAINT_SPAWNED_CAPA,
        complaint.id,
        after={"spawned_capa_id": str(capa.id)},
    )
    await session.commit()
    await session.refresh(capa)
    return capa, True


# --- CAPA (record subtype) --------------------------------------------------------------------


async def build_capa(
    session: AsyncSession,
    actor: AppUser,
    *,
    title: str,
    severity: NcSeverity,
    source: CapaSource,
    process_id: uuid.UUID | None = None,
    origin_finding_id: uuid.UUID | None = None,
    raised_block: dict[str, Any],
    _commit: bool = True,
) -> Capa:
    """The canonical CAPA-create core (S-aud-2 extraction): capture the immutable record, insert the
    ``Capa`` at ``Raised``, append the sealed ``Raised`` ``capa_stage`` block, emit ``CAPA_RAISED``.
    With ``_commit=False`` the caller owns the transaction (the S-aud-2 NC->CAPA auto-link sets
    ``audit_finding.auto_capa_id`` + emits the finding events + commits once). ``origin_finding_id``
    is the reverse half of the auto-link -- NULL for a directly-raised CAPA (the R39 invariant)."""
    record = await capture_record(session, actor, record_type="CAPA", title=title, _commit=False)
    capa = Capa(
        id=record.id,
        org_id=actor.org_id,
        origin_finding_id=origin_finding_id,
        source=source,
        severity=severity,
        process_id=process_id,
        close_state=CapaCloseState.Raised,
        cycle_marker=0,
    )
    session.add(capa)
    await session.flush()
    session.add(
        CapaStage(
            org_id=actor.org_id,
            capa_id=capa.id,
            stage=CapaCloseState.Raised,
            content_block=raised_block,
            cycle_marker=0,
            created_by=actor.id,
        )
    )
    emit_record_event(
        session,
        actor,
        EventType.CAPA_RAISED,
        capa.id,
        after={"source": source.value, "severity": severity.value},
    )
    if _commit:
        await session.commit()
        await session.refresh(capa)
    return capa


async def raise_capa(
    session: AsyncSession,
    actor: AppUser,
    *,
    title: str,
    severity: NcSeverity,
    source: CapaSource = CapaSource.process,
    process_id: uuid.UUID | None = None,
    problem: str | None = None,
) -> Capa:
    """Raise a CAPA directly (source defaults ``process``). ``origin_finding_id`` stays NULL — the
    NC→CAPA auto-link is S-aud-2. Captures the immutable record + the ``Raised`` stage block."""
    if source is CapaSource.review_output:
        raise _validation_error(
            "source", "reserved", "review_output is reserved for the Management-Review family"
        )
    await _check_process(session, actor, process_id)
    return await build_capa(
        session,
        actor,
        title=title,
        severity=severity,
        source=source,
        process_id=process_id,
        raised_block={"problem": problem, "source": source.value, "severity": severity.value},
        _commit=True,
    )


async def advance_capa_to_containment(
    session: AsyncSession,
    actor: AppUser,
    capa_id: uuid.UUID,
    *,
    content_block: dict[str, Any],
) -> Capa:
    """``Raised → Containment``: append the immediate-correction (symptom-fix) stage block + advance
    ``close_state`` (gate ``capa.update``). The only CAPA transition S-capa-1 wires; the pure FSM
    rejects any other source state with a 409."""
    if not content_block:
        raise _validation_error("content_block", "required", "content_block must be non-empty")
    capa = await repo.get_capa(session, capa_id, for_update=True)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    if not transition_allowed(capa.close_state, CapaCloseState.Containment):
        legal = sorted(s.value for s in allowed_targets(capa.close_state))
        hint = f" (legal next: {', '.join(legal)})" if legal else " (CAPA is terminal)"
        raise _conflict(
            "invalid_capa_transition",
            f"CAPA in {capa.close_state.value} cannot move to Containment{hint}",
        )
    session.add(
        CapaStage(
            org_id=actor.org_id,
            capa_id=capa.id,
            stage=CapaCloseState.Containment,
            content_block=content_block,
            cycle_marker=capa.cycle_marker,
            created_by=actor.id,
        )
    )
    before = capa.close_state
    capa.close_state = CapaCloseState.Containment
    emit_record_event(
        session,
        actor,
        EventType.CAPA_TRANSITIONED,
        capa.id,
        before={"close_state": before.value},
        after={"close_state": CapaCloseState.Containment.value},
    )
    await session.commit()
    await session.refresh(capa)
    return capa


# --- NCR (own table) --------------------------------------------------------------------------


async def create_ncr(
    session: AsyncSession,
    actor: AppUser,
    *,
    source: NcrSource,
    description: str,
    severity: NcSeverity,
    process_id: uuid.UUID | None = None,
) -> Ncr:
    """Raise an NCR (ISO 9001 8.7). Allocates a human ``NCR-{SEQ}`` identifier. The 8.7 disposition
    is
    a distinct later action (``record_ncr_disposition``)."""
    await _check_process(session, actor, process_id)
    seq = await vault_repo.allocate_seq(session, actor.org_id, _NCR_PREFIX, "")
    ncr = Ncr(
        org_id=actor.org_id,
        identifier=format_identifier(_NCR_PREFIX, seq),
        source=source,
        description=description,
        severity=severity,
        process_id=process_id,
        created_by=actor.id,
    )
    session.add(ncr)
    await session.flush()
    _emit_ncr(
        session,
        actor,
        EventType.NCR_CREATED,
        ncr.id,
        after={"identifier": ncr.identifier, "source": source.value, "severity": severity.value},
    )
    await session.commit()
    await session.refresh(ncr)
    return ncr


async def record_ncr_disposition(
    session: AsyncSession,
    actor: AppUser,
    ncr_id: uuid.UUID,
    *,
    disposition: NcrDisposition,
    notes: str | None = None,
) -> Ncr:
    """Record the ISO 9001 8.7 disposition decision + its authorizer (gate
    ``ncr.record_correction``).
    The disposition is one-shot — a 409 if already recorded. ``disposition_authorized_by`` is the
    acting authorizer (the caller)."""
    ncr = await repo.get_ncr(session, ncr_id, for_update=True)
    if ncr is None or ncr.org_id != actor.org_id:
        raise _not_found("NCR")
    if ncr.disposition is not None:
        raise _conflict("ncr_already_dispositioned", "This NCR already has a recorded disposition")
    ncr.disposition = disposition
    ncr.disposition_authorized_by = actor.id
    ncr.disposition_notes = notes
    ncr.disposed_at = _now()
    _emit_ncr(
        session,
        actor,
        EventType.NCR_DISPOSITIONED,
        ncr.id,
        before={"disposition": None},
        after={"disposition": disposition.value, "authorized_by": str(actor.id)},
    )
    await session.commit()
    await session.refresh(ncr)
    return ncr
