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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import CapaSource, NcSeverity
from ...db.models._iso_audit_enums import AuditState, FindingType
from ...db.models.app_user import AppUser
from ...db.models.audit import Audit
from ...db.models.audit_event import AuditEvent
from ...db.models.audit_finding import AuditFinding
from ...db.models.audit_plan import AuditPlan
from ...db.models.audit_program import AuditProgram
from ...db.models.documented_information import DocumentedInformation
from ...db.models.process import Process
from ...db.models.record import Record
from ...domain.audits import finding_blocks_close, next_state, transition_allowed
from ...domain.vault import format_identifier
from ...logging import request_id_var
from ...problems import ProblemException
from ..capa.service import build_capa
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


def _validation_error(field: str, code: str, message: str) -> ProblemException:
    return ProblemException(
        status=422,
        code="validation_error",
        title=message,
        errors=[{"field": field, "code": code, "message": message}],
    )


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


# --- Audit findings (record subtype) + the NC→CAPA auto-link (S-aud-2) ------------------------


async def _auto_capa_for_finding(
    session: AsyncSession,
    actor: AppUser,
    audit: Audit,
    finding: AuditFinding,
    *,
    severity: NcSeverity,
    identifier: str | None,
) -> uuid.UUID:
    """Atomically auto-create the mandatory CAPA for an NC finding (doc 02 / 10 §5.3) and return its
    id. The CAPA's ``process_id`` is the audited process (the audit's plan auditee process — NOT the
    finding's soft ``process_ref``); ``origin_finding_id`` is the reverse half of the link.

    SYSTEM-side under the auditor's ``finding.create`` authority (auditor independence: an Internal
    Auditor holds ``finding.create``, not ``capa.create``) — no separate ``capa.create`` gate.
    Uncommitted: the caller sets ``finding.auto_capa_id`` + emits the event + commits once."""
    plan = await repo.get_audit_plan(session, audit.plan_id)
    process_id = plan.auditee_process_id if plan is not None else None
    capa = await build_capa(
        session,
        actor,
        title=f"CAPA (from audit finding {identifier})"
        if identifier
        else "CAPA (from audit finding)",
        severity=severity,
        source=CapaSource.audit,
        process_id=process_id,
        origin_finding_id=finding.id,
        raised_block={
            "source": CapaSource.audit.value,
            "finding_id": str(finding.id),
            "audit_id": str(audit.id),
            "clause_ref": finding.clause_ref,
            "severity": severity.value,
        },
        _commit=False,
    )
    return capa.id


async def create_finding(
    session: AsyncSession,
    actor: AppUser,
    audit_id: uuid.UUID,
    *,
    finding_type: FindingType,
    severity: NcSeverity | None = None,
    clause_ref: str | None = None,
    process_ref: str | None = None,
    summary: str | None = None,
) -> AuditFinding:
    """Log a finding against an audit (gate ``finding.create``). An ``NC`` mandatorily auto-creates
    a linked CAPA in the SAME txn (the reverse link ``capa.origin_finding_id``); OBS/OFI do not. The
    audit is held ``FOR UPDATE`` so a concurrent create serializes against the close gate (which
    also locks it). Open-until-Closed (fork B): rejected only once the audit is Closed."""
    audit = await repo.get_audit(session, audit_id, for_update=True)
    if audit is None or audit.org_id != actor.org_id:
        raise _not_found("Audit")
    if audit.state is AuditState.Closed:
        raise _conflict("audit_finding_audit_closed", "Cannot add a finding to a Closed audit")
    if finding_type is FindingType.NC and severity is None:
        raise _validation_error("severity", "required", "An NC finding requires a severity")

    record = await capture_record(
        session,
        actor,
        record_type="AUDIT_FINDING",
        title=summary or f"{finding_type.value} finding",
        _commit=False,
    )
    base = await session.get(DocumentedInformation, record.id)
    finding = AuditFinding(
        id=record.id,
        org_id=actor.org_id,
        audit_id=audit_id,
        finding_type=finding_type,
        severity=severity,
        clause_ref=clause_ref,
        process_ref=process_ref,
        auto_capa_id=None,
    )
    session.add(finding)
    await session.flush()
    # `severity is not None` is guaranteed by the NC guard above; restating it narrows for the type
    # checker (an NC always carries a severity → an auto-CAPA is always created for an NC).
    if finding_type is FindingType.NC and severity is not None:
        finding.auto_capa_id = await _auto_capa_for_finding(
            session,
            actor,
            audit,
            finding,
            severity=severity,
            identifier=base.identifier if base else None,
        )
    emit_record_event(
        session,
        actor,
        EventType.AUDIT_FINDING_CREATED,
        finding.id,
        after={
            "identifier": base.identifier if base else None,
            "audit_id": str(audit_id),
            "finding_type": finding_type.value,
            "severity": severity.value if severity else None,
            "clause_ref": clause_ref,
            "auto_capa_id": str(finding.auto_capa_id) if finding.auto_capa_id else None,
        },
    )
    await session.commit()
    await session.refresh(finding)
    return finding


async def correct_finding(
    session: AsyncSession,
    actor: AppUser,
    finding_id: uuid.UUID,
    *,
    finding_type: FindingType,
    severity: NcSeverity | None = None,
    clause_ref: str | None = None,
    process_ref: str | None = None,
    reason: str | None = None,
) -> AuditFinding:
    """Correct/retype a finding by capturing a superseding successor (correct-don't-edit; the record
    correction mechanic). GENERAL retype (fork A): the successor may be ANY type — to ``NC``
    auto-creates its CAPA (re-entering the live-NC close gate); NC→OBS/OFI declassifies (clears the
    gate). 409 if already superseded; 409 if the audit is Closed."""
    original = await repo.get_finding(session, finding_id)
    if original is None or original.org_id != actor.org_id:
        raise _not_found("Finding")
    audit = await repo.get_audit(session, original.audit_id, for_update=True)
    if audit is None or audit.org_id != actor.org_id:
        raise _not_found("Audit")
    if audit.state is AuditState.Closed:
        raise _conflict("audit_finding_audit_closed", "Cannot correct a finding of a Closed audit")
    original_record = (
        await session.execute(select(Record).where(Record.id == finding_id).with_for_update())
    ).scalar_one_or_none()
    if original_record is None:
        raise _not_found("Finding")
    if original_record.superseded_by_correction is not None:
        raise _conflict("finding_already_corrected", "This finding is already superseded")
    if finding_type is FindingType.NC and severity is None:
        raise _validation_error("severity", "required", "An NC finding requires a severity")

    record = await capture_record(
        session,
        actor,
        record_type="AUDIT_FINDING",
        title=reason or f"{finding_type.value} finding (correction)",
        _correction_of=original.id,
        _commit=False,
    )
    base = await session.get(DocumentedInformation, record.id)
    successor = AuditFinding(
        id=record.id,
        org_id=actor.org_id,
        audit_id=original.audit_id,
        finding_type=finding_type,
        severity=severity,
        clause_ref=clause_ref if clause_ref is not None else original.clause_ref,
        process_ref=process_ref if process_ref is not None else original.process_ref,
        auto_capa_id=None,
    )
    session.add(successor)
    await session.flush()
    # `severity is not None` is guaranteed by the NC guard above; restating it narrows for the type
    # checker (a retype TO NC always carries a severity → its auto-CAPA is always created).
    if finding_type is FindingType.NC and severity is not None:
        successor.auto_capa_id = await _auto_capa_for_finding(
            session,
            actor,
            audit,
            successor,
            severity=severity,
            identifier=base.identifier if base else None,
        )
    original_record.superseded_by_correction = successor.id
    emit_record_event(
        session,
        actor,
        EventType.AUDIT_FINDING_CORRECTED,
        original.id,
        before={"finding_type": original.finding_type.value, "superseded_by_correction": None},
        after={
            "finding_type": finding_type.value,
            "superseded_by_correction": str(successor.id),
            "auto_capa_id": str(successor.auto_capa_id) if successor.auto_capa_id else None,
        },
    )
    await session.commit()
    await session.refresh(successor)
    return successor


async def _audit_close_gate(session: AsyncSession, audit: Audit) -> None:
    """The Closing→Closed gate (block-until-corrected, R39): an audit cannot close while any *live*
    NC finding lacks a linked CAPA at ``close_state=Closed``. Runs under the ``audit`` FOR UPDATE
    ``advance_audit`` already holds (create/correct also lock it → the set is stable). 409 with the
    blocker count; no separate refusal event (parity with the invalid-transition 409)."""
    rows = await repo.findings_for_close_gate(session, audit.id)
    blocking = sum(
        1 for ft, superseded, capa_state in rows if finding_blocks_close(ft, superseded, capa_state)
    )
    if blocking:
        raise _conflict(
            "audit_close_blocked",
            f"Cannot close: {blocking} live NC finding(s) without a Closed CAPA "
            "(close the CAPA, or correct the finding NC→Observation/OFI)",
        )


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
