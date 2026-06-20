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
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import (
    CapaCloseState,
    CapaSource,
    NcrDisposition,
    NcrSource,
    NcSeverity,
)
from ...db.models._dcr_enums import DcrChangeType, DcrReasonClass, DcrSourceLinkType
from ...db.models._vault_enums import ChangeSignificance
from ...db.models._workflow_enums import WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.capa import Capa
from ...db.models.capa_stage import CapaStage
from ...db.models.complaint import Complaint
from ...db.models.dcr import Dcr
from ...db.models.ncr import Ncr
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.capa import (
    VERIFIER_DECISIONS,
    ClosureOutcome,
    adjudicate_capa_closure,
    allowed_targets,
    capa_self_verify_blocked,
    derive_implementer_ids,
    transition_allowed,
)
from ...domain.vault import format_identifier
from ...logging import request_id_var
from ...problems import ProblemException
from ..dcr import raise_dcr
from ..records.service import capture_record, emit_record_event
from ..vault import repository as vault_repo
from ..vault.signature import SignatureEvent, SignatureEventSink
from ..workflow import engine
from ..workflow import repository as wf_repo
from . import repository as repo

_NCR_PREFIX = "NCR"  # {NCR}-{SEQ} identifier (own-table; the AUDPROG precedent, no area)

# The seeded (mig 0038) declarative definition that routes the CAPA action-plan approval by severity
# (Critical = QMS-Owner → Top-Management sequential stages; Major/Minor = QMS-Owner ANY; doc 10 §6).
_CAPA_APPROVAL_DEF_KEY = "capa_action_plan_approval"
# The engine's terminal/sentinel instance states — anything else means the flow is still running,
# so the propose guard treats only these as "no active approval" (NEEDS_ATTENTION is an abandoned
# fail-closed instance → re-propose is allowed once a real approver pool is assigned).
_TERMINAL_INSTANCE_STATES = (engine.COMPLETED, engine.REJECTED, engine.NEEDS_ATTENTION)


def _content_digest(content_block: dict[str, Any]) -> str:
    """A deterministic ``sha256:`` digest of a sealed stage block — binds the approval
    ``signature_event`` to the exact bytes it signed (the document-version ``source_blob_sha256``
    analogue for a JSONB stage block; canonical key order so it is reproducible)."""
    payload = json.dumps(content_block, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    # Codex P2: ``risk`` is the spawn-only origin tag — a direct raise with source=risk would mark a
    # CAPA risk-originated with NO risk id, NO linked_capa_id latch, and NO RISK_SPAWNED_CAPA audit,
    # breaking the risk→CAPA traceability. Reject it; the spawn endpoint sets it via build_capa.
    if source is CapaSource.risk:
        raise _validation_error(
            "source", "reserved", "risk is reserved for the risk→CAPA spawn (POST /risks/{id}/capa)"
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


async def advance_capa_to_root_cause(
    session: AsyncSession,
    actor: AppUser,
    capa_id: uuid.UUID,
    *,
    content_block: dict[str, Any],
) -> Capa:
    """``Containment → RootCause``: append the sealed RCA narrative (5-Whys / fishbone) + advance
    ``close_state`` (gate ``capa.record_rca``). RootCause is an informational gate — it carries NO
    signature (doc 10 §6.2; only the Action-Plan approval signs). Mirrors
    ``advance_capa_to_containment``."""
    if not content_block:
        raise _validation_error("content_block", "required", "content_block must be non-empty")
    capa = await repo.get_capa(session, capa_id, for_update=True)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    if not transition_allowed(capa.close_state, CapaCloseState.RootCause):
        legal = sorted(s.value for s in allowed_targets(capa.close_state))
        hint = f" (legal next: {', '.join(legal)})" if legal else " (CAPA is terminal)"
        raise _conflict(
            "invalid_capa_transition",
            f"CAPA in {capa.close_state.value} cannot move to RootCause{hint}",
        )
    session.add(
        CapaStage(
            org_id=actor.org_id,
            capa_id=capa.id,
            stage=CapaCloseState.RootCause,
            content_block=content_block,
            cycle_marker=capa.cycle_marker,
            created_by=actor.id,
        )
    )
    before = capa.close_state
    capa.close_state = CapaCloseState.RootCause
    emit_record_event(
        session,
        actor,
        EventType.CAPA_TRANSITIONED,
        capa.id,
        before={"close_state": before.value},
        after={"close_state": CapaCloseState.RootCause.value},
    )
    await session.commit()
    await session.refresh(capa)
    return capa


async def propose_action_plan(
    session: AsyncSession,
    actor: AppUser,
    capa_id: uuid.UUID,
    *,
    content_block: dict[str, Any],
) -> tuple[Capa, WorkflowInstance]:
    """Propose the corrective Action Plan + open the severity-routed approval workflow (gate
    ``capa.plan_action``). The proposed plan rides the ``workflow_instance.context`` (a draft until
    approved); ``close_state`` STAYS ``RootCause`` — the FSM flip to ``ActionPlan`` happens only at
    approval-complete (in ``decide_capa_action_plan``), so ``close_state == ActionPlan`` ⟺ the plan
    was APPROVED (doc 10 §6.2/§6.3). At most one active approval per CAPA (the engine's
    NEEDS_ATTENTION fail-closed instance — empty approver pool — is terminal, so a re-propose after
    assigning approvers is allowed)."""
    if not content_block:
        raise _validation_error("content_block", "required", "content_block must be non-empty")
    capa = await repo.get_capa(session, capa_id, for_update=True)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    if not transition_allowed(capa.close_state, CapaCloseState.ActionPlan):
        legal = sorted(s.value for s in allowed_targets(capa.close_state))
        hint = f" (legal next: {', '.join(legal)})" if legal else " (CAPA is terminal)"
        raise _conflict(
            "invalid_capa_transition",
            f"CAPA in {capa.close_state.value} cannot propose an action plan{hint}",
        )
    existing = await wf_repo.find_nonterminal_instance(
        session, actor.org_id, WorkflowSubjectType.CAPA, capa.id, _TERMINAL_INSTANCE_STATES
    )
    if existing is not None:
        raise _conflict(
            "capa_approval_in_progress",
            "An action-plan approval workflow is already in progress for this CAPA",
        )
    instance = await engine.instantiate(
        session,
        org_id=actor.org_id,
        definition_key=_CAPA_APPROVAL_DEF_KEY,
        subject_type=WorkflowSubjectType.CAPA,
        subject_id=capa.id,
        context={
            "severity": capa.severity.value,
            "action_plan": content_block,
            "proposed_by": str(actor.id),
        },
        actor=actor,
    )
    await session.commit()
    await session.refresh(capa)
    await session.refresh(instance)
    return capa, instance


async def _assert_capa_approver(
    session: AsyncSession, actor: AppUser, task: Task, instance: WorkflowInstance
) -> None:
    """The SOLE authorization gate for a CAPA action-plan approval decision (no catalog key gates it
    — the role-resolved candidate pool IS the authority, the self-scoped-task doctrine doc 07). Both
    collapse legs return **404** uniformly (the sensitive-task collapse — never reveal another
    approver's task or leak authority state); the cross-stage clash is a 409. MUST run under the
    instance lock the caller holds (``decide_capa_action_plan`` locks it FOR UPDATE first), so the
    cross-stage read is atomic with the engine's outcome write.

    - **Task ownership:** the caller must be THIS task's assignee / candidate (each engine candidate
      gets its own task) — else 404.
    - **Live authority (closes the role-revoked-mid-task staleness window):** the caller must
      CURRENTLY hold one of the stage's roles, re-resolved at decision time — not merely have been
      in the frozen candidate pool at materialize time — else 404.
    - **Cross-STAGE distinct-approver (honours the owner's two-tier Critical choice):** the caller
      must not already have decided an earlier stage of THIS approval, so a single user holding both
      the QMS-Owner and Top-Management roles cannot clear both sequential Critical tiers (409).
    """
    pool_frozen = task.candidate_pool or []
    if task.assignee_user_id != actor.id and str(actor.id) not in pool_frozen:
        raise _not_found("Task")
    stages = await wf_repo.all_stages(session, instance.definition_id)
    stage = stages.get(task.stage_key)
    if stage is None:
        raise _not_found("Task")
    roles = list((stage.assignees or {}).get("roles", []))
    pool = await wf_repo.users_with_roles(session, actor.org_id, roles)
    if actor.id not in pool:
        raise _not_found("Task")
    if await wf_repo.actor_decided_in_instance(
        session, instance.id, actor.id, exclude_task_id=task.id
    ):
        raise ProblemException(
            status=409,
            code="conflict",
            title="Already decided this approval",
            detail="an approver may not decide more than one stage of one action-plan approval",
        )


async def _enrich_completed_replay(
    session: AsyncSession, result: dict[str, Any], capa_id: uuid.UUID, instance_id: uuid.UUID
) -> None:
    """Re-derive the CAPA-specific fields (``capa_close_state`` + ``signature_event_id``) for an
    idempotent replay whose original decision COMPLETED the approval — so a retry's response matches
    the original. Scoped to THIS instance's sealed ActionPlan stage (each approval seals one stage,
    tagged with its ``workflow_instance_id``): after an effectiveness-loop re-approval there are
    MULTIPLE signed ActionPlan stages across cycles, so an unscoped ``signed[-1]`` would return a
    later cycle's signature on a replay of an earlier cycle's task (S-capa-3 fix)."""
    capa = await repo.get_capa(session, capa_id)
    if capa is None:
        return
    result["capa_close_state"] = capa.close_state.value
    signed = [
        s
        for s in await repo.list_capa_stages(session, capa.id)
        if s.stage is CapaCloseState.ActionPlan
        and s.signed_event_id is not None
        and (s.content_block or {}).get("workflow_instance_id") == str(instance_id)
    ]
    result["signature_event_id"] = str(signed[-1].signed_event_id) if signed else None


async def decide_capa_action_plan(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    sig_sink: SignatureEventSink,
) -> dict[str, Any]:
    """Decide a CAPA action-plan approval task (the ``POST /tasks/{id}/decision`` CAPA dispatch).
    Runs the generic engine decision WITHOUT committing, then — on the COMPLETING approval — writes
    the real ``signature_event(meaning=approval, signed_object=capa_stage)``, appends the SIGNED
    ``ActionPlan`` stage block, and flips ``close_state`` RootCause→ActionPlan, all in ONE
    transaction. The append-only ``capa_stage`` never gets an UPDATE: the stage id is pre-generated
    so the signature (``signed_object_id`` = that id) and the stage (``signed_event_id`` = the
    flushed signature id) are two mutually-referencing INSERTs."""
    # Lock the instance FIRST (the engine's serialization point): the cross-stage approver
    # read + the engine's outcome write then serialize under ONE lock, so a concurrent same-instance
    # decision blocks here rather than racing the cross-stage guard. engine.decide re-locks the
    # same row (re-entrant within the txn).
    instance = await wf_repo.lock_instance_for_update(session, task.instance_id)
    if instance is None or instance.org_id != actor.org_id:
        raise _not_found("Workflow instance")
    await _assert_capa_approver(session, actor, task, instance)

    result = await engine.decide(
        session,
        task,
        actor,
        outcome=outcome,
        comment=comment,
        idempotency_key=idempotency_key,
        _commit=False,
    )
    if result.get("replayed"):
        # An idempotent replay added no rows; re-derive the CAPA-specific fields the ORIGINAL
        # completing response carried (so a retry's body matches), then commit (no-op) to release
        # the engine's locks.
        if result.get("current_state") == engine.COMPLETED:
            await _enrich_completed_replay(session, result, instance.subject_id, instance.id)
        await session.commit()
        return result

    if result["current_state"] == engine.COMPLETED:
        capa = await repo.get_capa(session, instance.subject_id, for_update=True)
        if capa is None or capa.org_id != actor.org_id:
            raise _not_found("CAPA")
        if not transition_allowed(capa.close_state, CapaCloseState.ActionPlan):
            raise _conflict(
                "invalid_capa_transition",
                f"CAPA in {capa.close_state.value} cannot move to ActionPlan",
            )
        context = instance.context or {}
        action_plan = context.get("action_plan") or {}
        sealed: dict[str, Any] = {
            **action_plan,
            "proposed_by": context.get("proposed_by"),
            "approved_by": str(actor.id),
            "workflow_instance_id": str(instance.id),
        }
        stage_id = uuid.uuid4()  # pre-gen so signature ↔ stage cross-reference with no UPDATE
        sig = sig_sink.record(
            session,
            SignatureEvent(
                org_id=actor.org_id,
                signed_object_id=stage_id,
                meaning="approval",
                signer_user_id=actor.id,
                signed_object_type="capa_stage",
                content_digest=_content_digest(sealed),
                auth_context={"acr": "SESSION"},
            ),
        )
        await session.flush()  # the sink adds but does NOT flush — populate sig.id for the FK
        session.add(
            CapaStage(
                id=stage_id,
                org_id=actor.org_id,
                capa_id=capa.id,
                stage=CapaCloseState.ActionPlan,
                content_block=sealed,
                signed_event_id=sig.id if sig is not None else None,
                cycle_marker=capa.cycle_marker,
                created_by=actor.id,
            )
        )
        before = capa.close_state
        capa.close_state = CapaCloseState.ActionPlan
        emit_record_event(
            session,
            actor,
            EventType.CAPA_TRANSITIONED,
            capa.id,
            before={"close_state": before.value},
            after={
                "close_state": CapaCloseState.ActionPlan.value,
                "signed_event_id": str(sig.id) if sig is not None else None,
            },
        )
        result["capa_close_state"] = CapaCloseState.ActionPlan.value
        result["signature_event_id"] = str(sig.id) if sig is not None else None

    await session.commit()
    return result


async def advance_capa_to_implement(
    session: AsyncSession,
    actor: AppUser,
    capa_id: uuid.UUID,
    *,
    content_block: dict[str, Any],
) -> Capa:
    """``ActionPlan → Implement``: append the action-completion narrative stage + advance
    ``close_state`` (gate ``capa.capture_effectiveness``, a Process-Owner key). UNSIGNED — the
    implementer records what was done; the independent verifier signs at Verify (doc 10 §6.2).
    Completion evidence is linked to the returned Implement stage via
    ``POST /records/{id}/evidence-links`` (target_type=capa_stage). Mirrors
    ``advance_capa_to_root_cause``; the pure FSM 409s any non-ActionPlan source state."""
    if not content_block:
        raise _validation_error("content_block", "required", "content_block must be non-empty")
    capa = await repo.get_capa(session, capa_id, for_update=True)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    if not transition_allowed(capa.close_state, CapaCloseState.Implement):
        legal = sorted(s.value for s in allowed_targets(capa.close_state))
        hint = f" (legal next: {', '.join(legal)})" if legal else " (CAPA is terminal)"
        raise _conflict(
            "invalid_capa_transition",
            f"CAPA in {capa.close_state.value} cannot move to Implement{hint}",
        )
    session.add(
        CapaStage(
            org_id=actor.org_id,
            capa_id=capa.id,
            stage=CapaCloseState.Implement,
            content_block=content_block,
            cycle_marker=capa.cycle_marker,
            created_by=actor.id,
        )
    )
    before = capa.close_state
    capa.close_state = CapaCloseState.Implement
    emit_record_event(
        session,
        actor,
        EventType.CAPA_TRANSITIONED,
        capa.id,
        before={"close_state": before.value},
        after={"close_state": CapaCloseState.Implement.value, "cycle_marker": capa.cycle_marker},
    )
    await session.commit()
    await session.refresh(capa)
    return capa


async def verify_capa(
    session: AsyncSession,
    actor: AppUser,
    capa_id: uuid.UUID,
    *,
    decision: str,
    content_block: dict[str, Any],
    sig_sink: SignatureEventSink,
) -> Capa:
    """``Implement → Verify``: record the verifier's effectiveness ``decision`` (``effective`` /
    ``not_effective``) as a SIGNED Verify stage (gate ``capa.verify``, doc 10 §6.2).

    **SoD-4 (severity-aware) is enforced UNCONDITIONALLY here**, before the permission gate has any
    say (a SYSTEM ``capa.verify`` grant never bypasses it — the SoD-6 precedent): the verifier must
    not be among the CAPA's implementers (Critical / Major always; Minor unless the org set
    ``allow_capa_self_verify``). The implementer set is the union over the WHOLE append-only stage
    trail (every cycle). On a block, 409 ``sod_self_verify``.

    Writes ONE ``signature_event(meaning=verify, signed_object=capa_stage)`` the S-capa-2 way — a
    pre-generated stage UUID makes the signature (``signed_object_id``) and the stage
    (``signed_event_id``) two mutually-referencing INSERTs, never an UPDATE on the REVOKE-immutable
    table. The ``decision`` is sealed into the stage block; the M4 close gate reads it. Evidence
    linked to the returned Verify stage is then FROZEN (unlink-blocked)."""
    if decision not in VERIFIER_DECISIONS:
        raise _validation_error(
            "decision", "invalid", "decision must be 'effective' or 'not_effective'"
        )
    if not content_block:
        raise _validation_error("content_block", "required", "content_block must be non-empty")
    capa = await repo.get_capa(session, capa_id, for_update=True)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    if not transition_allowed(capa.close_state, CapaCloseState.Verify):
        legal = sorted(s.value for s in allowed_targets(capa.close_state))
        hint = f" (legal next: {', '.join(legal)})" if legal else " (CAPA is terminal)"
        raise _conflict(
            "invalid_capa_transition",
            f"CAPA in {capa.close_state.value} cannot move to Verify{hint}",
        )
    # SoD-4: verifier ≠ implementer (severity-aware), under FOR UPDATE, BEFORE any grant has say.
    stages = await repo.list_capa_stages(session, capa.id)
    implementer_ids = derive_implementer_ids(
        (s.stage, s.created_by, s.content_block) for s in stages
    )
    allow = await repo.allow_capa_self_verify(session, actor.org_id)
    if capa_self_verify_blocked(
        actor.id, implementer_ids, severity=capa.severity, allow_capa_self_verify=allow
    ):
        raise _conflict(
            "sod_self_verify",
            "Verification refused: the CAPA's action implementer may not verify it (SoD-4)",
        )
    sealed: dict[str, Any] = {**content_block, "decision": decision, "verified_by": str(actor.id)}
    stage_id = uuid.uuid4()  # pre-gen so signature ↔ stage cross-reference with no UPDATE
    sig = sig_sink.record(
        session,
        SignatureEvent(
            org_id=actor.org_id,
            signed_object_id=stage_id,
            meaning="verify",
            signer_user_id=actor.id,
            signed_object_type="capa_stage",
            content_digest=_content_digest(sealed),
            auth_context={"acr": "SESSION"},
        ),
    )
    await session.flush()  # the sink adds but does NOT flush — populate sig.id for the FK
    session.add(
        CapaStage(
            id=stage_id,
            org_id=actor.org_id,
            capa_id=capa.id,
            stage=CapaCloseState.Verify,
            content_block=sealed,
            signed_event_id=sig.id if sig is not None else None,
            cycle_marker=capa.cycle_marker,
            created_by=actor.id,
        )
    )
    before = capa.close_state
    capa.close_state = CapaCloseState.Verify
    emit_record_event(
        session,
        actor,
        EventType.CAPA_TRANSITIONED,
        capa.id,
        before={"close_state": before.value},
        after={
            "close_state": CapaCloseState.Verify.value,
            "decision": decision,
            "signed_event_id": str(sig.id) if sig is not None else None,
            "cycle_marker": capa.cycle_marker,
        },
    )
    await session.commit()
    await session.refresh(capa)
    return capa


async def close_capa(session: AsyncSession, actor: AppUser, capa_id: uuid.UUID) -> Capa:
    """The M4 closure gate (gate ``capa.close``, doc 10 §6.4). Requires ``close_state == Verify``;
    reads the CURRENT-cycle Verify decision + derives the M4 evidence booleans server-side under the
    ``capa`` FOR UPDATE, then adjudicates via the pure :func:`adjudicate_capa_closure`:

    - ``effective`` + root_cause ∧ implemented-action-with-evidence ∧ effectiveness-evidence →
      ``Verify → Closed`` (the audit-close gate of S-aud-2 becomes satisfiable here in production).
    - ``not_effective`` → the effectiveness LOOP: ``Verify → RootCause``, ``cycle_marker++`` (a
      revised plan must be re-proposed + re-approved).
    - ``effective`` but an evidence clause is missing → 409 ``capa_close_incomplete`` (the recorded
      verification is NOT discarded — the QM links the missing evidence and re-closes).

    Evidence presence is **current-cycle-scoped** (the Implement / Verify stages of this iteration);
    ``root_cause`` is cycle-agnostic (the established RCA carries across loop iterations)."""
    capa = await repo.get_capa(session, capa_id, for_update=True)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    if capa.close_state is not CapaCloseState.Verify:
        raise _conflict(
            "invalid_capa_transition",
            f"CAPA in {capa.close_state.value} cannot be closed (must be Verify)",
        )
    cycle = capa.cycle_marker
    stages = await repo.list_capa_stages(session, capa.id)
    verify_stages = [
        s for s in stages if s.stage is CapaCloseState.Verify and s.cycle_marker == cycle
    ]
    decision = (verify_stages[-1].content_block or {}).get("decision") if verify_stages else None
    if decision not in VERIFIER_DECISIONS:  # no valid verification recorded for this cycle
        raise _conflict(
            "capa_not_verified", "No effectiveness verification recorded for this cycle"
        )
    impl_ids = [
        s.id for s in stages if s.stage is CapaCloseState.Implement and s.cycle_marker == cycle
    ]
    verify_ids = [s.id for s in verify_stages]
    with_evidence = await repo.stages_with_evidence(session, impl_ids + verify_ids)
    outcome, missing = adjudicate_capa_closure(
        decision=decision,
        has_root_cause=any(s.stage is CapaCloseState.RootCause for s in stages),
        has_implemented_with_evidence=any(sid in with_evidence for sid in impl_ids),
        has_effectiveness_evidence=any(sid in with_evidence for sid in verify_ids),
    )
    if outcome is ClosureOutcome.INCOMPLETE:
        raise _conflict(
            "capa_close_incomplete",
            "Cannot close (effective verification missing evidence): " + ", ".join(missing),
        )
    target = CapaCloseState.Closed if outcome is ClosureOutcome.CLOSE else CapaCloseState.RootCause
    if not transition_allowed(capa.close_state, target):  # defensive — Verify→{Closed,RootCause}
        raise _conflict(
            "invalid_capa_transition",
            f"CAPA in {capa.close_state.value} cannot move to {target.value}",
        )
    before = capa.close_state
    if outcome is ClosureOutcome.LOOP:
        capa.cycle_marker = cycle + 1  # the next implement/verify iteration; under FOR UPDATE
    capa.close_state = target
    emit_record_event(
        session,
        actor,
        EventType.CAPA_TRANSITIONED,
        capa.id,
        before={"close_state": before.value, "cycle_marker": cycle},
        after={
            "close_state": target.value,
            "outcome": outcome.value,
            "decision": decision,
            "cycle_marker": capa.cycle_marker,
        },
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


# --- CAPA → DCR loop (S-dcr-5; doc 02 Cl 10.2 / doc 05 §5.1, the §10→§7.5 closed loop) ---------

_TERMINAL_CAPA_STATES = (CapaCloseState.Closed, CapaCloseState.Rejected)


async def _find_spawned_dcr(
    session: AsyncSession, org_id: uuid.UUID, capa_id: uuid.UUID, idempotency_key: str | None
) -> Dcr | None:
    """The DCR this CAPA already spawned for ``idempotency_key`` (None when no key). Scoped to
    (org, this CAPA, key) so the same key on a DIFFERENT CAPA does not collide — matching the
    ``(org_id, source_link_id, spawn_idempotency_key)`` partial-UNIQUE."""
    if idempotency_key is None:
        return None
    return (
        await session.execute(
            select(Dcr).where(
                Dcr.org_id == org_id,
                Dcr.source_link_type == DcrSourceLinkType.capa,
                Dcr.source_link_id == capa_id,
                Dcr.spawn_idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def raise_dcr_from_capa(
    session: AsyncSession,
    actor: AppUser,
    capa_id: uuid.UUID,
    *,
    change_type: DcrChangeType,
    change_significance: ChangeSignificance,
    reason_text: str,
    target_document_id: uuid.UUID | None = None,
    reason_class: DcrReasonClass = DcrReasonClass.capa,
    proposed_effective_from: datetime.datetime | None = None,
    idempotency_key: str | None = None,
) -> tuple[Dcr, bool]:
    """Spawn a DCR from a CAPA corrective action — the §10→§7.5 loop (doc 05 §5.1: the DCR's
    ``source_link`` = the ``capa_id``, supporting M4 traceability). Returns ``(dcr, created)``;
    ``created`` is False on an idempotent replay.

    The link lives on the DCR (``source_link_type=capa`` + ``source_link_id``), NOT a column on the
    CAPA — a CAPA may drive **multiple** document changes (doc 05 §5.3 'spawns child DCRs'), so
    there is no one-DCR-per-CAPA latch. An ``Idempotency-Key`` (the ``dcr.spawn_idempotency_key``
    partial-UNIQUE) makes a *retry* return the same DCR while preserving 1:N (distinct keys →
    distinct DCRs). A terminal (Closed/Rejected) CAPA cannot spawn (409 ``capa_terminal``). Builds
    the DCR via ``raise_dcr(_commit=False)`` so the spawn + genesis commit in ONE transaction."""
    capa = await repo.get_capa(session, capa_id)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    # Idempotent replay FIRST (before the terminal gate) so a retry against a now-Closed/Rejected
    # CAPA still replays the original DCR rather than 409'ing — the complaint→CAPA latch-before-gate
    # precedent. The dedup is scoped to (this CAPA, key) so the same key on a DIFFERENT CAPA spawns
    # fresh (the import-decision (run_id, key) precedent).
    existing = await _find_spawned_dcr(session, actor.org_id, capa.id, idempotency_key)
    if existing is not None:
        return existing, False
    if capa.close_state in _TERMINAL_CAPA_STATES:
        raise _conflict("capa_terminal", f"a {capa.close_state.value} CAPA cannot spawn a DCR")
    try:
        dcr = await raise_dcr(
            session,
            actor,
            change_type=change_type,
            change_significance=change_significance,
            reason_class=reason_class,
            reason_text=reason_text,
            target_document_id=target_document_id,
            source_link_type=DcrSourceLinkType.capa,
            source_link_id=capa.id,
            proposed_effective_from=proposed_effective_from,
            spawn_idempotency_key=idempotency_key,
            _commit=False,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_spawned_dcr(session, actor.org_id, capa.id, idempotency_key)
        if existing is not None:  # the concurrent winner — idempotent replay
            return existing, False
        raise
    await session.refresh(dcr)
    return dcr, True
