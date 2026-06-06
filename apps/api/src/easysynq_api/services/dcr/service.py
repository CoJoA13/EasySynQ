"""The Document Change Request (DCR) service — intake + cancel + edit-while-Open (slice S-dcr-1;
doc 05 §5, doc 14 §7, doc 15 §8.7, decisions-register R22).

Per **R22** the DCR is a mutable-state workflow object (NOT a record): ``raise_dcr`` creates it
at ``Open`` with a genesis ``dcr_stage_event`` (from=NULL→Open) and a ``DCR_RAISED`` audit; every
later state move appends one append-only ``dcr_stage_event`` and emits ``DCR_TRANSITIONED`` in
the SAME transaction (the ``capa`` service atomicity pattern). A DCR id is NOT a record id, so
its events key on ``audit_object_type='dcr'`` (the ``ncr`` own-table ``_emit_ncr`` precedent).

S-dcr-1 wired the intake rest-state: ``raise_dcr`` (Open), ``patch_dcr`` (edit
reason/significance while Open), ``cancel_dcr`` (Open/Assessed/Routed → Cancelled). **S-dcr-2
adds ``assess_dcr``** (Open→Assessed + the doc 05 §5.3 impact auto-population). Route + approval
(S-dcr-4), implement/close (S-dcr-5) follow; the full FSM is declared in ``domain/dcr/fsm``.

The ``_commit=False`` seam (the ``capa`` precedent) lets a caller open a DCR atomically inside a
larger transaction — used by S-dcr-5's CAPA-corrective-action → DCR spawn (the §10→§7.5 loop).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._dcr_enums import (
    DcrChangeType,
    DcrReasonClass,
    DcrSourceLinkType,
    DcrState,
    ImpactDimension,
)
from ...db.models._signature_enums import SignatureMeaning, SignedObjectType
from ...db.models._vault_enums import (
    ChangeSignificance,
    DocumentCurrentState,
    DocumentKind,
    VersionState,
)
from ...db.models._workflow_enums import TaskState, WorkflowSubjectType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.dcr import Dcr
from ...db.models.dcr_stage_event import DcrStageEvent
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.impact_assessment import ImpactAssessment
from ...db.models.signature_event import SignatureEvent as SignatureEventRow
from ...db.models.workflow import Task, WorkflowInstance
from ...domain.dcr import transition_allowed
from ...domain.vault import format_identifier
from ...logging import request_id_var
from ...problems import ProblemException
from ..vault import lifecycle
from ..vault import repository as vault_repo
from ..vault.audit import VaultAuditSink
from ..vault.signature import SignatureEvent, SignatureEventSink
from ..workflow import engine
from ..workflow import repository as wf_repo
from . import repository as repo
from .where_used import build_impact_rows, build_where_used

_DCR_APPROVAL_DEF_KEY = "dcr_approval"
_TERMINAL_INSTANCE_STATES = (engine.COMPLETED, engine.REJECTED, engine.NEEDS_ATTENTION)


def _content_digest(content_block: dict[str, Any]) -> str:
    """A deterministic ``sha256:`` digest binding a DCR approval ``signature_event`` to the exact
    decision bytes it signed (the ``capa`` ``_content_digest`` analogue; canonical key order)."""
    payload = json.dumps(content_block, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


_DCR_PREFIX = "DCR"  # DCR-{YYYY}-{SEQ}: per-(org, "DCR", year) counter; 4-digit SEQ (doc 14 §7).


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


def _emit_dcr(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    dcr_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a DCR ``audit_event`` (object_type=dcr) BEFORE commit (the ``_emit_ncr`` pattern).
    A DCR is an own table — its id is not a record id, so it cannot reuse ``object_type=record``."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.dcr,
            object_id=dcr_id,
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


async def _resolve_target(
    session: AsyncSession,
    actor: AppUser,
    change_type: DcrChangeType,
    target_document_id: uuid.UUID | None,
) -> None:
    """Enforce the CREATE ⟺ no-target rule (the DB CHECK is the backstop) and that a REVISE/RETIRE
    target is an in-org controlled Document (not a Record)."""
    if change_type is DcrChangeType.CREATE:
        if target_document_id is not None:
            raise _validation_error(
                "target_document_id", "create_has_target", "A CREATE DCR must not target a document"
            )
        return
    if target_document_id is None:
        raise _validation_error(
            "target_document_id",
            "target_required",
            f"A {change_type.value} DCR must target an existing document",
        )
    target = await session.get(DocumentedInformation, target_document_id)
    if target is None or target.org_id != actor.org_id:
        raise _not_found("Document")
    if target.kind is not DocumentKind.DOCUMENT:
        raise _validation_error(
            "target_document_id",
            "not_a_document",
            "A DCR target must be a controlled Document (not a Record)",
        )


async def raise_dcr(
    session: AsyncSession,
    actor: AppUser,
    *,
    change_type: DcrChangeType,
    change_significance: ChangeSignificance,
    reason_class: DcrReasonClass,
    reason_text: str,
    target_document_id: uuid.UUID | None = None,
    source_link_type: DcrSourceLinkType | None = None,
    source_link_id: uuid.UUID | None = None,
    proposed_effective_from: datetime.datetime | None = None,
    spawn_idempotency_key: str | None = None,
    _commit: bool = True,
) -> Dcr:
    """Raise a DCR at ``Open`` (doc 15 POST /dcrs). Allocates a ``DCR-{YYYY}-{SEQ}`` identifier,
    writes the genesis stage event + the ``DCR_RAISED`` audit, all in one transaction.
    ``spawn_idempotency_key`` (S-dcr-5 CAPA→DCR loop) stamps the retry-dedup key."""
    await _resolve_target(session, actor, change_type, target_document_id)
    year = _now().year
    seq = await vault_repo.allocate_seq(session, actor.org_id, _DCR_PREFIX, str(year))
    dcr = Dcr(
        org_id=actor.org_id,
        identifier=format_identifier(_DCR_PREFIX, seq, str(year), pad=4),
        target_document_id=target_document_id,
        change_type=change_type,
        change_significance=change_significance,
        reason_class=reason_class,
        reason_text=reason_text,
        source_link_type=source_link_type,
        source_link_id=source_link_id,
        proposed_effective_from=proposed_effective_from,
        spawn_idempotency_key=spawn_idempotency_key,
        state=DcrState.Open,
        created_by=actor.id,
    )
    session.add(dcr)
    await session.flush()  # materialize dcr.id for the genesis stage-event FK
    session.add(
        DcrStageEvent(
            org_id=actor.org_id,
            dcr_id=dcr.id,
            from_state=None,  # genesis — no predecessor
            to_state=DcrState.Open,
            actor_id=actor.id,
            payload={
                "change_type": change_type.value,
                "change_significance": change_significance.value,
                "reason_class": reason_class.value,
            },
        )
    )
    _emit_dcr(
        session,
        actor,
        EventType.DCR_RAISED,
        dcr.id,
        after={
            "identifier": dcr.identifier,
            "change_type": change_type.value,
            "change_significance": change_significance.value,
            "state": DcrState.Open.value,
        },
    )
    if _commit:
        await session.commit()
        await session.refresh(dcr)
    return dcr


async def patch_dcr(
    session: AsyncSession,
    actor: AppUser,
    dcr_id: uuid.UUID,
    *,
    reason_text: str | None = None,
    reason_class: DcrReasonClass | None = None,
    change_significance: ChangeSignificance | None = None,
    proposed_effective_from: datetime.datetime | None = None,
) -> Dcr:
    """Edit a DCR's request details while ``Open`` (doc 15 PATCH /dcrs/{id}). 409 once it has
    advanced past Open (a routed/approved change is immutable in its request fields). ``None``
    means "unchanged"
    (S-dcr-1 cannot clear a field — clearing is not an intake need)."""
    dcr = await repo.get_dcr(session, dcr_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    if dcr.state is not DcrState.Open:
        raise _conflict("dcr_not_editable", "A DCR can only be edited while Open")
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if reason_text is not None and reason_text != dcr.reason_text:
        before["reason_text"], after["reason_text"] = dcr.reason_text, reason_text
        dcr.reason_text = reason_text
    if reason_class is not None and reason_class is not dcr.reason_class:
        before["reason_class"], after["reason_class"] = dcr.reason_class.value, reason_class.value
        dcr.reason_class = reason_class
    if change_significance is not None and change_significance is not dcr.change_significance:
        before["change_significance"] = dcr.change_significance.value
        after["change_significance"] = change_significance.value
        dcr.change_significance = change_significance
    if (
        proposed_effective_from is not None
        and proposed_effective_from != dcr.proposed_effective_from
    ):
        before["proposed_effective_from"] = (
            dcr.proposed_effective_from.isoformat() if dcr.proposed_effective_from else None
        )
        after["proposed_effective_from"] = proposed_effective_from.isoformat()
        dcr.proposed_effective_from = proposed_effective_from
    if after:  # only audit when something actually changed
        _emit_dcr(session, actor, EventType.DCR_UPDATED, dcr.id, before=before, after=after)
    await session.commit()
    await session.refresh(dcr)
    return dcr


async def cancel_dcr(
    session: AsyncSession,
    actor: AppUser,
    dcr_id: uuid.UUID,
    *,
    comment: str | None = None,
) -> Dcr:
    """Withdraw a DCR (doc 15 POST /dcrs/{id}/cancel) while not yet approved/implemented. Appends a
    Cancelled stage event + ``DCR_TRANSITIONED``; 409 if the state cannot move to Cancelled."""
    dcr = await repo.get_dcr(session, dcr_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    if not transition_allowed(dcr.state, DcrState.Cancelled):
        raise _conflict("dcr_not_cancellable", f"A DCR in {dcr.state.value} cannot be cancelled")
    before = dcr.state
    dcr.state = DcrState.Cancelled
    session.add(
        DcrStageEvent(
            org_id=actor.org_id,
            dcr_id=dcr.id,
            from_state=before,
            to_state=DcrState.Cancelled,
            actor_id=actor.id,
            comment=comment,
        )
    )
    _emit_dcr(
        session,
        actor,
        EventType.DCR_TRANSITIONED,
        dcr.id,
        before={"state": before.value},
        after={"state": DcrState.Cancelled.value},
    )
    await session.commit()
    await session.refresh(dcr)
    return dcr


async def _upsert_impact(
    session: AsyncSession,
    org_id: uuid.UUID,
    dcr_id: uuid.UUID,
    rows: dict[ImpactDimension, dict[str, Any]],
) -> None:
    """UPSERT one impact_assessment row per dimension (auto_populated re-computed; the requester's
    annotation is PRESERVED on conflict — only auto_populated + updated_at change)."""
    for dimension, auto_populated in rows.items():
        stmt = pg_insert(ImpactAssessment).values(
            org_id=org_id,
            dcr_id=dcr_id,
            dimension=dimension,
            auto_populated=auto_populated,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["dcr_id", "dimension"],
                set_={"auto_populated": stmt.excluded.auto_populated, "updated_at": _now()},
            )
        )


async def assess_dcr(session: AsyncSession, actor: AppUser, dcr_id: uuid.UUID) -> Dcr:
    """Open → Assessed (doc 15 POST /dcrs/{id}/assess). Mirrors ``cancel_dcr`` (FOR UPDATE →
    transition_allowed → append dcr_stage_event → flip state → DCR_TRANSITIONED) AND, in the SAME
    txn, auto-populates the seven doc 05 §5.3 impact dimensions from the target document's
    where-used (a
    CREATE DCR → N/A rows). 409 ``dcr_not_assessable`` if not in Open."""
    dcr = await repo.get_dcr(session, dcr_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    if not transition_allowed(dcr.state, DcrState.Assessed):
        raise _conflict("dcr_not_assessable", f"A DCR in {dcr.state.value} cannot be assessed")
    before = dcr.state
    dcr.state = DcrState.Assessed
    session.add(
        DcrStageEvent(
            org_id=actor.org_id,
            dcr_id=dcr.id,
            from_state=before,
            to_state=DcrState.Assessed,
            actor_id=actor.id,
        )
    )
    where_used: dict[str, Any] = {}
    if dcr.target_document_id is not None:
        where_used = await build_where_used(session, actor.org_id, dcr.target_document_id)
    await _upsert_impact(session, actor.org_id, dcr.id, build_impact_rows(where_used, dcr))
    _emit_dcr(
        session,
        actor,
        EventType.DCR_TRANSITIONED,
        dcr.id,
        before={"state": before.value},
        after={"state": DcrState.Assessed.value},
    )
    await session.commit()
    await session.refresh(dcr)
    return dcr


async def annotate_impact(
    session: AsyncSession,
    actor: AppUser,
    dcr_id: uuid.UUID,
    annotations: dict[str, str],
) -> Dcr:
    """Set ``requester_annotation`` on the named impact dimensions (doc 15 PUT /dcrs/{id}/impact).
    Keys are ``ImpactDimension`` values; unknown keys → 422. Emits ``DCR_UPDATED``. The DCR must
    have
    been assessed (its impact_assessment rows exist) — annotating an absent dimension is a 409."""
    dcr = await repo.get_dcr(session, dcr_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    valid = {d.value for d in ImpactDimension}
    unknown = set(annotations) - valid
    if unknown:
        raise _validation_error(
            "annotations", "unknown_dimension", f"unknown impact dimension(s): {sorted(unknown)}"
        )
    existing = {
        ia.dimension.value: ia for ia in await repo.list_impact_assessments(session, dcr_id)
    }
    for dim_value, text in annotations.items():
        row = existing.get(dim_value)
        if row is None:
            raise _conflict("impact_not_assessed", f"dimension {dim_value} has no assessment yet")
        row.requester_annotation = text
    if annotations:
        _emit_dcr(
            session,
            actor,
            EventType.DCR_UPDATED,
            dcr.id,
            after={"impact_annotated": sorted(annotations)},
        )
    await session.commit()
    await session.refresh(dcr)
    return dcr


# --- S-dcr-4: routing + approval (the declarative engine; doc 05 §5.4)
# --------------------------


async def route_dcr(
    session: AsyncSession, actor: AppUser, dcr_id: uuid.UUID
) -> tuple[Dcr, WorkflowInstance]:
    """Assessed → Routed → InApproval (doc 15 POST /dcrs/{id}/route, gate ``changeRequest.route``).
    Instantiates the severity-routed ``dcr_approval`` workflow (ROUTER on
    ``change_significance``: MAJOR → Process Owner → QMS Owner SEQUENTIAL; MINOR → QMS Owner) and
    flips the DCR to InApproval in ONE txn (no concrete draft is submitted here — the resulting
    version is produced at implement, S-dcr-5; so route resolves the route AND activates the
    approval atomically). 409 if not Assessed, if an approval is already in progress, or if the
    routed role pool is empty (NEEDS_ATTENTION —
    assign the Process Owner / QMS Owner role, then re-route)."""
    dcr = await repo.get_dcr(session, dcr_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    if not transition_allowed(dcr.state, DcrState.Routed):
        raise _conflict("dcr_not_routable", f"A DCR in {dcr.state.value} cannot be routed")
    existing = await wf_repo.find_nonterminal_instance(
        session, actor.org_id, WorkflowSubjectType.DCR, dcr.id, _TERMINAL_INSTANCE_STATES
    )
    if existing is not None:
        raise _conflict(
            "dcr_approval_in_progress", "An approval is already in progress for this DCR"
        )
    instance = await engine.instantiate(
        session,
        org_id=actor.org_id,
        definition_key=_DCR_APPROVAL_DEF_KEY,
        subject_type=WorkflowSubjectType.DCR,
        subject_id=dcr.id,
        context={"change_significance": dcr.change_significance.value},
        actor=actor,
    )
    if instance.current_state == engine.NEEDS_ATTENTION:
        # Empty candidate pool → fail-fast (roll back the dead instance); the DCR stays Assessed
        # so an admin can assign the Process Owner / QMS Owner role and re-route.
        raise _conflict(
            "dcr_no_approvers",
            "no users hold the routed approver role(s); assign Process Owner / QMS Owner first",
        )
    before = dcr.state  # Assessed
    session.add(
        DcrStageEvent(
            org_id=actor.org_id,
            dcr_id=dcr.id,
            from_state=before,
            to_state=DcrState.Routed,
            actor_id=actor.id,
        )
    )
    session.add(
        DcrStageEvent(
            org_id=actor.org_id,
            dcr_id=dcr.id,
            from_state=DcrState.Routed,
            to_state=DcrState.InApproval,
            actor_id=actor.id,
            payload={"workflow_instance_id": str(instance.id)},
        )
    )
    dcr.state = DcrState.InApproval
    _emit_dcr(
        session,
        actor,
        EventType.DCR_TRANSITIONED,
        dcr.id,
        before={"state": before.value},
        after={"state": DcrState.InApproval.value, "workflow_instance_id": str(instance.id)},
    )
    await session.commit()
    await session.refresh(dcr)
    await session.refresh(instance)
    return dcr, instance


async def _assert_dcr_approver(
    session: AsyncSession, actor: AppUser, task: Task, instance: WorkflowInstance
) -> None:
    """The SOLE authorization gate for a DCR approval decision (no catalog key — the role-resolved
    candidate pool IS the authority; ``changeRequest.approve`` rides the pool, the CAPA precedent).
    Mirrors ``_assert_capa_approver``: task-ownership + live-role both 404-collapse (never leak
    another approver's task); the cross-STAGE distinct-approver clash is 409 (one user holding both
    the Proc-Owner and QMS roles can't clear both MAJOR tiers). Runs under the held lock."""
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
        raise _conflict(
            "dcr_approver_conflict",
            "an approver may not decide more than one stage of one DCR approval",
        )


async def _enrich_replay(
    session: AsyncSession, result: dict[str, Any], dcr_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Re-derive the DCR response fields for an idempotent replay (so a retry's body matches the
    original — the CAPA replay-parity precedent). Both ``dcr_state`` and ``signature_event_id`` must
    match. The signature is re-derived by querying signature_event DIRECTLY (NOT a stage row — a
    non-completing MAJOR stage-1 approve signs WITHOUT appending a stage event); the cross-stage
    distinct-approver guard means at most one approval signature per (dcr, signer) per run. A
    reject / changes-requested replay → no approval signature → None."""
    dcr = await repo.get_dcr(session, dcr_id)
    if dcr is None:
        return
    result["dcr_state"] = dcr.state.value
    sig = (
        await session.execute(
            select(SignatureEventRow)
            .where(
                SignatureEventRow.signed_object_id == dcr_id,
                SignatureEventRow.signed_object_type == SignedObjectType.dcr,
                SignatureEventRow.signer_user_id == actor_id,
                SignatureEventRow.meaning == SignatureMeaning.approval,
            )
            .order_by(SignatureEventRow.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    result["signature_event_id"] = str(sig.id) if sig is not None else None


async def decide_dcr_approval(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    sig_sink: SignatureEventSink,
) -> dict[str, Any]:
    """Decide a DCR approval task (the ``POST /tasks/{id}/decision`` DCR dispatch). Runs the generic
    engine decision WITHOUT committing, then maps the outcome onto the DCR FSM in ONE txn:

    - **approve** writes a per-approver ``signature_event(meaning=approval,
      signed_object_type=dcr, signed_object_id=<the DCR id>)`` (doc 05 §5.4 — EACH approval
      signs; a MAJOR DCR yields two); on the COMPLETING approval it ALSO appends the SIGNED
      ``InApproval→Approved`` stage event (``signed_event_id`` = the sealing signature) + flips
      ``state`` → Approved + sets the decision.
    - **reject** → ``state`` → Rejected (terminal); no signature.
    - **changes_requested** → ``state`` → Open (the R40 changes-requested loop); no signature; a
      subsequent re-route opens a FRESH approval instance.
    """
    instance = await wf_repo.lock_instance_for_update(session, task.instance_id)
    if instance is None or instance.org_id != actor.org_id:
        raise _not_found("Workflow instance")
    await _assert_dcr_approver(session, actor, task, instance)

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
        await _enrich_replay(session, result, instance.subject_id, actor.id)
        await session.commit()
        return result

    dcr = await repo.get_dcr(session, instance.subject_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    if dcr.state is not DcrState.InApproval:
        raise _conflict(
            "dcr_not_in_approval", f"A DCR in {dcr.state.value} has no pending approval"
        )

    sig_id: uuid.UUID | None = None
    if outcome == "approve":
        # Per-approver signature on EVERY approve (doc 05 §5.4), completing or not.
        sealed = {
            "dcr_id": str(dcr.id),
            "change_significance": dcr.change_significance.value,
            "stage_key": task.stage_key,
            "approved_by": str(actor.id),
            "workflow_instance_id": str(instance.id),
        }
        sig = sig_sink.record(
            session,
            SignatureEvent(
                org_id=actor.org_id,
                signed_object_id=dcr.id,
                meaning="approval",
                signer_user_id=actor.id,
                signed_object_type="dcr",
                content_digest=_content_digest(sealed),
                auth_context={"acr": "SESSION"},
            ),
        )
        await session.flush()  # populate sig.id for the stage-event FK
        sig_id = sig.id if sig is not None else None
        if result["current_state"] == engine.COMPLETED:
            session.add(
                DcrStageEvent(
                    org_id=actor.org_id,
                    dcr_id=dcr.id,
                    from_state=DcrState.InApproval,
                    to_state=DcrState.Approved,
                    actor_id=actor.id,
                    signed_event_id=sig_id,
                    payload={"workflow_instance_id": str(instance.id)},
                )
            )
            dcr.state = DcrState.Approved
            dcr.decision = "approved"
            dcr.decided_by = actor.id
            dcr.decided_at = _now()
            _emit_dcr(
                session,
                actor,
                EventType.DCR_TRANSITIONED,
                dcr.id,
                before={"state": DcrState.InApproval.value},
                after={"state": DcrState.Approved.value, "signed_event_id": str(sig_id)},
            )
        # else: a non-completing MAJOR stage-1 approve — signature recorded, state stays
        # InApproval.
    elif outcome in ("reject", "changes_requested"):
        target = DcrState.Rejected if outcome == "reject" else DcrState.Open
        session.add(
            DcrStageEvent(
                org_id=actor.org_id,
                dcr_id=dcr.id,
                from_state=DcrState.InApproval,
                to_state=target,
                actor_id=actor.id,
                comment=comment,
            )
        )
        dcr.state = target
        dcr.decision = outcome
        dcr.decided_by = actor.id
        dcr.decided_at = _now()
        # A DCR reject / changes-requested is DECISIVE — one approver ends the approval (the change
        # doesn't proceed as-is). The engine's ANY quorum does NOT fail on a single negative if the
        # stage has other live candidates, so force the instance terminal + skip its sibling PENDING
        # tasks here; else the lingering non-terminal instance would block the re-route after a fix.
        pending = (
            (
                await session.execute(
                    select(Task)
                    .where(Task.instance_id == instance.id, Task.state == TaskState.PENDING)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for sibling in pending:
            sibling.state = TaskState.SKIPPED
        instance.current_state = engine.REJECTED
        _emit_dcr(
            session,
            actor,
            EventType.DCR_TRANSITIONED,
            dcr.id,
            before={"state": DcrState.InApproval.value},
            after={"state": target.value},
        )
    # (any other outcome was already rejected by the engine's TaskOutcomeKind validation → 422)

    await session.commit()
    result["dcr_state"] = dcr.state.value
    result["signature_event_id"] = str(sig_id) if sig_id is not None else None
    return result


# --- S-dcr-5: implement / close (DCR-as-orchestrator; doc 05 §5.5/§6/§7.3) --------------------


def _append_implemented(
    session: AsyncSession,
    actor: AppUser,
    dcr: Dcr,
    *,
    resulting_version_id: uuid.UUID | None,
) -> None:
    """Append the Approved→Implemented stage event + flip ``state`` + emit ``DCR_TRANSITIONED`` (the
    cancel/route transition skeleton). The caller owns the commit (folded into the vault action's
    txn for RETIRE, or its own commit for REVISE/CREATE) so the flip is atomic with the change."""
    before = dcr.state
    rv = str(resulting_version_id) if resulting_version_id else None
    session.add(
        DcrStageEvent(
            org_id=actor.org_id,
            dcr_id=dcr.id,
            from_state=before,
            to_state=DcrState.Implemented,
            actor_id=actor.id,
            payload={"change_type": dcr.change_type.value, "resulting_version_id": rv},
        )
    )
    dcr.state = DcrState.Implemented
    _emit_dcr(
        session,
        actor,
        EventType.DCR_TRANSITIONED,
        dcr.id,
        before={"state": before.value},
        after={"state": DcrState.Implemented.value, "resulting_version_id": rv},
    )


async def _latest_approved_version(
    session: AsyncSession, doc_id: uuid.UUID
) -> DocumentVersion | None:
    # FOR UPDATE: locking the version row makes the "claim it for this implement" (set dcr_id +
    # effective_from) atomic vs a concurrent REVISE implement on the same document — without it two
    # DCRs could both link the same version (the cross-FK has no row-uniqueness; the diff-critic
    # race).
    return (
        await session.execute(
            select(DocumentVersion)
            .where(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.version_state == VersionState.Approved,
            )
            .order_by(DocumentVersion.version_seq.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()


def _assert_version_unclaimed(version: DocumentVersion) -> None:
    """A version may be the resulting version of at most one DCR implement — reject a second claim
    (the cross-FK back-edge would otherwise be silently overwritten). Checked under the version's
    FOR UPDATE lock so concurrent implements serialize."""
    if version.dcr_id is not None:
        raise _conflict(
            "version_already_linked",
            "this version was already linked by another DCR's implement",
        )


async def _resolve_implement_version(
    session: AsyncSession,
    actor: AppUser,
    dcr: Dcr,
    resulting_version_id: uuid.UUID | None,
) -> DocumentVersion:
    """The Approved version a REVISE/CREATE implement will release (RETIRE never calls this).
    REVISE → the target's latest Approved revision (409 ``no_approved_draft`` if none); CREATE → the
    out-of-band-authored ``resulting_version_id`` (422 if absent; 404 cross-org; 409 if not
    Approved). Validates the version belongs to a controlled Document so a Record id cannot be
    released."""
    if dcr.change_type is DcrChangeType.REVISE:
        if (
            dcr.target_document_id is None
        ):  # defensive (the create-iff-no-target CHECK guarantees it)
            raise _not_found("Document")
        version = await _latest_approved_version(session, dcr.target_document_id)
        if version is None:
            raise _conflict(
                "no_approved_draft",
                "the target has no Approved revision to release; approve the revision first",
            )
        _assert_version_unclaimed(version)
        return version
    if resulting_version_id is None:  # CREATE
        raise _validation_error(
            "resulting_version_id",
            "required",
            "a CREATE DCR implement must reference the authored version to release",
        )
    version = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.id == resulting_version_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if version is None or version.org_id != actor.org_id:
        raise _not_found("Version")
    doc = await session.get(DocumentedInformation, version.document_id)
    if doc is None or doc.org_id != actor.org_id or doc.kind is not DocumentKind.DOCUMENT:
        raise _not_found("Document")
    if version.version_state is not VersionState.Approved:
        raise _conflict(
            "version_not_approved",
            f"the resulting version is {version.version_state.value}, not Approved",
        )
    _assert_version_unclaimed(version)
    return version


async def implement_dcr(
    session: AsyncSession,
    actor: AppUser,
    dcr_id: uuid.UUID,
    *,
    sink: VaultAuditSink,
    sig_sink: SignatureEventSink,
    resulting_version_id: uuid.UUID | None = None,
    force_retire: bool = False,
    override_justification: str | None = None,
) -> Dcr:
    """Approved → Implemented (doc 15 POST /dcrs/{id}/implement). DCR-as-orchestrator: the DCR
    drives the vault action for its change_type, atomically with the FSM flip:

    - **REVISE/CREATE** → schedule the resulting version's go-live: set ``effective_from``
      (``proposed_effective_from`` or now) + the cross-FK link (``document_version.dcr_id`` +
      ``dcr.resulting_version_id``) + flip → Implemented, in ONE commit. The ``release_due``
      Beat sweep then runs the SERIALIZABLE single-Effective cutover (the human implementer's
      ``document.release`` + SoD-2 were enforced at the endpoint; the sweep cutover is
      system-attributed — the scheduled-release norm). Close requires the version Effective.
    - **RETIRE** → ``lifecycle.obsolete`` (runs the §7.3 gate then mutates + commits the SAME
      session) atomically with the flip; a coverage gap is 409 unless ``force_retire`` + a reason.

    The endpoint enforces ``changeRequest.implement`` AND the underlying ``document.release`` /
    ``document.obsolete`` (no DCR side-door past document control) before calling this."""
    dcr = await repo.get_dcr(session, dcr_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    if not transition_allowed(dcr.state, DcrState.Implemented):
        raise _conflict(
            "dcr_not_implementable", f"A DCR in {dcr.state.value} cannot be implemented"
        )

    if dcr.change_type is DcrChangeType.RETIRE:
        if dcr.target_document_id is None:  # defensive
            raise _not_found("Document")
        doc = await session.get(DocumentedInformation, dcr.target_document_id)
        if doc is None or doc.org_id != actor.org_id:
            raise _not_found("Document")
        _append_implemented(session, actor, dcr, resulting_version_id=None)
        # obsolete() runs the §7.3 gate then mutates + commits the SAME session — the DCR flip
        # commits atomically with the obsoletion (a blocked gate / illegal move rolls it back).
        await lifecycle.obsolete(
            session,
            sink,
            sig_sink,
            actor,
            doc,
            reason=dcr.reason_text,
            force_retire=force_retire,
            override_justification=override_justification,
        )
        await session.refresh(dcr)
        return dcr

    # REVISE / CREATE: schedule the cutover (immediate = now → swept on the next release_due tick).
    version = await _resolve_implement_version(session, actor, dcr, resulting_version_id)
    version.effective_from = dcr.proposed_effective_from or _now()
    version.dcr_id = dcr.id
    dcr.resulting_version_id = version.id
    _append_implemented(session, actor, dcr, resulting_version_id=version.id)
    await session.commit()
    await session.refresh(dcr)
    return dcr


async def _assert_change_effective(session: AsyncSession, dcr: Dcr) -> None:
    """The §5.5 close gate: the implemented change must have actually taken effect — REVISE/CREATE →
    the resulting version is Effective (the cutover sweep ran); RETIRE → the target is Obsolete."""
    if dcr.change_type is DcrChangeType.RETIRE:
        doc = (
            await session.get(DocumentedInformation, dcr.target_document_id)
            if dcr.target_document_id is not None
            else None
        )
        if doc is None or doc.current_state is not DocumentCurrentState.Obsolete:
            raise _conflict("dcr_effectivity_pending", "the retirement has not yet taken effect")
        return
    if dcr.resulting_version_id is None:
        raise _conflict("dcr_effectivity_pending", "no resulting version is linked yet")
    version = await session.get(DocumentVersion, dcr.resulting_version_id)
    # "Took effect" = the cutover ran, i.e. the version LEFT Approved. It may since have been
    # Superseded by a later revision or Obsoleted — all still count (the change went live). Only a
    # still-Approved version (immediate-pending OR future-scheduled) blocks the close.
    if version is None or version.version_state is VersionState.Approved:
        raise _conflict(
            "dcr_effectivity_pending",
            "the resulting version is not yet Effective (the scheduled cutover is pending)",
        )


async def close_dcr(session: AsyncSession, actor: AppUser, dcr_id: uuid.UUID) -> Dcr:
    """Implemented → Closed (doc 15 POST /dcrs/{id}/close, gate ``changeRequest.close``). The close
    gate requires the change to have actually taken effect (§5.5); 409 ``dcr_effectivity_pending``
    while a scheduled cutover is still outstanding."""
    dcr = await repo.get_dcr(session, dcr_id, for_update=True)
    if dcr is None or dcr.org_id != actor.org_id:
        raise _not_found("DCR")
    if not transition_allowed(dcr.state, DcrState.Closed):
        raise _conflict("dcr_not_closable", f"A DCR in {dcr.state.value} cannot be closed")
    await _assert_change_effective(session, dcr)
    before = dcr.state
    session.add(
        DcrStageEvent(
            org_id=actor.org_id,
            dcr_id=dcr.id,
            from_state=before,
            to_state=DcrState.Closed,
            actor_id=actor.id,
        )
    )
    dcr.state = DcrState.Closed
    _emit_dcr(
        session,
        actor,
        EventType.DCR_TRANSITIONED,
        dcr.id,
        before={"state": before.value},
        after={"state": DcrState.Closed.value},
    )
    await session.commit()
    await session.refresh(dcr)
    return dcr
