"""Interested Parties register service (clause 4.2, S-interested-parties-1) — the txn owner for the
interested_party rows.

The register is ONE ``kind=DOCUMENT`` ``IPR`` head (``is_singleton``) per org holding many
``interested_party`` satellite rows. ``resolve_or_create_head`` is an advisory-lock-serialized
get-or-create (the only multi-head window is the concurrent first-create; ``IPR`` revisions in
place,
so the existing Effective-only ``uq_doc_info_singleton_effective`` covers the rest). Rows are
editable only while the head is Draft/UnderRevision (the context/objectives edit gate),
serialized against a concurrent publish freeze by a head ``FOR UPDATE`` lock (the S-context-1 P1
version-integrity discipline). Clause 4.2 interested parties is ORG-LEVEL — there is no
``process_id`` and the register rides ``register.*`` at SYSTEM (the authz lives entirely in the
route's ``require`` deps; the service never re-enforces, since there is no process-reassign TOCTOU
to close — unlike risk). The Context register service clone.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._interested_party_enums import (
    InterestedPartyInfluence,
    InterestedPartyStatus,
    InterestedPartyType,
)
from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.document_type import DocumentType
from ...db.models.documented_information import DocumentedInformation
from ...db.models.interested_party import InterestedParty
from ...problems import ProblemException
from ..vault import VaultAuditSink, create_document

# A per-org transaction advisory lock that serializes the concurrent *first* head-create. The
# two-arg (ns, oid) form is distinct from the RISK (7710100) / CTX (7710101) head locks and the
# single-arg global LOCK_* keys; it auto-releases at the txn boundary (incl. create_document's
# internal commit). Only taken when no head is found.
_IP_HEAD_LOCK_NS = 7710102
_EDITABLE = (DocumentCurrentState.Draft, DocumentCurrentState.UnderRevision)


def _org_head_lock_oid(org_id: uuid.UUID) -> int:
    """A stable signed int32 from the org id (PostgreSQL advisory keys are int4 in the two-arg
    form)."""
    return int.from_bytes(hashlib.blake2b(org_id.bytes, digest_size=4).digest(), "big", signed=True)


async def _ipr_document_type_id(session: AsyncSession, org_id: uuid.UUID) -> uuid.UUID:
    dt = (
        await session.execute(
            select(DocumentType).where(DocumentType.org_id == org_id, DocumentType.code == "IPR")
        )
    ).scalar_one_or_none()
    if dt is None:
        raise ProblemException(
            status=422, code="validation_error", title="IPR document_type is not seeded"
        )
    return dt.id


async def find_head(
    session: AsyncSession, org_id: uuid.UUID, *, for_update: bool = False
) -> DocumentedInformation | None:
    """The single non-Obsolete IPR head for the org (Effective OR its Draft/UnderRevision successor)
    — the one source of truth for "the org's interested-parties register head" (used by the
    get-or-create, the publish/revision lifecycle, and the GET status read). ``for_update`` locks
    ONLY the head row + forces populate_existing (the S-drift-1 stale-identity-map trap: a locking
    SELECT returns the cached attributes unless populate_existing overrides them)."""
    stmt = (
        select(DocumentedInformation)
        .join(DocumentType, DocumentedInformation.document_type_id == DocumentType.id)
        .where(
            DocumentedInformation.org_id == org_id,
            DocumentType.code == "IPR",
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
    """Get-or-create the org's single IPR register head. The head carries ZERO ProcessLinks
    (``create_document(processes=())``) — clause 4.2 is org-level (no row carries a process), so a
    bound Process-Owner's PROCESS-scoped ``document.*`` grant cannot match the org head. Auto-maps
    the head to clause 4.2. The advisory lock guards the concurrent first-create (double-checked
    under the lock)."""
    head = await find_head(session, actor.org_id)
    if head is not None:
        return head
    # No head — serialize the create so two concurrent first-party POSTs cannot mint two heads.
    await session.execute(
        # Cast to int4 so PG resolves the two-arg pg_advisory_xact_lock(int, int) overload (psycopg
        # would otherwise bind the Python ints as bigint, for which no two-arg form exists).
        text("SELECT pg_advisory_xact_lock(CAST(:ns AS integer), CAST(:oid AS integer))"),
        {"ns": _IP_HEAD_LOCK_NS, "oid": _org_head_lock_oid(actor.org_id)},
    )
    head = await find_head(session, actor.org_id)  # re-check under the lock
    if head is not None:
        return head
    dt_id = await _ipr_document_type_id(session, actor.org_id)
    head = await create_document(  # commits the base doc (releases the xact lock)
        session,
        vault_sink,
        actor,
        title="Interested Parties Register",
        document_type_id=dt_id,
    )
    clause_4_2 = (
        await session.execute(
            select(Clause).where(Clause.number == "4.2", Clause.framework_id == head.framework_id)
        )
    ).scalar_one_or_none()
    if clause_4_2 is not None:
        session.add(
            ClauseMapping(
                org_id=actor.org_id,
                framework_id=head.framework_id,
                clause_id=clause_4_2.id,
                documented_information_id=head.id,
                is_requirement_level=True,
                created_by=actor.id,
            )
        )
        await session.commit()
        await session.refresh(head)
    return head


async def add_interested_party(
    session: AsyncSession,
    vault_sink: VaultAuditSink,
    actor: AppUser,
    *,
    party_type: InterestedPartyType,
    party_name: str,
    needs_expectations: str,
    influence: InterestedPartyInfluence | None = None,
    last_reviewed_at: datetime.datetime | None = None,
) -> InterestedParty:
    """Author an interested party on the register's working satellite. A new party is always
    ``active`` (closed via PATCH, never created closed). The head must be Draft/UnderRevision (the
    edit gate)."""
    await resolve_or_create_head(session, vault_sink, actor)
    # Re-load the head FOR UPDATE so the editable-gate + the insert serialize against a concurrent
    # publish (which holds the head FOR UPDATE while it freezes the rows): without this lock a row
    # insert could commit AFTER the freeze + the head moves to InReview, leaving live register
    # content out of the version the approver signs and un-editable once Effective (the S-context-1
    # Codex P1). find_head excludes Obsolete → None only on a concurrent retire (IPR is reserved).
    head = await find_head(session, actor.org_id, for_update=True)
    if head is None or head.current_state not in _EDITABLE:
        state = head.current_state.value if head is not None else "obsolete"
        raise ProblemException(
            status=409,
            code="conflict",
            title="Interested Parties register is not editable",
            detail=f"current_state is {state}; start a revision to edit",
        )
    row = InterestedParty(
        register_doc_id=head.id,
        org_id=actor.org_id,
        party_type=party_type,
        party_name=party_name,
        needs_expectations=needs_expectations,
        influence=influence,
        status=InterestedPartyStatus.active,
        last_reviewed_at=last_reviewed_at,
        row_version=1,
        created_by=actor.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


_NON_NULL_FIELDS = frozenset({"party_type", "status", "party_name", "needs_expectations"})
_UPDATABLE_FIELDS = frozenset(
    {"party_type", "party_name", "needs_expectations", "influence", "status", "last_reviewed_at"}
)


async def get_interested_party(
    session: AsyncSession, party_id: uuid.UUID, *, for_update: bool = False
) -> InterestedParty | None:
    stmt = select(InterestedParty).where(InterestedParty.id == party_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_interested_parties(
    session: AsyncSession, org_id: uuid.UUID
) -> list[InterestedParty]:
    return list(
        (
            await session.execute(
                select(InterestedParty)
                .where(InterestedParty.org_id == org_id)
                .order_by(InterestedParty.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def update_interested_party_row(
    session: AsyncSession,
    actor: AppUser,
    party_id: uuid.UUID,
    *,
    updates: dict[str, Any],
) -> InterestedParty:
    """Apply a partial PATCH to an interested party row, emitting an ``INTERESTED_PARTY_UPDATED``
    audit event. Authz (``register.manage`` @ SYSTEM) is fully handled by the route dependency —
    clause 4.2 is org-level, so there is NO process-reassign TOCTOU and no under-lock re-enforce
    (unlike risk). The head is still locked ``FOR UPDATE`` (row→head order, publish locks
    head-only — no cycle) so a row edit cannot land after the version is frozen (the S-context-1 P1
    discipline).
    Rows are editable only while the head is Draft/UnderRevision."""
    row = await get_interested_party(session, party_id, for_update=True)
    if row is None or row.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Interested party not found")
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
            title="Interested Parties register is not editable",
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
        "party_type": row.party_type.value,
        "party_name": row.party_name,
        "needs_expectations": row.needs_expectations,
        "influence": row.influence.value if row.influence else None,
        "status": row.status.value,
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
            event_type=EventType.INTERESTED_PARTY_UPDATED,
            object_type=AuditObjectType.document,
            object_id=row.register_doc_id,
            scope_ref=head.identifier,
            before=before,
            after={
                "party_id": str(row.id),
                "party_type": row.party_type.value,
                "party_name": row.party_name,
                "needs_expectations": row.needs_expectations,
                "influence": row.influence.value if row.influence else None,
                "status": row.status.value,
                "last_reviewed_at": (
                    row.last_reviewed_at.isoformat() if row.last_reviewed_at else None
                ),
            },
        )
    )
    await session.commit()
    await session.refresh(row)
    return row
