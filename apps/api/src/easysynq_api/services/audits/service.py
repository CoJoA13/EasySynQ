"""The internal-audit use-case layer (slice S-aud-1; doc 02 Cl 9.2, doc 10 §5, doc 14 §9/§14).

Three entities, two shapes:
- ``audit_program`` / ``audit_plan`` — own-table scheduling containers. Their lifecycle events use
  ``object_type=audit`` (the reserved ``AuditObjectType.audit`` value — no ADD VALUE).
- ``audit`` — a ``kind=RECORD`` shared-PK subtype written via ``capture_record(_commit=False)`` (the
  evidence-pack precedent): the base ``documented_information`` (kind=RECORD) + ``record`` row + the
  ``audit`` satellite all commit in ONE transaction. Its events reuse ``object_type=record``
  (``audit.id`` is a record id) so ``GET /documents/{id}/audit-events`` surfaces them.

The audit FSM (``advance_audit``) mirrors the disposition service: load the audit ``FOR UPDATE``,
validate the transition (pure ``domain.audits``), run the Closing→Closed gate (a no-op until S-aud-2
adds findings), flip ``state``, append the audit row, commit — all atomically.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._iso_audit_enums import AuditState
from ...db.models.app_user import AppUser
from ...db.models.audit import Audit
from ...db.models.audit_event import AuditEvent
from ...db.models.audit_plan import AuditPlan
from ...db.models.audit_program import AuditProgram
from ...db.models.documented_information import DocumentedInformation
from ...db.models.process import Process
from ...domain.audits import next_state, transition_allowed
from ...domain.vault import format_identifier
from ...logging import request_id_var
from ...problems import ProblemException
from ..records.service import capture_record, emit_record_event
from ..vault import repository as vault_repo
from . import repository as repo

_PROGRAM_PREFIX = (
    "AUDPROG"  # {AUDPROG}-{SEQ} identifier (doc 04 §7); programmes are org-wide (no area)
)


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
    object_type: AuditObjectType,
    object_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append an audit_event BEFORE commit (the emit_record_event pattern), parameterized on
    object_type so programme/plan events key on ``audit``, the audit record on ``record``."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=object_type,
            object_id=object_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


def _not_found(what: str) -> ProblemException:
    return ProblemException(status=404, code="not_found", title=f"{what} not found")


def _conflict(code: str, title: str) -> ProblemException:
    return ProblemException(status=409, code=code, title=title)


# --- Audit Programme --------------------------------------------------------------------------


async def create_audit_program(
    session: AsyncSession,
    actor: AppUser,
    *,
    title: str,
    period: str | None = None,
    coverage: dict[str, Any] | None = None,
) -> AuditProgram:
    seq = await vault_repo.allocate_seq(session, actor.org_id, _PROGRAM_PREFIX, "")
    program = AuditProgram(
        org_id=actor.org_id,
        identifier=format_identifier(_PROGRAM_PREFIX, seq),
        title=title,
        period=period,
        coverage=coverage,
        created_by=actor.id,
    )
    session.add(program)
    await session.flush()
    _emit(
        session,
        actor,
        EventType.AUDIT_PROGRAM_CREATED,
        AuditObjectType.audit,
        program.id,
        after={"identifier": program.identifier, "title": title, "period": period},
    )
    await session.commit()
    await session.refresh(program)
    return program


async def update_audit_program(
    session: AsyncSession,
    actor: AppUser,
    program_id: uuid.UUID,
    *,
    title: str | None = None,
    period: str | None = None,
    coverage: dict[str, Any] | None = None,
    archived: bool | None = None,
) -> AuditProgram:
    program = await repo.get_audit_program(session, program_id)
    if program is None or program.org_id != actor.org_id:
        raise _not_found("Audit programme")
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if title is not None and title != program.title:
        before["title"], program.title, after["title"] = program.title, title, title
    if period is not None and period != program.period:
        before["period"], program.period, after["period"] = program.period, period, period
    if coverage is not None and coverage != program.coverage:
        before["coverage"], program.coverage, after["coverage"] = (
            program.coverage,
            coverage,
            coverage,
        )
    if archived is not None and archived != program.archived:
        before["archived"], program.archived, after["archived"] = (
            program.archived,
            archived,
            archived,
        )
    if after:
        _emit(
            session,
            actor,
            EventType.AUDIT_PROGRAM_UPDATED,
            AuditObjectType.audit,
            program.id,
            before=before,
            after=after,
        )
        await session.commit()
        await session.refresh(program)
    return program


# --- Audit Plan -------------------------------------------------------------------------------


async def create_audit_plan(
    session: AsyncSession,
    actor: AppUser,
    program_id: uuid.UUID,
    *,
    auditee_process_id: uuid.UUID | None = None,
    lead_auditor_user_id: uuid.UUID | None = None,
    scheduled_date: datetime.date | None = None,
    checklist_ref: str | None = None,
) -> AuditPlan:
    program = await repo.get_audit_program(session, program_id)
    if program is None or program.org_id != actor.org_id:
        raise _not_found("Audit programme")
    if program.archived:
        raise _conflict("program_archived", "Cannot add a plan to an archived programme")
    if auditee_process_id is not None:
        proc = await session.get(Process, auditee_process_id)
        if proc is None or proc.org_id != actor.org_id:
            raise _not_found("Process")
    plan = AuditPlan(
        org_id=actor.org_id,
        program_id=program_id,
        auditee_process_id=auditee_process_id,
        lead_auditor_user_id=lead_auditor_user_id,
        scheduled_date=scheduled_date,
        checklist_ref=checklist_ref,
        created_by=actor.id,
    )
    session.add(plan)
    await session.flush()
    _emit(
        session,
        actor,
        EventType.AUDIT_PLAN_CREATED,
        AuditObjectType.audit,
        plan.id,
        after={
            "program_id": str(program_id),
            "auditee_process_id": str(auditee_process_id) if auditee_process_id else None,
            "scheduled_date": scheduled_date.isoformat() if scheduled_date else None,
        },
    )
    await session.commit()
    await session.refresh(plan)
    return plan


# --- Audit (record subtype) -------------------------------------------------------------------


async def create_audit(
    session: AsyncSession,
    actor: AppUser,
    *,
    plan_id: uuid.UUID,
    title: str | None = None,
    lead_auditor_user_id: uuid.UUID | None = None,
) -> Audit:
    plan = await repo.get_audit_plan(session, plan_id)
    if plan is None or plan.org_id != actor.org_id:
        raise _not_found("Audit plan")
    resolved_title = title or f"Internal Audit ({plan.scheduled_date or 'unscheduled'})"
    # Capture the immutable record (documented_information kind=RECORD + record row) WITHOUT
    # committing, then attach the audit satellite + emit + commit atomically.
    record = await capture_record(
        session,
        actor,
        record_type="AUDIT",
        title=resolved_title,
        _commit=False,
    )
    # The human identifier (REC-{AREA}-{SEQ}) lives on the base documented_information row, not the
    # record subtype — read it back from the session identity map (capture_record flushed the base).
    base = await session.get(DocumentedInformation, record.id)
    audit = Audit(
        id=record.id,
        org_id=actor.org_id,
        plan_id=plan_id,
        lead_auditor_user_id=lead_auditor_user_id or plan.lead_auditor_user_id,
        state=AuditState.Scheduled,
    )
    session.add(audit)
    await session.flush()
    emit_record_event(
        session,
        actor,
        EventType.AUDIT_CREATED,
        audit.id,
        after={
            "identifier": base.identifier if base else None,
            "plan_id": str(plan_id),
            "state": AuditState.Scheduled.value,
        },
    )
    await session.commit()
    await session.refresh(audit)
    return audit


async def _audit_close_gate(session: AsyncSession, audit: Audit) -> None:
    """The Closing→Closed gate. S-aud-1: a no-op (no ``audit_finding`` table yet). S-aud-2 fills it
    with the 'every live NC finding has a Closed CAPA' check (block-until-corrected, decision 3)."""
    return None


async def advance_audit(
    session: AsyncSession, actor: AppUser, audit_id: uuid.UUID, target: AuditState
) -> Audit:
    audit = await repo.get_audit(session, audit_id, for_update=True)
    if audit is None or audit.org_id != actor.org_id:
        raise _not_found("Audit")
    if not transition_allowed(audit.state, target):
        legal = next_state(audit.state)
        raise _conflict(
            "invalid_audit_transition",
            f"Audit in {audit.state.value} cannot move to {target.value}"
            + (f" (next legal state: {legal.value})" if legal else " (audit is Closed)"),
        )
    if target is AuditState.Closed:
        await _audit_close_gate(session, audit)
    before_state = audit.state
    audit.state = target
    if target is AuditState.InProgress and audit.started_at is None:
        audit.started_at = _now().date()
    if target is AuditState.Closed:
        audit.completed_at = _now().date()
    emit_record_event(
        session,
        actor,
        EventType.AUDIT_CLOSED if target is AuditState.Closed else EventType.AUDIT_TRANSITIONED,
        audit.id,
        before={"state": before_state.value},
        after={"state": target.value},
    )
    await session.commit()
    await session.refresh(audit)
    return audit
