"""The rendition seam (slices S7 + S7b) — turns a version's source bytes into a controlled,
watermarked PDF rendition.

S7 wired the seam as a no-op; **S7b makes it real** (:class:`GotenbergRenderSink` in
``render_gotenberg.py``, installed as the worker's default). ``render`` is **async** and returns a
three-way :class:`RenderResult` so the caller (the mirror's ``build_tree``) can tell apart:

* ``RENDERED`` — a watermarked PDF (``pdf`` set); the mirror writes the ``.pdf`` + ``"rendered"``.
* ``PENDING`` — not rendered yet / transient renderer outage; the mirror writes **source bytes** +
  ``render_status="pending"`` and self-heals on the next rebuild.
* ``NON_RENDERABLE`` — a format Gotenberg/LibreOffice genuinely cannot render (R26, doc 04 §11.4);
  the mirror writes **source bytes** + ``"unrenderable"`` + ``no_controlled_rendition=true``.

The two-way ``bytes | None`` of S7 collapsed PENDING and NON_RENDERABLE into one ``None``; the S7
review flagged that R26's flag must be DISTINCT from "pending" — this three-way result is that fix.
The default sink stays the no-op :class:`LoggingRenderSink` (PENDING) so the api/tests never render;
the worker's Beat task + the ``easysynq mirror`` CLI construct :class:`GotenbergRenderSink`
explicitly and pass it to ``sync_mirror`` (the api just presigns the cached rendition).
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import logging
import uuid
from typing import Protocol

logger = logging.getLogger("easysynq.vault")


class RenderStatus(enum.Enum):
    RENDERED = "rendered"
    PENDING = "pending"
    NON_RENDERABLE = "unrenderable"


@dataclasses.dataclass(frozen=True, slots=True)
class RenderResult:
    """The outcome of a render attempt. ``pdf`` is set only when ``status is RENDERED``; ``reason``
    is a short human string (R26) carried only on a NON_RENDERABLE result that wants to explain WHY
    (e.g. an externally-linked source that LibreOffice 8.34 omits) — it lands in the mirror's
    ``metadata.json`` for an auditor. RENDERED/PENDING leave it None."""

    status: RenderStatus
    pdf: bytes | None = None
    reason: str | None = None

    @classmethod
    def rendered(cls, pdf: bytes) -> RenderResult:
        return cls(RenderStatus.RENDERED, pdf)

    @classmethod
    def pending(cls) -> RenderResult:
        return cls(RenderStatus.PENDING, None)

    @classmethod
    def non_renderable(cls, reason: str | None = None) -> RenderResult:
        return cls(RenderStatus.NON_RENDERABLE, None, reason)


@dataclasses.dataclass(frozen=True, slots=True)
class RenderRequest:
    """Everything the renderer needs to convert + stamp the §11.3 controlled-copy band. The band's
    mandatory, non-removable payload (``revision_label`` + ``effective_from`` + ``copy_status``) is
    threaded here (doc 04 §11.3); ``version_id`` keys the overlay's deterministic document id. The
    sink is PURE (convert + overlay) — ``build_tree`` owns caching/persistence."""

    identifier: str
    title: str
    revision_label: str
    effective_from: datetime.datetime | None
    classification: str
    # "CONTROLLED COPY" | "SUPERSEDED" | "OBSOLETE" — non-suppressible (doc 04 §11.2)
    copy_status: str
    owner: str
    mime_type: str
    source_filename: str
    version_id: uuid.UUID
    # S7c: the public verify URL (token + QR) drawn into the footer; None → plaintext placeholder.
    verify_url: str | None = None


class RenderSink(Protocol):
    async def render(self, request: RenderRequest, source_bytes: bytes) -> RenderResult: ...


class LoggingRenderSink:
    """Default sink — renders nothing, returns ``PENDING`` so the mirror falls back to source bytes.
    The api keeps this default (it never renders); the worker/CLI pass the Gotenberg sink (S7b)."""

    async def render(self, request: RenderRequest, source_bytes: bytes) -> RenderResult:
        logger.info(
            "vault.render.deferred",
            extra={
                "extra_fields": {
                    "identifier": request.identifier,
                    "revision_label": request.revision_label,
                    "mime_type": request.mime_type,
                    "reason": "no render sink installed",
                }
            },
        )
        return RenderResult.pending()


_default_render_sink: RenderSink = LoggingRenderSink()


def get_render_sink() -> RenderSink:
    """The active render sink — the no-op default unless overridden. The render-bearing entrypoints
    (Beat task, CLI) pass :class:`GotenbergRenderSink` to ``sync_mirror`` explicitly rather than
    relying on this default, so the api stays a pure non-renderer."""
    return _default_render_sink


def set_render_sink(sink: RenderSink) -> RenderSink:
    """Swap the process-wide render sink (a test seam — tests inject a stub). Returns the previous
    sink so the caller can restore it."""
    global _default_render_sink
    previous = _default_render_sink
    _default_render_sink = sink
    return previous
