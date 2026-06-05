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
from ...db.models._ingestion_enums import (
    ImportConfidenceBand,
    ImportKind,
    ImportRunStatus,
)
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.import_classification import ImportClassification
from ...db.models.import_dupe_cluster import ImportDupeCluster
from ...db.models.import_extract import ImportExtract
from ...db.models.import_file import ImportFile
from ...db.models.import_proposal_node import ImportProposalNode
from ...db.models.import_run import ImportRun
from ...db.models.import_version_family import ImportVersionFamily
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
# Terminal = no further stage + lock freed. S-ing-3: the pipeline auto-chains
# scan→extract→classify→dedup→propose (the lock is held continuously, freed only at
# Proposed/Failed/Cancelled). Classified is NO LONGER terminal — it chains to dedup. Every
# in-progress state (incl. the rest-states Scanned/Classified) stays cancellable + reapable.
_TERMINAL = (
    ImportRunStatus.PROPOSED,
    ImportRunStatus.COMPLETED,
    ImportRunStatus.FAILED,
    ImportRunStatus.CANCELLED,
)
_IN_PROGRESS = (
    ImportRunStatus.SCANNING,
    ImportRunStatus.SCANNED,
    ImportRunStatus.EXTRACTING,
    ImportRunStatus.CLASSIFYING,
    ImportRunStatus.CLASSIFIED,
    ImportRunStatus.DEDUPING,
    ImportRunStatus.PROPOSING,
)
# S-ing-5: COMMITTING/PARTIALLY_COMMITTED are DELIBERATELY absent from _IN_PROGRESS +
# repository._ACTIVE_STATES (the lock-liveness reaper keys on the source-root lock, which commit
# does
# NOT hold — it was freed at Proposed; the reaper would instantly FAIL a commit run). They get their
# own
# progress-liveness reaper (reap_stalled_commits) that RE-ENQUEUEs, never fails. They are NOT
# _TERMINAL
# either (COMMITTING is in-flight; PARTIALLY_COMMITTED is resumable) — so cancel is gated by a
# dedicated
# _CANCEL_BLOCKED instead (a vault write has happened → cancel must 409; doc 09 §11.4
# WORM-no-rollback).
_CANCEL_BLOCKED = (
    *_TERMINAL,
    ImportRunStatus.COMMITTING,
    ImportRunStatus.PARTIALLY_COMMITTED,
)
# POST /commit accepts a reviewed run to START commit, or a partial run to RESUME it.
_COMMIT_START = (ImportRunStatus.PROPOSED, ImportRunStatus.REVIEWING)
_COMMIT_RESUME = (ImportRunStatus.PARTIALLY_COMMITTED,)
# S-ing-4: the states a human review write (decision/merge/split) is accepted in. REVIEWING is the
# resting state the run enters on the first decision. NB: REVIEWING is DELIBERATELY absent from
# _IN_PROGRESS + repository._ACTIVE_STATES — the source-root lock is freed at Proposed, so a
# lock-free REVIEWING run must NOT be swept by the lock-liveness reaper (it would FAIL a run
# mid-review), and a re-import of the same root during review is allowed (a new run). It is also not
# _TERMINAL (cancel still works).
_REVIEWABLE = (
    ImportRunStatus.PROPOSED,
    ImportRunStatus.REVIEWING,
)


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
    object_type: AuditObjectType = AuditObjectType.import_run,
    object_id: uuid.UUID | None = None,
    scope_ref: str | None = None,
) -> None:
    """A *system*-actor import-run event (actor_id NULL) — the detached scan/commit worker / the
    reaper have no HTTP caller (the records ``emit_record_event_system`` precedent). Defaults key
    the row to the run (object_type=import_run, object_id=run_id); the S-ing-5 per-item event keys
    to the created vault row instead (object_type=document|record, object_id=the new id,
    scope_ref=identifier) so ``GET /documents/{id}/audit-events`` surfaces the import as the doc's
    creation event (AC#6 per-doc history)."""
    session.add(
        AuditEvent(
            org_id=org_id,
            occurred_at=_now(),
            actor_id=None,
            actor_type=ActorType.system,
            event_type=event_type,
            object_type=object_type,
            object_id=object_id if object_id is not None else run_id,
            scope_ref=scope_ref,
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

    # Enqueue AFTER commit so the worker never reads an uncommitted Created row. Best-effort (the
    # records ``_enqueue_structured_pdf`` precedent): the run is already committed, so a broker
    # hiccup
    # must not 500 the create — the run stays Created (operator-recoverable). In production the lock
    # Redis and the Celery broker Redis are the same instance, so a reachable lock (above) implies a
    # reachable broker; the swallow matters only for a momentary blip / a test with no broker.
    from ...tasks.ingestion import scan_source

    try:
        scan_source.delay(str(run.id))
    except Exception:  # noqa: BLE001 — best-effort enqueue; the run is committed (Created)
        logger.warning(
            "ingestion.scan.enqueue_failed", extra={"extra_fields": {"run_id": str(run.id)}}
        )
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
    kind: ImportKind | None = None,
    band: ImportConfidenceBand | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[ImportRun, Sequence[tuple[ImportFile, ImportClassification | None]]]:
    run = await get_import_run(session, caller, run_id)  # org-scoped 404 first
    rows = await repo.list_files_with_classification(
        session,
        run_id,
        classifier_version=run.classifier_version,
        disposition=disposition,
        kind=kind,
        band=band,
        limit=min(limit, 200),
        offset=max(offset, 0),
    )
    return run, rows


async def list_import_file_detail(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID, file_id: uuid.UUID
) -> tuple[ImportRun, ImportFile, ImportExtract | None, ImportClassification | None]:
    run = await get_import_run(session, caller, run_id)  # org-scoped 404 first
    detail = await repo.get_file_detail(
        session, run_id, file_id, classifier_version=run.classifier_version
    )
    if detail is None:
        raise ProblemException(status=404, code="not_found", title="Import file not found")
    f, ext, cls = detail
    return run, f, ext, cls


async def list_import_dupe_clusters(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID
) -> tuple[ImportRun, Sequence[ImportDupeCluster]]:
    """The run's dedup clusters (S-ing-3 review read surface; org-scoped 404 first)."""
    run = await get_import_run(session, caller, run_id)
    return run, await repo.list_dupe_clusters(session, run_id)


async def list_import_version_families(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID
) -> tuple[ImportRun, Sequence[ImportVersionFamily]]:
    """The run's version families (S-ing-3 review read surface; org-scoped 404 first)."""
    run = await get_import_run(session, caller, run_id)
    return run, await repo.list_version_families(session, run_id)


async def get_import_file_membership(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID, file_id: uuid.UUID
) -> tuple[Sequence[ImportDupeCluster], ImportVersionFamily | None, ImportProposalNode | None]:
    """A file's dedup/family/proposal context for the per-file detail (org-scoped 404 first)."""
    await get_import_run(session, caller, run_id)
    return await repo.get_file_membership(session, run_id, file_id)


async def get_import_file_commit(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID, file_id: uuid.UUID
) -> Any:
    """A file's S-ing-5 commit ledger row for the per-file detail (org-scoped 404 first; None until
    the item is committed)."""
    await get_import_run(session, caller, run_id)
    return await repo.get_commit_result(session, run_id, file_id)


async def cancel_import_run(session: AsyncSession, caller: AppUser, run_id: uuid.UUID) -> ImportRun:
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")
    if run.status in _CANCEL_BLOCKED:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Import run cannot be cancelled (terminal or committing/committed)",
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


async def start_import_commit(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID
) -> ImportRun:
    """Flip a reviewed run (Proposed/Reviewing) — or RESUME a PartiallyCommitted one — to Committing
    and enqueue the detached commit worker. A start checks the §9.3 checklist (blocking conflicts →
    422 ``commit_blocked``); a resume skips it (the conflicts were cleared at the original start and
    the idempotent ledger only re-attempts failed/remaining items). The Reviewing→Committing flip
    is a USER act (the committer is in scope); the per-item commits run as a SYSTEM worker, with the
    human committer carried by ``committed_by`` + the import_baseline signature. Idempotency is the
    status routing + the ``(run_id, file_id)`` ledger, not a header (commit is one transition, not
    an append-of-N like decisions)."""
    run = await repo.get_run(session, run_id, for_update=True)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")

    if run.status in _COMMIT_START:
        # Lazy import to avoid the service↔review module cycle (review imports from service).
        from .review import compute_review_checklist

        checklist = await compute_review_checklist(session, caller, run_id)
        if not checklist["ready"]:
            raise ProblemException(
                status=422,
                code="commit_blocked",
                title="Resolve the blocking conflicts before committing",
                members={"blocking": checklist["blocking"]},
            )
        prev = run.status.value
    elif run.status in _COMMIT_RESUME:
        prev = run.status.value
    elif run.status is ImportRunStatus.COMMITTING:
        raise ProblemException(status=409, code="conflict", title="A commit is already in progress")
    elif run.status is ImportRunStatus.COMPLETED:
        raise ProblemException(
            status=409, code="conflict", title="Import run is already fully committed"
        )
    else:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Import run is not in a reviewable/resumable state to commit",
            members={"status": run.status.value},
        )

    run.committed_by = caller.id
    run.committing_started_at = _now()
    run.status = ImportRunStatus.COMMITTING
    emit_import_event(
        session,
        caller,
        EventType.IMPORT_RUN_STAGE_CHANGED,
        run.id,
        before={"status": prev},
        after={"status": "Committing"},
    )
    await session.commit()
    await session.refresh(run)

    # Enqueue AFTER commit (the create precedent). Best-effort — the reap_stalled_commits backstop
    # re-enqueues a Committing run whose commit-ledger makes no progress, and a re-POST /commit also
    # resumes (idempotent via the ledger).
    from ...tasks.ingestion import commit_source

    try:
        commit_source.delay(str(run.id))
    except Exception:  # noqa: BLE001 — best-effort enqueue; the run is committed (Committing)
        logger.warning(
            "ingestion.commit.enqueue_failed", extra={"extra_fields": {"run_id": str(run.id)}}
        )
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
            # Stop on ANY status change away from Scanning — a cancel, OR a reaper that FAILED a run
            # whose lock lapsed mid-batch (the release is a CAS no-op if already freed).
            if await repo.get_status(session, run_id) is not ImportRunStatus.SCANNING:
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
        # completed_at stays NULL — the pipeline auto-chains to extract→classify and completes at
        # Classified. The source-root lock is held continuously (NOT released here), so a re-import
        # of the same root stays blocked through the whole pipeline (doc 09 §3.3).
        emit_import_event_system(
            session,
            org_id,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run_id,
            before={"status": "Scanning"},
            after={"status": "Scanned", "counts": counts},
        )
        await session.commit()
        _enqueue_extract(run_id)  # chain to Stage 2 (best-effort; the reaper backstops a drop)
    except Exception as exc:
        await session.rollback()
        await _fail_run(session, run_id, repr(exc)[:500])
        if token:
            await locks.release(src_hash, token)
        raise


def _enqueue_extract(run_id: uuid.UUID) -> None:
    """Best-effort chain to Stage 2 AFTER the Scanned commit (the ``_enqueue_structured_pdf``
    precedent): the run is committed + the lock held, so a broker blip must not fail the scan — the
    lock-liveness reaper FAILs a stranded run once its TTL lapses (operator re-runs to resume)."""
    from ...tasks.ingestion import extract_source

    try:
        extract_source.delay(str(run_id))
    except Exception:  # noqa: BLE001 — best-effort enqueue; the run is committed (Scanned)
        logger.warning(
            "ingestion.extract.enqueue_failed", extra={"extra_fields": {"run_id": str(run_id)}}
        )


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


async def _fail_run(session: AsyncSession, run_id: uuid.UUID, reason: str) -> None:
    """Mark a run FAILED in its own transaction (the packs ``_fail`` discipline)."""
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


async def reap_stalled_runs(
    session: AsyncSession,
    *,
    now: datetime.datetime | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, int]:
    """Flip wedged in-progress runs (Scanning/Scanned/Extracting/Classifying) → FAILED + force-free
    the source-root lock, so a crashed worker never wedges the root. S-ing-2 primary signal:
    **lock-liveness** — the lock is held continuously + heartbeated per batch, so a missing lock on
    an in-progress run means the worker died (the TTL lapsed with no heartbeat). A generous absolute
    ``import_run_stall_seconds`` backstop on ``scan_started_at`` (the whole-pipeline anchor) covers
    the rare alive-but-wedged case. ``FOR UPDATE SKIP LOCKED`` avoids racing a live worker; a Beat
    job drives this, tests call it directly."""
    from sqlalchemy import select

    settings = get_settings()
    now = now or _now()
    max_age = max_age_seconds if max_age_seconds is not None else settings.import_run_stall_seconds
    cutoff = now - datetime.timedelta(seconds=max_age)
    candidates = (
        (
            await session.execute(
                select(ImportRun)
                .where(ImportRun.status.in_(_IN_PROGRESS))
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    hashes: list[str] = []
    for run in candidates:
        lock_alive = await locks.is_alive(run.source_root_hash)
        too_old = run.scan_started_at is not None and run.scan_started_at < cutoff
        if lock_alive and not too_old:
            continue  # progressing (heartbeat keeps the lock alive)
        run.status = ImportRunStatus.FAILED
        run.error = "stage_timeout"
        run.completed_at = now
        hashes.append(run.source_root_hash)
        emit_import_event_system(
            session,
            run.org_id,
            EventType.IMPORT_RUN_FAILED,
            run.id,
            after={"error": "stage_timeout"},
        )
    await session.commit()
    for src_hash in hashes:
        await locks.force_release(src_hash)
    return {"reaped": len(hashes)}


async def reap_stalled_commits(
    session: AsyncSession,
    *,
    now: datetime.datetime | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, int]:
    """RE-ENQUEUE a wedged Committing run (a crashed commit worker) — NEVER fail it (committed WORM
    items are permanent; doc 09 §11.2 resume + §11.4 no-rollback). Commit holds NO source-root lock,
    so this uses **progress-liveness**: a Committing run whose latest
    ``import_commit_result.committed_at`` (else ``committing_started_at``) is older than the stall
    window has made no progress → re-enqueue ``commit_source`` (idempotent via the ledger CLAIM,
    which makes a re-enqueue alongside a still-live worker commit each item exactly once). Distinct
    from ``reap_stalled_runs`` (lock-liveness → FAIL). ``FOR UPDATE SKIP LOCKED`` avoids racing the
    row; a Beat job drives this, tests call it directly."""
    from sqlalchemy import select

    settings = get_settings()
    now = now or _now()
    max_age = max_age_seconds if max_age_seconds is not None else settings.import_run_stall_seconds
    cutoff = now - datetime.timedelta(seconds=max_age)
    candidates = (
        (
            await session.execute(
                select(ImportRun)
                .where(ImportRun.status == ImportRunStatus.COMMITTING)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    requeue: list[uuid.UUID] = []
    for run in candidates:
        progress = await repo.max_commit_progress(session, run.id)
        # The GREATEST of the two liveness signals — so a freshly-resumed run (new
        # committing_started_at, but a STALE max(committed_at) from the prior partial pass) counts
        # as
        # live and is not instantly re-reaped.
        anchors = [s for s in (progress, run.committing_started_at) if s is not None]
        anchor = max(anchors) if anchors else None
        if anchor is not None and anchor >= cutoff:
            continue  # the commit worker is making progress (or just started)
        requeue.append(run.id)
    await session.commit()  # release the FOR UPDATE before enqueueing

    if requeue:
        from ...tasks.ingestion import commit_source

        for rid in requeue:
            try:
                commit_source.delay(str(rid))
            except Exception:  # noqa: BLE001 — best-effort; the next reaper tick retries
                logger.warning(
                    "ingestion.commit.reap_enqueue_failed",
                    extra={"extra_fields": {"run_id": str(rid)}},
                )
    return {"requeued": len(requeue)}
