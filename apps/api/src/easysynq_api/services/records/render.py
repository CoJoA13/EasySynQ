"""Stage-2 structured-record PDF rendition (slice S-rec-3, doc 06 §4.2).

A captured structured (Mode-B) Record renders as a read-only fielded view AND a sealed PDF
rendition for export/print. This builds that PDF **best-effort, after capture commits** (the
S-pack-2 portfolio precedent): a deterministic reportlab page (NO Gotenberg) listing the record's
identity, provenance, content hash, and its fielded data (labelled from the pinned template schema).
It is a DERIVED, regenerable view — NOT part of the ``content_hash`` seal — cached in the
**non-WORM** renditions bucket and pointed at by ``record.structured_pdf_blob_sha256`` (a plain Text
pointer, no FK, doc 14 §5.4). The record id + content hash are folded into the rendered bytes, so
each record's PDF has a DISTINCT sha — a per-record content-address, never shared, so the
WORM-destroy purge (which drops the pointer's blob row to keep blob-row-iff-bytes) is always safe.

Idempotent: ``FOR UPDATE`` on the record + early-return if the pointer is set or a destructive
disposition tombstone exists; one transaction (content-addressed writes dedup on re-run). A bounded
hourly Beat redrive re-enqueues structured records whose pointer is still absent, recovering both
dropped broker publishes and failed builds while ``GET /records/{id}/rendition`` remains a pure
409 poll.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from collections.abc import Callable
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import exists, not_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._retention_enums import DispositionAction
from ...db.models.blob import Blob
from ...db.models.disposition_event import DispositionEvent
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.record import Record
from ..vault import schema_from_version, storage

logger = logging.getLogger("easysynq.records.render")

_PAGE_W, _PAGE_H = float(letter[0]), float(letter[1])
_MARGIN = 54.0
_LINE = 12.0
_MAX_CHARS = 96
_REDRIVE_BATCH_SIZE = 250


def _wrap(text: str) -> list[str]:
    out: list[str] = []
    while len(text) > _MAX_CHARS:
        out.append(text[:_MAX_CHARS])
        text = text[_MAX_CHARS:]
    out.append(text)
    return out


def _text_pdf(title: str, lines: list[str]) -> bytes:
    """A deterministic (invariant), paginated text PDF (mirrors ``packs.portfolio._text_pdf``)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    wrapped: list[str] = []
    for ln in lines:
        wrapped.extend(_wrap(ln) if ln else [""])

    def _new_page(with_title: bool) -> float:
        c.setFillColor(colors.black)
        y = _PAGE_H - _MARGIN
        if with_title:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(_MARGIN, y, title[:_MAX_CHARS])
            y -= _LINE * 2
        c.setFont("Helvetica", 9)
        return y

    y = _new_page(with_title=True)
    for ln in wrapped:
        if y < _MARGIN + _LINE:
            c.showPage()
            y = _new_page(with_title=False)
        c.drawString(_MARGIN, y, ln)
        y -= _LINE
    c.showPage()
    c.save()
    return buf.getvalue()


def _render_lines(
    record: Record,
    base: DocumentedInformation,
    version: DocumentVersion | None,
    version_base: DocumentedInformation | None,
) -> list[str]:
    """The fielded view: identity + provenance + content hash + the labelled field/value rows. The
    record id + content hash are included so each record's PDF bytes (hence its sha) differ."""
    schema = schema_from_version(version) if version is not None else None
    labels: dict[str, str] = {}
    if schema is not None:
        for field in schema.get("fields", []):
            if isinstance(field, dict) and isinstance(field.get("key"), str):
                labels[field["key"]] = str(field.get("label") or field["key"])

    template = (
        f"{version_base.identifier} Rev {version.revision_label}"
        if version is not None and version_base is not None
        else "—"
    )
    lines = [
        "RECORD — structured form capture",
        "",
        f"Record id:     {record.id}",
        f"Identifier:    {base.identifier}",
        f"Title:         {base.title}",
        f"Record type:   {record.record_type.value}",
        f"Captured at:   {record.captured_at.isoformat() if record.captured_at else '—'}",
        f"Captured by:   {record.captured_by}",
        f"Source form:   {template}",
        f"Content hash:  {record.content_hash}",
        f"Retention:     policy {record.retention_policy_id}, basis {record.retention_basis_date}",
        f"Disposition:   {record.disposition_state.value}",
        "",
        "Fielded data (validated against the pinned template schema)",
        "",
    ]
    values: dict[str, Any] = record.form_field_values or {}
    for key in sorted(values):
        lines.append(f"  {labels.get(key, key)}: {values[key]}")
    lines += [
        "",
        "This is a regenerable, NON-WORM rendition (doc 14 §5.4) — a printable view of the",
        "structured content. The record's integrity is sealed by its content_hash above (over the",
        "field values + the pinned source version + the attached evidence manifest).",
    ]
    return lines


async def build_structured_pdf(session: AsyncSession, record_id: uuid.UUID) -> None:
    """Build + cache the structured-record PDF (idempotent, best-effort). Skips a record that is not
    structured, already rendered, destructively disposed, or absent. The row lock serializes with
    disposition: once DESTROY has committed, a delayed task sees its tombstone and cannot recreate
    the purged rendition; ARCHIVE_COLD/TRANSFER may still finish a missing derived view."""
    record = (
        await session.execute(select(Record).where(Record.id == record_id).with_for_update())
    ).scalar_one_or_none()
    if record is None or record.structured_pdf_blob_sha256 is not None:
        await session.rollback()
        return
    destroyed = await session.scalar(
        select(DispositionEvent.id)
        .where(
            DispositionEvent.record_id == record_id,
            DispositionEvent.action == DispositionAction.DESTROY,
        )
        .limit(1)
    )
    if destroyed is not None:
        await session.rollback()
        return
    base = await session.get(DocumentedInformation, record_id)
    if base is None:  # pragma: no cover - the shared-PK FK guarantees it
        await session.rollback()
        return
    version = (
        await session.get(DocumentVersion, record.source_version_id)
        if record.source_version_id is not None
        else None
    )
    # Pre-fix optional-form captures can carry NULL values even though their pinned immutable
    # version contains a form schema. Keep those legacy records recoverable without confusing a
    # genuinely unstructured ad-hoc record with a structured one.
    if record.form_field_values is None and (
        version is None or schema_from_version(version) is None
    ):
        await session.rollback()
        return
    version_base = (
        await session.get(DocumentedInformation, version.document_id)
        if version is not None
        else None
    )

    pdf = _text_pdf(f"Record {base.identifier}", _render_lines(record, base, version, version_base))
    bucket = get_settings().s3_bucket_renditions
    sha = hashlib.sha256(pdf).hexdigest()
    await storage.put_bytes(pdf, sha, bucket=bucket, content_type="application/pdf")
    await session.execute(
        pg_insert(Blob)
        .values(
            sha256=sha,
            org_id=record.org_id,
            size_bytes=len(pdf),
            mime_type="application/pdf",
            bucket=bucket,
            object_key=sha,
            worm_locked=False,  # derived + rebuildable (doc 14 §5.4)
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    record.structured_pdf_blob_sha256 = sha
    await session.commit()


async def _missing_structured_pdf_ids(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """Return oldest missing live renditions first.

    Successful builds leave the candidate set, so later ticks advance through the backlog.
    """
    pinned_form_version = exists(
        select(1).where(
            DocumentVersion.id == Record.source_version_id,
            DocumentVersion.metadata_snapshot.op("?")("field_schema"),
        )
    )
    destructive_disposition = exists(
        select(1).where(
            DispositionEvent.record_id == Record.id,
            DispositionEvent.action == DispositionAction.DESTROY,
        )
    )
    return list(
        (
            await session.scalars(
                select(Record.id)
                .where(
                    or_(Record.form_field_values.is_not(None), pinned_form_version),
                    Record.structured_pdf_blob_sha256.is_(None),
                    not_(destructive_disposition),
                )
                .order_by(Record.captured_at, Record.id)
                .limit(limit)
            )
        ).all()
    )


async def redrive_missing_structured_pdfs(
    session: AsyncSession,
    *,
    enqueue: Callable[[uuid.UUID], Any],
    limit: int = _REDRIVE_BATCH_SIZE,
) -> dict[str, int]:
    """Re-enqueue a bounded batch whose derived PDF pointer is still absent.

    Publishing is intentionally per-record and best-effort: one broker failure does not suppress
    later candidates, and every failed candidate remains pointer-less for the next Beat tick.
    """
    record_ids = await _missing_structured_pdf_ids(session, limit=limit)
    await session.rollback()  # release the read transaction before publishing to the broker
    enqueued = 0
    failed = 0
    for record_id in record_ids:
        try:
            enqueue(record_id)
            enqueued += 1
        except Exception:  # noqa: BLE001 — a later Beat tick retries this still-missing pointer
            failed += 1
            logger.warning(
                "records.structured_pdf_redrive_enqueue_failed",
                extra={"extra_fields": {"record_id": str(record_id)}},
            )
    return {"candidates": len(record_ids), "enqueued": enqueued, "failed": failed}
