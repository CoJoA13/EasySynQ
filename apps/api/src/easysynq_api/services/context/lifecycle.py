"""Context register lifecycle (S-context-1, clause 4.1) — the controlled-document
publish/freeze/release layer over the single per-org ``CTX`` head whose ``context_issue`` rows are
the version content.

``start_context_revision`` opens an FSM revision (T7) on the Effective head so its rows become
editable again; ``publish_register`` folds a content-aware freeze (the rows; clause 4.1 has no
scoring ``criteria``) + the T2/T9 submit + the approval-workflow instantiation into ONE transaction,
then the CTX head rides the generic DOCUMENT decide leg (approve) + the shared ``release`` cutover,
unchanged (the risk ``publish_register`` precedent). The publish path calls the freeze/submit
service
functions DIRECTLY — it must NOT go through the reserved generic byte endpoints (which 422 the CTX
head via ``reject_managed_register_mutation``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.context_issue import ContextIssue
from ...db.models.documented_information import DocumentedInformation
from ...domain.context.register_content import build_register, register_needs_freeze
from ...problems import ProblemException
from ..vault import VaultAuditSink, audit_transition, locks, start_revision, submit_review
from ..vault import repository as vault_repo
from ..vault.service import checkin_context_register
from ..workflow import instantiate_approval
from .service import find_head

logger = logging.getLogger(__name__)

_EDITABLE = (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision)


def _frozen_row(row: ContextIssue) -> dict[str, Any]:
    """A context issue's CONTROLLED content as-of freeze (the version's WORM body). Excludes audit
    bookkeeping (created_at/_by, updated_at/_by) — non-content + non-reproducible. ``org_id`` and
    ``register_doc_id`` are head-implied, so they are not part of the frozen row body."""
    return {
        "id": str(row.id),
        "classification": row.classification.value,
        "category": row.category.value if row.category else None,
        "status": row.status.value,
        "description": row.description,
        "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
        "row_version": row.row_version,
    }


async def _working_register(session: AsyncSession, head_id: uuid.UUID) -> dict[str, Any]:
    """Build the canonical working register (rows only — clause 4.1 has no scoring criteria) from
    the head's live satellite rows. ``build_register`` sorts by id so the bytes are stable."""
    rows = list(
        (
            await session.execute(
                select(ContextIssue)
                .where(ContextIssue.register_doc_id == head_id)
                .order_by(ContextIssue.created_at)
            )
        )
        .scalars()
        .all()
    )
    return build_register(rows=[_frozen_row(r) for r in rows])


async def start_context_revision(
    session: AsyncSession, vault_sink: VaultAuditSink, actor: AppUser
) -> DocumentedInformation:
    """T7 (Effective → UnderRevision) for the register head — a thin wrapper over the SAME vault
    ``start_revision`` (FSM guard requires Effective → 409 otherwise, Redis edit lock, WorkingDraft
    seeded from Effective, REVISION_STARTED audit, commits). Opens the edit window so the steward
    can
    change rows; the Effective version keeps governing until publish→release supersedes it."""
    head = await find_head(session, actor.org_id, for_update=True)
    if head is None:
        raise ProblemException(
            status=409,
            code="conflict",
            title="No context register to revise",
            detail="add a context issue and publish a register first",
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
    instantiate the approval workflow → audit, all in one transaction (the risk ``publish_register``
    shape). The head MUST be Draft (the first register) or UnderRevision (a revision opened by
    ``start_context_revision``). Leaving the editable window, the start-revision WorkingDraft is
    deleted in the txn and its edit lock released post-commit. Approval then routes through
    ``POST /tasks/{id}/decision`` (DOCUMENT leg) and release through the shared ``release``
    cutover."""
    head = await find_head(session, actor.org_id, for_update=True)
    if head is None:
        raise ProblemException(
            status=409,
            code="conflict",
            title="No context register to publish",
            detail="add a context issue before publishing the register",
        )
    if head.current_state not in _EDITABLE:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Context register is not editable",
            detail=(
                f"current_state is {head.current_state.value}; start a revision to publish a change"
            ),
        )
    working = await _working_register(session, head.id)
    # Reject an empty register (the risk precedent): besides being meaningless to control, this
    # closes a race — the first POST /context commits the (0-row) head in resolve_or_create_head
    # BEFORE it takes the head FOR UPDATE to insert the row, so a publish that locks the head in
    # that
    # window would freeze an EMPTY version and 409 the in-flight first issue (losing it). Rejecting
    # under the lock keeps the head Draft, so the blocked row insert resumes and succeeds.
    if not working["rows"]:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Context register has no rows to publish",
            detail="add at least one context issue before publishing the register",
        )
    latest = await vault_repo.latest_version(session, head.id)
    if register_needs_freeze(
        latest_version_state=latest.version_state if latest is not None else None,
        latest_register=(
            (latest.metadata_snapshot or {}).get("context_register") if latest is not None else None
        ),
        working=working,
    ):
        default_reason = (
            "Context register revised"
            if head.current_state is DocumentCurrentState.UnderRevision
            else "Context register submitted for review"
        )
        await checkin_context_register(
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
        logger.warning("context publish: edit-lock token no longer matched (lock had lapsed)")
    return result.doc
