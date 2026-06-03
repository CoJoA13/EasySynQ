"""Records DB access: the retention-policy tier queries + the evidence satellites (slice S-rec-1).

The retention resolver (``domain/records/retention.py``) is pure; the per-tier *matching* is here —
each tier query returns at most one candidate policy (smallest ``id`` tiebreak) for the org. The
default policy (``"System Default Retention"``) is the always-present fallback: ``0023`` seeds one
per org, but a fresh install whose org row postdates the migration has none, so
``ensure_default_policy`` creates it idempotently on first capture (``UNIQUE(org_id, name)``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import asc, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models._retention_enums import DispositionAction, RetentionBasis
from ...db.models.blob import Blob
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_blob import EvidenceBlob
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.record import Record
from ...db.models.retention_policy import RetentionPolicy

SYSTEM_DEFAULT_POLICY_NAME = "System Default Retention"


# --- record lookups ----------------------------------------------------------------------


async def get_record(session: AsyncSession, record_id: uuid.UUID) -> Record | None:
    return await session.get(Record, record_id)


async def get_base(session: AsyncSession, record_id: uuid.UUID) -> DocumentedInformation | None:
    """The shared-PK base row (``documented_information``, kind=RECORD) for a record."""
    return await session.get(DocumentedInformation, record_id)


async def list_records(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    filters: list,  # type: ignore[type-arg]  # list of SQLAlchemy boolean ColumnElements
    limit: int,
) -> list[tuple[Record, DocumentedInformation]]:
    """The records list (record ⨝ base), newest capture first; pre-authz ``limit`` cap."""
    rows = (
        await session.execute(
            select(Record, DocumentedInformation)
            .join(DocumentedInformation, Record.id == DocumentedInformation.id)
            .where(Record.org_id == org_id, *filters)
            .order_by(desc(Record.captured_at))
            .limit(limit)
        )
    ).all()
    return [(r, d) for r, d in rows]


# --- retention policy --------------------------------------------------------------------


async def get_policy(
    session: AsyncSession, policy_id: uuid.UUID, org_id: uuid.UUID
) -> RetentionPolicy | None:
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None or policy.org_id != org_id:
        return None
    return policy


async def system_default_policy(session: AsyncSession, org_id: uuid.UUID) -> RetentionPolicy | None:
    return (
        await session.execute(
            select(RetentionPolicy).where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.name == SYSTEM_DEFAULT_POLICY_NAME,
            )
        )
    ).scalar_one_or_none()


async def ensure_default_policy(session: AsyncSession, org_id: uuid.UUID) -> RetentionPolicy:
    """Get-or-create the org's system-default policy idempotently (no rollback — safe to call inside
    the capture transaction). ``ON CONFLICT DO NOTHING`` serializes a concurrent create on the
    ``UNIQUE(org_id, name)`` index; the follow-up select always returns the row."""
    await session.execute(
        pg_insert(RetentionPolicy)
        .values(
            org_id=org_id,
            name=SYSTEM_DEFAULT_POLICY_NAME,
            basis=RetentionBasis.CAPTURED_AT,
            duration="P10Y",
            disposition_action=DispositionAction.RETAIN_PERMANENT,
            review_required=False,
        )
        .on_conflict_do_nothing(index_elements=["org_id", "name"])
    )
    policy = await system_default_policy(session, org_id)
    assert policy is not None  # noqa: S101 — just inserted-or-existing under the unique index
    return policy


async def record_type_default_policy(
    session: AsyncSession, org_id: uuid.UUID, record_type: str
) -> RetentionPolicy | None:
    """The record-type default tier: a policy whose ``applies_to.record_type`` matches."""
    return (
        await session.execute(
            select(RetentionPolicy)
            .where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.applies_to["record_type"].astext == record_type,
            )
            .order_by(asc(RetentionPolicy.id))
            .limit(1)
        )
    ).scalar_one_or_none()


async def clause_default_policy(
    session: AsyncSession, org_id: uuid.UUID, clause_ids: frozenset[str]
) -> RetentionPolicy | None:
    if not clause_ids:
        return None
    return (
        await session.execute(
            select(RetentionPolicy)
            .where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.applies_to["clause_id"].astext.in_(clause_ids),
            )
            .order_by(asc(RetentionPolicy.id))
            .limit(1)
        )
    ).scalar_one_or_none()


async def process_default_policy(
    session: AsyncSession, org_id: uuid.UUID, process_ids: frozenset[str]
) -> RetentionPolicy | None:
    if not process_ids:
        return None
    return (
        await session.execute(
            select(RetentionPolicy)
            .where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.applies_to["process_id"].astext.in_(process_ids),
            )
            .order_by(asc(RetentionPolicy.id))
            .limit(1)
        )
    ).scalar_one_or_none()


# --- evidence satellites -----------------------------------------------------------------


async def list_evidence_blobs(
    session: AsyncSession, record_id: uuid.UUID
) -> list[tuple[EvidenceBlob, Blob]]:
    rows = (
        await session.execute(
            select(EvidenceBlob, Blob)
            .join(Blob, EvidenceBlob.blob_sha256 == Blob.sha256)
            .where(EvidenceBlob.record_id == record_id)
            .order_by(asc(EvidenceBlob.created_at))
        )
    ).all()
    return [(eb, b) for eb, b in rows]


async def get_evidence_blob(
    session: AsyncSession, record_id: uuid.UUID, sha256: str
) -> EvidenceBlob | None:
    return (
        await session.execute(
            select(EvidenceBlob).where(
                EvidenceBlob.record_id == record_id, EvidenceBlob.blob_sha256 == sha256
            )
        )
    ).scalar_one_or_none()


async def get_evidence_link(
    session: AsyncSession,
    record_id: uuid.UUID,
    target_type: EvidenceForTargetType,
    target_id: uuid.UUID,
) -> EvidenceForLink | None:
    return (
        await session.execute(
            select(EvidenceForLink).where(
                EvidenceForLink.record_id == record_id,
                EvidenceForLink.target_type == target_type,
                EvidenceForLink.target_id == target_id,
            )
        )
    ).scalar_one_or_none()


async def get_evidence_link_by_id(
    session: AsyncSession, link_id: uuid.UUID
) -> EvidenceForLink | None:
    return await session.get(EvidenceForLink, link_id)


async def list_evidence_links(session: AsyncSession, record_id: uuid.UUID) -> list[EvidenceForLink]:
    return list(
        (
            await session.execute(
                select(EvidenceForLink)
                .where(EvidenceForLink.record_id == record_id)
                .order_by(asc(EvidenceForLink.created_at))
            )
        )
        .scalars()
        .all()
    )
