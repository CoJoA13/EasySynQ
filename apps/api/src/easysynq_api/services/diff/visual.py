"""The visual-diff orchestration (slice S-dcr-3b; doc 05 §8.1, worker-async). I/O around the pure
``domain/diff/visual``.

``build_visual_diff`` (run by the ``easysynq.visual_diff`` worker task) obtains a rendition PDF for
each version (cache hit on the mirror's ``rendition_blob_sha256``; else render the source via the
injected ``RenderSink`` — the worker's ``GotenbergRenderSink`` — **transiently, NOT persisted**, to
avoid poisoning the mirror's controlled-copy cache: see ``_ensure_rendition_pdf``), rasterizes both
(pypdfium2), diffs page-by-page (Pillow), caches each page's from/to/diff PNG as a content-addressed
non-WORM ``Blob`` (Blob row + bytes written together, the blob-row-iff-bytes invariant), and flips
the ``visual_diff`` row Pending → Ready (or Unavailable / Failed).

The diff rasterizes the **controlled-copy (watermarked) rendition** — the footer band differs by
revision (label / effective date / state), so it shows as a changed footer region on every page
(accepted + documented for v1; a raw-render diff is a v1.x follow-up).
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._dcr_enums import VisualDiffStatus
from ...db.models.blob import Blob
from ...db.models.document_version import DocumentVersion
from ...db.models.visual_diff import VisualDiff
from ...domain.diff.visual import diff_pages, rasterize
from ..vault import repository as vault_repo
from ..vault import storage
from ..vault.render import RenderRequest, RenderSink, RenderStatus

logger = logging.getLogger("easysynq.visual_diff")


class _VisualUnavailable(Exception):
    """A version is non-renderable (R26) → no page images; the visual diff is Unavailable."""


class _VisualPending(Exception):
    """A transient renderer outage → leave the row Pending; the task retries / re-POST re-runs."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _cache_png(session: AsyncSession, org_id: uuid.UUID, png: bytes) -> str:
    """Content-address a page PNG into the non-WORM renditions bucket + INSERT its Blob row (bytes +
    row together — the _cache_rendition / blob-row-iff-bytes pattern). Returns the sha256."""
    bucket = get_settings().s3_bucket_renditions
    sha = hashlib.sha256(png).hexdigest()
    await storage.put_bytes(png, sha, bucket=bucket, content_type="image/png")
    await session.execute(
        pg_insert(Blob)
        .values(
            sha256=sha,
            org_id=org_id,
            size_bytes=len(png),
            mime_type="image/png",
            bucket=bucket,
            object_key=sha,
            worm_locked=False,  # derived + regenerable (doc 14 §5.4)
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    return sha


async def _ensure_rendition_pdf(
    session: AsyncSession, version: DocumentVersion, render_sink: RenderSink
) -> bytes:
    """The version's controlled-copy rendition PDF: cache hit on ``rendition_blob_sha256``, else
    render the source via the sink + cache it. Raises ``_VisualUnavailable`` (NON_RENDERABLE) or
    ``_VisualPending`` (transient outage)."""
    settings = get_settings()
    if version.rendition_blob_sha256:
        try:
            return await storage.fetch_bytes(
                version.rendition_blob_sha256, bucket=settings.s3_bucket_renditions
            )
        except Exception:  # noqa: BLE001 — cached rendition vanished; re-render below
            logger.warning(
                "visual_diff.rendition_cache_miss",
                extra={"extra_fields": {"version_id": str(version.id)}},
            )
    src_blob = await vault_repo.get_blob(session, version.source_blob_sha256)
    if src_blob is None:  # pragma: no cover - defensive (the FK guarantees it)
        raise _VisualUnavailable
    source_bytes = await storage.fetch_bytes(src_blob.object_key, bucket=src_blob.bucket)
    snap = version.metadata_snapshot
    request = RenderRequest(
        identifier=str(snap.get("identifier") or version.document_id),
        title=str(snap.get("title") or ""),
        revision_label=version.revision_label,
        effective_from=version.effective_from,
        classification=str(snap.get("classification") or "Internal"),
        copy_status=version.version_state.value,  # band reflects the version's state for the diff
        owner=str(snap.get("owner_user_id") or ""),
        mime_type=src_blob.mime_type,
        source_filename=f"{snap.get('identifier') or version.document_id}-{version.revision_label}",
        version_id=version.id,
        verify_url=None,  # a diff rendition is not a distributed controlled copy → no verify token
    )
    result = await render_sink.render(request, source_bytes)
    if result.status is RenderStatus.RENDERED and result.pdf is not None:
        # Deliberately TRANSIENT — we do NOT write ``rendition_blob_sha256``. That pointer is the
        # mirror's controlled-copy cache (rendered with copy_status="CONTROLLED COPY" + a verify QR
        # for Effective versions); writing this diff rendition (copy_status=state, no QR) onto it
        # would POISON the mirror cache for a Draft that later goes Effective. The build is
        # idempotent (once per (from,to) pair) + the page PNGs cached, so re-rendering is bounded.
        return result.pdf
    if result.status is RenderStatus.NON_RENDERABLE:
        raise _VisualUnavailable
    raise _VisualPending


async def build_visual_diff(
    session: AsyncSession, visual_diff_id: uuid.UUID, render_sink: RenderSink
) -> None:
    """Render + rasterize + diff + cache the page comparisons; flip the visual_diff row to its
    terminal status. Idempotent: a terminal row early-returns (FOR-UPDATE serializes redelivery)."""
    vd = (
        await session.execute(
            select(VisualDiff).where(VisualDiff.id == visual_diff_id).with_for_update()
        )
    ).scalar_one_or_none()
    if vd is None or vd.status is not VisualDiffStatus.Pending:
        return  # gone, or already terminal (idempotent re-delivery)
    from_v = await session.get(DocumentVersion, vd.from_version_id)
    to_v = await session.get(DocumentVersion, vd.to_version_id)
    if from_v is None or to_v is None:  # pragma: no cover - defensive (FKs guarantee it)
        vd.status = VisualDiffStatus.Failed
        vd.reason = "a version row is missing"
        vd.completed_at = _now()
        await session.commit()
        return
    try:
        from_pdf = await _ensure_rendition_pdf(session, from_v, render_sink)
        to_pdf = await _ensure_rendition_pdf(session, to_v, render_sink)
    except _VisualUnavailable:
        vd.status = VisualDiffStatus.Unavailable
        vd.reason = "a version is not renderable to PDF (no page images available)"
        vd.completed_at = _now()
        await session.commit()
        return
    # _VisualPending propagates → the row stays Pending; the reaper / a re-run retries.

    page_diffs = diff_pages(rasterize(from_pdf), rasterize(to_pdf))
    pages: list[dict[str, object]] = []
    for pd in page_diffs:
        pages.append(
            {
                "page": pd.page,
                "changed": pd.changed,
                "from_blob_sha": (
                    await _cache_png(session, vd.org_id, pd.from_png) if pd.from_png else None
                ),
                "to_blob_sha": (
                    await _cache_png(session, vd.org_id, pd.to_png) if pd.to_png else None
                ),
                "diff_blob_sha": (
                    await _cache_png(session, vd.org_id, pd.diff_png) if pd.diff_png else None
                ),
            }
        )
    vd.pages = pages
    vd.page_count = len(pages)
    vd.status = VisualDiffStatus.Ready
    vd.completed_at = _now()
    await session.commit()


async def get_or_create_visual_diff(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    document_id: uuid.UUID,
    from_version_id: uuid.UUID,
    to_version_id: uuid.UUID,
) -> tuple[VisualDiff, bool]:
    """The cached visual_diff row for (from, to), creating a Pending one if absent. Returns
    (row, should_enqueue) — enqueue when freshly created OR still Pending (re-drives a stalled
    task;
    idempotent — the task FOR-UPDATEs + early-returns on terminal). Race-safe via ON CONFLICT."""
    await session.execute(
        pg_insert(VisualDiff)
        .values(
            org_id=org_id,
            document_id=document_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            status=VisualDiffStatus.Pending,
        )
        .on_conflict_do_nothing(index_elements=["from_version_id", "to_version_id"])
    )
    await session.commit()
    row = (
        await session.execute(
            select(VisualDiff).where(
                VisualDiff.from_version_id == from_version_id,
                VisualDiff.to_version_id == to_version_id,
            )
        )
    ).scalar_one()
    return row, row.status is VisualDiffStatus.Pending


async def get_visual_diff(
    session: AsyncSession, from_version_id: uuid.UUID, to_version_id: uuid.UUID
) -> VisualDiff | None:
    return (
        await session.execute(
            select(VisualDiff).where(
                VisualDiff.from_version_id == from_version_id,
                VisualDiff.to_version_id == to_version_id,
            )
        )
    ).scalar_one_or_none()
