"""Management Review service (S-mr-1, clause 9.3) — the txn owner. ``create_review`` reuses the
vault ``create_document`` (kind=DOCUMENT, type MR), then adds the satellite + a clause_mapping to
9.3 (the OBJ recipe). ``submit_review_for_review`` freezes the minutes (Phase 2's
``checkin_mgmt_review_minutes``) + submits + instantiates approval, all in one txn.
``release_review`` is a thin wrapper over the generic release cutover. Output CRUD + meta edits are
Draft-only."""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._mgmt_review_enums import ManagementReviewCloseState, ReviewOutputType
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.management_review import ManagementReview
from ...db.models.review_output import ReviewOutput
from ...domain.mgmt_review.close_gate import output_blocks_close
from ...domain.mgmt_review.minutes import build_minutes
from ...problems import ProblemException
from ..vault import (
    SignatureEventSink,
    VaultAuditSink,
    audit_transition,
    create_document,
    release,
    submit_review,
)
from ..vault.service import checkin_mgmt_review_minutes
from ..workflow import instantiate_approval
from . import repository as repo

logger = logging.getLogger(__name__)


# --- conflict helpers (copied from services/audits/service.py:93-107) ---
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


async def _mr_document_type_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    dt = (
        await session.execute(
            select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "MR")
        )
    ).scalar_one_or_none()
    if dt is None:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="MR document_type is not seeded",
        )
    return dt.id


async def create_review(
    session: AsyncSession,
    sink: VaultAuditSink,
    actor: AppUser,
    *,
    title: str,
    period_label: str | None = None,
    review_date: datetime.date | None = None,
    area_code: str | None = None,
    folder_path: str | None = None,
    classification: str = "Internal",
) -> ManagementReview:
    """Create a Management Review as a kind=DOCUMENT subtype (type MR), auto-mapped to clause 9.3.

    Mirrors ``create_objective`` (objectives/service.py:111-159): validate FK inputs BEFORE
    ``create_document`` (it commits the base doc internally — a bad FK after would orphan a base
    ``documented_information`` row with no satellite), then add the satellite + the 9.3 mapping in
    the same final txn."""
    # (No FK inputs to validate at create today — period_label/review_date are scalars; the OBJ
    # process_id/policy_id pre-checks have no MR analogue. The validate-before-create_document
    # discipline is kept structurally for when one is added.)
    dt_id = await _mr_document_type_id(session, actor.org_id)
    # create_document commits the base doc (the OBJ/form_template two-step precedent).
    doc = await create_document(
        session,
        sink,
        actor,
        title=title,
        document_type_id=dt_id,
        area_code=area_code,
        folder_path=folder_path,
        classification=classification,
    )
    mr = ManagementReview(
        id=doc.id,
        org_id=actor.org_id,
        period_label=period_label,
        review_date=review_date,
    )
    session.add(mr)
    # Auto-map to clause 9.3 so the submit gate's >=1-mapping requirement AND the ★ checklist node
    # both resolve on release (mirror objectives/service.py:137-156).
    clause_9_3 = (
        await session.execute(
            select(Clause).where(
                Clause.number == "9.3",
                Clause.framework_id == doc.framework_id,
            )
        )
    ).scalar_one_or_none()
    if clause_9_3 is not None:
        session.add(
            ClauseMapping(
                org_id=actor.org_id,
                framework_id=doc.framework_id,
                clause_id=clause_9_3.id,
                documented_information_id=doc.id,
                is_requirement_level=True,
                created_by=actor.id,
            )
        )
    await session.commit()
    await session.refresh(mr)
    return mr


# --- lifecycle ---------------------------------------------------------------------------------
def _minutes_input(ri: Any) -> dict[str, Any]:
    """The JSON-safe input row for the frozen minutes (the source_ref is already JSON-safe)."""
    return {
        "input_type": ri.input_type.value,
        "available": ri.available,
        "source_ref": ri.source_ref,
        "position": ri.position,
    }


def _minutes_output(ro: ReviewOutput) -> dict[str, Any]:
    """The JSON-safe decision content for the frozen minutes (owner/due freeze; spawned_* don't)."""
    return {
        "output_type": ro.output_type.value,
        "description": ro.description,
        "owner_user_id": str(ro.owner_user_id) if ro.owner_user_id is not None else None,
        "due_date": ro.due_date.isoformat() if ro.due_date is not None else None,
    }


async def submit_review_for_review(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    doc: DocumentedInformation,
    mr: ManagementReview,
    *,
    change_reason: str | None = None,
) -> DocumentedInformation:
    """Freeze the compiled minutes → T2 (Draft → InReview) → instantiate the approval workflow →
    audit, all in one transaction (mirrors the first-release path of
    ``submit_objective_for_review``).

    ``doc`` and ``mr`` MUST be loaded ``with_for_update`` + ``populate_existing`` (the authz
    resolver already identity-mapped both rows — the S-drift-1 trap; a stale satellite would freeze
    yesterday's minutes).

    S-mr-1 is first-release-only (Draft → InReview). Deferred to a future MR-revision slice (the
    S-obj-3 → S-obj-4 progression): the UnderRevision/T9 transition, the ``minutes_need_freeze``
    content-dedup (so an unchanged re-submit doesn't re-freeze), and guarding the generic vault
    checkout/checkin byte-path on an MR row."""
    if doc.current_state is not DocumentCurrentState.Draft:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Management Review is not in Draft",
            detail=f"current_state is {doc.current_state.value}",
        )
    inputs = await repo.list_inputs(session, mr.id)
    outputs = await repo.list_outputs(session, mr.id)
    minutes = build_minutes(
        period_label=mr.period_label,
        review_date=mr.review_date,
        attendees=mr.attendees,
        inputs=[_minutes_input(ri) for ri in inputs],
        outputs=[_minutes_output(ro) for ro in outputs],
        compiled_at=datetime.datetime.now(datetime.UTC),
    )
    # First-release freeze: unconditional on this Draft → InReview path (no prior frozen minutes to
    # dedup against). The content-aware ``minutes_need_freeze`` dedup lands with the revision slice.
    default_reason = "Management Review minutes submitted for review"
    await checkin_mgmt_review_minutes(
        session,
        vault_sink,
        actor,
        doc,
        minutes=minutes,
        change_reason=(change_reason or "").strip() or default_reason,
        change_significance="MAJOR",
    )
    result = await submit_review(session, actor, doc)
    await instantiate_approval(session, result.doc, actor)
    audit_transition(session, vault_sink, result, actor)
    await session.commit()
    return result.doc


async def release_review(
    actor: AppUser,
    review_id: uuid.UUID,
    vault_sink: VaultAuditSink,
    sig_sink: SignatureEventSink,
) -> DocumentedInformation:
    """T6 (Approved → Effective) — a thin wrapper over the generic release cutover (the same INV-1
    SERIALIZABLE cutover OBJ uses; the shared doc id drives it with zero subtype code). The release
    endpoint owns the ``document.release`` enforce + the SoD-2 scope + ``session.expire_all()`` +
    the Phase-5 ``spawn_mr_actions`` call."""
    return await release(actor, review_id, vault_sink, sig_sink)


# --- the close gate (ActionsTracked → Closed) --------------------------------------------------
async def close_review(
    session: AsyncSession,
    actor: AppUser,
    review: ManagementReview,
    doc: DocumentedInformation,
) -> ManagementReview:
    """Close a released Management Review — the honest ``_audit_close_gate`` pattern (R39 parity).

    The gate is **block-until-done**: every ``ACTION`` output's spawned ``MR_ACTION`` task must be
    ``DONE`` (an unlinked/unspawned ACTION yields ``None`` via the OUTERJOIN, so
    ``output_blocks_close`` BLOCKS fail-closed). A review with only ``DECISION``/``IMPROVEMENT``
    outputs (no actions) closes immediately — the gate is empty. Runs the gate BEFORE flipping
    ``close_state``; 409 with the blocker count (no separate refusal event — parity with the audit
    close gate's 409).

    Precondition (the ``advance_audit`` ``transition_allowed`` parity): a review may only be closed
    while its actions are being tracked — i.e. ``close_state is ActionsTracked``, which release's
    ``spawn_mr_actions`` sets unconditionally for EVERY released review (even a DECISION-only one).
    This both blocks closing a never-released review (``close_state is None`` → still Draft, never
    reached Effective — an incoherent terminal state) AND makes re-closing an already-``Closed``
    review a clean 409 (idempotent-safe: no re-stamp of ``closed_at``, no duplicate audit row)."""
    if review.close_state is not ManagementReviewCloseState.ActionsTracked:
        raise _conflict(
            "review_not_open_to_close",
            "A Management Review can only be closed while its actions are being tracked "
            "(it must be released, and not already closed).",
        )
    rows = await repo.outputs_for_close_gate(session, review.id)
    blocking = sum(
        1 for output_type, task_state in rows if output_blocks_close(output_type, task_state)
    )
    if blocking:
        raise _conflict(
            "review_close_blocked",
            f"Cannot close: {blocking} open action(s) whose MR_ACTION task is not done",
        )
    review.close_state = ManagementReviewCloseState.Closed
    review.closed_at = datetime.datetime.now(datetime.UTC)
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.MGMT_REVIEW_CLOSED,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            after={"close_state": review.close_state.value},
        )
    )
    await session.commit()
    await session.refresh(review)
    return review


# --- output CRUD (Draft-only) ------------------------------------------------------------------
# By system posture, working-copy edits (``update_output``/``delete_output``/``update_review_meta``)
# emit NO audit event — only the initial ``add_output`` records MGMT_REVIEW_OUTPUT_RECORDED. The
# trail of what was reviewed is the freeze's CHECKIN snapshot (the S-obj-4 unaudited-edits
# decision); this is intentional, not an omission.
async def _require_draft(
    session: AsyncSession, review_id: uuid.UUID
) -> tuple[ManagementReview, DocumentedInformation]:
    pair = await repo.get_review_doc(session, review_id)
    if pair is None:
        raise _not_found("Management Review")
    mr, doc = pair
    if doc.current_state is not DocumentCurrentState.Draft:
        raise _conflict(
            "conflict",
            "Management Review outputs are only editable in Draft",
        )
    return mr, doc


def _emit_output_recorded(
    session: AsyncSession,
    actor: AppUser,
    doc: DocumentedInformation,
    ro: ReviewOutput,
) -> None:
    """Audit MGMT_REVIEW_OUTPUT_RECORDED (object_type=document, scope_ref=identifier — R39;
    the objectives add_objective_plan emit shape)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.MGMT_REVIEW_OUTPUT_RECORDED,
            object_type=AuditObjectType.document,
            object_id=doc.id,
            scope_ref=doc.identifier,
            after={"output_id": str(ro.id), "output_type": ro.output_type.value},
        )
    )


async def add_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_type: ReviewOutputType,
    description: str,
    owner_user_id: uuid.UUID | None = None,
    due_date: datetime.date | None = None,
) -> ReviewOutput:
    """Author a 9.3.3 decision/action (Draft-only). An ``ACTION`` requires ``owner_user_id`` — it
    spawns an ``MR_ACTION`` task at release (Phase 5)."""
    mr, doc = await _require_draft(session, review_id)
    if output_type is ReviewOutputType.ACTION and owner_user_id is None:
        raise _validation_error(
            "owner_user_id", "required", "An ACTION output requires an owner_user_id"
        )
    ro = ReviewOutput(
        org_id=actor.org_id,
        management_review_id=mr.id,
        output_type=output_type,
        description=description,
        owner_user_id=owner_user_id,
        due_date=due_date,
    )
    session.add(ro)
    await session.flush()
    _emit_output_recorded(session, actor, doc, ro)
    await session.commit()
    await session.refresh(ro)
    return ro


async def update_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_id: uuid.UUID,
    fields: set[str],
    output_type: ReviewOutputType | None = None,
    description: str | None = None,
    owner_user_id: uuid.UUID | None = None,
    due_date: datetime.date | None = None,
) -> ReviewOutput:
    """Edit an output (Draft-only). ``fields`` is the request's ``model_fields_set`` (omitted ≠
    null — the objectives PATCH precedent). An ``ACTION`` (resulting type) must keep an owner."""
    _mr, _doc = await _require_draft(session, review_id)
    ro = await session.get(ReviewOutput, output_id)
    if ro is None or ro.management_review_id != review_id:
        raise _not_found("Output")
    if "output_type" in fields and output_type is not None:
        ro.output_type = output_type
    if "description" in fields and description is not None:
        ro.description = description
    if "owner_user_id" in fields:
        ro.owner_user_id = owner_user_id
    if "due_date" in fields:
        ro.due_date = due_date
    if ro.output_type is ReviewOutputType.ACTION and ro.owner_user_id is None:
        raise _validation_error(
            "owner_user_id", "required", "An ACTION output requires an owner_user_id"
        )
    await session.commit()
    await session.refresh(ro)
    return ro


async def delete_output(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    output_id: uuid.UUID,
) -> None:
    """Delete an output (Draft-only)."""
    _mr, _doc = await _require_draft(session, review_id)
    ro = await session.get(ReviewOutput, output_id)
    if ro is None or ro.management_review_id != review_id:
        raise _not_found("Output")
    await session.delete(ro)
    await session.commit()


async def update_review_meta(
    session: AsyncSession,
    actor: AppUser,
    *,
    review_id: uuid.UUID,
    fields: set[str],
    period_label: str | None = None,
    review_date: datetime.date | None = None,
    attendees: list[dict[str, Any]] | None = None,
) -> ManagementReview:
    """Edit the review meta the freeze reads into the minutes (period_label/review_date/attendees),
    Draft-only. ``fields`` is the request's ``model_fields_set`` (omitted ≠ null — the objectives
    PATCH precedent)."""
    mr, _doc = await _require_draft(session, review_id)
    if "period_label" in fields:
        mr.period_label = period_label
    if "review_date" in fields:
        mr.review_date = review_date
    if "attendees" in fields:
        mr.attendees = attendees
    await session.commit()
    await session.refresh(mr)
    return mr
