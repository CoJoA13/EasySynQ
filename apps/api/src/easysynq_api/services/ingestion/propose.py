"""Stage 5 — Proposed target structure & numbering (slice S-ing-3, doc 09 §8).

``run_propose`` is the detached propose body (entered already in ``Proposing``, set by dedup's end —
the classify-enters-CLASSIFYING precedent). It computes ONE ``import_proposal_node`` per keep-item
(an included file that is not a non-canonical duplicate and not a non-effective family member;
redundant/superseded files are represented by their cluster/family membership, so nothing silently
vanishes — §11.3), atomically replaces the run's nodes, merges the namespaced dedup + proposal
counts into ``run.counts``, transitions ``Proposing → Proposed`` (the new resting terminal awaiting
S-ing-4 review), and **releases the source-root lock** (the end of the continuous scan→…→propose
hold). It writes NOTHING to the vault and **never** consumes a numbering sequence (real
``{TYPE}-{AREA}-{SEQ}`` allocation is the commit slice, S-ing-5). ``kind`` is NEVER confirmed (R10).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._ingestion_enums import ImportKind, ImportRunStatus
from ...db.models.clause import Clause
from ...db.models.documented_information import DocumentedInformation
from ...db.models.framework import Framework
from ...db.models.import_classification import ImportClassification
from ...db.models.import_extract import ImportExtract
from ...domain.ingestion.normalize import extract_doc_code
from ..vault.mirror import ClauseRef, fetch_top_words, ia_placement_dir
from . import locks
from . import repository as repo
from .service import _fail_run, _now, emit_import_event_system

logger = logging.getLogger("easysynq.ingestion.propose")

_RECORDS_DIR = "Records"
_UNMAPPED_DIR = "_unmapped"
_OWNER_HINT_MAX = 255


async def _clause_ref_map(session: AsyncSession, org_id: uuid.UUID) -> dict[str, ClauseRef]:
    """``{clause_number: ClauseRef}`` for the org's clauses — so a classification's clause CODES
    resolve to the mirror placement (reusing the exact mirror IA layout, doc 09 §8.1)."""
    rows = (
        await session.execute(
            select(
                Clause.number,
                Clause.pdca_phase,
                Clause.title,
                Clause.is_mandatory_star,
                Clause.framework_id,
            )
            .join(Framework, Framework.id == Clause.framework_id)
            .where(Framework.org_id == org_id)
        )
    ).all()
    return {
        number: ClauseRef(
            number=number,
            pdca_phase=phase.value if hasattr(phase, "value") else str(phase),
            title=title,
            is_mandatory_star=star,
            framework_id=framework_id,
        )
        for number, phase, title, star, framework_id in rows
    }


def _target_ia_path(
    cls: ImportClassification | None,
    clause_map: dict[str, ClauseRef],
    top_words: dict[tuple[uuid.UUID, str], str],
) -> str:
    """The §8.1 proposed mirror home: ``Records`` for a RECORD, the ``{PHASE}/{NN}-{Word}`` clause
    placement for a DOCUMENT (the mirror layout, so it byte-matches the eventual mirror), else
    ``_unmapped``."""
    if cls is not None and cls.kind is ImportKind.RECORD:
        return _RECORDS_DIR
    refs = [clause_map[code] for code in (cls.clause_numbers if cls else []) if code in clause_map]
    return ia_placement_dir(refs, top_words) if refs else _UNMAPPED_DIR


def _owner_hint(ext: ImportExtract | None) -> tuple[str | None, str | None]:
    if ext is not None and ext.embedded_props:
        author = ext.embedded_props.get("author")
        if isinstance(author, str) and author.strip():
            return author.strip()[:_OWNER_HINT_MAX], "embedded_author"
    return None, None


async def rebuild_proposals(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    org_id: uuid.UUID,
    version: str | None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Re-derive the keep-set + ``import_proposal_node`` rows from the run's CURRENT clusters /
    families + included files, atomically replace them, and return the namespaced proposal counts.
    **Pure w.r.t. run state** (no status/lock transition) so BOTH ``run_propose`` (worker) and the
    S-ing-4 review merge/split ops can call it after a structural change — it reads the PERSISTED
    clusters/families, so it reflects any mutation applied in the same txn (the keep-set uses
    each cluster's ``canonical_file_id`` + each family's ``effective_file_id``, so those MUST be
    recomputed + persisted by the caller BEFORE this call). The optional ``heartbeat`` is the
    worker's source-root-lock keep-alive (the review path passes ``None`` — review is lock-free)."""
    rows = list(await repo.included_files_with_context(session, run_id, version))
    clusters = await repo.list_dupe_clusters(session, run_id)
    families = await repo.list_version_families(session, run_id)
    if heartbeat is not None:
        await heartbeat()

    # keep-item = included MINUS (non-canonical dup members + non-effective family members), built
    # from the PERSISTED rows so it is order-independent + reproducible on re-delivery/re-derive.
    excluded: set[uuid.UUID] = set()
    for cl in clusters:
        excluded.update(m for m in cl.member_file_ids if m != cl.canonical_file_id)
    for fam in families:
        excluded.update(m for m in fam.ordered_member_file_ids if m != fam.effective_file_id)
    keep = [(f, e, c) for f, e, c in rows if f.id not in excluded]

    clause_map = await _clause_ref_map(session, org_id)
    top_words = await fetch_top_words(session)

    nodes: list[dict[str, object]] = []
    code_to_files: dict[str, list[uuid.UUID]] = {}
    for n, (f, ext, cls) in enumerate(keep):
        doc_code = extract_doc_code(f.filename, ext.header_block if ext is not None else None)
        conflict_flags: dict[str, object] = {}
        if doc_code:
            proposed_identifier: str | None = doc_code
            identifier_source: str | None = "preserved_doc_code"
            code_to_files.setdefault(doc_code, []).append(f.id)
        elif cls is not None and cls.type_code:
            proposed_identifier = f"{cls.type_code}-<new>"
            identifier_source = "suggested_default"
        else:
            proposed_identifier = None
            identifier_source = None
            conflict_flags["needs_identifier"] = True
        owner, owner_source = _owner_hint(ext)
        nodes.append(
            {
                "file_id": f.id,
                "proposed_identifier": proposed_identifier,
                "identifier_source": identifier_source,
                "target_ia_path": _target_ia_path(cls, clause_map, top_words),
                "proposed_owner": owner,
                "owner_source": owner_source,
                "conflict_flags": conflict_flags,
            }
        )
        if n % 256 == 0 and heartbeat is not None:
            await heartbeat()

    await _detect_conflicts(session, org_id, nodes, code_to_files)
    await repo.replace_proposals(session, run_id, org_id=org_id, nodes=nodes)
    return await repo.compute_proposal_counts(session, run_id)


async def run_propose(session: AsyncSession, run_id: uuid.UUID) -> None:
    settings = get_settings()
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.status is not ImportRunStatus.PROPOSING:
        await session.rollback()  # re-delivery of a terminal/absent/not-yet-deduped run → no-op
        return
    src_hash = run.source_root_hash
    token = run.lock_token
    org_id = run.org_id
    version = run.classifier_version
    await session.commit()  # release the FOR UPDATE before the (read-heavy) compute

    async def _hb() -> None:
        if token:
            await locks.heartbeat(src_hash, token, ttl=settings.import_lock_ttl_seconds)

    try:
        proposal_counts = await rebuild_proposals(
            session, run_id, org_id=org_id, version=version, heartbeat=_hb
        )
        dedup_counts = await repo.compute_dedup_counts(session, run_id)
        final = await repo.get_run(session, run_id, for_update=True)
        if final is None or final.status is not ImportRunStatus.PROPOSING:
            await session.rollback()  # a late cancel won → discard the staged proposals
            if final is not None and final.status is ImportRunStatus.CANCELLED and token:
                await locks.release(src_hash, token)
            return
        final.status = ImportRunStatus.PROPOSED
        final.counts = {**(final.counts or {}), **dedup_counts, **proposal_counts}
        final.completed_at = _now()
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Proposing"},
            after={"status": "Proposed", "counts": final.counts},
        )
        await session.commit()
        if token:
            await locks.release(src_hash, token)  # end of the continuous scan→…→propose hold
    except Exception as exc:
        await session.rollback()
        await _fail_run(session, run_id, repr(exc)[:500])
        if token:
            await locks.release(src_hash, token)
        raise


async def _detect_conflicts(
    session: AsyncSession,
    org_id: uuid.UUID,
    nodes: list[dict[str, object]],
    code_to_files: dict[str, list[uuid.UUID]],
) -> None:
    """§11.3 conflict flags over the keep-item nodes (preserved doc-codes only — the ``<new>``
    sentinels never collide). Mutates each node's ``conflict_flags`` in place."""
    preserved = list(code_to_files)
    # collision with an existing vault document identifier (or legacy_identifier).
    vault_hit: dict[str, str] = {}
    if preserved:
        rows = (
            await session.execute(
                select(
                    DocumentedInformation.id,
                    DocumentedInformation.identifier,
                    DocumentedInformation.legacy_identifier,
                ).where(
                    DocumentedInformation.org_id == org_id,
                    or_(
                        DocumentedInformation.identifier.in_(preserved),
                        DocumentedInformation.legacy_identifier.in_(preserved),
                    ),
                )
            )
        ).all()
        for doc_id, ident, legacy in rows:
            if ident in code_to_files:
                vault_hit[ident] = str(doc_id)
            if legacy in code_to_files:
                vault_hit[legacy] = str(doc_id)
    node_by_file = {n["file_id"]: n for n in nodes}
    for code, file_ids in code_to_files.items():
        dupes_within = len(file_ids) > 1
        vault_doc = vault_hit.get(code)
        for fid in file_ids:
            flags = cast(dict[str, object], node_by_file[fid]["conflict_flags"])
            if dupes_within:
                flags["duplicate_identifier_within_import"] = [
                    str(other) for other in file_ids if other != fid
                ]
            if vault_doc is not None:
                flags["collides_with_vault_doc"] = vault_doc
