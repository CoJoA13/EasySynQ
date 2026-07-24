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
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_blob import EvidenceBlob
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.pending_blob_purge import PendingBlobPurge
from ...db.models.process_link import ProcessLink
from ...db.models.record import Record
from ...db.models.retention_policy import RetentionPolicy
from ...db.models.storage_config import StorageConfig
from ...db.models.system_config import SystemConfig
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


# --- process binding (S-records-R: the records process-scope read source of truth) -------


async def record_process_ids(session: AsyncSession, record: Record) -> set[str]:
    """The processes a record is bound to — for the PDP ``ResourceContext`` so a PROCESS-scoped
    ``record.read`` grant is honored: the record's evidence-for PROCESS links (leg A) + its source
    document's process links (leg B). A record holds no ``ProcessLink`` of its own. This is the ONE
    source of truth shared by the records read gate AND the evidence-pack classifier (do NOT
    re-derive the union elsewhere)."""
    via_link = (
        await session.scalars(
            select(EvidenceForLink.target_id).where(
                EvidenceForLink.record_id == record.id,
                EvidenceForLink.target_type == EvidenceForTargetType.PROCESS,
            )
        )
    ).all()
    via_doc: list[uuid.UUID] = []
    if record.source_document_id is not None:
        via_doc = list(
            (
                await session.scalars(
                    select(ProcessLink.process_id).where(
                        ProcessLink.documented_information_id == record.source_document_id
                    )
                )
            ).all()
        )
    return {str(x) for x in (*via_link, *via_doc)}


async def record_process_ids_effective(session: AsyncSession, record: Record) -> set[str]:
    """``record_process_ids`` with the R3-1 correction-chain fallback: a source-LESS evidence
    correction inherits no source-doc binding, so it would be invisible to the process that owned
    the original. When a record's OWN union is empty AND it is a correction (``correction_of``),
    walk the chain to the first ancestor with a non-empty binding and return that. The walk is
    ITERATIVE (no recursion limit) with NO hop cap (a long but legitimate chain keeps its
    visibility — the Codex CX-3 findings) and cycle-safe via a visited set (the chain is acyclic by
    construction — ``capture_correction`` rejects an already-superseded original — but the set makes
    it robust on ANY input). Never crosses an org; never widens a record with its own binding."""
    own = await record_process_ids(session, record)
    if own or record.correction_of is None:
        return own
    seen = {record.id}
    cursor: uuid.UUID | None = record.correction_of
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        predecessor = await session.get(Record, cursor)
        if predecessor is None or predecessor.org_id != record.org_id:
            return set()
        ancestor_own = await record_process_ids(session, predecessor)
        if ancestor_own:
            return ancestor_own
        cursor = predecessor.correction_of
    return set()


async def record_process_ids_for(
    session: AsyncSession, records: list[Record]
) -> dict[uuid.UUID, set[str]]:
    """Batched base unions (leg A + leg B) for a list of records — two grouped queries, no N+1. The
    correction-chain (R3-1) fallback is applied PER-ROW by the caller only for the rare source-less
    corrected rows (empty union + ``correction_of``), so the common path stays batched."""
    if not records:
        return {}
    ids = [r.id for r in records]
    out: dict[uuid.UUID, set[str]] = {r.id: set() for r in records}

    link_rows = (
        await session.execute(
            select(EvidenceForLink.record_id, EvidenceForLink.target_id).where(
                EvidenceForLink.record_id.in_(ids),
                EvidenceForLink.target_type == EvidenceForTargetType.PROCESS,
            )
        )
    ).all()
    for rid, pid in link_rows:
        out[rid].add(str(pid))

    source_by_record = {r.id: r.source_document_id for r in records if r.source_document_id}
    if source_by_record:
        doc_rows = (
            await session.execute(
                select(ProcessLink.documented_information_id, ProcessLink.process_id).where(
                    ProcessLink.documented_information_id.in_(set(source_by_record.values()))
                )
            )
        ).all()
        by_doc: dict[uuid.UUID, set[str]] = {}
        for did, pid in doc_rows:
            by_doc.setdefault(did, set()).add(str(pid))
        for rid, did in source_by_record.items():
            out[rid] |= by_doc.get(did, set())
    return out


# --- retention policy --------------------------------------------------------------------


async def get_policy(
    session: AsyncSession, policy_id: uuid.UUID, org_id: uuid.UUID
) -> RetentionPolicy | None:
    policy = await session.get(RetentionPolicy, policy_id)
    if policy is None or policy.org_id != org_id:
        return None
    return policy


async def system_default_policy(session: AsyncSession, org_id: uuid.UUID) -> RetentionPolicy | None:
    return await policy_by_name(session, org_id, SYSTEM_DEFAULT_POLICY_NAME)


async def policy_by_name(
    session: AsyncSession, org_id: uuid.UUID, name: str
) -> RetentionPolicy | None:
    """The org's policy with this exact name, if any (UNIQUE(org_id, name) → at most one)."""
    return (
        await session.execute(
            select(RetentionPolicy).where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.name == name,
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
    """The record-type default tier: an ACTIVE policy whose ``applies_to.record_type`` matches (an
    archived policy stops auto-attaching to new captures — S-rec-4)."""
    return (
        await session.execute(
            select(RetentionPolicy)
            .where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.active.is_(True),
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
                RetentionPolicy.active.is_(True),
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
                RetentionPolicy.active.is_(True),
                RetentionPolicy.applies_to["process_id"].astext.in_(process_ids),
            )
            .order_by(asc(RetentionPolicy.id))
            .limit(1)
        )
    ).scalar_one_or_none()


async def list_retention_policies(
    session: AsyncSession, org_id: uuid.UUID, *, include_archived: bool
) -> list[RetentionPolicy]:
    """All of an org's retention policies (newest first), optionally including archived ones."""
    stmt = select(RetentionPolicy).where(RetentionPolicy.org_id == org_id)
    if not include_archived:
        stmt = stmt.where(RetentionPolicy.active.is_(True))
    stmt = stmt.order_by(desc(RetentionPolicy.created_at), asc(RetentionPolicy.id))
    return list((await session.execute(stmt)).scalars().all())


async def count_active_pinned_records(session: AsyncSession, policy_id: uuid.UUID) -> int:
    """How many non-DISPOSED records are pinned to this policy — the extend-forward guard fires only
    when this is > 0 (the spec's "already-captured records" qualifier, doc 06 §5.2)."""
    count = await session.scalar(
        select(func.count())
        .select_from(Record)
        .where(
            Record.retention_policy_id == policy_id,
            Record.disposition_state != RecordDispositionState.DISPOSED,
        )
    )
    return int(count or 0)


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


async def allow_self_disposition(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """The org's SoD-6 relaxation flag (S-rec-4). ``False`` (STRICT — creator-not-disposer enforced)
    when no ``system_config`` row exists yet, so the default fails closed (the
    ``get_allow_approver_release`` precedent)."""
    value = await session.scalar(
        select(SystemConfig.allow_self_disposition).where(SystemConfig.org_id == org_id)
    )
    return bool(value)


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
    """``True`` if destroying this blob's bytes would orphan a still-live reference — so a DESTROY
    purges the bytes only when this is ``False`` (the disposed record keeps its ``evidence_blob``
    tombstone row regardless; the bytes simply 404 once gone).

    Two legs:
    1. Some OTHER non-``DISPOSED`` record still attaches this blob (records may share a
       records-bucket WORM blob — the S-rec-1 dedup).
    2. A ``document_version`` references this sha as ``source_blob_sha256`` /
       ``rendition_blob_sha256`` (both RESTRICT FKs onto ``blob.sha256``). CR-1 defense-in-depth:
       the check-in guard (``_assert_documents_worm_blob``) makes this cross-kind sharing
       UNREACHABLE for new check-ins, but this leg stops a record disposition from physically
       destroying bytes a controlled document still needs — the D2 data-loss AND the
       ``delete_blob_and_links`` RESTRICT-FK IntegrityError that would otherwise crash-loop the
       retention sweep."""
    record_leg = await session.scalar(
        select(func.count())
        .select_from(EvidenceBlob)
        .join(Record, EvidenceBlob.record_id == Record.id)
        .where(
            EvidenceBlob.blob_sha256 == blob_sha256,
            EvidenceBlob.record_id != exclude_record_id,
            Record.disposition_state != RecordDispositionState.DISPOSED,
        )
    )
    if record_leg:
        return True
    version_leg = await session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(
            or_(
                DocumentVersion.source_blob_sha256 == blob_sha256,
                DocumentVersion.rendition_blob_sha256 == blob_sha256,
            )
        )
    )
    return bool(version_leg)


async def lock_blob_for_update(session: AsyncSession, blob_sha256: str) -> None:
    """Row-lock the ``blob`` (``SELECT … FOR UPDATE``) so a shared-blob disposition serialises: two
    records sharing one blob must not both read the liveness check while the peer's disposition is
    uncommitted and then both skip the purge, orphaning the bytes. Held until the caller commits, so
    the second disposer re-reads liveness AFTER the first's committed DISPOSED flip and purges as
    the last referencer. A no-op if the row is already gone (nothing to lock)."""
    await session.execute(select(Blob.sha256).where(Blob.sha256 == blob_sha256).with_for_update())


async def insert_pending_purge(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    sha256: str,
    bucket: str,
    object_key: str,
    bypass_governance: bool,
) -> uuid.UUID:
    """Record a to-be-purged marker — the reaper-recoverable follow-up committed alongside the
    blob-row delete, so a crash between that commit and the physical S3 purge never loses the bytes.
    Returns the marker id so the immediate post-commit purge can delete exactly this row."""
    marker = PendingBlobPurge(
        org_id=org_id,
        sha256=sha256,
        bucket=bucket,
        object_key=object_key,
        bypass_governance=bypass_governance,
    )
    session.add(marker)
    await session.flush()
    return marker.id


async def blob_owns_object(
    session: AsyncSession, *, sha256: str, bucket: str, object_key: str
) -> bool:
    """True if a live ``blob`` row now owns THIS EXACT object — same sha AND bucket AND object_key.
    The purge path checks this before erasing a marker's bytes: a re-capture of the same content
    after the marker was written re-creates the ``blob`` row over the still-present (not-yet-purged)
    object, so the bytes are live again and the stale marker must be dropped, not replayed.

    ⚠ Matching the sha ALONE is wrong: ``blob.sha256`` is a GLOBAL content-addressed PK, so the
    identical bytes can be re-owned by a blob row in a DIFFERENT bucket — e.g. a document check-in
    lands the sha in the ``documents`` bucket while a records-evidence marker still targets the
    ``records`` bucket (both objects physically distinct). A sha-only match would then cancel the
    marker WITHOUT erasing the orphaned records object, leaking a disposed record's evidence. Keying
    on (sha, bucket, object_key) cancels only when the marker's OWN object was re-created."""
    return (
        await session.scalar(
            select(Blob.sha256).where(
                Blob.sha256 == sha256,
                Blob.bucket == bucket,
                Blob.object_key == object_key,
            )
        )
    ) is not None


async def list_pending_purges(
    session: AsyncSession, *, limit: int = 200, exclude_ids: set[uuid.UUID] | None = None
) -> list[PendingBlobPurge]:
    """Claim a batch of pending purge markers, oldest first (``FOR UPDATE SKIP LOCKED`` so two
    overlapping reaper runs don't double-process the same marker). ``exclude_ids`` lets one reaper
    run loop PAST a set of markers it already handled this pass (so a persistent-failure cohort in
    the oldest rows can't starve newer, purgeable markers — the per-run rotation)."""
    stmt = select(PendingBlobPurge)
    if exclude_ids:
        stmt = stmt.where(PendingBlobPurge.id.notin_(exclude_ids))
    stmt = (
        stmt.order_by(asc(PendingBlobPurge.created_at))
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def delete_pending_purge(session: AsyncSession, purge_id: uuid.UUID) -> None:
    """Drop a purge marker once its bytes are confirmed gone (purge_object is idempotent)."""
    await session.execute(delete(PendingBlobPurge).where(PendingBlobPurge.id == purge_id))


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
