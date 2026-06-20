"""Risk & Opportunity register lifecycle (S-risk-1b, clause 6.1) — the controlled-document
publish/freeze/release layer the S-risk-1 deferral named.

``start_register_revision`` opens an FSM revision (T7) on the Effective head so its rows become
editable again; ``publish_register`` folds a content-aware freeze (the rows + the per-method scoring
criteria) + the T2/T9 submit + the approval-workflow instantiation into ONE transaction, then the
RSK head rides the generic DOCUMENT decide leg (approve) + the shared ``release`` cutover, unchanged
(the objectives ``submit_objective_for_review`` precedent). The publish path calls the freeze/submit
service functions DIRECTLY — it must NOT go through the reserved generic byte endpoints (which 422
the RSK head via ``reject_rsk_register_mutation``)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.documented_information import DocumentedInformation
from ...db.models.risk_opportunity import RiskOpportunity
from ...domain.risk.register_content import (
    build_register,
    criteria_for_methods,
    register_needs_freeze,
)
from ...problems import ProblemException
from ..vault import VaultAuditSink, audit_transition, locks, start_revision, submit_review
from ..vault import repository as vault_repo
from ..vault.service import checkin_risk_register
from ..workflow import instantiate_approval
from .service import find_head

logger = logging.getLogger(__name__)

_EDITABLE = (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision)


def _frozen_row(row: RiskOpportunity) -> dict[str, Any]:
    """A risk row's CONTROLLED content as-of freeze (the version's WORM body). Excludes audit
    bookkeeping (created_at/_by, updated_at/_by) — non-content + non-reproducible; the band BASIS is
    the frozen criteria, not a per-row column.

    ⚠ ``linked_capa_id`` is EXCLUDED (S-risk-3): it is OPERATIONAL metadata, not version content —
    the one-click risk→CAPA spawn sets it on the live satellite at any head state (its own
    row-locked latch path, not a register revision), so it is deliberately not signed into the
    frozen version (the objectives operational-rollup posture). No controlled read-of-record reads
    it (the MR 9.3.2(e) summary reads treatment/effectiveness/rating/type/band — never the CAPA
    link), so its absence from the snapshot changes no governing read."""
    return {
        "id": str(row.id),
        "type": row.type.value,
        "description": row.description,
        "process_id": str(row.process_id) if row.process_id else None,
        "clause_id": str(row.clause_id) if row.clause_id else None,
        "likelihood": row.likelihood,
        "severity": row.severity,
        "risk_rating": row.risk_rating,
        "scoring_method": row.scoring_method.value,
        "treatment": row.treatment,
        "effectiveness": row.effectiveness,
        "row_version": row.row_version,
    }


async def _working_register(session: AsyncSession, head_id: uuid.UUID) -> dict[str, Any]:
    """Build the canonical working register (rows + per-method frozen criteria) from the head's live
    satellite rows. The rows belong to the single head (revised in place), but filter by
    ``register_doc_id`` for precision. ``build_register`` sorts by id so the bytes are stable."""
    rows = list(
        (
            await session.execute(
                select(RiskOpportunity)
                .where(RiskOpportunity.register_doc_id == head_id)
                .order_by(RiskOpportunity.created_at)
            )
        )
        .scalars()
        .all()
    )
    methods = {r.scoring_method for r in rows}
    return build_register(
        rows=[_frozen_row(r) for r in rows], criteria=criteria_for_methods(methods)
    )


async def start_register_revision(
    session: AsyncSession, vault_sink: VaultAuditSink, actor: AppUser
) -> DocumentedInformation:
    """T7 (Effective → UnderRevision) for the register head — a thin wrapper over the SAME vault
    ``start_revision`` (FSM guard requires Effective → 409 otherwise, Redis edit lock, WorkingDraft
    seeded from Effective, REVISION_STARTED audit, commits). Opens the edit window so a steward
    (and, within it, bound process owners) can change rows; the Effective version keeps governing
    until publish→release supersedes it."""
    head = await find_head(session, actor.org_id, for_update=True)
    if head is None:
        raise ProblemException(
            status=409,
            code="conflict",
            title="No risk register to revise",
            detail="create and publish a register first",
        )
    return await start_revision(session, vault_sink, actor, head)


async def publish_register(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    *,
    change_reason: str | None = None,
) -> DocumentedInformation:
    """Freeze the working register when it changed (``register_needs_freeze``) → T2/T9 submit →
    instantiate the approval workflow → audit, all in one transaction (the
    ``submit_objective_for_review`` shape). The head MUST be Draft (the first register) or
    UnderRevision (a revision opened by ``start_register_revision``). Leaving the editable window,
    the start-revision WorkingDraft is deleted in the txn and its edit lock released post-commit
    (the generic-checkin O-4 pattern). Approval then routes through ``POST /tasks/{id}/decision``
    (DOCUMENT leg) and release through the shared ``release`` cutover."""
    head = await find_head(session, actor.org_id, for_update=True)
    if head is None:
        raise ProblemException(
            status=409,
            code="conflict",
            title="No risk register to publish",
            detail="add a risk before publishing the register",
        )
    if head.current_state not in _EDITABLE:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Risk register is not editable",
            detail=(
                f"current_state is {head.current_state.value}; start a revision to publish a change"
            ),
        )
    working = await _working_register(session, head.id)
    # Reject an empty register (Codex): besides being meaningless to control, this closes a race —
    # the first POST /risks commits the (0-row) head in resolve_or_create_head BEFORE it takes the
    # head FOR UPDATE to insert the row, so a publish that locks the head in that window would
    # freeze an EMPTY version and 409 the in-flight first risk (losing it). Rejecting under the lock
    # keeps the head Draft, so the blocked row insert resumes and succeeds.
    if not working["rows"]:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Risk register has no rows to publish",
            detail="add at least one risk or opportunity before publishing the register",
        )
    latest = await vault_repo.latest_version(session, head.id)
    if register_needs_freeze(
        latest_version_state=latest.version_state if latest is not None else None,
        latest_register=(
            (latest.metadata_snapshot or {}).get("risk_register") if latest is not None else None
        ),
        working=working,
    ):
        default_reason = (
            "Risk register revised"
            if head.current_state is DocumentCurrentState.UnderRevision
            else "Risk register submitted for review"
        )
        await checkin_risk_register(
            session,
            vault_sink,
            actor,
            head,
            register=working,
            change_reason=(change_reason or "").strip() or default_reason,
            change_significance="MAJOR",
        )
    # O-4: leaving the editable window — drop the start-revision WorkingDraft (in-txn) and release
    # its edit lock post-commit. No WD exists on a plain first-register Draft publish — both no-op.
    wd = await vault_repo.get_working_draft(session, head.id)
    token = (wd.lock_token or "") if wd is not None else ""
    if wd is not None:
        await session.delete(wd)
    result = await submit_review(session, actor, head)
    await instantiate_approval(session, result.doc, actor)
    audit_transition(session, vault_sink, result, actor)
    await session.commit()
    if token and not await locks.release(head.id, token):
        logger.warning("risk publish: edit-lock token no longer matched (lock had lapsed)")
    return result.doc
