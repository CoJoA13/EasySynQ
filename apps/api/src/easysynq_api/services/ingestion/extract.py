"""Stage 2 — Extraction worker body (slice S-ing-2, doc 09 §5).

``run_extract`` is the detached extract body: it transitions ``Scanned → Extracting``, batches over
the run's included files that have no ``import_extract`` row yet (the resume key), pulls each staged
copy's bytes, runs the Tika ``-full`` extractor (§5.2 OCR ladder), upserts the result, checkpoints +
heartbeats per batch, and on completion transitions ``→ Classifying`` and chains to Stage 3. It is
**idempotent** (status-guarded re-delivery + per-(run,file) upsert) and **fail-closed** (a per-file
extract failure is recorded, never fails the run — §5.3; a crash leaves the run resumable, the
lock-liveness reaper FAILs a wedged worker). The source-root lock is held continuously (NOT
released here) — only Classify releases it at the Classified rest, or a cancel/fail does.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._ingestion_enums import ImportExtractStatus, ImportRunStatus
from ...db.models.import_file import ImportFile
from ...domain.ingestion.extractor import ExtractInput, ExtractResult
from . import locks, storage
from . import repository as repo
from .extractor_tika import TikaExtractorProvider
from .service import _fail_run, emit_import_event_system

logger = logging.getLogger("easysynq.ingestion.extract")

_STAGE_START = (ImportRunStatus.SCANNED, ImportRunStatus.EXTRACTING)


def _extract_status(result: ExtractResult) -> ImportExtractStatus:
    if result.failed:
        return ImportExtractStatus.FAILED
    # EMPTY before OCR: an OCR pass that yielded no text is EMPTY, not OCR-with-no-content.
    if not result.full_text:
        return ImportExtractStatus.EMPTY
    if result.ocr_used:
        return ImportExtractStatus.OCR
    return ImportExtractStatus.EXTRACTED


def _cap_text(text: str | None, max_bytes: int) -> tuple[str | None, bool]:
    """Cap ``full_text`` to ``max_bytes`` (UTF-8), preserving valid codepoints. Returns
    ``(text, truncated)``."""
    if text is None:
        return None, False
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", "ignore"), True


async def _extract_one(
    extractor: TikaExtractorProvider, f: ImportFile, *, ocr_enabled: bool, ocr_language: str
) -> ExtractResult:
    if f.sha256 is None:  # an included candidate with no staged bytes (defensive) — nothing to read
        return ExtractResult(failed=True, error="no_staged_bytes")
    try:
        data = await storage.fetch_staged_bytes(f.sha256)
    except Exception as exc:  # noqa: BLE001 — a missing/unreadable staged object never fails the run
        return ExtractResult(failed=True, error=f"fetch_failed: {repr(exc)[:300]}")
    meta = ExtractInput(
        rel_path=f.rel_path,
        filename=f.filename,
        ext=f.ext,
        mime_type=f.mime_type,
        size_bytes=f.size_bytes,
    )
    return await extractor.extract(data, meta, ocr_enabled=ocr_enabled, ocr_language=ocr_language)


async def run_extract(session: AsyncSession, run_id: uuid.UUID) -> None:
    settings = get_settings()
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.status not in _STAGE_START:
        await session.rollback()  # re-delivery of a terminal/absent/not-yet-scanned run → no-op
        return
    src_hash = run.source_root_hash
    token = run.lock_token
    org_id = run.org_id
    ocr_enabled = run.ocr_enabled
    if run.status is ImportRunStatus.SCANNED:
        run.status = ImportRunStatus.EXTRACTING
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Scanned"},
            after={"status": "Extracting"},
        )
    await session.commit()

    extractor = TikaExtractorProvider()
    try:
        while True:
            batch = await repo.files_pending_extract(
                session, run_id, limit=settings.import_extract_batch_size
            )
            if not batch:
                break
            for f in batch:
                result = await _extract_one(
                    extractor, f, ocr_enabled=ocr_enabled, ocr_language=settings.import_ocr_language
                )
                full_text, truncated = _cap_text(
                    result.full_text, settings.import_max_extract_text_bytes
                )
                await repo.upsert_extract(
                    session,
                    org_id=org_id,
                    run_id=run_id,
                    file_id=f.id,
                    full_text=full_text,
                    text_truncated=truncated,
                    header_block=result.header_block,
                    embedded_props=dict(result.embedded_props) or None,
                    language=result.language,
                    structure_hints=dict(result.structure_hints) or None,
                    ocr_used=result.ocr_used,
                    ocr_confidence=result.ocr_confidence,
                    char_count=result.char_count,
                    page_count=result.page_count,
                    status=_extract_status(result),
                    error=result.error,
                    extractor_version=result.extractor_version,
                )
            await session.commit()
            if token:
                await locks.heartbeat(src_hash, token, ttl=settings.import_lock_ttl_seconds)
            # Stop on ANY status change away from Extracting — a cancel, OR a reaper that FAILED a
            # run whose lock lapsed mid-batch (don't keep writing to a no-longer-active run).
            if await repo.get_status(session, run_id) is not ImportRunStatus.EXTRACTING:
                if token:
                    await locks.release(src_hash, token)
                return

        final = await repo.get_run(session, run_id, for_update=True)
        if final is None or final.status is not ImportRunStatus.EXTRACTING:
            await session.commit()  # a late cancel won the race — respect it
            if final is not None and final.status is ImportRunStatus.CANCELLED and token:
                await locks.release(src_hash, token)
            return
        final.status = ImportRunStatus.CLASSIFYING
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Extracting"},
            after={"status": "Classifying"},
        )
        await session.commit()
        _enqueue_classify(run_id)  # chain to Stage 3 (lock still held)
    except Exception as exc:
        await session.rollback()
        await _fail_run(session, run_id, repr(exc)[:500])
        if token:
            await locks.release(src_hash, token)
        raise


def _enqueue_classify(run_id: uuid.UUID) -> None:
    """Best-effort chain to Stage 3 AFTER the Classifying commit (the scan→extract precedent)."""
    from ...tasks.ingestion import classify_source

    try:
        classify_source.delay(str(run_id))
    except Exception:  # noqa: BLE001 — best-effort; the reaper backstops a dropped enqueue
        logger.warning(
            "ingestion.classify.enqueue_failed", extra={"extra_fields": {"run_id": str(run_id)}}
        )
