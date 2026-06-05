"""Stage 3 — Classification worker body (slice S-ing-2, doc 09 §6).

``run_classify`` is the detached classify body: it batches over the run's included files with no
``import_classification`` row for the active classifier version (the resume key), builds
``FileFeatures`` from each file + its extract (or filename/path-only if the extract failed/absent —
§5.3), runs the pure ``RuleHeuristicClassifier``, upserts the scored proposal, checkpoints +
heartbeats per batch, and on completion transitions ``Classifying → Classified`` (the resting
checkpoint awaiting S-ing-4 review), merges the §4.3 classify counts, and **releases the source-root
lock** (the end of the continuous scan→extract→classify hold). Idempotent + fail-closed (the extract
precedent). **Nothing is confirmed or committed here** (R10: kind confirmation is S-ing-4).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._clause_enums import PdcaPhase
from ...db.models._ingestion_enums import (
    ImportConfidenceBand,
    ImportKind,
    ImportRunStatus,
)
from ...db.models.import_extract import ImportExtract
from ...db.models.import_file import ImportFile
from ...domain.ingestion.rule_classifier import (
    ClassificationResult,
    FileFeatures,
    RuleHeuristicClassifier,
)
from ...domain.ingestion.rule_pack import default_rule_pack
from . import locks
from . import repository as repo
from .service import _fail_run, emit_import_event_system

logger = logging.getLogger("easysynq.ingestion.classify")


def _features(f: ImportFile, ext: ImportExtract | None) -> FileFeatures:
    return FileFeatures(
        filename=f.filename,
        rel_path=f.rel_path,
        ext=f.ext,
        mime_type=f.mime_type,
        header_block=ext.header_block if ext else None,
        full_text=ext.full_text if ext else None,
        embedded_props=dict(ext.embedded_props) if ext and ext.embedded_props else {},
        structure_hints=dict(ext.structure_hints) if ext and ext.structure_hints else {},
        extract_failed=ext is None or ext.error is not None,
    )


def _to_values(result: ClassificationResult) -> dict[str, Any]:
    return {
        "kind": ImportKind(result.kind),
        "kind_conf": result.kind_conf,
        "type_code": result.type_code,
        "type_conf": result.type_conf,
        "clause_numbers": list(result.clause_numbers),
        "clause_conf": result.clause_conf,
        "process_names": list(result.process_names) or None,
        "process_conf": result.process_conf,
        "pdca_phase": PdcaPhase(result.pdca_phase) if result.pdca_phase else None,
        "band": ImportConfidenceBand(result.band),
        "ambiguous": result.ambiguous,
        "top2_margin": result.top2_margin,
        "evidence": [e.to_dict() for e in result.evidence],
    }


async def run_classify(session: AsyncSession, run_id: uuid.UUID) -> None:
    settings = get_settings()
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.status is not ImportRunStatus.CLASSIFYING:
        await session.rollback()  # re-delivery of a terminal/absent / not-yet-extracted run → no-op
        return
    src_hash = run.source_root_hash
    token = run.lock_token
    org_id = run.org_id

    classifier = RuleHeuristicClassifier(default_rule_pack())
    version = classifier.classifier_version
    # Record the version that ACTUALLY classified this run (unconditional — a create-body hint is
    # overwritten), so run.classifier_version == the import_classification rows' version. The read
    # paths (files list / detail / counts) pin to it, so a future re-classify with a new version
    # never double-counts or duplicates file rows (the §6.6 distinct-comparable-row model).
    run.classifier_version = version
    await session.commit()

    clause_pdca = await repo.clause_pdca_map(session, org_id)
    proc_names = await repo.process_names(session, org_id)
    try:
        while True:
            batch = await repo.files_pending_classify(
                session, run_id, version, limit=settings.import_classify_batch_size
            )
            if not batch:
                break
            for f, ext in batch:
                result = classifier.classify(
                    _features(f, ext), clause_pdca=clause_pdca, process_names=proc_names
                )
                await repo.upsert_classification(
                    session,
                    org_id=org_id,
                    run_id=run_id,
                    file_id=f.id,
                    classifier_version=version,
                    values=_to_values(result),
                )
            await session.commit()
            if token:
                await locks.heartbeat(src_hash, token, ttl=settings.import_lock_ttl_seconds)
            # Stop on ANY status change away from Classifying — a cancel, OR a reaper that FAILED a
            # run whose lock lapsed mid-batch (don't keep writing to a no-longer-active run).
            if await repo.get_status(session, run_id) is not ImportRunStatus.CLASSIFYING:
                if token:
                    await locks.release(src_hash, token)
                return

        classify_counts = await repo.compute_classify_counts(session, run_id, version)
        final = await repo.get_run(session, run_id, for_update=True)
        if final is None or final.status is not ImportRunStatus.CLASSIFYING:
            await session.commit()  # a late cancel won the race — respect it
            if final is not None and final.status is ImportRunStatus.CANCELLED and token:
                await locks.release(src_hash, token)
            return
        final.status = ImportRunStatus.CLASSIFIED
        final.counts = {**(final.counts or {}), **classify_counts}
        # S-ing-3: Classified is NO LONGER terminal — the pipeline chains to dedup→propose. Do NOT
        # release the lock or set completed_at here; the propose stage is now the terminal doing it.
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Classifying"},
            after={"status": "Classified", "counts": final.counts},
        )
        await session.commit()
        _enqueue_dedup(run_id)  # chain to Stage 4 (lock still held)
    except Exception as exc:
        await session.rollback()
        await _fail_run(session, run_id, repr(exc)[:500])
        if token:
            await locks.release(src_hash, token)
        raise


def _enqueue_dedup(run_id: uuid.UUID) -> None:
    """Best-effort chain to Stage 4 AFTER the Classified commit (the scan→extract precedent): run
    is committed + the lock held, so a broker blip must not fail classify — the reaper backstops a
    stranded run once its TTL lapses (operator re-runs to resume)."""
    from ...tasks.ingestion import dedup_source

    try:
        dedup_source.delay(str(run_id))
    except Exception:  # noqa: BLE001 — best-effort; the reaper backstops a dropped enqueue
        logger.warning(
            "ingestion.dedup.enqueue_failed", extra={"extra_fields": {"run_id": str(run_id)}}
        )
