"""Context register service (clause 4.1, S-context-1) — the txn owner for the context_issue rows.

The register is ONE ``kind=DOCUMENT`` ``CTX`` head (``is_singleton``) per org holding many
``context_issue`` satellite rows. ``resolve_or_create_head`` is an advisory-lock-serialized
get-or-create (the only multi-head window is the concurrent first-create; ``CTX`` revisions in
place,
so the existing Effective-only ``uq_doc_info_singleton_effective`` covers the rest). Rows are
editable only while the head is Draft/UnderRevision (the ``form_template``/objectives edit gate),
serialized against a concurrent publish freeze by a head ``FOR UPDATE`` lock (the S-risk-1b P1
version-integrity discipline). Clause 4.1 context is ORG-LEVEL — there is no ``process_id`` and the
register rides ``register.*`` at SYSTEM (the authz lives entirely in the route's ``require`` deps;
the
service never re-enforces, since there is no process-reassign TOCTOU to close — unlike risk).
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._context_enums import (
    ContextCategory,
    ContextClassification,
    ContextIssueStatus,
)
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.context_issue import ContextIssue
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...problems import ProblemException
from ..vault import VaultAuditSink, create_document

# A per-org transaction advisory lock that serializes the concurrent *first* head-create. The
# two-arg (ns, oid) form is distinct from the RISK head lock (7710100) and the single-arg global
# LOCK_* keys; it auto-releases at the txn boundary (incl. create_document's internal commit). Only
# taken when no head is found.
_CONTEXT_HEAD_LOCK_NS = 7710101
_EDITABLE = (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision)


def _org_head_lock_oid(org_id: uuid.UUID) -> int:
    """A stable signed int32 from the org id (PostgreSQL advisory keys are int4 in the two-arg
    form)."""
    return int.from_bytes(hashlib.blake2b(org_id.bytes, digest_size=4).digest(), "big", signed=True)


async def _ctx_document_type_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    dt = (
        await session.execute(
            select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "CTX")
        )
    ).scalar_one_or_none()
    if dt is None:
        raise ProblemException(
            status=422, code="validation_error", title="CTX document_type is not seeded"
        )
    return dt.id


async def find_head(
    session: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False
) -> DocumentedInformation | None:
    """The single non-Obsolete CTX head for the org (Effective OR its Draft/UnderRevision successor)
    — the one source of truth for "the org's context register head" (used by the get-or-create, the
    publish/revision lifecycle, and the GET status read). ``for_update`` locks ONLY the head row +
    forces populate_existing (the S-drift-1 stale-identity-map trap: a locking SELECT returns the
    cached attributes unless populate_existing overrides them)."""
    stmt = (
        select(DocumentedInformation)
        .join(DocumentType, DocumentedInformation.document_type_id == DocumentType.id)
        .where(
            DocumentedInformation.org_id == org_id,
            DocumentType.code == "CTX",
            DocumentedInformation.current_state != DocumentCurrentState.Obsolete,
        )
        .order_by(DocumentedInformation.created_at)
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update(of=DocumentedInformation).execution_options(
            populate_existing=True
        )
    return (await session.execute(stmt)).scalar_one_or_none()


async def resolve_or_create_head(
    session: AsyncSession, vault_sink: VaultAuditSink, actor: AppUser
) -> DocumentedInformation:
    """Get-or-create the org's single CTX register head. The head carries ZERO ProcessLinks
    (``create_document(processes=())``) — clause 4.1 is org-level (no row carries a process), so a
    bound Process-Owner's PROCESS-scoped ``document.*`` grant cannot match the org head. Auto-maps
    the head to clause 4.1. The advisory lock guards the concurrent first-create (double-checked
    under the lock)."""
    head = await find_head(session, actor.org_id)
    if head is not None:
        return head
    # No head — serialize the create so two concurrent first-issue POSTs cannot mint two heads.
    await session.execute(
        # Cast to int4 so PG resolves the two-arg pg_advisory_xact_lock(int, int) overload (psycopg
        # would otherwise bind the Python ints as bigint, for which no two-arg form exists).
        text("SELECT pg_advisory_xact_lock(CAST(:ns AS integer), CAST(:oid AS integer))"),
        {"ns": _CONTEXT_HEAD_LOCK_NS, "oid": _org_head_lock_oid(actor.org_id)},
    )
    head = await find_head(session, actor.org_id)  # re-check under the lock
    if head is not None:
        return head
    dt_id = await _ctx_document_type_id(session, actor.org_id)
    head = await create_document(  # commits the base doc (releases the xact lock)
        session,
        vault_sink,
        actor,
        title="Context Register",
        document_type_id=dt_id,
    )
    clause_4_1 = (
        await session.execute(
            select(Clause).where(Clause.number == "4.1", Clause.framework_id == head.framework_id)
        )
    ).scalar_one_or_none()
    if clause_4_1 is not None:
        session.add(
            ClauseMapping(
                org_id=actor.org_id,
                framework_id=head.framework_id,
                clause_id=clause_4_1.id,
                documented_information_id=head.id,
                is_requirement_level=True,
                created_by=actor.id,
            )
        )
        await session.commit()
        await session.refresh(head)
    return head


async def add_context_issue(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    *,
    classification: ContextClassification,
    description: str,
    category: ContextCategory | None = None,
    last_reviewed_at: datetime.datetime | None = None,
) -> ContextIssue:
    """Author a context issue on the register's working satellite. A new issue is always ``active``
    (closed via PATCH, never created closed). The head must be Draft/UnderRevision (the edit
    gate)."""
    await resolve_or_create_head(session, vault_sink, actor)
    # Re-load the head FOR UPDATE so the editable-gate + the insert serialize against a concurrent
    # publish (which holds the head FOR UPDATE while it freezes the rows): without this lock a row
    # insert could commit AFTER the freeze + the head moves to InReview, leaving live register
    # content out of the version the approver signs and un-editable once Effective (the S-risk-1b
    # Codex P1). find_head excludes Obsolete → None only on a concurrent retire (CTX is reserved).
    head = await find_head(session, actor.org_id, for_update=True)
    if head is None or head.current_state not in _EDITABLE:
        state = head.current_state.value if head is not None else "obsolete"
        raise ProblemException(
            status=409,
            code="conflict",
            title="Context register is not editable",
            detail=f"current_state is {state}; start a revision to edit",
        )
    row = ContextIssue(
        register_doc_id=head.id,
        org_id=actor.org_id,
        classification=classification,
        category=category,
        status=ContextIssueStatus.active,
        description=description,
        last_reviewed_at=last_reviewed_at,
        row_version=1,
        created_by=actor.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


_NON_NULL_FIELDS = frozenset({"classification", "status", "description"})
_UPDATABLE_FIELDS = frozenset(
    {"classification", "category", "status", "description", "last_reviewed_at"}
)


async def get_context_issue(
    session: AsyncSession, issue_id: uuid.UUID, *, for_update: bool = False
) -> ContextIssue | None:
    stmt = select(ContextIssue).where(ContextIssue.id == issue_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_context_issues(session: AsyncSession, org_id: uuid.UUID) -> list[ContextIssue]:
    return list(
        (
            await session.execute(
                select(ContextIssue)
                .where(ContextIssue.org_id == org_id)
                .order_by(ContextIssue.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def update_context_issue_row(
    session: AsyncSession,
    actor: AppUser,
    issue_id: uuid.UUID,
    *,
    updates: dict[str, Any],
) -> ContextIssue:
    """Apply a partial PATCH to a context issue row, emitting a ``CONTEXT_ISSUE_UPDATED`` audit
    event. Authz (``register.manage`` @ SYSTEM) is fully handled by the route dependency — clause
    4.1 is org-level, so there is NO process-reassign TOCTOU and no under-lock re-enforce (unlike
    risk). The head is still locked ``FOR UPDATE`` (row→head order, publish locks head-only — no
    cycle) so a row edit cannot land after the version is frozen (the S-risk-1b P1 discipline). Rows
    are editable only while the head is Draft/UnderRevision."""
    row = await get_context_issue(session, issue_id, for_update=True)
    if row is None or row.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Context issue not found")
    head = (
        await session.execute(
            select(DocumentedInformation)
            .where(DocumentedInformation.id == row.register_doc_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if head.current_state not in _EDITABLE:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Context register is not editable",
            detail=f"current_state is {head.current_state.value}; start a revision to edit",
        )
    unknown = set(updates) - _UPDATABLE_FIELDS
    if unknown:
        raise ProblemException(
            status=422, code="validation_error", title=f"Unknown field(s): {sorted(unknown)}"
        )
    for field in _NON_NULL_FIELDS & set(updates):
        if updates[field] is None:
            raise ProblemException(
                status=422, code="validation_error", title=f"{field} may not be null"
            )

    before = {
        "classification": row.classification.value,
        "category": row.category.value if row.category else None,
        "status": row.status.value,
        "description": row.description,
        "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
    }
    for field in _UPDATABLE_FIELDS & set(updates):
        setattr(row, field, updates[field])
    row.row_version += 1
    row.updated_by = actor.id
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType.CONTEXT_ISSUE_UPDATED,
            object_type=AuditObjectType.document,
            object_id=row.register_doc_id,
            scope_ref=head.identifier,
            before=before,
            after={
                "issue_id": str(row.id),
                "classification": row.classification.value,
                "category": row.category.value if row.category else None,
                "status": row.status.value,
                "description": row.description,
                "last_reviewed_at": (
                    row.last_reviewed_at.isoformat() if row.last_reviewed_at else None
                ),
            },
        )
    )
    await session.commit()
    await session.refresh(row)
    return row
