"""S7b unit proofs — the controlled-copy overlay + the Gotenberg render sink (no DB / MinIO).

The §11.3 band carries the mandatory non-removable Rev/EffectiveDate/copy_status on every page; the
overlay is byte-deterministic (content-addressable); the Gotenberg sink maps convert outcomes to the
three-way RenderResult (RENDERED / NON_RENDERABLE for R26 / PENDING for a transient outage) — proven
against a mocked Gotenberg, so no live renderer is needed. A license guard fails if PyMuPDF/AGPL
sneaks into the lockfile.
"""

from __future__ import annotations

import datetime
import io
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from easysynq_api.services.vault import render_gotenberg
from easysynq_api.services.vault.render import RenderRequest, RenderStatus
from easysynq_api.services.vault.render_gotenberg import GotenbergRenderSink
from easysynq_api.services.vault.watermark import stamp_controlled_copy, stamp_per_request_copy


def _req(
    copy_status: str = "CONTROLLED COPY",
    mime: str = "application/pdf",
    verify_url: str | None = None,
) -> RenderRequest:
    return RenderRequest(
        identifier="SOP-PUR-014",
        title="Purchasing Procedure",
        revision_label="Rev C",
        effective_from=datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC),
        classification="Internal",
        copy_status=copy_status,
        owner="p.author",
        mime_type=mime,
        source_filename="x.pdf",
        version_id=uuid.UUID(int=7),
        verify_url=verify_url,
    )


def test_watermark_embeds_verify_qr() -> None:
    """[S7c] With a verify_url, the footer carries the scan hint + a QR image; deterministic."""
    url = "http://localhost/api/v1/verify?t=" + "A" * 171
    a = stamp_controlled_copy(_pdf(pages=1), _req(verify_url=url))
    assert a == stamp_controlled_copy(_pdf(pages=1), _req(verify_url=url))  # deterministic
    page = PdfReader(io.BytesIO(a)).pages[0]
    assert "Scan the QR" in page.extract_text()
    xobjects = page.get("/Resources", {}).get("/XObject")
    assert xobjects is not None
    assert any(xobjects[k].get("/Subtype") == "/Image" for k in xobjects), "no QR image embedded"


def _pdf(pages: int = 2, body: str = "Body") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792), invariant=1)
    for i in range(pages):
        c.drawString(72, 700, f"{body} {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


# --- watermark overlay -------------------------------------------------------------------


def test_watermark_band_carries_rev_effective_copystatus() -> None:
    """[HEADLINE] The mandatory non-removable band (Rev + EffectiveDate + copy_status) is on EVERY
    page, plus identifier + Page n-of-N; copy_status appears twice (diagonal watermark + footer)."""
    stamped = stamp_controlled_copy(_pdf(pages=3), _req())
    reader = PdfReader(io.BytesIO(stamped))
    assert len(reader.pages) == 3
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        for needle in ("Rev C", "Effective 2026-06-02", "CONTROLLED COPY", "SOP-PUR-014"):
            assert needle in text, f"page {i + 1} missing {needle!r}"
        assert f"Page {i + 1} of 3" in text
        # copy_status is drawn BOTH as the diagonal watermark AND in the footer — prove both exist.
        assert text.count("CONTROLLED COPY") >= 2, f"page {i + 1}: diagonal watermark missing"


def test_watermark_band_is_positioned_on_page() -> None:
    """The band text is drawn within the page (header near the top, footer near the bottom) —
    defeats an off-page regression that per-page extract_text alone would miss."""
    ys: list[float] = []

    def _visit(text: str, _cm: object, tm: list[float], _font: object, _size: object) -> None:
        if text.strip():
            ys.append(tm[5])  # tm[5] = the text's y translation, in page points

    reader = PdfReader(io.BytesIO(stamp_controlled_copy(_pdf(pages=1), _req())))
    reader.pages[0].extract_text(visitor_text=_visit)
    assert any(y < 60 for y in ys), "footer band not near the page bottom"
    assert any(y > 740 for y in ys), "header band not near the page top (792pt page)"
    assert all(0 <= y <= 792 for y in ys), "text drawn off the page"


def test_obsolete_superseded_watermark_renders() -> None:
    """The stamp is parameterized on copy_status — OBSOLETE/SUPERSEDED render their diagonal text
    (used by the deferred in-app/export path; the mirror only ever passes CONTROLLED COPY)."""
    for status in ("OBSOLETE", "SUPERSEDED"):
        stamped = stamp_controlled_copy(_pdf(pages=1), _req(copy_status=status))
        assert status in PdfReader(io.BytesIO(stamped)).pages[0].extract_text()


def test_overlay_deterministic() -> None:
    """Identical (source, request) → byte-identical output, so a re-render after a cache miss is
    reproducible (guards reportlab invariant mode + the pinned pypdf trailer /ID)."""
    base = _pdf()
    assert stamp_controlled_copy(base, _req()) == stamp_controlled_copy(base, _req())


# --- S7d per-request export/print overlay ------------------------------------------------

_BANNER = "UNCONTROLLED WHEN PRINTED — valid as of 2026-06-02"
_FOOTER = "Exported 2026-06-02T10:00:00+00:00 by p.author"


def test_per_request_stamp_adds_banner_and_footer_every_page() -> None:
    """[S7d] The per-request overlay draws the banner + footer note on EVERY page of the base."""
    out = stamp_per_request_copy(_pdf(pages=3), banner=_BANNER, footer_note=_FOOTER)
    reader = PdfReader(io.BytesIO(out))
    assert len(reader.pages) == 3
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        assert "UNCONTROLLED WHEN PRINTED" in text, f"page {i + 1} missing banner"
        assert "Exported 2026-06-02T10:00:00+00:00 by p.author" in text, f"page {i + 1} no footer"


def test_per_request_stamp_overlays_on_banded_base_without_removing_it() -> None:
    """[S7d] The export banner is overlaid onto the cached CONTROLLED COPY — keeping the original
    provenance band AND adding the uncontrolled banner (the dual-marking design)."""
    banded = stamp_controlled_copy(_pdf(pages=1), _req())  # the worker's cached controlled copy
    out = stamp_per_request_copy(banded, banner=_BANNER, footer_note=_FOOTER)
    text = PdfReader(io.BytesIO(out)).pages[0].extract_text()
    assert "CONTROLLED COPY" in text  # the base band survives the overlay
    assert "UNCONTROLLED WHEN PRINTED" in text  # the per-request export banner is added on top


def test_per_request_stamp_deterministic_for_fixed_inputs() -> None:
    """[S7d] Fixed (base, banner, footer_note) → byte-identical (testable); the per-request variance
    comes only from the timestamp/user the caller bakes into the text."""
    base = _pdf()
    assert stamp_per_request_copy(base, banner=_BANNER, footer_note=_FOOTER) == (
        stamp_per_request_copy(base, banner=_BANNER, footer_note=_FOOTER)
    )


def test_per_request_stamp_varies_with_footer() -> None:
    """[S7d] A different footer (different ts/user) → different bytes, so the export is genuinely
    per-request and must never be content-addressed / cached like the controlled copy."""
    base = _pdf()
    a = stamp_per_request_copy(base, banner=_BANNER, footer_note="Exported T1 by u")
    b = stamp_per_request_copy(base, banner=_BANNER, footer_note="Exported T2 by u")
    assert a != b


# --- Gotenberg sink (mocked) -------------------------------------------------------------


def _mock_gotenberg(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*_args: object, **_kw: object) -> httpx.AsyncClient:
        return real(transport=transport)

    monkeypatch.setattr(render_gotenberg.httpx, "AsyncClient", factory)


async def test_gotenberg_pdf_passthrough_is_rendered() -> None:
    """An application/pdf source skips Gotenberg (passthrough) and is overlaid → RENDERED."""
    result = await GotenbergRenderSink().render(_req(mime="application/pdf"), _pdf())
    assert result.status is RenderStatus.RENDERED
    assert result.pdf is not None and result.pdf[:5] == b"%PDF-"
    assert "CONTROLLED COPY" in PdfReader(io.BytesIO(result.pdf)).pages[0].extract_text()


async def test_octet_stream_short_circuits_non_renderable() -> None:
    """A pre-declared non-renderable mime is R26 without any Gotenberg round-trip."""
    result = await GotenbergRenderSink().render(_req(mime="application/octet-stream"), b"\x00\x01")
    assert result.status is RenderStatus.NON_RENDERABLE


async def test_gotenberg_200_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 from Gotenberg (office→PDF) is overlaid → RENDERED. Also proves the sink POSTs to the
    LibreOffice route with the right upload filename (so LibreOffice picks the docx filter)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/forms/libreoffice/convert"
        assert b'filename="source.docx"' in request.content
        return httpx.Response(200, content=_pdf())

    _mock_gotenberg(monkeypatch, _handler)
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    result = await GotenbergRenderSink().render(_req(mime=mime), b"docx-bytes")
    assert result.status is RenderStatus.RENDERED
    assert (
        result.pdf is not None
        and "CONTROLLED COPY" in PdfReader(io.BytesIO(result.pdf)).pages[0].extract_text()
    )


async def test_gotenberg_5xx_is_non_renderable(monkeypatch: pytest.MonkeyPatch) -> None:
    """[R26] A Gotenberg rejection (LibreOffice can't convert the input) → NON_RENDERABLE."""
    _mock_gotenberg(monkeypatch, lambda _r: httpx.Response(500, text="conversion error"))
    result = await GotenbergRenderSink().render(_req(mime="application/vnd.ms-works"), b"junk")
    assert result.status is RenderStatus.NON_RENDERABLE


async def test_gotenberg_503_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 503 (renderer busy/unhealthy) → PENDING (transient; the next sync retries)."""
    _mock_gotenberg(monkeypatch, lambda _r: httpx.Response(503))
    result = await GotenbergRenderSink().render(_req(mime="text/plain"), b"hello")
    assert result.status is RenderStatus.PENDING


async def test_gotenberg_unreachable_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport error (renderer down) → PENDING, never a failure that breaks the sync."""

    def _boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("renderer down")

    _mock_gotenberg(monkeypatch, _boom)
    result = await GotenbergRenderSink().render(_req(mime="text/plain"), b"hello")
    assert result.status is RenderStatus.PENDING


async def test_corrupt_pdf_is_non_renderable() -> None:
    """[R26] A passthrough PDF the overlay cannot open (corrupt bytes) → NON_RENDERABLE."""
    result = await GotenbergRenderSink().render(_req(mime="application/pdf"), b"not a real pdf")
    assert result.status is RenderStatus.NON_RENDERABLE


async def test_encrypted_pdf_is_non_renderable() -> None:
    """[R26] A genuinely password-encrypted passthrough PDF (overlay can't open it) → R26."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("a-password")
    buf = io.BytesIO()
    writer.write(buf)
    result = await GotenbergRenderSink().render(_req(mime="application/pdf"), buf.getvalue())
    assert result.status is RenderStatus.NON_RENDERABLE


def test_structured_data_mimes_are_non_renderable() -> None:
    """[S-rec-3] A Form/Template's controlled source IS its JSON field schema — the mirror must mark
    application/json (and xml) non-renderable (R26, source-only), never route it to LibreOffice."""
    assert render_gotenberg._is_non_renderable("application/json")
    assert render_gotenberg._is_non_renderable("application/xml")
    assert render_gotenberg._is_non_renderable("text/xml")
    # A real office doc stays renderable.
    assert not render_gotenberg._is_non_renderable(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


# --- license guard -----------------------------------------------------------------------


def test_no_pymupdf_or_fitz_in_lockfile() -> None:
    """[LICENSE] PyMuPDF/fitz is AGPL — unacceptable for a self-hosted product. Fail if present."""
    lock = (Path(__file__).resolve().parents[2] / "uv.lock").read_text().lower()
    assert 'name = "pymupdf"' not in lock
    assert 'name = "fitz"' not in lock
