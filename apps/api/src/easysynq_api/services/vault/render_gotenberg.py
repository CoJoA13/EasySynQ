"""The real render sink (slice S7b) — Gotenberg office→PDF + the controlled-copy overlay.

``GotenbergRenderSink`` is a **pure** convert+overlay function: it converts the source to a base PDF
via Gotenberg (LibreOffice for office formats, Chromium for HTML, passthrough for PDF) and stamps
the §11.3 band via :func:`watermark.stamp_controlled_copy`. It touches **no DB, no MinIO writes** —
the mirror's ``build_tree`` owns caching + persistence (and the cache-hit short-circuit). That keeps
the sink testable against a mocked Gotenberg with no infra.

The three-way :class:`RenderResult` carries the R26 distinction (doc 04 §11.4): a format that
LibreOffice/Gotenberg rejects, or an encrypted/corrupt passthrough PDF the overlay can't open, is
``NON_RENDERABLE`` (→ ``no_controlled_rendition``); a renderer outage/timeout is ``PENDING`` (→ the
mirror writes source bytes and self-heals on the next rebuild). The worker's Beat task and the
``easysynq mirror`` CLI construct this sink explicitly (``tasks/mirror.py`` / ``cli/mirror.py``);
the api keeps the no-op default (it never renders). ``set_render_sink`` is a test seam.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes

import httpx

from ...config import get_settings
from .render import RenderRequest, RenderResult, RenderStatus
from .watermark import stamp_controlled_copy

logger = logging.getLogger("easysynq.vault")

# Formats Gotenberg/LibreOffice cannot normalize to a controlled PDF (R26) — short-circuit without a
# round-trip. octet-stream is the "unknown binary" stored when no content-type was declared.
_NON_RENDERABLE_PREFIXES = (
    "application/octet-stream",
    "application/zip",
    "application/x-",
    # Structured-data blobs (S-rec-3: a Form/Template's controlled source IS its JSON field schema):
    # mark them non-renderable so the mirror keeps the source bytes + a no_controlled_rendition flag
    # (R26) instead of routing a schema to LibreOffice (host-dependent 4xx-vs-garbage).
    "application/json",
    "application/xml",
    "text/xml",
    "image/vnd.dwg",
    "image/vnd.dxf",
    "model/",
    "video/",
    "audio/",
)

_LIBREOFFICE_ROUTE = "/forms/libreoffice/convert"
_CHROMIUM_HTML_ROUTE = "/forms/chromium/convert/html"

# Explicit, host-independent extensions for the formats we route to LibreOffice (mimetypes.
# guess_extension reads the host's mime DB and varies across machines). The extension is the upload
# filename LibreOffice uses to pick its import filter.
_OFFICE_EXT = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


def _is_non_renderable(mime_type: str) -> bool:
    return any(mime_type.startswith(prefix) for prefix in _NON_RENDERABLE_PREFIXES)


def _route(mime_type: str) -> tuple[str, str]:
    """(Gotenberg route, upload filename). Chromium needs the main file named ``index.html``;
    LibreOffice infers the format from the filename extension (deterministic via _OFFICE_EXT)."""
    if mime_type in ("text/html", "application/xhtml+xml"):
        return _CHROMIUM_HTML_ROUTE, "index.html"
    base = mime_type.split(";")[0].strip()
    ext = _OFFICE_EXT.get(base) or mimetypes.guess_extension(base) or ".bin"
    return _LIBREOFFICE_ROUTE, f"source{ext}"


class GotenbergRenderSink:
    """Convert + overlay. Pure: no DB, no MinIO writes."""

    def __init__(self, *, gotenberg_url: str | None = None, timeout: float = 30.0) -> None:
        self._url = (gotenberg_url or get_settings().gotenberg_url).rstrip("/")
        self._timeout = timeout

    async def render(self, request: RenderRequest, source_bytes: bytes) -> RenderResult:
        mime = request.mime_type
        if _is_non_renderable(mime):
            return RenderResult.non_renderable()

        if mime == "application/pdf":
            base = source_bytes
        else:
            converted = await self._convert(mime, source_bytes)
            if converted.status is not RenderStatus.RENDERED or converted.pdf is None:
                return converted
            base = converted.pdf

        try:
            # reportlab/pypdf is sync CPU work — offload it (like storage's boto3 calls) so it
            # never blocks the event loop.
            stamped = await asyncio.to_thread(stamp_controlled_copy, base, request)
        except Exception:  # noqa: BLE001 — an encrypted/corrupt base the overlay can't open is R26
            logger.warning(
                "vault.render.overlay_failed",
                extra={"extra_fields": {"identifier": request.identifier, "mime": mime}},
            )
            return RenderResult.non_renderable()
        return RenderResult.rendered(stamped)

    async def _convert(self, mime_type: str, source_bytes: bytes) -> RenderResult:
        route, filename = _route(mime_type)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._url}{route}",
                    files={"files": (filename, source_bytes, mime_type)},
                )
        except httpx.TransportError:  # connect/timeout/read/network — renderer down or slow
            logger.warning(
                "vault.render.gotenberg_unreachable", extra={"extra_fields": {"mime": mime_type}}
            )
            return RenderResult.pending()

        if resp.status_code == 200:
            return RenderResult.rendered(resp.content)
        if resp.status_code == 503:  # Gotenberg busy/unhealthy — transient
            return RenderResult.pending()
        # 4xx/5xx: Gotenberg/LibreOffice rejected the input (unsupported format) → R26
        logger.info(
            "vault.render.gotenberg_rejected",
            extra={"extra_fields": {"mime": mime_type, "status": resp.status_code}},
        )
        return RenderResult.non_renderable()
