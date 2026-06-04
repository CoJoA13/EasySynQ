"""The v1 ``ExtractorProvider`` — an Apache Tika ``-full`` HTTP sidecar (slice S-ing-2, doc 09 §5).

``TikaExtractorProvider`` is the §3.4 ExtractorProvider implementation: it PUTs the staged bytes to
the Tika ``-full`` sidecar's ``/rmeta/text`` endpoint (one JSON call: metadata + text) and
maps the response to an :class:`ExtractResult`. The ``-full`` image bundles Tesseract OCR + the
extractors, so extraction AND OCR run **locally** (no telemetry) over HTTP — the Gotenberg
``render_gotenberg`` sidecar-client precedent.

**§5.2 OCR ladder.** Office/text/other → one native call. PDF → pass 1 ``X-Tika-PDFOcrStrategy:
no_ocr`` (native text + page count); if ``char_count / page_count`` is below the configured
threshold AND the run enabled OCR → pass 2 ``ocr_only`` (``ocr_used=True``). Image → one call (the
sidecar's image parser is OCR-backed). ``ocr_confidence`` is best-effort (Tika does not surface
per-doc Tesseract confidence in ``/rmeta`` — left ``None``). A note on OCR-off: the flag gates the
expensive PDF OCR pass; a standalone image is always OCR'd by the sidecar's image parser.

**Never raises (§5.3).** Any transport/HTTP/parse error → ``ExtractResult(failed=True, error=…)``;
the run continues and the classifier falls back to filename/path-only signals.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import httpx

from ...config import get_settings
from ...domain.ingestion.extractor import ExtractInput, ExtractResult

logger = logging.getLogger("easysynq.ingestion.extract")

_EXTRACTOR_VERSION = "tika-rmeta-1"
_HEADER_BLOCK_CHARS = 1500  # the §5.1 high-signal header slice fed to the classifier
_TIKA_CONTENT_KEY = "X-TIKA:content"
_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "tif", "tiff", "bmp", "gif", "webp"})
_PAGE_COUNT_KEYS = ("xmpTPg:NPages", "Page-Count", "meta:page-count", "pdf:pages", "pdf:Page-Count")
_AUTHOR_KEYS = ("dc:creator", "meta:author", "Author", "creator")
_TITLE_KEYS = ("dc:title", "title", "Title")
_CREATED_KEYS = ("dcterms:created", "meta:creation-date", "Creation-Date")
_MODIFIED_KEYS = ("dcterms:modified", "Last-Modified", "modified")


def _is_pdf(meta: ExtractInput) -> bool:
    return (meta.mime_type or "").startswith("application/pdf") or (meta.ext or "").lower() == "pdf"


def _is_image(meta: ExtractInput) -> bool:
    return (meta.mime_type or "").startswith("image/") or (meta.ext or "").lower() in _IMAGE_EXTS


def _first(meta0: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = meta0.get(k)
        if isinstance(v, list):
            v = v[0] if v else None
        if v:
            return str(v)
    return None


def _page_count(meta0: dict[str, Any]) -> int | None:
    for k in _PAGE_COUNT_KEYS:
        v = meta0.get(k)
        if v is None:
            continue
        try:
            n = int(str(v).split(".")[0])
        except (ValueError, TypeError):
            continue
        if n > 0:
            return n
    return None


class TikaExtractorProvider:
    """Calls the Tika ``-full`` sidecar. ``extract`` honours the §5.2 ladder and never raises."""

    def __init__(self, *, tika_url: str | None = None, timeout: float = 120.0) -> None:
        settings = get_settings()
        self._url = (tika_url or settings.tika_url).rstrip("/")
        self._timeout = timeout
        self._threshold = settings.import_ocr_char_per_page_threshold

    async def extract(
        self, data: bytes, meta: ExtractInput, *, ocr_enabled: bool, ocr_language: str
    ) -> ExtractResult:
        try:
            if _is_pdf(meta):
                native = await self._rmeta(data, meta.mime_type, "no_ocr", ocr_language)
                needs_ocr = (
                    native.page_count is not None
                    and native.page_count > 0
                    and (native.char_count / native.page_count) < self._threshold
                )
                if ocr_enabled and (needs_ocr or native.char_count == 0):
                    ocr = await self._rmeta(data, meta.mime_type, "ocr_only", ocr_language)
                    return replace(ocr, ocr_used=True)
                return native
            if _is_image(meta):
                res = await self._rmeta(data, meta.mime_type, None, ocr_language)
                # The -full sidecar's image parser IS OCR (Tesseract) — OCR was invoked regardless
                # of whether it yielded text (an empty result maps to EMPTY downstream, not OCR).
                return replace(res, ocr_used=True)
            return await self._rmeta(data, meta.mime_type, None, ocr_language)
        except Exception as exc:  # noqa: BLE001 — §5.3: a failed extract never fails the run
            logger.warning(
                "ingestion.extract.failed",
                extra={"extra_fields": {"rel_path": meta.rel_path, "error": repr(exc)[:200]}},
            )
            return ExtractResult(
                failed=True, error=repr(exc)[:500], extractor_version=_EXTRACTOR_VERSION
            )

    async def _rmeta(
        self, data: bytes, mime: str | None, ocr_strategy: str | None, ocr_language: str
    ) -> ExtractResult:
        headers = {"Accept": "application/json"}
        if mime:
            headers["Content-Type"] = mime
        if ocr_language:
            headers["X-Tika-OCRLanguage"] = ocr_language
        if ocr_strategy:
            headers["X-Tika-PDFOcrStrategy"] = ocr_strategy
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.put(f"{self._url}/rmeta/text", content=data, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
        meta0: dict[str, Any] = (
            payload[0]
            if isinstance(payload, list) and payload
            else (payload if isinstance(payload, dict) else {})
        )
        text = str(meta0.get(_TIKA_CONTENT_KEY) or "").strip()
        props: dict[str, Any] = {}
        for key, keys in (
            ("author", _AUTHOR_KEYS),
            ("title", _TITLE_KEYS),
            ("created", _CREATED_KEYS),
            ("modified", _MODIFIED_KEYS),
        ):
            val = _first(meta0, keys)
            if val:
                props[key] = val
        content_type = _first(meta0, ("Content-Type",))
        if content_type:
            props["content_type"] = content_type
        page_count = _page_count(meta0)
        return ExtractResult(
            full_text=text or None,
            header_block=text[:_HEADER_BLOCK_CHARS] or None,
            embedded_props=props,
            language=_first(meta0, ("language",)),
            structure_hints={"page_count": page_count} if page_count is not None else {},
            ocr_used=False,
            char_count=len(text),
            page_count=page_count,
            extractor_version=_EXTRACTOR_VERSION,
        )
