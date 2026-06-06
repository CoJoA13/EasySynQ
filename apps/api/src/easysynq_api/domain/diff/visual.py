"""Pure page-image rasterization + diff (slice S-dcr-3b; doc 05 §8.1). No DB/network I/O.

Rasterizes a PDF to per-page RGB images via **pypdfium2** (Apache/BSD, prebuilt wheels bundling
PDFium — NOT PyMuPDF/fitz, so the AGPL guard passes) and diffs two versions' page sets via
**Pillow** ``ImageChops`` (a permissive lib). Per page: ``changed`` + the changed-region bounding
box + a ``diff`` overlay (the ``to`` page with the changed region tinted red — doc 05 §8.1 "page
thumbnails with changed regions highlighted"). Pages beyond the shorter version are added/removed
(changed, the lone image). The service layer (``services/diff/visual``) does the I/O
(fetch/render/cache); this is pure CPU.
"""

from __future__ import annotations

import dataclasses
import io

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw

_SCALE = 2.0  # ~144 dpi (72 dpi native x2) — legible thumbnails without huge images.
_MAX_PAGES = 100  # cap so a pathological doc can't exhaust CPU/memory (logged when truncated).


@dataclasses.dataclass(frozen=True)
class PageDiff:
    page: int
    changed: bool
    from_png: bytes | None  # the old page (None if the page was added in the new version)
    to_png: bytes | None  # the new page (None if the page was removed)
    diff_png: bytes | None  # the new page with changed regions highlighted (or the lone page)


def rasterize(
    pdf: bytes, *, scale: float = _SCALE, max_pages: int = _MAX_PAGES
) -> list[Image.Image]:
    """PDF bytes → per-page RGB PIL images (capped at ``max_pages``)."""
    doc = pdfium.PdfDocument(pdf)
    try:
        count = min(len(doc), max_pages)
        return [doc[i].render(scale=scale).to_pil().convert("RGB") for i in range(count)]
    finally:
        doc.close()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _overlay(to_img: Image.Image, bbox: tuple[int, int, int, int]) -> Image.Image:
    """The ``to`` page with the changed-region bbox tinted translucent-red + outlined."""
    out = to_img.convert("RGB").copy()
    draw = ImageDraw.Draw(out, "RGBA")
    draw.rectangle(bbox, fill=(255, 0, 0, 64), outline=(255, 0, 0, 255), width=3)
    return out


def diff_pages(from_imgs: list[Image.Image], to_imgs: list[Image.Image]) -> list[PageDiff]:
    """Per-page redline of two rasterized versions, aligned by page index."""
    out: list[PageDiff] = []
    for i in range(max(len(from_imgs), len(to_imgs))):
        fi = from_imgs[i] if i < len(from_imgs) else None
        ti = to_imgs[i] if i < len(to_imgs) else None
        if fi is None and ti is not None:  # page added in the new version
            out.append(PageDiff(i, True, None, _png(ti), _png(ti)))
        elif ti is None and fi is not None:  # page removed in the new version
            out.append(PageDiff(i, True, _png(fi), None, _png(fi)))
        elif fi is not None and ti is not None:
            # Align sizes (a re-rendered page may differ by a pixel) before pixel-diffing.
            cmp_to = ti if ti.size == fi.size else ti.resize(fi.size)
            bbox = ImageChops.difference(fi, cmp_to).getbbox()
            changed = bbox is not None
            diff_img = _overlay(ti, bbox) if (changed and bbox is not None) else ti
            out.append(PageDiff(i, changed, _png(fi), _png(ti), _png(diff_img)))
    return out
