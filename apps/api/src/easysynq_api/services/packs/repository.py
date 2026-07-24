"""Evidence-pack DB access: scope resolution + pack/item CRUD (slice S-pack-1, doc 06 §7).

The resolution queries are the heart of the slice. Records reach clauses ONLY via
``evidence_for_link`` (capture adds no ``clause_mapping``), so each scope is a **UNION of 2 legs**:
the explicit evidence-for links AND records under a clause-mapped/process-linked source document.
Everything is ``org_id``-scoped; ``Record`` rows are intrinsically ``kind=RECORD`` (the shared-PK
subtype) so no extra kind filter is needed on the record side.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, asc, cast, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models._pack_enums import PackInclusionStatus, PackItemType, PackScopeKind
from ...db.models._retention_enums import DispositionAction
from ...db.models._signature_enums import SignedObjectType
from ...db.models.app_user import AppUser
from ...db.models.audit import Audit
from ...db.models.audit_finding import AuditFinding
from ...db.models.capa import Capa
from ...db.models.capa_stage import CapaStage
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.disposition_event import DispositionEvent
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_for_link import EvidenceForLink
from ...db.models.evidence_pack import EvidencePack
from ...db.models.pack_item import PackItem
from ...db.models.pack_share_link import PackShareLink
from ...db.models.process_link import ProcessLink
from ...db.models.record import Record
from ...db.models.signature_event import SignatureEvent

# --- pack header CRUD --------------------------------------------------------------------


async def get_pack(
    session: AsyncSession, pack_id: uuid.UUID, *, for_update: bool = False
) -> EvidencePack | None:
    if for_update:
        return (
            await session.execute(
                select(EvidencePack).where(EvidencePack.id == pack_id).with_for_update()
            )
        ).scalar_one_or_none()
    return await session.get(EvidencePack, pack_id)


async def list_packs(session: AsyncSession, org_id: uuid.UUID, *, limit: int) -> list[EvidencePack]:
    return list(
        (
            await session.execute(
                select(EvidencePack)
                .where(EvidencePack.org_id == org_id)
                .order_by(desc(EvidencePack.created_at))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def list_pack_items(session: AsyncSession, pack_id: uuid.UUID) -> list[PackItem]:
    return list(
        (
            await session.execute(
                select(PackItem)
                .where(PackItem.pack_id == pack_id)
                .order_by(asc(PackItem.created_at))
            )
        )
        .scalars()
        .all()
    )


async def delete_pack_items(session: AsyncSession, pack_id: uuid.UUID) -> None:
    """Drop all membership rows for a pack — preview and build both rebuild from scratch so the seal
    is over one coherent set (the TOCTOU fix: build never merges stale preview rows)."""
    await session.execute(delete(PackItem).where(PackItem.pack_id == pack_id))


# --- content resolution ------------------------------------------------------------------


async def _clause_candidate_ids(
    session: AsyncSession, org_id: uuid.UUID, clause_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """CLAUSE scope: UNION of (records evidence-for the clause) AND (records under a clause-mapped
    source document — records do NOT auto-inherit their source-doc's clause mappings)."""
    if not clause_ids:
        return set()
    leg_a = (
        await session.scalars(
            select(EvidenceForLink.record_id).where(
                EvidenceForLink.org_id == org_id,
                EvidenceForLink.target_type == EvidenceForTargetType.CLAUSE,
                EvidenceForLink.target_id.in_(clause_ids),
            )
        )
    ).all()
    leg_b = (
        await session.scalars(
            select(Record.id)
            .join(
                ClauseMapping,
                ClauseMapping.documented_information_id == Record.source_document_id,
            )
            .where(Record.org_id == org_id, ClauseMapping.clause_id.in_(clause_ids))
        )
    ).all()
    return set(leg_a) | set(leg_b)


async def _process_candidate_ids(
    session: AsyncSession, org_id: uuid.UUID, process_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """PROCESS scope: UNION of (records evidence-for the process) AND (records under a
    process-linked source document) AND (source-less correction successors that inherit a selected
    process via ``correction_of`` — so a corrected record stays in the PROCESS evidence pack exactly
    as it stays visible at ``/records``; the Codex CX-2 finding)."""
    if not process_ids:
        return set()
    leg_a = (
        await session.scalars(
            select(EvidenceForLink.record_id).where(
                EvidenceForLink.org_id == org_id,
                EvidenceForLink.target_type == EvidenceForTargetType.PROCESS,
                EvidenceForLink.target_id.in_(process_ids),
            )
        )
    ).all()
    leg_b = (
        await session.scalars(
            select(Record.id)
            .join(ProcessLink, ProcessLink.documented_information_id == Record.source_document_id)
            .where(Record.org_id == org_id, ProcessLink.process_id.in_(process_ids))
        )
    ).all()
    base = set(leg_a) | set(leg_b)
    # CX-2: also include source-LESS correction successors that INHERIT a selected process via
    # ``correction_of`` (matching ``records_repo.record_process_ids_effective`` / the read gate). A
    # successor with its OWN binding is already selected by its own leg; only the empty-own ones
    # inherit, and the walk stops at any OWNED successor (its successors inherit from IT, not from a
    # selected process). Acyclic chain → terminates; ``fresh - base`` is the cycle backstop.
    frontier = set(base)
    while frontier:
        successors = set(
            (
                await session.scalars(
                    select(Record.id).where(
                        Record.org_id == org_id, Record.correction_of.in_(frontier)
                    )
                )
            ).all()
        )
        fresh = successors - base
        if not fresh:
            break
        # "owned" == a NON-EMPTY own union, matching record_process_ids exactly (leg A: a PROCESS
        # evidence link; leg B: a source doc that HAS a process link). A source doc with NO process
        # link leaves the own union empty, so that successor still inherits (the Codex round-3
        # finding — ``source_document_id IS NOT NULL`` was too coarse).
        owned = set(
            (
                await session.scalars(
                    select(Record.id).where(
                        Record.id.in_(fresh),
                        or_(
                            Record.id.in_(
                                select(EvidenceForLink.record_id).where(
                                    EvidenceForLink.record_id.in_(fresh),
                                    EvidenceForLink.target_type == EvidenceForTargetType.PROCESS,
                                )
                            ),
                            Record.source_document_id.in_(
                                select(ProcessLink.documented_information_id)
                            ),
                        ),
                    )
                )
            ).all()
        )
        inheriting = fresh - owned
        if not inheriting:
            break
        base |= inheriting
        frontier = inheriting
    return base


async def _finding_candidate_ids(
    session: AsyncSession, org_id: uuid.UUID, finding_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """FINDING scope: the records linked AS EVIDENCE to the finding(s) — one leg (a finding is a
    record subtype, never a source document of other records, so no clause-mapping leg). The finding
    itself is NOT a candidate record (no evidence_blob → no ZIP bytes); its fields live in the
    synthesized dossier (build_dossier)."""
    if not finding_ids:
        return set()
    rows = (
        await session.scalars(
            select(EvidenceForLink.record_id).where(
                EvidenceForLink.org_id == org_id,
                EvidenceForLink.target_type == EvidenceForTargetType.FINDING,
                EvidenceForLink.target_id.in_(finding_ids),
            )
        )
    ).all()
    return set(rows)


async def _capa_candidate_ids(
    session: AsyncSession, org_id: uuid.UUID, capa_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """CAPA scope: the records linked AS EVIDENCE to ANY of the CAPA's stages (Implement-completion
    + Verify-effectiveness evidence). Evidence attaches to ``capa_stage`` rows, NEVER to the CAPA
    record, so two legs: stage ids of the CAPA(s) → records evidence-for those stages. The CAPA
    itself (and its origin finding) are NOT candidate records — they live in the dossier."""
    if not capa_ids:
        return set()
    stage_ids = (
        await session.scalars(
            select(CapaStage.id).where(CapaStage.org_id == org_id, CapaStage.capa_id.in_(capa_ids))
        )
    ).all()
    if not stage_ids:
        return set()
    rows = (
        await session.scalars(
            select(EvidenceForLink.record_id).where(
                EvidenceForLink.org_id == org_id,
                EvidenceForLink.target_type == EvidenceForTargetType.CAPA_STAGE,
                EvidenceForLink.target_id.in_(list(stage_ids)),
            )
        )
    ).all()
    return set(rows)


async def resolve_candidates(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    scope_kind: str,
    scope_ids: list[uuid.UUID],
    period_start: datetime.date | None,
    period_end: datetime.date | None,
) -> list[tuple[Record, DocumentedInformation]]:
    """Resolve the in-scope record candidates (Record ⨝ base), DATE overlay applied on
    ``captured_at`` (the cast-to-date window is inclusive on both ends). Dispatch is explicit +
    fail-closed — a new/unknown scope kind raises rather than silently running a wrong leg."""
    if scope_kind == "CLAUSE":
        ids = await _clause_candidate_ids(session, org_id, scope_ids)
    elif scope_kind == "PROCESS":
        ids = await _process_candidate_ids(session, org_id, scope_ids)
    elif scope_kind == "FINDING":
        ids = await _finding_candidate_ids(session, org_id, scope_ids)
    elif scope_kind == "CAPA":
        ids = await _capa_candidate_ids(session, org_id, scope_ids)
    else:  # pragma: no cover - fail-closed; the API/enum reject an unknown kind first
        raise ValueError(f"unknown pack scope_kind: {scope_kind}")
    if not ids:
        return []
    stmt = (
        select(Record, DocumentedInformation)
        .join(DocumentedInformation, Record.id == DocumentedInformation.id)
        .where(Record.org_id == org_id, Record.id.in_(ids))
    )
    if period_start is not None:
        stmt = stmt.where(cast(Record.captured_at, Date) >= period_start)
    if period_end is not None:
        stmt = stmt.where(cast(Record.captured_at, Date) <= period_end)
    rows = (await session.execute(stmt.order_by(asc(Record.captured_at)))).all()
    return [(r, d) for r, d in rows]


# ``record_process_ids`` moved to ``services/records/repository.py`` (the ONE source of truth,
# shared with the records read gate — S-records-R). ``packs/service.classify_candidates`` calls
# ``records_repo.record_process_ids_effective`` directly.


async def has_destroy_tombstone(session: AsyncSession, record_id: uuid.UUID) -> bool:
    """``True`` if the record's evidence was physically destroyed (a DESTROY / WORM-destroy
    disposition tombstone) — the R28 genuine-absence signal (NOT "no evidence_blob rows", which a
    valid form-only record also has)."""
    count = await session.scalar(
        select(func.count())
        .select_from(DispositionEvent)
        .where(
            DispositionEvent.record_id == record_id,
            or_(
                DispositionEvent.is_worm_destroy.is_(True),
                DispositionEvent.action == DispositionAction.DESTROY,
            ),
        )
    )
    return bool(count)


def _pack_subject_record_ids(pack: EvidencePack) -> list[uuid.UUID]:
    """The FINDING/CAPA subject ids of a pack — which ARE record ids (``audit_finding``/``capa`` are
    shared-PK RECORD subtypes), so a destroyed subject is a disposition tombstone on that record id.
    Read from ``scope_selector``: the subject is a *dossier* subject, never materialised as a
    ``pack_item`` row (``services/packs/build.py``). CLAUSE/PROCESS packs carry no subject dossier →
    no subject records. Keys mirror ``service._SCOPE_SELECTOR_KEY`` (which this lower layer cannot
    import without a cycle)."""
    if pack.scope_kind is PackScopeKind.FINDING:
        key = "finding_ids"
    elif pack.scope_kind is PackScopeKind.CAPA:
        key = "capa_ids"
    else:
        return []
    raw = pack.scope_selector.get(key) if isinstance(pack.scope_selector, dict) else None
    ids: list[uuid.UUID] = []
    for value in raw if isinstance(raw, list) else []:
        try:
            ids.append(uuid.UUID(str(value)))
        except (ValueError, TypeError):  # pragma: no cover - validated at create time
            continue
    return ids


async def _capa_origin_finding_ids(
    session: AsyncSession, capa_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """The non-null origin-finding ids of the given CAPAs. A CAPA dossier embeds its origin
    finding's type/severity/summary/identifier (``dossier._capa_subject``), and the origin finding
    is a shared-PK record, so a DESTROY on it must fail-close the serve gate too."""
    if not capa_ids:
        return []
    rows = await session.scalars(
        select(Capa.origin_finding_id).where(
            Capa.id.in_(capa_ids), Capa.origin_finding_id.is_not(None)
        )
    )
    return [r for r in rows.all() if r is not None]


async def _finding_linked_capa_ids(
    session: AsyncSession, finding_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """The non-null auto-CAPA ids of the given findings — the mirror of
    ``_capa_origin_finding_ids``: a finding dossier embeds its linked auto-CAPA's identifier +
    close_state (``dossier._finding_subject``), also a shared-PK record."""
    if not finding_ids:
        return []
    rows = await session.scalars(
        select(AuditFinding.auto_capa_id).where(
            AuditFinding.id.in_(finding_ids), AuditFinding.auto_capa_id.is_not(None)
        )
    )
    return [r for r in rows.all() if r is not None]


async def _pack_embedded_record_ids(session: AsyncSession, pack: EvidencePack) -> list[uuid.UUID]:
    """Every record whose metadata/narrative is baked into the sealed pack but is NOT an INCLUDED
    ``pack_item`` member — so a DESTROY tombstone on any must also fail-close the serve gate:

    * the FINDING/CAPA scope **subjects** (dossier subjects; shared-PK records),
    * a CAPA subject's **origin finding** / a FINDING subject's **linked auto-CAPA** — the
      cross-reference each dossier embeds (also shared-PK records), and
    * the pack's own registered EVIDENCE **record** (``pack_record_id`` — the sealed ZIP-as-record;
      an R27 destroy of it purges the ZIP blob but leaves the portfolio serving).

    The CAPA-stage / finding evidence records ARE INCLUDED ``pack_item`` members (the member join
    covers them), so they are not repeated here."""
    ids = _pack_subject_record_ids(pack)
    if ids:
        if pack.scope_kind is PackScopeKind.CAPA:
            ids = ids + await _capa_origin_finding_ids(session, ids)
        elif pack.scope_kind is PackScopeKind.FINDING:
            ids = ids + await _finding_linked_capa_ids(session, ids)
    if pack.pack_record_id is not None:
        ids = [*ids, pack.pack_record_id]
    return ids


async def _count_destroy_tombstones(session: AsyncSession, record_ids: list[uuid.UUID]) -> int:
    """Count DESTROY / WORM-destroy disposition tombstones over a set of record ids."""
    if not record_ids:
        return 0
    return int(
        await session.scalar(
            select(func.count())
            .select_from(DispositionEvent)
            .where(
                DispositionEvent.record_id.in_(record_ids),
                or_(
                    DispositionEvent.is_worm_destroy.is_(True),
                    DispositionEvent.action == DispositionAction.DESTROY,
                ),
            )
        )
        or 0
    )


async def pack_has_destroyed_member(session: AsyncSession, pack: EvidencePack) -> bool:
    """``True`` if ANY record whose bytes/metadata are baked into this sealed pack was LATER
    physically destroyed (a DESTROY / WORM-destroy disposition tombstone). Two populations:

    * INCLUDED ``pack_item`` RECORD members — the evidence bytes in the ZIP / portfolio, and
    * every dossier-embedded / derived record (``_pack_embedded_record_ids``): the FINDING/CAPA
      subjects, their embedded origin-finding / linked-CAPA cross-reference, and the pack's own
      registered EVIDENCE record — none of which are ``pack_item`` rows.

    Serve paths use this to fail-closed AFTER the seal — a record destroyed post-seal must not stay
    reachable via the pack's cached ZIP / portfolio (their bytes/narrative are baked into the sealed
    artifacts, so delivery is gated here). Records destroyed BEFORE the seal are already
    EXCLUDED_ABSENCE / refused at build time, so only INCLUDED members + the embedded set matter."""
    member_count = await session.scalar(
        select(func.count())
        .select_from(PackItem)
        .join(DispositionEvent, DispositionEvent.record_id == PackItem.record_id)
        .where(
            PackItem.pack_id == pack.id,
            PackItem.item_type == PackItemType.RECORD,
            PackItem.inclusion_status == PackInclusionStatus.INCLUDED,
            or_(
                DispositionEvent.is_worm_destroy.is_(True),
                DispositionEvent.action == DispositionAction.DESTROY,
            ),
        )
    )
    if member_count:
        return True
    embedded_ids = await _pack_embedded_record_ids(session, pack)
    return await _count_destroy_tombstones(session, embedded_ids) > 0


async def process_clause_ids(
    session: AsyncSession, org_id: uuid.UUID, process_ids: list[uuid.UUID]
) -> set[str]:
    """The clauses transitively in scope for a PROCESS pack: process → process_link documents →
    those documents' clause_mappings. Used to scope the gap report (there is no direct
    process→clause edge in the model)."""
    if not process_ids:
        return set()
    doc_subq = select(ProcessLink.documented_information_id).where(
        ProcessLink.org_id == org_id, ProcessLink.process_id.in_(process_ids)
    )
    clause_ids = (
        await session.scalars(
            select(ClauseMapping.clause_id).where(
                ClauseMapping.org_id == org_id,
                ClauseMapping.documented_information_id.in_(doc_subq),
            )
        )
    ).all()
    return {str(c) for c in clause_ids}


async def get_document_versions(
    session: AsyncSession, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, DocumentVersion]:
    if not version_ids:
        return {}
    rows = (
        await session.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(version_ids)))
    ).all()
    return {v.id: v for v in rows}


async def get_records_with_base(
    session: AsyncSession, record_ids: list[uuid.UUID]
) -> list[tuple[Record, DocumentedInformation]]:
    """Record ⨝ its shared-PK base, for the portfolio's traceability index (Stage 2)."""
    if not record_ids:
        return []
    rows = (
        await session.execute(
            select(Record, DocumentedInformation)
            .join(DocumentedInformation, Record.id == DocumentedInformation.id)
            .where(Record.id.in_(record_ids))
            .order_by(asc(Record.captured_at))
        )
    ).all()
    return [(r, d) for r, d in rows]


async def get_base_docs(
    session: AsyncSession, doc_ids: list[uuid.UUID]
) -> dict[uuid.UUID, DocumentedInformation]:
    """The governing documents of a set of pinned versions (for the portfolio section headers)."""
    if not doc_ids:
        return {}
    rows = (
        await session.scalars(
            select(DocumentedInformation).where(DocumentedInformation.id.in_(doc_ids))
        )
    ).all()
    return {d.id: d for d in rows}


# --- share-link CRUD (S-pack-2 external delivery, doc 06 §7.4) ---------------------------


async def get_share_link(
    session: AsyncSession, link_id: uuid.UUID, *, for_update: bool = False
) -> PackShareLink | None:
    """Load a share link by its id (the id rides in the verified token payload). ``for_update``
    re-checks the revocation state under a row lock for the revoke transition."""
    if for_update:
        return (
            await session.execute(
                select(PackShareLink).where(PackShareLink.id == link_id).with_for_update()
            )
        ).scalar_one_or_none()
    return await session.get(PackShareLink, link_id)


async def list_share_links(session: AsyncSession, pack_id: uuid.UUID) -> list[PackShareLink]:
    return list(
        (
            await session.execute(
                select(PackShareLink)
                .where(PackShareLink.pack_id == pack_id)
                .order_by(desc(PackShareLink.created_at))
            )
        )
        .scalars()
        .all()
    )


# --- finding/CAPA scope validation + dossier inputs (S-aud-capa-pack, doc 06 §7.1) -------


async def get_finding(session: AsyncSession, finding_id: uuid.UUID) -> AuditFinding | None:
    return await session.get(AuditFinding, finding_id)


async def get_capa(session: AsyncSession, capa_id: uuid.UUID) -> Capa | None:
    return await session.get(Capa, capa_id)


async def get_audit(session: AsyncSession, audit_id: uuid.UUID) -> Audit | None:
    return await session.get(Audit, audit_id)


async def list_capa_stages(session: AsyncSession, capa_id: uuid.UUID) -> list[CapaStage]:
    """A CAPA's append-only stage trail, chronological (the dossier narrative spine)."""
    return list(
        (
            await session.execute(
                select(CapaStage)
                .where(CapaStage.capa_id == capa_id)
                .order_by(asc(CapaStage.created_at))
            )
        )
        .scalars()
        .all()
    )


async def signature_events_by_id(
    session: AsyncSession, sig_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SignatureEvent]:
    """The signature_event rows behind signed capa_stage blocks (ActionPlan=approval/Verify=verify),
    keyed by id — for the dossier's per-stage e-signature metadata (capa_stage targets only)."""
    if not sig_ids:
        return {}
    rows = (
        await session.scalars(
            select(SignatureEvent).where(
                SignatureEvent.id.in_(sig_ids),
                SignatureEvent.signed_object_type == SignedObjectType.capa_stage,
            )
        )
    ).all()
    return {s.id: s for s in rows}


async def users_by_ids(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, AppUser]:
    """The AppUser rows for a set of signer/creator ids — projected to {user_id, display_name} by
    the dossier serializer (the PII boundary; email/keycloak_subject never leave it)."""
    if not user_ids:
        return {}
    rows = (await session.scalars(select(AppUser).where(AppUser.id.in_(user_ids)))).all()
    return {u.id: u for u in rows}


async def evidence_records_for_targets(
    session: AsyncSession,
    org_id: uuid.UUID,
    target_type: EvidenceForTargetType,
    target_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[tuple[uuid.UUID, str | None]]]:
    """Per ``target_id`` (a finding id or a capa_stage id), the records linked AS EVIDENCE to it,
    each with its human identifier — for the dossier's per-stage / per-finding evidence list. The
    serializer sorts each group by record id for a stable seal."""
    if not target_ids:
        return {}
    rows = (
        await session.execute(
            select(
                EvidenceForLink.target_id,
                EvidenceForLink.record_id,
                DocumentedInformation.identifier,
            )
            .join(DocumentedInformation, DocumentedInformation.id == EvidenceForLink.record_id)
            .where(
                EvidenceForLink.org_id == org_id,
                EvidenceForLink.target_type == target_type,
                EvidenceForLink.target_id.in_(target_ids),
            )
        )
    ).all()
    out: dict[uuid.UUID, list[tuple[uuid.UUID, str | None]]] = {}
    for target_id, record_id, identifier in rows:
        out.setdefault(target_id, []).append((record_id, identifier))
    return out
