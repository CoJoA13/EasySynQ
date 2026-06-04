"""The ``ExtractorProvider`` seam + its DTOs (slice S-ing-2, doc 09 §3.4, §5).

``ExtractorProvider`` is the reserved Stage-2 pluggability seam (doc 09 §3.4): v1 ships the
``TikaExtractorProvider`` (``services/ingestion/extractor_tika.py``, an Apache Tika ``-full`` HTTP
sidecar that bundles the extractors + Tesseract OCR); future cloud-OCR / layout-aware extractors are
drop-in implementations of this Protocol with no pipeline rewrite. The Protocol is pure interface;
``ExtractInput``/``ExtractResult`` are the §5.1 feature record (mirrors the ``SourceProvider``).

The result deliberately carries no DB enum — the service maps ``ocr_used``/``failed``/text-presence
onto the ``import_extract_status`` (keeps this domain module free of ``db.models``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ExtractInput:
    """The minimal file context the extractor needs to choose a strategy (doc 09 §5.2)."""

    rel_path: str
    filename: str
    ext: str | None
    mime_type: str | None
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ExtractResult:
    """The §5.1 extraction output. ``failed`` (corrupt/unknown sub-format) never fails the run
    (§5.3) — the classifier falls back to filename/path-only signals. ``ocr_confidence`` is
    best-effort (Tika may not surface per-doc Tesseract confidence)."""

    full_text: str | None = None
    header_block: str | None = None
    embedded_props: Mapping[str, Any] = field(default_factory=dict)
    language: str | None = None
    structure_hints: Mapping[str, Any] = field(default_factory=dict)
    ocr_used: bool = False
    ocr_confidence: float | None = None
    char_count: int = 0
    page_count: int | None = None
    failed: bool = False
    error: str | None = None
    extractor_version: str | None = None


@runtime_checkable
class ExtractorProvider(Protocol):
    """The reserved extractor seam (doc 09 §3.4). ``extract`` takes the staged bytes + the file
    context + the run's OCR config and returns the §5.1 feature record; it must NEVER raise on a
    corrupt/unsupported file — it returns ``ExtractResult(failed=True, error=...)`` (§5.3)."""

    async def extract(
        self, data: bytes, meta: ExtractInput, *, ocr_enabled: bool, ocr_language: str
    ) -> ExtractResult: ...
