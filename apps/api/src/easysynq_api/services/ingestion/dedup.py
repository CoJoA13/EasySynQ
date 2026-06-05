"""Stage 4 — Duplicate / near-dup / version-family detection (slice S-ing-3, doc 09 §7).

``run_dedup`` is the detached dedup body. It transitions ``Classified → Deduping``, loads the run's
included files + their extract/classification context (one query), runs the **§7.1 cascade** in
memory (EXACT sha256 → NEAR MinHash over the survivors → version FAMILY over what remains — so a
file is claimed by at most one), picks each cluster/family's canonical by the **§7.2 TOTAL order**
(``version_family.order_members``), then **atomically replaces** the run's clusters/families
(DELETE-then-INSERT in one txn) and transitions ``Deduping → Proposing``, chaining to Stage 5. It is
idempotent (status-guarded re-delivery + whole-run replace) and fail-closed (classify precedent).
The source-root lock is held continuously (NOT released here) and **heartbeated from inside the
near-dup compute** (the detector calls back) so a long build is never mis-reaped. It writes NOTHING
to the vault.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._ingestion_enums import ImportDupeMethod, ImportRunStatus
from ...db.models.import_classification import ImportClassification
from ...db.models.import_extract import ImportExtract
from ...db.models.import_file import ImportFile
from ...domain.ingestion.normalize import (
    extract_doc_code,
    is_obsolete_filename,
    normalize_base_name,
    parse_version_marker,
)
from ...domain.ingestion.version_family import FileForPick, order_members
from ..similarity import NearDupItem, get_dedup_detector
from . import locks
from . import repository as repo
from .service import _fail_run, emit_import_event_system

logger = logging.getLogger("easysynq.ingestion.dedup")

_FileCtx = tuple[ImportFile, ImportExtract | None, ImportClassification | None]


def _file_for_pick(f: ImportFile, ext: ImportExtract | None) -> FileForPick:
    embedded_modified = None
    if ext is not None and ext.embedded_props:
        raw = ext.embedded_props.get("modified")
        embedded_modified = raw if isinstance(raw, str) else None
    version, status = parse_version_marker(f.filename)
    return FileForPick(
        file_id=f.id,
        filename=f.filename,
        rel_path=f.rel_path,
        ext=f.ext,
        mtime=f.mtime,
        embedded_modified=embedded_modified,
        version=version,
        status_rank=status,
    )


async def _compute(
    rows: list[_FileCtx],
    detector_threshold: float,
    *,
    hb: object,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """The pure §7.1 cascade over a run's included files → (clusters, families). ``hb`` is an async
    heartbeat the near-dup detector calls back on its own cadence."""
    ctx_by_id: dict[uuid.UUID, _FileCtx] = {f.id: (f, e, c) for f, e, c in rows}

    def pick(file_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        members = [_file_for_pick(ctx_by_id[i][0], ctx_by_id[i][1]) for i in file_ids]
        return [m.file_id for m in order_members(members)]

    # 1. EXACT — group included files with a sha256 by content; >1 = a cluster.
    by_sha: dict[str, list[uuid.UUID]] = defaultdict(list)
    for f, _e, _c in rows:
        if f.sha256 is not None:
            by_sha[f.sha256].append(f.id)
    clusters: list[dict[str, object]] = []
    redundant: set[uuid.UUID] = set()
    for group in by_sha.values():
        if len(group) < 2:
            continue
        ordered = pick(group)
        clusters.append(
            {
                "method": ImportDupeMethod.EXACT,
                "member_file_ids": ordered,
                "canonical_file_id": ordered[0],
                "jaccard": 1.0,
                "evidence": {},
            }
        )
        redundant.update(ordered[1:])

    # 2. NEAR — MinHash over the survivors (exact redundants removed) WITH extract text.
    items = [
        NearDupItem(file_id=f.id, text=e.full_text)
        for f, e, _c in rows
        if f.id not in redundant and e is not None and e.full_text
    ]
    near = await get_dedup_detector().detect_near(items, threshold=detector_threshold, heartbeat=hb)  # type: ignore[arg-type]
    truncated_by_id = {f.id: bool(e is not None and e.text_truncated) for f, e, _c in rows}
    for cluster in near:
        ordered = pick(list(cluster.member_file_ids))
        truncated = any(truncated_by_id.get(i) for i in ordered)
        clusters.append(
            {
                "method": ImportDupeMethod.NEAR,
                "member_file_ids": ordered,
                "canonical_file_id": ordered[0],
                "jaccard": cluster.jaccard,
                "evidence": {"truncated_comparison": True} if truncated else {},
            }
        )
        redundant.update(ordered[1:])

    # 3. VERSION FAMILY — over what remains (no exact/near redundants), grouped by doc-code else
    #    normalized base-name; ≥2 members.
    fam: dict[str, list[uuid.UUID]] = defaultdict(list)
    fam_doc_code: dict[str, str | None] = {}
    for f, e, _c in rows:
        if f.id in redundant:
            continue
        doc_code = extract_doc_code(f.filename, e.header_block if e is not None else None)
        key = doc_code or normalize_base_name(f.filename)
        if not key:
            continue
        fam[key].append(f.id)
        if doc_code and key not in fam_doc_code:
            fam_doc_code[key] = doc_code
    families: list[dict[str, object]] = []
    for key, file_ids in fam.items():
        if len(file_ids) < 2:
            continue
        ordered = pick(file_ids)
        obsolete = [str(i) for i in ordered if is_obsolete_filename(ctx_by_id[i][0].filename)]
        rep = ctx_by_id[ordered[0]][0]
        families.append(
            {
                "family_key": key,
                "base_name": normalize_base_name(rep.filename),
                "doc_code": fam_doc_code.get(key),
                "ordered_member_file_ids": ordered,
                "effective_file_id": ordered[0],
                "evidence": {"obsolete_candidates": obsolete} if obsolete else {},
            }
        )
    return clusters, families


async def run_dedup(session: AsyncSession, run_id: uuid.UUID) -> None:
    settings = get_settings()
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.status not in (ImportRunStatus.CLASSIFIED, ImportRunStatus.DEDUPING):
        await session.rollback()  # re-delivery of a terminal/absent/not-yet-classified run → no-op
        return
    src_hash = run.source_root_hash
    token = run.lock_token
    org_id = run.org_id
    version = run.classifier_version
    if run.status is ImportRunStatus.CLASSIFIED:
        run.status = ImportRunStatus.DEDUPING
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Classified"},
            after={"status": "Deduping"},
        )
    await session.commit()

    async def _hb() -> None:
        if token:
            await locks.heartbeat(src_hash, token, ttl=settings.import_lock_ttl_seconds)

    try:
        rows = list(await repo.included_files_with_context(session, run_id, version))
        await _hb()
        # A cancel/reaper while we loaded → stop before recomputing (the classify stop-check).
        if await repo.get_status(session, run_id) is not ImportRunStatus.DEDUPING:
            if token:
                await locks.release(src_hash, token)
            return
        clusters, families = await _compute(rows, settings.import_near_dup_threshold, hb=_hb)
        # Atomic full-replace; committed together with the Proposing transition below.
        await repo.replace_dedup(
            session, run_id, org_id=org_id, clusters=clusters, families=families
        )

        final = await repo.get_run(session, run_id, for_update=True)
        if final is None or final.status is not ImportRunStatus.DEDUPING:
            await session.rollback()  # a late cancel won → discard the staged replace
            if final is not None and final.status is ImportRunStatus.CANCELLED and token:
                await locks.release(src_hash, token)
            return
        final.status = ImportRunStatus.PROPOSING
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Deduping"},
            after={"status": "Proposing"},
        )
        await session.commit()
        _enqueue_propose(run_id)  # chain to Stage 5 (lock still held)
    except Exception as exc:
        await session.rollback()
        await _fail_run(session, run_id, repr(exc)[:500])
        if token:
            await locks.release(src_hash, token)
        raise


def _enqueue_propose(run_id: uuid.UUID) -> None:
    """Best-effort chain to Stage 5 AFTER the Proposing commit (the scan→classify precedent)."""
    from ...tasks.ingestion import propose_source

    try:
        propose_source.delay(str(run_id))
    except Exception:  # noqa: BLE001 — best-effort; the reaper backstops a dropped enqueue
        logger.warning(
            "ingestion.propose.enqueue_failed", extra={"extra_fields": {"run_id": str(run_id)}}
        )
