"""The controlled-copy watermark/stamp overlay (slice S7b, doc 04 §11.2/§11.3).

``stamp_controlled_copy`` takes a base PDF (Gotenberg-converted source, or a passed-through PDF) and
returns a new PDF with the §11.3 band drawn onto **every page** — a header (identifier · title ·
classification), a faint diagonal ``{copy_status}`` watermark, and a footer carrying the
**mandatory, non-removable** Rev + Effective + copy_status (plus Owner and Page n-of-N). The band is
drawn into the page content stream (not document metadata), so it cannot be stripped without
re-rendering from the vault.

License-safe + deterministic: reportlab (BSD) draws the overlay in **invariant** mode (no embedded
timestamp / fixed doc id); pypdf (BSD-3) merges it onto each page and the output id is pinned to a
hash of the inputs — so an identical (source, request) yields **byte-identical** output, making the
rendition genuinely content-addressable. NO PyMuPDF/AGPL.

``stamp_per_request_copy`` (slice S7d, doc 04 §11.2 export/print rows) is the **per-request**
sibling: it overlays only a prominent top banner + a single footer note (e.g. "UNCONTROLLED WHEN
PRINTED — valid as of {date}" + "Exported {ts} by {user}") onto the **already-banded** cached
controlled-copy PDF — it draws no second band and no second QR (the cached base already carries
them). Its output embeds a per-request timestamp + username, so it is **inherently
non-deterministic across requests** and MUST NOT be content-addressed / cached (it never enters
``rendition_blob_sha256``). It is deterministic only for fixed (base, banner, footer_note).
"""

from __future__ import annotations

import hashlib
import io

import segno
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .render import RenderRequest

_QR_SIZE = 42.0  # points

_HEADER_FONT = "Helvetica-Bold"
_FOOTER_FONT = "Helvetica"
_MARGIN = 24.0  # points from the page edge


def _effective_date(request: RenderRequest) -> str:
    # UTC calendar date (org-tz display per R8 deferred); always truthful for this exact rendition.
    return request.effective_from.date().isoformat() if request.effective_from else "—"


def _draw_overlay(
    width: float, height: float, page_no: int, total: int, request: RenderRequest
) -> bytes:
    """One overlay page sized to (width, height) carrying the band + the diagonal watermark."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height), invariant=1)

    # Header band: identifier — title (left), classification (right).
    c.setFont(_HEADER_FONT, 8)
    c.setFillColor(colors.HexColor("#444444"))
    c.drawString(_MARGIN, height - _MARGIN, f"{request.identifier} — {request.title}"[:110])
    c.drawRightString(width - _MARGIN, height - _MARGIN, request.classification)

    # Diagonal watermark: the copy_status, faint, centred, 45°. Non-suppressible for OBSOLETE/
    # SUPERSEDED (the caller passes that copy_status); the mirror always passes "CONTROLLED COPY".
    c.saveState()
    c.translate(width / 2.0, height / 2.0)
    c.rotate(45)
    c.setFont(_HEADER_FONT, 52)
    c.setFillColor(colors.HexColor("#C8C8C8"))
    c.setFillAlpha(0.30)
    c.drawCentredString(0, 0, request.copy_status)
    c.restoreState()

    # Footer band: the mandatory non-removable Rev/Effective/copy_status + Owner + Page n-of-N.
    c.setFillColor(colors.HexColor("#444444"))
    c.setFont(_FOOTER_FONT, 7)
    line1 = (
        f"Rev {request.revision_label} · Effective {_effective_date(request)} · "
        f"Owner {request.owner}"
    )
    line2 = f"Controlled in EasySynQ · {request.copy_status} · Page {page_no} of {total}"
    # S7c: the verify line + a QR of the signed verify URL (doc 05 §6.4) — scan to check currency.
    line3 = (
        "Scan the QR to verify this revision's currency"
        if request.verify_url
        else "Verify current revision in EasySynQ"
    )
    c.drawString(_MARGIN, _MARGIN + 16, line1)
    c.drawString(_MARGIN, _MARGIN + 8, line2)
    c.drawString(_MARGIN, _MARGIN, line3)
    if request.verify_url:
        _draw_qr(c, request.verify_url, width)

    c.showPage()
    c.save()
    return buf.getvalue()


def _draw_qr(c: canvas.Canvas, url: str, width: float) -> None:
    """Bottom-right QR of the verify URL (deterministic PNG via segno)."""
    png = io.BytesIO()
    segno.make(url, error="m").save(png, kind="png", scale=3, border=1)
    png.seek(0)
    c.drawImage(
        ImageReader(png), width - _MARGIN - _QR_SIZE, _MARGIN, _QR_SIZE, _QR_SIZE, mask="auto"
    )


def stamp_controlled_copy(base_pdf: bytes, request: RenderRequest) -> bytes:
    """Stamp the §11.3 band + diagonal watermark onto every page of ``base_pdf``. Deterministic:
    identical inputs → identical bytes."""
    reader = PdfReader(io.BytesIO(base_pdf))
    total = len(reader.pages)
    writer = PdfWriter()

    for index, page in enumerate(reader.pages):
        box = page.mediabox
        overlay = PdfReader(
            io.BytesIO(
                _draw_overlay(float(box.width), float(box.height), index + 1, total, request)
            )
        )
        # Attach the page to the writer FIRST, then merge the overlay onto the writer's copy
        # (pypdf 7 deprecates merging onto a page not yet owned by a writer).
        writer.add_page(page)
        writer.pages[index].merge_page(overlay.pages[0], over=True)

    # Pin the document id to a hash of the inputs so the output is byte-reproducible (content
    # addressing). reportlab's invariant=1 already fixed the overlay's timestamp/id.
    digest = hashlib.sha256(
        base_pdf
        + request.version_id.bytes
        + request.copy_status.encode()
        + (request.verify_url or "").encode()
    ).digest()[:16]
    writer._ID = ArrayObject([ByteStringObject(digest), ByteStringObject(digest)])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


_BANNER_FONT = "Helvetica-Bold"
_BANNER_COLOR = colors.HexColor("#B00020")  # attention red — the per-request copy-status warning


def _draw_dynamic_overlay(width: float, height: float, banner: str, footer_note: str) -> bytes:
    """One overlay page carrying ONLY the per-request additions (S7d): a prominent top banner + a
    single footer note, sized to (width, height). Drawn on top of the already-banded base, so it
    deliberately adds nothing else (no header/diagonal/QR — the cached base owns those)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height), invariant=1)

    # Top banner: a faint white backing strip (so it stays legible over content) + bold red text,
    # centred just below where the base band's header sits. Drawn on every page.
    band_y = height - _MARGIN - 16
    c.setFillColor(colors.white)
    c.setFillAlpha(0.65)
    c.rect(_MARGIN, band_y - 4, width - 2 * _MARGIN, 16, stroke=0, fill=1)
    c.setFillAlpha(1.0)
    c.setFillColor(_BANNER_COLOR)
    c.setFont(_BANNER_FONT, 10)
    c.drawCentredString(width / 2.0, band_y, banner[:120])

    # Footer note: one line just above the base band's 3-line footer (which sits at _MARGIN..+16),
    # left-aligned so it clears the bottom-right verify QR.
    c.setFillColor(colors.HexColor("#444444"))
    c.setFont(_FOOTER_FONT, 7)
    c.drawString(_MARGIN, _MARGIN + 26, footer_note[:110])

    c.showPage()
    c.save()
    return buf.getvalue()


def stamp_per_request_copy(base_pdf: bytes, *, banner: str, footer_note: str) -> bytes:
    """Overlay the per-request ``banner`` + ``footer_note`` onto every page of an already-banded
    ``base_pdf`` (the cached controlled-copy rendition). Non-cached by design — the caller passes a
    timestamp/user in the text, so the bytes vary per request. Deterministic for fixed inputs (the
    output id is pinned to a hash of base + banner + footer_note), which the unit tests rely on."""
    reader = PdfReader(io.BytesIO(base_pdf))
    total = len(reader.pages)
    writer = PdfWriter()

    for index in range(total):
        page = reader.pages[index]
        box = page.mediabox
        overlay = PdfReader(
            io.BytesIO(
                _draw_dynamic_overlay(float(box.width), float(box.height), banner, footer_note)
            )
        )
        writer.add_page(page)
        writer.pages[index].merge_page(overlay.pages[0], over=True)

    digest = hashlib.sha256(
        base_pdf + b"\x00" + banner.encode() + b"\x00" + footer_note.encode()
    ).digest()[:16]
    writer._ID = ArrayObject([ByteStringObject(digest), ByteStringObject(digest)])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
