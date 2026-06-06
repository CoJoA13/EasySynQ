"""The evidence-pack PDF portfolio variant (slice S-pack-2, doc 06 §7.4) — build Stage 2.

A single, printable PDF assembled AFTER the canonical ZIP is sealed (Stage 1): a cover page, a
human-readable traceability index (included records + governing versions + the R28 gap/exclusion
reports — the honesty surface), and each included controlled document's §11.3-stamped rendition. It
is a DERIVED VIEW of the sealed pack — NOT part of the content_hash seal (the ZIP content list is
the sealed truth) — cached in the non-WORM renditions bucket and pointed at by
``evidence_pack.portfolio_blob_sha256``.

Renderer-independent: a controlled version's already-§11.3-stamped **cached** rendition (from the
mirror) is embedded as-is; a version whose state changed since capture gets a truthful pure-pypdf
"no longer governs" overlay; an uncached version gets an honest placeholder (its source bytes are in
the ZIP). So a Gotenberg outage never blocks the build and the API never renders. Stage 2 is its own
transaction after the seal commits, idempotent on ``portfolio_blob_sha256``, and **best-effort** — a
failure leaves the pointer NULL (the ZIP is the canonical artefact; ``format=pdf`` then 409s).

The public guest download (``format=pdf``) overlays a fresh per-request banner/footer on this cached
base via ``watermark.stamp_per_request_copy`` (the "live" stamping — non-deterministic, not cached).
"""

from __future__ import annotations

import datetime
import hashlib
import io
import logging
import uuid
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._pack_enums import (
    PackInclusionStatus,
    PackItemType,
    PackScopeKind,
    PackStatus,
)
from ...db.models._vault_enums import VersionState
from ...db.models.blob import Blob
from ...db.models.document_version import DocumentVersion
from ...db.models.documented_information import DocumentedInformation
from ...db.models.evidence_pack import EvidencePack
from ..vault import storage, watermark
from . import repository as repo

logger = logging.getLogger("easysynq.packs")

_PAGE_W, _PAGE_H = float(letter[0]), float(letter[1])  # (612, 792) points
_MARGIN = 54.0
_LINE = 12.0
_BODY_FONT = "Helvetica"
_TITLE_FONT = "Helvetica-Bold"
_MAX_CHARS = 96  # soft wrap width for the monospace-ish body


def _wrap(text: str) -> list[str]:
    """Hard-wrap a logical line to the page width (cheap char wrap — printable, deterministic)."""
    out: list[str] = []
    while len(text) > _MAX_CHARS:
        out.append(text[:_MAX_CHARS])
        text = text[_MAX_CHARS:]
    out.append(text)
    return out


def _text_pdf(title: str, lines: list[str]) -> bytes:
    """A deterministic (invariant), paginated text PDF for the cover / index / placeholder pages."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    wrapped: list[str] = []
    for ln in lines:
        wrapped.extend(_wrap(ln) if ln else [""])

    def _new_page(with_title: bool) -> float:
        c.setFillColor(colors.black)
        y = _PAGE_H - _MARGIN
        if with_title:
            c.setFont(_TITLE_FONT, 14)
            c.drawString(_MARGIN, y, title[:_MAX_CHARS])
            y -= _LINE * 2
        c.setFont(_BODY_FONT, 9)
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


def _merge(pdfs: list[bytes]) -> bytes:
    """Concatenate PDFs into one. The output id is pinned to a hash of the inputs so an unchanged
    pack re-builds to byte-identical bytes (cache dedup); correctness does not depend on it (we
    content-address the OUTPUT). Encrypted/corrupt member PDFs are skipped with a logged warning."""
    writer = PdfWriter()
    used: list[bytes] = []
    for pdf in pdfs:
        try:
            writer.append(PdfReader(io.BytesIO(pdf)))
            used.append(pdf)
        except Exception:  # noqa: BLE001 — a corrupt/encrypted member must not sink the portfolio
            logger.warning("packs.portfolio_member_skipped")
    digest = hashlib.sha256(b"".join(used)).digest()[:16]
    writer._ID = ArrayObject([ByteStringObject(digest), ByteStringObject(digest)])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _is_dossier_scope(pack: EvidencePack) -> bool:
    return pack.scope_kind in (PackScopeKind.FINDING, PackScopeKind.CAPA)


def _cover_lines(pack: EvidencePack, generated_at: datetime.datetime | None) -> list[str]:
    gap = pack.gap_summary or {}
    excl = pack.exclusion_summary or {}
    # FINDING/CAPA packs are sealed with the v2 scheme (the dossier digest folds into the content
    # hash); CLAUSE/PROCESS stay v1. The PDF's verify instruction MUST match, or a re-verifier
    # computes the wrong hash and sees a false tamper signal.
    scheme = "easysynq.evidencepack.v2" if _is_dossier_scope(pack) else "easysynq.evidencepack.v1"
    if gap.get("applicable", True) is False:
        gap_line = "Gap report:    N/A (finding/CAPA scope)"
    else:
        gap_line = (
            f"Gap report:    {gap.get('gap_count', 0)} of {gap.get('in_scope_star_clauses', 0)} "
            "in-scope mandatory clauses lacking current evidence"
        )
    dossier_note = (
        ["", "Dossier:       the finding/CAPA narrative + e-signatures are in the ZIP variant."]
        if _is_dossier_scope(pack)
        else []
    )
    return [
        "EVIDENCE PACK — controlled audit bundle (portfolio)",
        "",
        f"Pack ID:       {pack.id}",
        f"Title:         {pack.title}",
        f"Scope:         {pack.scope_kind.value} {pack.scope_selector}",
        f"Period:        {pack.period_start} .. {pack.period_end} (by captured_at)",
        f"Generated at:  {generated_at.isoformat() if generated_at else '—'}",
        f"Content hash:  {pack.content_hash}",
        f"Records:       {pack.item_count} included",
        f"Excluded:      {excl.get('permission_count', 0)} (permission), "
        f"{excl.get('absence_count', 0)} (absence)",
        gap_line,
        *dossier_note,
        "",
        "This portfolio is a printable, derived view of the sealed pack. The canonical, content-",
        "addressed artefact is the ZIP variant (its SHA-256 is the integrity anchor); record",
        "evidence files are bundled there. Verify by re-hashing the ZIP manifest content list with",
        f"the {scheme} scheme and comparing to the Content hash above.",
    ]


def _index_lines(
    records: list[tuple[Any, DocumentedInformation]],
    versions: list[tuple[DocumentVersion, DocumentedInformation | None]],
    pack: EvidencePack,
) -> list[str]:
    gap = pack.gap_summary or {}
    excl = pack.exclusion_summary or {}
    lines = ["Included records", ""]
    for record, base in records:
        cap = record.captured_at.date().isoformat() if record.captured_at else "—"
        lines.append(f"  • {base.identifier}  {record.record_type.value}  {cap}  {base.title}")
        lines.append(f"      content_hash: {record.content_hash}")
    lines += ["", "Governing document versions (editions in force at capture)", ""]
    for version, vbase in versions:
        ident = vbase.identifier if vbase is not None else str(version.document_id)
        title = vbase.title if vbase is not None else ""
        eff = version.effective_from.date().isoformat() if version.effective_from else "—"
        lines.append(f"  • {ident}  Rev {version.revision_label}  effective {eff}  {title}")
    lines += ["", "Gap report (mandatory ★ clauses lacking current evidence)", ""]
    if gap.get("applicable", True) is False:
        lines.append("  N/A — gap analysis does not apply to finding/CAPA scope.")
    else:
        for clause in gap.get("clauses", []) or []:
            lines.append(
                f"  • {clause.get('number')}  {clause.get('title')}  [{clause.get('status')}]"
            )
        if not (gap.get("clauses") or []):
            lines.append("  (none — all in-scope mandatory clauses have current evidence)")
    lines += ["", "Exclusion report (R28 — never silently dropped)", ""]
    lines.append(f"  Excluded (permission): {excl.get('permission_count', 0)}")
    for rid in excl.get("permission", []) or []:
        lines.append(f"    - {rid}")
    lines.append(f"  Excluded (absence): {excl.get('absence_count', 0)}")
    for rid in excl.get("absence", []) or []:
        lines.append(f"    - {rid}")
    return lines


async def _version_pages(version: DocumentVersion, base: DocumentedInformation | None) -> bytes:
    """One controlled document's pages: its cached §11.3-stamped rendition (a truthful overlay if it
    no longer governs), or an honest placeholder when no rendition is cached. Renderer-free."""
    ident = base.identifier if base is not None else str(version.document_id)
    title = base.title if base is not None else ""
    header = f"Controlled document: {ident} — {title} (Rev {version.revision_label})"
    if version.rendition_blob_sha256:
        try:
            pdf = await storage.fetch_bytes(
                version.rendition_blob_sha256, bucket=get_settings().s3_bucket_renditions
            )
        except Exception:  # noqa: BLE001 — cached rendition vanished → fall through to placeholder
            pdf = None
        if pdf is not None:
            if version.version_state is not VersionState.Effective:
                state = version.version_state.value
                pdf = watermark.stamp_per_request_copy(
                    pdf,
                    banner=f"{state.upper()} — this edition no longer governs",
                    footer_note=f"Edition state at pack seal: {state}. Verify current rev via QR.",
                )
            return pdf
    return _text_pdf(
        "Controlled document (rendition pending)",
        [
            header,
            "",
            "A controlled PDF rendition is not yet cached for this edition.",
            "Its source bytes are included in the ZIP variant of this pack.",
        ],
    )


async def assemble(session: AsyncSession, pack: EvidencePack) -> bytes:
    """Assemble the portfolio PDF from the SEALED pack's membership (reads only — no DB writes)."""
    items = await repo.list_pack_items(session, pack.id)
    record_ids = [
        i.record_id
        for i in items
        if i.item_type is PackItemType.RECORD
        and i.inclusion_status is PackInclusionStatus.INCLUDED
        and i.record_id is not None
    ]
    version_ids = [
        i.version_id
        for i in items
        if i.item_type is PackItemType.DOCUMENT_VERSION and i.version_id is not None
    ]
    records = await repo.get_records_with_base(session, record_ids)
    versions_by_id = await repo.get_document_versions(session, version_ids)
    base_docs = await repo.get_base_docs(session, [v.document_id for v in versions_by_id.values()])
    ordered = sorted(
        versions_by_id.values(),
        key=lambda v: (
            base_docs[v.document_id].identifier if v.document_id in base_docs else "",
            v.revision_label,
        ),
    )
    version_pairs = [(v, base_docs.get(v.document_id)) for v in ordered]

    pdfs = [
        _text_pdf("Evidence Pack — Cover", _cover_lines(pack, pack.generated_at)),
        _text_pdf("Traceability index", _index_lines(records, version_pairs, pack)),
    ]
    for version, base in version_pairs:
        pdfs.append(await _version_pages(version, base))
    return _merge(pdfs)


async def build_and_cache_portfolio(session: AsyncSession, pack_id: uuid.UUID) -> None:
    """Build Stage 2: assemble the PDF portfolio + cache it (idempotent, best-effort). Runs once the
    seal commits — never blocks/fails the canonical pack. Skips if not SEALED or already cached."""
    pack = await repo.get_pack(session, pack_id, for_update=True)
    if (
        pack is None
        or pack.status is not PackStatus.SEALED
        or pack.portfolio_blob_sha256 is not None
    ):
        await session.rollback()
        return
    try:
        pdf = await assemble(session, pack)
    except Exception as exc:  # noqa: BLE001 — best-effort; the ZIP remains the canonical artefact
        logger.warning(
            "packs.portfolio_build_failed",
            extra={"extra_fields": {"pack_id": str(pack_id), "error": f"{type(exc).__name__}"}},
        )
        await session.rollback()
        return
    bucket = get_settings().s3_bucket_renditions
    sha = hashlib.sha256(pdf).hexdigest()
    await storage.put_bytes(pdf, sha, bucket=bucket, content_type="application/pdf")
    await session.execute(
        pg_insert(Blob)
        .values(
            sha256=sha,
            org_id=pack.org_id,
            size_bytes=len(pdf),
            mime_type="application/pdf",
            bucket=bucket,
            object_key=sha,
            worm_locked=False,  # derived + rebuildable (doc 14 §5.4)
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
    )
    pack.portfolio_blob_sha256 = sha
    await session.commit()
