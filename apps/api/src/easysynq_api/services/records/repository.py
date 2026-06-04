"""Records DB access: the retention-policy tier queries + the evidence satellites (slice S-rec-1).

The retention resolver (``domain/records/retention.py``) is pure; the per-tier *matching* is here —
each tier query returns at most one candidate policy (smallest ``id`` tiebreak) for the org. The
default policy (``"System Default Retention"``) is the always-present fallback: ``0023`` seeds one
per org, but a fresh install whose org row postdates the migration has none, so
``ensure_default_policy`` creates it idempotently on first capture (``UNIQUE(org_id, name)``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, asc, delete, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models._record_enums import RecordDispositionState
from ...db.models._retention_enums import DispositionAction, RetentionBasis
from ...db.models.blob import Blob
from ...db.models.disposition_event import DispositionEvent
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_blob import EvidenceBlob
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.record import Record
from ...db.models.retention_policy import RetentionPolicy
from ...db.models.storage_config import StorageConfig
from ...db.models.worm_destroy_request import WormDestroyRequest

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


# --- disposition (slice S-rec-2) ---------------------------------------------------------


async def org_object_lock_mode(session: AsyncSession, org_id: uuid.UUID) -> str:
    """The org's recorded object-lock mode (``GOVERNANCE`` | ``COMPLIANCE``); ``GOVERNANCE`` (the
    D-7 default) when no ``storage_config`` row exists. Drives the R27 destroy bypass-vs-refuse."""
    mode = await session.scalar(
        select(StorageConfig.object_lock_mode).where(StorageConfig.org_id == org_id)
    )
    return mode or "GOVERNANCE"


async def due_active_records(
    session: AsyncSession, *, for_update: bool = True
) -> list[tuple[Record, RetentionPolicy]]:
    """The retention-sweep candidate set, not legal-held, with a known basis date and a
    non-``RETAIN_PERMANENT`` policy (``PERMANENT`` durations never expire):

    * ``ACTIVE`` records (flip to DUE_FOR_REVIEW when their clock has elapsed), and
    * ``DUE_FOR_REVIEW`` + ``review_required=false`` records (the low-risk retry leg — a DESTROY
      whose WORM lock had not yet expired on an earlier sweep). ``review_required=true`` DUE records
      are excluded — they await human approval and must not be re-processed.

    The caller computes ``retention_until`` in-app and keeps only the rows whose clock has elapsed.
    ``FOR UPDATE SKIP LOCKED`` (of ``record`` only) reserves the batch so overlapping sweeps don't
    double-process."""
    stmt = (
        select(Record, RetentionPolicy)
        .join(RetentionPolicy, Record.retention_policy_id == RetentionPolicy.id)
        .where(
            Record.legal_hold.is_(False),
            Record.retention_basis_date.is_not(None),
            RetentionPolicy.disposition_action != DispositionAction.RETAIN_PERMANENT,
            or_(
                Record.disposition_state == RecordDispositionState.ACTIVE,
                and_(
                    Record.disposition_state == RecordDispositionState.DUE_FOR_REVIEW,
                    RetentionPolicy.review_required.is_(False),
                ),
            ),
        )
    )
    if for_update:
        stmt = stmt.with_for_update(skip_locked=True, of=Record)
    rows = (await session.execute(stmt)).all()
    return [(r, p) for r, p in rows]


async def delete_blob_and_links(session: AsyncSession, blob_sha256: str) -> None:
    """After a blob's bytes are physically destroyed, drop the now-false ``blob`` row + every
    ``evidence_blob`` row referencing it, so the invariant **a ``blob`` row exists iff its object
    exists** holds — no backup/restore (or any 'copy every blob' sweep) ever hits a destroyed
    object (doc 06 §5.3 "removes the blob"; the ``disposition_event`` tombstone + the record
    ``content_hash`` preserve what existed). Only called when no live record needs the bytes."""
    await session.execute(delete(EvidenceBlob).where(EvidenceBlob.blob_sha256 == blob_sha256))
    await session.execute(delete(Blob).where(Blob.sha256 == blob_sha256))


async def blob_needed_by_other_live_record(
    session: AsyncSession, blob_sha256: str, exclude_record_id: uuid.UUID
) -> bool:
    """``True`` if some OTHER non-``DISPOSED`` record still attaches this blob — so destroying its
    bytes would orphan a live record's evidence. Records may share a records-bucket WORM blob (the
    S-rec-1 dedup), so a DESTROY purges bytes only when this is ``False``; the disposed record keeps
    its ``evidence_blob`` tombstone row regardless (the bytes simply 404 once gone)."""
    count = await session.scalar(
        select(func.count())
        .select_from(EvidenceBlob)
        .join(Record, EvidenceBlob.record_id == Record.id)
        .where(
            EvidenceBlob.blob_sha256 == blob_sha256,
            EvidenceBlob.record_id != exclude_record_id,
            Record.disposition_state != RecordDispositionState.DISPOSED,
        )
    )
    return bool(count)


async def list_disposition_events(
    session: AsyncSession, record_id: uuid.UUID
) -> list[DispositionEvent]:
    return list(
        (
            await session.execute(
                select(DispositionEvent)
                .where(DispositionEvent.record_id == record_id)
                .order_by(asc(DispositionEvent.executed_at))
            )
        )
        .scalars()
        .all()
    )


async def open_worm_destroy_request(
    session: AsyncSession, record_id: uuid.UUID
) -> WormDestroyRequest | None:
    """The single open (neither executed nor cancelled) destroy request for a record, if any (the
    partial-unique index guarantees at most one)."""
    return (
        await session.execute(
            select(WormDestroyRequest).where(
                WormDestroyRequest.record_id == record_id,
                WormDestroyRequest.executed_at.is_(None),
                WormDestroyRequest.cancelled_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def get_worm_destroy_request(
    session: AsyncSession, req_id: uuid.UUID, *, for_update: bool = False
) -> WormDestroyRequest | None:
    if for_update:
        return (
            await session.execute(
                select(WormDestroyRequest).where(WormDestroyRequest.id == req_id).with_for_update()
            )
        ).scalar_one_or_none()
    return await session.get(WormDestroyRequest, req_id)


async def list_worm_destroy_requests(
    session: AsyncSession, record_id: uuid.UUID
) -> list[WormDestroyRequest]:
    return list(
        (
            await session.execute(
                select(WormDestroyRequest)
                .where(WormDestroyRequest.record_id == record_id)
                .order_by(asc(WormDestroyRequest.requested_at))
            )
        )
        .scalars()
        .all()
    )
