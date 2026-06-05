"""The Tika extractor + the §5.2 OCR ladder (S-ing-2), against a mocked Tika sidecar.

Mirrors ``test_render.py``'s ``httpx.MockTransport`` pattern (no real Tika in CI — the real path is
validated on the Docker stack). Proves: native single-pass for office; the PDF two-pass OCR ladder
(no_ocr -> ocr_only below the density threshold); image OCR; the page-count-missing fallback; the
OCR-language header; and that any HTTP/transport error degrades to ``failed`` (never raises)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from easysynq_api.domain.ingestion.extractor import ExtractInput
from easysynq_api.services.ingestion import extractor_tika
from easysynq_api.services.ingestion.extractor_tika import TikaExtractorProvider

_Handler = Callable[[httpx.Request], httpx.Response]


def _mock_tika(monkeypatch: pytest.MonkeyPatch, handler: _Handler) -> None:
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*_args: object, **_kw: object) -> httpx.AsyncClient:
        return real(transport=transport)

    monkeypatch.setattr(extractor_tika.httpx, "AsyncClient", factory)


def _meta(filename: str, ext: str, mime: str) -> ExtractInput:
    return ExtractInput(
        rel_path=filename, filename=filename, ext=ext, mime_type=mime, size_bytes=10
    )


async def test_office_native_single_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "X-Tika-PDFOcrStrategy" not in req.headers
        assert req.headers.get("X-Tika-OCRLanguage") == "eng"
        return httpx.Response(
            200,
            json=[
                {"X-TIKA:content": "Hello body text", "xmpTPg:NPages": "2", "dc:creator": "Alice"}
            ],
        )

    _mock_tika(monkeypatch, handler)
    r = await TikaExtractorProvider().extract(
        b"x",
        _meta("a.docx", "docx", "application/vnd.oasis.opendocument.text"),
        ocr_enabled=True,
        ocr_language="eng",
    )
    assert r.full_text == "Hello body text" and r.page_count == 2 and not r.ocr_used
    assert r.embedded_props["author"] == "Alice" and r.char_count > 0 and not r.failed


async def test_pdf_two_pass_ocr_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        strat = req.headers["X-Tika-PDFOcrStrategy"]
        seen.append(strat)
        if strat == "no_ocr":
            return httpx.Response(200, json=[{"X-TIKA:content": "   ", "xmpTPg:NPages": "3"}])
        return httpx.Response(
            200, json=[{"X-TIKA:content": "OCR extracted text", "xmpTPg:NPages": "3"}]
        )

    _mock_tika(monkeypatch, handler)
    r = await TikaExtractorProvider().extract(
        b"x", _meta("scan.pdf", "pdf", "application/pdf"), ocr_enabled=True, ocr_language="eng"
    )
    assert seen == ["no_ocr", "ocr_only"]
    assert r.ocr_used and r.full_text == "OCR extracted text"


async def test_pdf_enough_native_text_no_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Tika-PDFOcrStrategy"] == "no_ocr"  # one pass only
        return httpx.Response(200, json=[{"X-TIKA:content": "native " * 100, "xmpTPg:NPages": "1"}])

    _mock_tika(monkeypatch, handler)
    r = await TikaExtractorProvider().extract(
        b"x", _meta("doc.pdf", "pdf", "application/pdf"), ocr_enabled=True, ocr_language="eng"
    )
    assert not r.ocr_used and r.char_count > 50


async def test_pdf_zero_text_missing_page_count_triggers_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.headers["X-Tika-PDFOcrStrategy"] == "no_ocr":
            return httpx.Response(200, json=[{"X-TIKA:content": ""}])  # no text, no page_count
        return httpx.Response(200, json=[{"X-TIKA:content": "ocr text"}])

    _mock_tika(monkeypatch, handler)
    r = await TikaExtractorProvider().extract(
        b"x", _meta("img.pdf", "pdf", "application/pdf"), ocr_enabled=True, ocr_language="eng"
    )
    assert r.ocr_used and r.full_text == "ocr text"


async def test_pdf_native_text_missing_page_count_no_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Tika-PDFOcrStrategy"] == "no_ocr"  # density unknown → no second pass
        return httpx.Response(200, json=[{"X-TIKA:content": "some native text"}])  # no page_count

    _mock_tika(monkeypatch, handler)
    r = await TikaExtractorProvider().extract(
        b"x", _meta("doc.pdf", "pdf", "application/pdf"), ocr_enabled=True, ocr_language="eng"
    )
    assert not r.ocr_used and r.full_text == "some native text"


async def test_pdf_ocr_disabled_never_ocrs(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Tika-PDFOcrStrategy"] == "no_ocr"
        return httpx.Response(200, json=[{"X-TIKA:content": "", "xmpTPg:NPages": "3"}])

    _mock_tika(monkeypatch, handler)
    r = await TikaExtractorProvider().extract(
        b"x", _meta("scan.pdf", "pdf", "application/pdf"), ocr_enabled=False, ocr_language="eng"
    )
    assert not r.ocr_used


async def test_image_single_pass_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_tika(
        monkeypatch, lambda _r: httpx.Response(200, json=[{"X-TIKA:content": "scanned label"}])
    )
    r = await TikaExtractorProvider().extract(
        b"x", _meta("pic.png", "png", "image/png"), ocr_enabled=True, ocr_language="eng"
    )
    assert r.ocr_used and r.full_text == "scanned label"


async def test_image_ocr_used_true_even_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # An image always goes through the OCR parser → ocr_used reflects INVOCATION, not text presence
    # (an empty result maps to EMPTY downstream, not OCR). The diff-review fix.
    _mock_tika(monkeypatch, lambda _r: httpx.Response(200, json=[{"X-TIKA:content": "  "}]))
    r = await TikaExtractorProvider().extract(
        b"x", _meta("blank.tiff", "tiff", "image/tiff"), ocr_enabled=True, ocr_language="eng"
    )
    assert r.ocr_used and r.full_text is None


async def test_http_error_is_failed_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_tika(monkeypatch, lambda _r: httpx.Response(500, text="boom"))
    r = await TikaExtractorProvider().extract(
        b"x", _meta("a.docx", "docx", "application/msword"), ocr_enabled=True, ocr_language="eng"
    )
    assert r.failed and r.error and r.full_text is None


async def test_transport_error_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("tika down")

    _mock_tika(monkeypatch, boom)
    r = await TikaExtractorProvider().extract(
        b"x", _meta("a.pdf", "pdf", "application/pdf"), ocr_enabled=False, ocr_language="eng"
    )
    assert r.failed
