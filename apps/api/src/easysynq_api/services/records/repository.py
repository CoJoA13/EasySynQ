"""Records DB access: the retention-policy tier queries + the evidence satellites (slice S-rec-1).

The retention resolver (``domain/records/retention.py``) is pure; the per-tier *matching* is here —
each tier query returns at most one candidate policy (smallest ``id`` tiebreak) for the org. The
default policy (``"System Default Retention"``) is the always-present fallback: ``0023`` seeds one
per org, but a fresh install whose org row postdates the migration has none, so
``ensure_default_policy`` creates it idempotently on first capture (``UNIQUE(org_id, name)``).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from sqlalchemy import and_, asc, delete, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models._pack_enums import PackStatus
from ...db.models._record_enums import RecordDispositionState
from ...db.models._retention_enums import DispositionAction, RetentionBasis
from ...db.models._vault_enums import DocumentKind
from ...db.models.app_user import AppUser
from ...db.models.audit_finding import AuditFinding
from ...db.models.blob import Blob
from ...db.models.capa import Capa
from ...db.models.capa_stage import CapaStage
from ...db.models.clause import Clause
from ...db.models.disposition_event import DispositionEvent
from ...db.models.document_type import DocumentType
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_blob import EvidenceBlob
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.evidence_pack import EvidencePack
from ...db.models.framework import Framework
from ...db.models.pending_blob_purge import PendingBlobPurge
from ...db.models.process import Process
from ...db.models.record import Record
from ...db.models.retention_policy import RetentionPolicy
from ...db.models.storage_config import StorageConfig
from ...db.models.system_config import SystemConfig
from ...db.models.worm_destroy_request import WormDestroyRequest
from ..vault import repository as vault_repo
from .listing import (
    RecordListCriteria,
    RecordListCursor,
    escape_ilike_literal,
    normalize_record_search,
)

SYSTEM_DEFAULT_POLICY_NAME = "System Default Retention"
SEALED_PACK_POLICY_NAME = "Sealed Evidence Pack Retention"
SEALED_PACK_POLICY_ID_SALT = "easysynq.sealed-pack-retention.v1:"
_PRESERVED_PACK_POLICY_PREFIX = f"{SEALED_PACK_POLICY_NAME} (preserved user policy: "


def sealed_pack_policy_id(org_id: uuid.UUID) -> uuid.UUID:
    """Return the stable per-org id used to distinguish the managed policy from a name collision."""
    digest = hashlib.sha256(f"{SEALED_PACK_POLICY_ID_SALT}{org_id}".encode()).digest()
    return uuid.UUID(bytes=digest[:16])


# --- record lookups ----------------------------------------------------------------------


async def get_record(session: AsyncSession, record_id: uuid.UUID) -> Record | None:
    return await session.get(Record, record_id)


async def get_base(session: AsyncSession, record_id: uuid.UUID) -> DocumentedInformation | None:
    """The shared-PK base row (``documented_information``, kind=RECORD) for a record."""
    return await session.get(DocumentedInformation, record_id)


async def load_label_actors(
    session: AsyncSession, org_id: uuid.UUID, actor_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, AppUser]:
    """Load record capturers in one tenant-constrained query."""
    ids = set(actor_ids)
    if not ids:
        return {}
    rows = (
        await session.scalars(select(AppUser).where(AppUser.org_id == org_id, AppUser.id.in_(ids)))
    ).all()
    return {row.id: row for row in rows}


async def load_label_documents(
    session: AsyncSession, org_id: uuid.UUID, document_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[DocumentedInformation, DocumentType | None]]:
    """Load document label/authz inputs in one query, anchored directly to the caller's org."""
    ids = set(document_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(DocumentedInformation, DocumentType)
            .outerjoin(
                DocumentType,
                and_(
                    DocumentType.id == DocumentedInformation.document_type_id,
                    DocumentType.org_id == org_id,
                ),
            )
            .where(
                DocumentedInformation.org_id == org_id,
                DocumentedInformation.kind == DocumentKind.DOCUMENT,
                DocumentedInformation.id.in_(ids),
            )
        )
    ).all()
    return {doc.id: (doc, document_type) for doc, document_type in rows}


async def load_label_versions(
    session: AsyncSession, org_id: uuid.UUID, version_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, DocumentVersion]:
    """Load pinned-version labels in one tenant-constrained query."""
    ids = set(version_ids)
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.org_id == org_id,
                DocumentVersion.id.in_(ids),
            )
        )
    ).all()
    return {row.id: row for row in rows}


async def load_label_policies(
    session: AsyncSession, org_id: uuid.UUID, policy_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, RetentionPolicy]:
    """Load snapshotted retention-policy names in one tenant-constrained query."""
    ids = set(policy_ids)
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(RetentionPolicy).where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.id.in_(ids),
            )
        )
    ).all()
    return {row.id: row for row in rows}


async def load_label_records(
    session: AsyncSession, org_id: uuid.UUID, record_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[Record, DocumentedInformation]]:
    """Load related record label/authz inputs in one query without crossing tenants."""
    ids = set(record_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Record, DocumentedInformation)
            .join(DocumentedInformation, DocumentedInformation.id == Record.id)
            .where(
                Record.org_id == org_id,
                DocumentedInformation.org_id == org_id,
                DocumentedInformation.kind == DocumentKind.RECORD,
                Record.id.in_(ids),
            )
        )
    ).all()
    return {record.id: (record, base) for record, base in rows}


async def load_label_clauses(
    session: AsyncSession, org_id: uuid.UUID, clause_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Clause]:
    """Load clause labels in one query through the framework tenant anchor."""
    ids = set(clause_ids)
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(Clause)
            .join(Framework, Framework.id == Clause.framework_id)
            .where(Framework.org_id == org_id, Clause.id.in_(ids))
        )
    ).all()
    return {row.id: row for row in rows}


async def load_label_processes(
    session: AsyncSession, org_id: uuid.UUID, process_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Process]:
    """Load process labels in one tenant-constrained query."""
    ids = set(process_ids)
    if not ids:
        return {}
    rows = (
        await session.scalars(select(Process).where(Process.org_id == org_id, Process.id.in_(ids)))
    ).all()
    return {row.id: row for row in rows}


async def load_label_findings(
    session: AsyncSession, org_id: uuid.UUID, finding_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[AuditFinding, DocumentedInformation]]:
    """Load finding labels from their shared record bases in one tenant-constrained query."""
    ids = set(finding_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(AuditFinding, DocumentedInformation)
            .join(DocumentedInformation, DocumentedInformation.id == AuditFinding.id)
            .where(
                AuditFinding.org_id == org_id,
                DocumentedInformation.org_id == org_id,
                DocumentedInformation.kind == DocumentKind.RECORD,
                AuditFinding.id.in_(ids),
            )
        )
    ).all()
    return {finding.id: (finding, base) for finding, base in rows}


async def load_label_capa_stages(
    session: AsyncSession, org_id: uuid.UUID, stage_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[CapaStage, Capa, DocumentedInformation]]:
    """Load stage, parent CAPA, and shared CAPA label in one tenant-constrained query."""
    ids = set(stage_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(CapaStage, Capa, DocumentedInformation)
            .join(Capa, Capa.id == CapaStage.capa_id)
            .join(DocumentedInformation, DocumentedInformation.id == Capa.id)
            .where(
                CapaStage.org_id == org_id,
                Capa.org_id == org_id,
                DocumentedInformation.org_id == org_id,
                DocumentedInformation.kind == DocumentKind.RECORD,
                CapaStage.id.in_(ids),
            )
        )
    ).all()
    return {stage.id: (stage, capa, base) for stage, capa, base in rows}


async def list_record_candidates(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    criteria: RecordListCriteria,
    after: RecordListCursor | None,
    limit: int,
) -> list[tuple[Record, DocumentedInformation]]:
    """Return deterministic tenant/search/filter candidates for the caller's PDP scan."""
    filters: list[ColumnElement[bool]] = [
        Record.org_id == org_id,
        DocumentedInformation.org_id == org_id,
        DocumentedInformation.kind == DocumentKind.RECORD,
    ]
    search = normalize_record_search(criteria.q)
    if search is not None:
        pattern = f"%{escape_ilike_literal(search)}%"
        filters.append(
            or_(
                DocumentedInformation.identifier.ilike(pattern, escape="\\"),
                DocumentedInformation.title.ilike(pattern, escape="\\"),
            )
        )
    if criteria.record_type is not None:
        filters.append(Record.record_type == criteria.record_type)
    if criteria.source_document_id is not None:
        filters.append(Record.source_document_id == criteria.source_document_id)
    if criteria.captured_by is not None:
        filters.append(Record.captured_by == criteria.captured_by)
    if criteria.disposition_state is not None:
        filters.append(Record.disposition_state == criteria.disposition_state)
    if criteria.legal_hold is not None:
        filters.append(Record.legal_hold == criteria.legal_hold)
    if after is not None:
        filters.append(
            or_(
                Record.captured_at < after.captured_at,
                and_(
                    Record.captured_at == after.captured_at,
                    Record.id < after.record_id,
                ),
            )
        )

    rows = (
        await session.execute(
            select(Record, DocumentedInformation)
            .join(DocumentedInformation, Record.id == DocumentedInformation.id)
            .where(*filters)
            .order_by(desc(Record.captured_at), desc(Record.id))
            .limit(limit)
        )
    ).all()
    return [(r, d) for r, d in rows]


# --- process binding (S-records-R: the records process-scope read source of truth) -------


async def record_process_ids(session: AsyncSession, record: Record) -> set[str]:
    """The processes a record is bound to — for the PDP ``ResourceContext`` so a PROCESS-scoped
    ``record.read`` grant is honored: the record's evidence-for PROCESS links (leg A) + its source
    document's canonical process tuple (leg B), including satellite bindings such as a Quality
    Objective's ``process_id``. A record holds no ``ProcessLink`` of its own. This is the ONE source
    of truth shared by the records read gate AND the evidence-pack classifier (do NOT re-derive the
    authorization tuple elsewhere)."""
    via_link = (
        await session.scalars(
            select(EvidenceForLink.target_id).where(
                EvidenceForLink.record_id == record.id,
                EvidenceForLink.target_type == EvidenceForTargetType.PROCESS,
            )
        )
    ).all()
    via_doc: frozenset[str] = frozenset()
    if record.source_document_id is not None:
        via_doc = await vault_repo.process_ids_for_doc(session, record.source_document_id)
    return {str(x) for x in via_link} | set(via_doc)


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
    if own or record.source_document_id is not None or record.correction_of is None:
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
        by_doc = await vault_repo.process_ids_for_docs(
            session, list(set(source_by_record.values()))
        )
        for rid, did in source_by_record.items():
            out[rid] |= by_doc.get(did, set())
    return out


async def record_process_ids_effective_for(
    session: AsyncSession, records: list[Record]
) -> dict[uuid.UUID, set[str]]:
    """Batch the effective process tuple, including the source-less correction fallback.

    The common leg-A/leg-B union is always two grouped queries. Correction ancestors are then
    visited breadth-first: every depth uses one ``IN`` query for records plus the same grouped
    union loader, so query count follows chain depth rather than output-row count. Tenant and cycle
    checks are applied independently for every root.
    """
    out = await record_process_ids_for(session, records)
    roots = {record.id: record for record in records}
    cursors = {
        record.id: record.correction_of
        for record in records
        if not out[record.id]
        and record.source_document_id is None
        and record.correction_of is not None
    }
    seen: dict[uuid.UUID, set[uuid.UUID]] = {record_id: {record_id} for record_id in cursors}

    while cursors:
        cursor_ids = set(cursors.values())
        ancestors = list(
            (
                await session.scalars(
                    select(Record).where(
                        Record.id.in_(cursor_ids),
                        Record.org_id.in_({root.org_id for root in roots.values()}),
                    )
                )
            ).all()
        )
        by_id = {ancestor.id: ancestor for ancestor in ancestors}
        own_by_ancestor = await record_process_ids_for(session, ancestors)
        next_cursors: dict[uuid.UUID, uuid.UUID] = {}

        for root_id, cursor_id in cursors.items():
            root = roots[root_id]
            ancestor = by_id.get(cursor_id)
            if ancestor is None or ancestor.org_id != root.org_id or cursor_id in seen[root_id]:
                continue
            seen[root_id].add(cursor_id)
            ancestor_own = own_by_ancestor.get(cursor_id) or set()
            if ancestor_own:
                out[root_id] = ancestor_own
            elif ancestor.correction_of is not None:
                next_cursors[root_id] = ancestor.correction_of

        cursors = next_cursors

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


async def ensure_sealed_pack_policy(session: AsyncSession, org_id: uuid.UUID) -> RetentionPolicy:
    """Get-or-create the system-managed policy reserved for sealed evidence packs.

    Its deterministic id distinguishes it from a pre-existing user policy that happens to use the
    newly reserved name. Such a collision is renamed in place (preserving its id, settings, and
    pinned records) before the separate managed row is created. The managed row is normalized on
    every use so an out-of-band edit cannot make a newly sealed pack disposable.
    """
    policy_id = sealed_pack_policy_id(org_id)
    collision = (
        await session.execute(
            select(RetentionPolicy)
            .where(
                RetentionPolicy.org_id == org_id,
                RetentionPolicy.name == SEALED_PACK_POLICY_NAME,
                RetentionPolicy.id != policy_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if collision is not None:
        collision.name = f"{_PRESERVED_PACK_POLICY_PREFIX}{collision.id}:{uuid.uuid4().hex[:16]})"
        await session.flush()

    await session.execute(
        pg_insert(RetentionPolicy)
        .values(
            id=policy_id,
            org_id=org_id,
            name=SEALED_PACK_POLICY_NAME,
            applies_to=None,
            basis=RetentionBasis.CAPTURED_AT,
            duration="PERMANENT",
            disposition_action=DispositionAction.RETAIN_PERMANENT,
            review_required=False,
            worm_lock_period=None,
            active=True,
            archived_at=None,
            archived_by=None,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "name": SEALED_PACK_POLICY_NAME,
                "applies_to": None,
                "basis": RetentionBasis.CAPTURED_AT,
                "duration": "PERMANENT",
                "disposition_action": DispositionAction.RETAIN_PERMANENT,
                "review_required": False,
                "worm_lock_period": None,
                "active": True,
                "archived_at": None,
                "archived_by": None,
                "updated_at": func.now(),
            },
        )
    )
    policy = await session.get(RetentionPolicy, policy_id)
    assert policy is not None  # noqa: S101 — just inserted-or-normalized under the unique index
    await session.refresh(policy)
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
                RetentionPolicy.is_active.is_(True),
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
                RetentionPolicy.is_active.is_(True),
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
                RetentionPolicy.is_active.is_(True),
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
        stmt = stmt.where(RetentionPolicy.is_active.is_(True))
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
    ``content_hash`` preserve what existed). Only called when no live domain owner needs the
    bytes."""
    await session.execute(delete(EvidenceBlob).where(EvidenceBlob.blob_sha256 == blob_sha256))
    await session.execute(delete(Blob).where(Blob.sha256 == blob_sha256))


async def detach_record_evidence_blob(
    session: AsyncSession, record_id: uuid.UUID, blob_sha256: str
) -> None:
    """Remove one disposed Record's reachability while another live owner retains the Blob."""
    await session.execute(
        delete(EvidenceBlob).where(
            EvidenceBlob.record_id == record_id,
            EvidenceBlob.blob_sha256 == blob_sha256,
        )
    )


def _record_content_is_preserved() -> ColumnElement[bool]:
    """Match Records whose bytes remain lawful owners of a Blob.

    ``DISPOSED`` alone is not an erasure signal: ``ARCHIVE_COLD`` and ``TRANSFER`` deliberately
    preserve content. Only an immutable destructive disposition event removes Record ownership.
    """
    return ~(
        select(DispositionEvent.id)
        .where(
            DispositionEvent.record_id == Record.id,
            or_(
                DispositionEvent.is_worm_destroy.is_(True),
                DispositionEvent.action == DispositionAction.DESTROY,
            ),
        )
        .exists()
    )


async def blob_needed_by_other_live_record(
    session: AsyncSession, blob_sha256: str, exclude_record_id: uuid.UUID
) -> bool:
    """``True`` if destroying this blob's bytes would orphan a still-live reference — so a DESTROY
    purges the bytes only when this is ``False``. The caller still detaches the disposed Record's
    own ``evidence_blob`` row when this is ``True`` so its generic download route cannot reach bytes
    retained for an unrelated lawful owner.

    Four live-owner legs:
    1. Some OTHER record whose content was not destructively disposed still attaches this blob
       (records may share a records-bucket WORM blob — the S-rec-1 dedup). A Record disposed through
       ``ARCHIVE_COLD`` or ``TRANSFER`` remains an owner because those actions preserve its bytes.
    2. A ``document_version`` references this sha as ``source_blob_sha256`` /
       ``rendition_blob_sha256`` (both RESTRICT FKs onto ``blob.sha256``). CR-1 defense-in-depth:
       the check-in guard (``_assert_documents_worm_blob``) makes this cross-kind sharing
       UNREACHABLE for new check-ins, but this leg stops a record disposition from physically
       destroying bytes a controlled document still needs — the D2 data-loss AND the
       ``delete_blob_and_links`` RESTRICT-FK IntegrityError that would otherwise crash-loop the
       retention sweep.
    3. Another live Record points at the sha as its structured PDF rendition.
    4. A non-invalidated Evidence Pack still points at the sha as its ZIP or portfolio."""
    record_leg = await session.scalar(
        select(func.count())
        .select_from(EvidenceBlob)
        .join(Record, EvidenceBlob.record_id == Record.id)
        .where(
            EvidenceBlob.blob_sha256 == blob_sha256,
            EvidenceBlob.record_id != exclude_record_id,
            _record_content_is_preserved(),
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
    if version_leg:
        return True
    structured_leg = await session.scalar(
        select(func.count())
        .select_from(Record)
        .where(
            Record.id != exclude_record_id,
            Record.structured_pdf_blob_sha256 == blob_sha256,
            _record_content_is_preserved(),
        )
    )
    if structured_leg:
        return True
    pack_leg = await session.scalar(
        select(func.count())
        .select_from(EvidencePack)
        .where(
            EvidencePack.status != PackStatus.UNAVAILABLE,
            or_(
                EvidencePack.zip_blob_sha256 == blob_sha256,
                EvidencePack.portfolio_blob_sha256 == blob_sha256,
            ),
        )
    )
    return bool(pack_leg)


async def blob_needed_by_any_live_owner(session: AsyncSession, blob_sha256: str) -> bool:
    """Return whether any still-live domain pointer owns this content-addressed Blob.

    Issue #361 uses this after clearing every affected pack pointer. Unlike the record-specific
    helper above, a portfolio has no EvidenceBlob parent, so its last-owner decision must cover all
    four pointer families explicitly: preserved Record evidence, controlled DocumentVersions,
    preserved structured-record renditions, and non-invalidated Evidence Pack ZIP/portfolio
    pointers. ``ARCHIVE_COLD``/``TRANSFER`` Records remain preserved owners despite their terminal
    ``DISPOSED`` state; only a destructive disposition event removes ownership.
    """
    evidence_leg = await session.scalar(
        select(func.count())
        .select_from(EvidenceBlob)
        .join(Record, EvidenceBlob.record_id == Record.id)
        .where(
            EvidenceBlob.blob_sha256 == blob_sha256,
            _record_content_is_preserved(),
        )
    )
    if evidence_leg:
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
    if version_leg:
        return True
    structured_leg = await session.scalar(
        select(func.count())
        .select_from(Record)
        .where(
            Record.structured_pdf_blob_sha256 == blob_sha256,
            _record_content_is_preserved(),
        )
    )
    if structured_leg:
        return True
    pack_leg = await session.scalar(
        select(func.count())
        .select_from(EvidencePack)
        .where(
            EvidencePack.status != PackStatus.UNAVAILABLE,
            or_(
                EvidencePack.zip_blob_sha256 == blob_sha256,
                EvidencePack.portfolio_blob_sha256 == blob_sha256,
            ),
        )
    )
    return bool(pack_leg)


async def lock_blob_for_update(session: AsyncSession, blob_sha256: str) -> None:
    """Row-lock the ``blob`` (``SELECT … FOR UPDATE``) so a shared-blob disposition serialises: two
    records sharing one blob must not both read the liveness check while the peer's disposition is
    uncommitted and then both skip the purge, orphaning the bytes. Held until the caller commits, so
    the second disposer re-reads liveness AFTER the first's committed DISPOSED flip and purges as
    the last referencer. A no-op if the row is already gone (nothing to lock)."""
    await session.execute(select(Blob.sha256).where(Blob.sha256 == blob_sha256).with_for_update())


async def lock_physical_objects(session: AsyncSession, objects: Iterable[tuple[str, str]]) -> None:
    """Serialize capture and purge for physical objects until transaction end.

    A row lock cannot cover the pending-purge re-capture race because the old ``blob`` row has
    already been deleted. Both sides instead take the same PostgreSQL transaction advisory lock
    before either promoting bytes/creating an owner or checking ownership/erasing bytes. The lock
    is released automatically by commit, rollback, or connection loss.

    Resolve PostgreSQL's actual 32-bit ``hashtext`` keys first, then de-duplicate and sort those
    numeric keys before acquiring any lock. Sorting raw object names is insufficient: two different
    names can collide, and overlapping multi-object captures could otherwise acquire the collided
    key and a non-colliding key in opposite orders. With actual-key ordering, collisions add only
    harmless serialization; they cannot weaken the exclusion or introduce a deadlock.
    """
    lock_keys: set[int] = set()
    for bucket, object_key in objects:
        identity = f"{bucket}\x1f{object_key}"
        lock_key = await session.scalar(select(func.hashtext(identity)))
        if lock_key is None:  # PostgreSQL hashtext(non-NULL text) is total; fail closed on drift.
            raise RuntimeError("PostgreSQL returned no physical-object advisory lock key")
        lock_keys.add(int(lock_key))
    for lock_key in sorted(lock_keys):
        await session.execute(select(func.pg_advisory_xact_lock(lock_key)))


async def lock_physical_object(session: AsyncSession, *, bucket: str, object_key: str) -> None:
    """Single-object convenience wrapper for ``lock_physical_objects``."""
    await lock_physical_objects(session, ((bucket, object_key),))


async def lock_pending_purge_for_update(session: AsyncSession, purge_id: uuid.UUID) -> bool:
    """Claim one marker before its physical-object lock.

    The reaper claims marker rows before taking physical-object locks. Immediate purge must use the
    same order so an overlapping reaper cannot hold the marker while waiting on the object lock
    that immediate purge holds. ``False`` means another successful path already removed the marker.
    """
    return (
        await session.scalar(
            select(PendingBlobPurge.id).where(PendingBlobPurge.id == purge_id).with_for_update()
        )
    ) is not None


async def insert_pending_purge(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    sha256: str,
    bucket: str,
    object_key: str,
    bypass_governance: bool,
    record_id: uuid.UUID,
    disposition_event_id: uuid.UUID,
    worm_destroy_request_id: uuid.UUID | None,
) -> uuid.UUID:
    """Record an authority-bound to-be-purged marker.

    The marker is committed alongside the immutable DESTROY event and blob-row delete, so a crash
    between that commit and the physical S3 purge never loses the erasure work. Its Record/event
    references (plus the executed R27 request when present) let the reaper derive authority instead
    of trusting marker-controlled fields. Returns the id used by the immediate post-commit purge.
    """
    marker = PendingBlobPurge(
        org_id=org_id,
        sha256=sha256,
        bucket=bucket,
        object_key=object_key,
        bypass_governance=bypass_governance,
        record_id=record_id,
        disposition_event_id=disposition_event_id,
        worm_destroy_request_id=worm_destroy_request_id,
    )
    session.add(marker)
    await session.flush()
    return marker.id


async def object_is_owned(session: AsyncSession, *, bucket: str, object_key: str) -> bool:
    """True if a live ``blob`` row owns this exact physical object location.

    The purge path checks this before erasing a marker's bytes: a re-capture of the same content
    after the marker was written re-creates the ``blob`` row over the still-present (not-yet-purged)
    object, so the bytes are live again and the stale marker must be dropped, not replayed.

    The marker's SHA is deliberately not part of this decision: it is untrusted diagnostic
    provenance, and a forged false SHA must not hide a live owner at the named location. Conversely,
    matching content in another bucket is a physically distinct object and does not cancel this
    purge. Keying on ``(bucket, object_key)`` protects exactly the object the worker would erase.
    """
    return (
        await session.scalar(
            select(Blob.sha256).where(
                Blob.bucket == bucket,
                Blob.object_key == object_key,
            )
        )
    ) is not None


async def get_pending_purge_authority(
    session: AsyncSession,
    *,
    record_id: uuid.UUID,
    disposition_event_id: uuid.UUID,
    worm_destroy_request_id: uuid.UUID | None,
) -> tuple[Record, DispositionEvent, WormDestroyRequest | None] | None:
    """Load the rows a bound marker claims authorize it.

    Relationships are intentionally not filtered here: the service validates every org/Record/
    actor/legal-basis edge and must be able to distinguish a mismatched-but-existing row from a
    legitimate tuple. The outer request join yields ``None`` for an ordinary policy disposition.
    """
    row = (
        await session.execute(
            select(Record, DispositionEvent, WormDestroyRequest)
            .select_from(Record)
            .join(DispositionEvent, DispositionEvent.id == disposition_event_id)
            .outerjoin(
                WormDestroyRequest,
                WormDestroyRequest.id == worm_destroy_request_id,
            )
            .where(Record.id == record_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1], row[2]


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
