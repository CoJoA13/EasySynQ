"""The ingestion use-case layer (slice S-ing-1, doc 09 §3-4).

Run lifecycle (API path) + the scan/inventory worker body. The scan is **idempotent**
(status-guarded
re-delivery + upsert + content-address dedup) and **fail-closed** (a crash leaves the run FAILED,
never
half-done), mirroring the ``services/packs`` build/reaper discipline. It writes NOTHING to the
vault.

Ingestion audit rows are written directly (object_type=import_run), BEFORE commit, so the mutation
+ its
audit commit atomically (AC#6) — the ``services/records`` ``emit_record_event`` pattern. The
worker-driven
stage transitions are *system*-actor events (the scan runs detached, with no HTTP caller); creation
and
cancel are the caller's (user) events."""

from __future__ import annotations

import datetime
import hashlib
import logging
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._ingestion_enums import ImportRunStatus
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.import_file import ImportFile
from ...db.models.import_run import ImportRun
from ...domain.ingestion.classifier import ScanFlags, classify
from ...domain.ingestion.source import FileMeta
from ...logging import request_id_var
from ...problems import ProblemException
from . import locks, storage
from . import repository as repo
from .mime import sniff_mime
from .source import FilesystemSourceProvider, resolve_confined

logger = logging.getLogger("easysynq.ingestion")

_HEAD_BYTES = 2048  # leading bytes read for mime sniff + the cheap encrypted-header probe
_CFB_MAGIC = (
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE/CFB compound file (an encrypted OOXML wrapper)
)
_OOXML_EXTS = frozenset({"docx", "xlsx", "pptx"})
_TERMINAL = (ImportRunStatus.SCANNED, ImportRunStatus.FAILED, ImportRunStatus.CANCELLED)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def emit_import_event(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    run_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Append an import-run ``audit_event`` (object_type=import_run) BEFORE commit (AC#6)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.import_run,
            object_id=run_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


def emit_import_event_system(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_type: EventType,
    run_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """A *system*-actor import-run event (actor_id NULL) — the detached scan worker / the reaper
    have
    no HTTP caller (the records ``emit_record_event_system`` precedent)."""
    session.add(
        AuditEvent(
            org_id=org_id,
            occurred_at=_now(),
            actor_id=None,
            actor_type=ActorType.system,
            event_type=event_type,
            object_type=AuditObjectType.import_run,
            object_id=run_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


# --------------------------------------------------------------------------- API path


async def create_import_run(
    session: AsyncSession,
    caller: AppUser,
    *,
    source_root: str,
    profile: str | None,
    ocr_enabled: bool,
    classifier_version: str | None,
) -> ImportRun:
    """Validate + confine ``source_root``, take the source-root lock (atomic ``SET NX`` → 409 if a
    scan
    is already active for the root), persist the run, audit, and enqueue the scan AFTER commit."""
    settings = get_settings()
    root = Path(settings.import_source_root)
    try:
        confined = resolve_confined(root, source_root)
    except ValueError as exc:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="source_root is outside the import source root",
            detail=str(exc),
        ) from exc
    if not confined.is_dir():
        raise ProblemException(
            status=422,
            code="validation_error",
            title="source_root is not an existing directory",
            detail=source_root,
        )

    src_hash = hashlib.sha256(f"{caller.org_id}|{confined}".encode()).hexdigest()
    token = await locks.acquire(src_hash, ttl=settings.import_lock_ttl_seconds)
    if token is None:
        active = await repo.active_run_for_hash(session, caller.org_id, src_hash)
        raise ProblemException(
            status=409,
            code="conflict",
            title="A scan is already in progress for this source",
            members={"active_run_id": str(active.id)} if active is not None else None,
        )

    try:
        run = ImportRun(
            org_id=caller.org_id,
            source_root=str(confined),
            source_root_hash=src_hash,
            status=ImportRunStatus.CREATED,
            lock_token=token,
            profile=profile,
            ocr_enabled=ocr_enabled,
            classifier_version=classifier_version,
            created_by=caller.id,
        )
        session.add(run)
        await session.flush()
        emit_import_event(
            session,
            caller,
            EventType.IMPORT_RUN_CREATED,
            run.id,
            after={"status": "Created", "source_root": str(confined), "profile": profile},
        )
        await session.commit()
    except Exception:
        await session.rollback()
        await locks.release(src_hash, token)  # never leak the lock on a failed create
        raise
    await session.refresh(run)

    # Enqueue AFTER commit so the worker never reads an uncommitted Created row.
    from ...tasks.ingestion import scan_source

    scan_source.delay(str(run.id))
    return run


async def get_import_run(session: AsyncSession, caller: AppUser, run_id: uuid.UUID) -> ImportRun:
    run = await repo.get_run(session, run_id)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")
    return run


async def list_import_runs(
    session: AsyncSession,
    caller: AppUser,
    *,
    status: ImportRunStatus | None = None,
    limit: int = 50,
) -> Sequence[ImportRun]:
    return await repo.list_runs(session, caller.org_id, status=status, limit=min(limit, 200))


async def list_import_files(
    session: AsyncSession,
    caller: AppUser,
    run_id: uuid.UUID,
    *,
    disposition: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[ImportRun, Sequence[ImportFile]]:
    run = await get_import_run(session, caller, run_id)  # org-scoped 404 first
    files = await repo.list_files(
        session, run_id, disposition=disposition, limit=min(limit, 200), offset=max(offset, 0)
    )
    return run, files


async def cancel_import_run(session: AsyncSession, caller: AppUser, run_id: uuid.UUID) -> ImportRun:
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")
    if run.status in _TERMINAL:
        raise ProblemException(
            status=409, code="conflict", title="Import run is already in a terminal state"
        )
    prev = run.status.value
    src_hash = run.source_root_hash
    token = run.lock_token
    run.status = ImportRunStatus.CANCELLED
    run.completed_at = _now()
    emit_import_event(
        session,
        caller,
        EventType.IMPORT_RUN_CANCELLED,
        run.id,
        before={"status": prev},
        after={"status": "Cancelled"},
    )
    await session.commit()
    if token:  # free the root immediately (CAS — no-op if a later run already re-acquired)
        await locks.release(src_hash, token)
    await session.refresh(run)
    return run


# --------------------------------------------------------------------------- worker path


async def run_scan(session: AsyncSession, run_id: uuid.UUID) -> None:
    """The detached scan body (idempotent + fail-closed). Walks the confined source root,
    classifies +
    content-address-stages included files, upserts inventory rows per (run_id, rel_path),
    checkpoints
    per batch, and flips the run Created→Scanning→Scanned (releasing the source-root lock on
    terminal)."""
    settings = get_settings()
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.status not in (ImportRunStatus.CREATED, ImportRunStatus.SCANNING):
        await session.rollback()  # re-delivery of a terminal/absent run → no-op
        return
    src_hash = run.source_root_hash
    token = run.lock_token
    source_root = run.source_root
    org_id = run.org_id
    if run.status is ImportRunStatus.CREATED:
        run.status = ImportRunStatus.SCANNING
        run.scan_started_at = _now()
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Created"},
            after={"status": "Scanning"},
        )
    await session.commit()  # release the FOR UPDATE; persist Scanning before the (long) walk

    try:
        provider = FilesystemSourceProvider(Path(source_root))
        for batch in provider.walk(batch_size=settings.import_walk_batch_size):
            for meta in batch:
                flags, sha, staged_uri, mime_type = await _process_file(provider, meta, settings)
                await repo.upsert_file(
                    session,
                    org_id=org_id,
                    run_id=run_id,
                    meta=meta,
                    flags=flags,
                    sha256=sha,
                    staged_blob_uri=staged_uri,
                    mime_type=mime_type,
                )
            await session.commit()  # per-batch checkpoint (the §11.2 resume granularity)
            if token:
                await locks.heartbeat(src_hash, token, ttl=settings.import_lock_ttl_seconds)
            if await repo.get_status(session, run_id) is ImportRunStatus.CANCELLED:
                if token:
                    await locks.release(src_hash, token)
                return

        counts = await repo.compute_counts(session, run_id)
        final = await repo.get_run(session, run_id, for_update=True)
        if final is None or final.status is not ImportRunStatus.SCANNING:
            await session.commit()  # a late cancel won the race — respect it
            if token:
                await locks.release(src_hash, token)
            return
        final.status = ImportRunStatus.SCANNED
        final.counts = counts
        final.completed_at = _now()
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Scanning"},
            after={"status": "Scanned", "counts": counts},
        )
        await session.commit()
        if token:
            await locks.release(src_hash, token)
    except Exception as exc:
        await session.rollback()
        await _fail_scan(session, run_id, repr(exc)[:500])
        if token:
            await locks.release(src_hash, token)
        raise


async def _process_file(
    provider: FilesystemSourceProvider, meta: FileMeta, settings: Any
) -> tuple[ScanFlags, str | None, str | None, str | None]:
    """Classify one file and, if it survives as an included candidate, content-address-stage it.
    Returns
    ``(scan_flags, sha256, staged_blob_uri, mime_type)`` — sha/uri/mime are None for non-staged
    files."""
    if meta.error is not None:  # symlink / unreadable — excluded, never opened
        reason = meta.error.split(":", 1)[0]
        return ScanFlags("excluded", reason, detail=meta.error), None, None, None

    pre = classify(meta.filename, meta.ext, meta.size_bytes, settings.import_oversize_bytes)
    if not pre.included_candidate:  # junk/empty/temp/oversize/archive/unsupported-by-ext — no read
        return pre, None, None, None

    try:
        with provider.open_stream(meta.rel_path) as handle:
            head = handle.read(_HEAD_BYTES)
            mime_type = sniff_mime(head, meta.filename)
            encrypted = _looks_encrypted(head, meta.ext)
            post = classify(
                meta.filename,
                meta.ext,
                meta.size_bytes,
                settings.import_oversize_bytes,
                mime=mime_type,
                encrypted=encrypted,
            )
            if (
                not post.included_candidate
            ):  # mime-unsupported / needs_password — recorded, not staged
                return post, None, None, mime_type
            handle.seek(0)
            staged = await storage.stage_stream(handle)
            return post, staged.sha256, staged.staged_blob_uri, mime_type
    except (
        OSError
    ) as exc:  # raced delete / permission / O_NOFOLLOW refusal — excluded, never dropped
        return ScanFlags("excluded", "unreadable", detail=str(exc)), None, None, None


def _looks_encrypted(head: bytes, ext: str | None) -> bool:
    """Cheap header-only encryption signal (the deep probe is slice 2): an encrypted PDF carries
    ``/Encrypt`` in its head; an encrypted OOXML is wrapped in an OLE/CFB container (a plain OOXML
    is a
    ZIP), so a ``docx``/``xlsx``/``pptx`` whose magic is CFB is an encrypted package."""
    if head[:5] == b"%PDF-" and b"/Encrypt" in head:
        return True
    if head[:8] == _CFB_MAGIC and (ext or "").lower() in _OOXML_EXTS:
        return True
    return False


async def _fail_scan(session: AsyncSession, run_id: uuid.UUID, reason: str) -> None:
    """Mark a scan FAILED in its own transaction (the packs ``_fail`` discipline)."""
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.status in _TERMINAL:
        await session.rollback()
        return
    run.status = ImportRunStatus.FAILED
    run.error = reason
    run.completed_at = _now()
    emit_import_event_system(
        session, run.org_id, EventType.IMPORT_RUN_FAILED, run_id, after={"error": reason}
    )
    await session.commit()


# --------------------------------------------------------------------------- reaper (Beat)


async def reap_stalled_scans(
    session: AsyncSession,
    *,
    now: datetime.datetime | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, int]:
    """Flip scans stuck in SCANNING past the stall window → FAILED (system-actor) + force-release
    the
    abandoned source-root lock, so a crashed scan never wedges the root. ``FOR UPDATE SKIP LOCKED``
    avoids racing a live scan; a Beat job drives this, tests call it directly."""
    from sqlalchemy import select

    settings = get_settings()
    now = now or _now()
    max_age = max_age_seconds if max_age_seconds is not None else settings.import_scan_stall_seconds
    cutoff = now - datetime.timedelta(seconds=max_age)
    stalled = (
        (
            await session.execute(
                select(ImportRun)
                .where(
                    ImportRun.status == ImportRunStatus.SCANNING,
                    ImportRun.scan_started_at.is_not(None),
                    ImportRun.scan_started_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    hashes: list[str] = []
    for run in stalled:
        run.status = ImportRunStatus.FAILED
        run.error = "scan_timeout"
        run.completed_at = now
        hashes.append(run.source_root_hash)
        emit_import_event_system(
            session,
            run.org_id,
            EventType.IMPORT_RUN_FAILED,
            run.id,
            after={"error": "scan_timeout"},
        )
    await session.commit()
    for src_hash in hashes:
        await locks.force_release(src_hash)
    return {"reaped": len(stalled)}
