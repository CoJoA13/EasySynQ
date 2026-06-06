"""S-dcr-3b unit proofs — the pure page-image rasterize + diff (``domain/diff/visual``). No I/O."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas

from easysynq_api.domain.diff.visual import PageDiff, diff_pages, rasterize

pytestmark = pytest.mark.unit


def _img(color: tuple[int, int, int], *, mark: bool = False) -> Image.Image:
    img = Image.new("RGB", (80, 100), color)
    if mark:
        ImageDraw.Draw(img).rectangle((10, 10, 40, 40), fill=(0, 0, 0))
    return img


# --- diff_pages ------------------------------------------------------------------------------


def test_diff_pages_identical_is_unchanged() -> None:
    base = _img((255, 255, 255))
    [pd] = diff_pages([base], [base.copy()])
    assert isinstance(pd, PageDiff)
    assert pd.changed is False
    assert pd.from_png and pd.to_png and pd.diff_png  # all three images present


def test_diff_pages_changed_when_pixels_differ() -> None:
    [pd] = diff_pages([_img((255, 255, 255))], [_img((255, 255, 255), mark=True)])
    assert pd.changed is True
    assert pd.diff_png is not None  # the overlay highlights the changed region


def test_diff_pages_added_page() -> None:
    pds = diff_pages([_img((255, 255, 255))], [_img((255, 255, 255)), _img((200, 200, 200))])
    assert len(pds) == 2
    assert pds[1].changed is True
    assert pds[1].from_png is None  # the page did not exist in the old version
    assert pds[1].to_png is not None


def test_diff_pages_removed_page() -> None:
    pds = diff_pages([_img((255, 255, 255)), _img((200, 200, 200))], [_img((255, 255, 255))])
    assert len(pds) == 2
    assert pds[1].changed is True
    assert pds[1].to_png is None  # the page was removed in the new version
    assert pds[1].from_png is not None


# --- rasterize -------------------------------------------------------------------------------


def _one_page_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_rasterize_pdf_to_page_images() -> None:
    imgs = rasterize(_one_page_pdf("hello"))
    assert len(imgs) == 1
    assert imgs[0].mode == "RGB"
    assert imgs[0].width > 0 and imgs[0].height > 0


def test_rasterize_then_diff_detects_text_change() -> None:
    [pd] = diff_pages(rasterize(_one_page_pdf("alpha")), rasterize(_one_page_pdf("bravo")))
    assert pd.changed is True
