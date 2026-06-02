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
"""

from __future__ import annotations

import hashlib
import io

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from .render import RenderRequest

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
    line3 = "Verify current revision in EasySynQ"
    c.drawString(_MARGIN, _MARGIN + 16, line1)
    c.drawString(_MARGIN, _MARGIN + 8, line2)
    c.drawString(_MARGIN, _MARGIN, line3)

    c.showPage()
    c.save()
    return buf.getvalue()


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
        base_pdf + request.version_id.bytes + request.copy_status.encode()
    ).digest()[:16]
    writer._ID = ArrayObject([ByteStringObject(digest), ByteStringObject(digest)])

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
