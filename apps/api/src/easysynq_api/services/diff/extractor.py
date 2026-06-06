"""The text-extraction seam for version diff (slice S-dcr-3a).

Diff text is extracted **on-demand** from each version's WORM-immutable source blob (no
per-version text is persisted; a cache is a v1.x optimization). The default extractor reuses the
S-ing-2 Tika ``-full`` sidecar via ``TikaExtractorProvider`` (native text, OCR off — diff cares
about "what the procedure says", and OCR is slow); it **fails closed** (Tika down /
non-extractable → ``None`` → the diff degrades to ``text_diff: unavailable`` while the metadata
diff still works).

The seam is a module-level get/set (the ``RenderSink`` / ``SignatureEventSink`` precedent) so an
integration test can inject a deterministic fake extractor and prove the redline without a live
Tika.
"""

from __future__ import annotations

from typing import Protocol

from ...domain.ingestion.extractor import ExtractInput
from ..ingestion.extractor_tika import TikaExtractorProvider


class TextExtractor(Protocol):
    async def extract_text(
        self, *, data: bytes, mime_type: str | None, filename: str
    ) -> str | None:
        """Return the extracted plain text, or ``None`` when unavailable / non-extractable. NEVER
        raises (fail-closed — the diff degrades gracefully)."""
        ...


class TikaTextExtractor:
    """The default — the S-ing-2 Tika sidecar (native text, OCR off)."""

    async def extract_text(
        self, *, data: bytes, mime_type: str | None, filename: str
    ) -> str | None:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else None
        meta = ExtractInput(
            rel_path=filename,
            filename=filename,
            ext=ext,
            mime_type=mime_type,
            size_bytes=len(data),
        )
        result = await TikaExtractorProvider().extract(
            data, meta, ocr_enabled=False, ocr_language="eng"
        )
        if result.failed:
            return None
        return result.full_text


_extractor: TextExtractor = TikaTextExtractor()


def get_text_extractor() -> TextExtractor:
    return _extractor


def set_text_extractor(extractor: TextExtractor) -> None:
    """Override the diff text extractor (tests inject a deterministic fake)."""
    global _extractor
    _extractor = extractor
