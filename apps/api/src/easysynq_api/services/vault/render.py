"""The rendition seam (slice S7) — turns a version's source bytes into a controlled, watermarked
PDF rendition.

S7 wires the seam; **S7b makes it real.** The default sink is the no-op :class:`LoggingRenderSink`:
it returns ``None``, so the mirror writes the version's **source bytes** and marks the document
``render_status="pending"`` (an honest "not yet rendered" marker — NOT R26's
``no_controlled_rendition``, which is reserved for formats Gotenberg/LibreOffice genuinely cannot
render). When S7b lands, :class:`LoggingRenderSink` is swapped for a Gotenberg-backed sink that
returns the watermarked PDF (the §11.3 header/footer band — Rev + EffectiveDate + copy_status,
non-removable; Obsolete/Superseded stamps non-suppressible) and the mirror writes that instead.

This mirrors how S4 wired ``SignatureEventSink`` as a logging no-op before S5 made it real.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
from typing import Protocol

logger = logging.getLogger("easysynq.vault")


@dataclasses.dataclass(frozen=True, slots=True)
class RenderRequest:
    """Everything a renderer needs to stamp the §11.3 controlled-copy band onto a source blob.

    The no-op default ignores all of it; the fields are present so the S7b renderer needs no new
    seam — the band's mandatory, non-removable payload (``revision_label`` + ``effective_from`` +
    ``copy_status``) is already threaded here (doc 04 §11.3)."""

    identifier: str
    title: str
    revision_label: str
    effective_from: datetime.datetime | None
    classification: str
    # "CONTROLLED COPY" | "SUPERSEDED" | "OBSOLETE" — non-suppressible (doc 04 §11.2)
    copy_status: str
    mime_type: str
    source_filename: str


class RenderSink(Protocol):
    # S7b note: this two-way return (bytes = rendered PDF | None = not rendered) collapses
    # "render pending/failed" and "genuinely non-renderable" into one None. When the Gotenberg sink
    # lands, WIDEN this to a three-way result so a non-renderable format (CAD/proprietary/large
    # media) surfaces as R26 ``no_controlled_rendition`` — NOT as ``render_status="pending"`` (which
    # would falsely imply a watermarked PDF is still coming). doc 04 §11.4 / R26.
    def render(self, request: RenderRequest, source_bytes: bytes) -> bytes | None: ...


class LoggingRenderSink:
    """Default S7 sink — renders nothing. Returns ``None`` so the mirror falls back to source bytes
    + ``render_status="pending"``. Replaced by the Gotenberg-backed sink in S7b."""

    def render(self, request: RenderRequest, source_bytes: bytes) -> None:
        logger.info(
            "vault.render.deferred",
            extra={
                "extra_fields": {
                    "identifier": request.identifier,
                    "revision_label": request.revision_label,
                    "mime_type": request.mime_type,
                    "reason": "renderer deferred to S7b",
                }
            },
        )
        return None


_default_render_sink: RenderSink = LoggingRenderSink()


def get_render_sink() -> RenderSink:
    """The active render sink — overridden in tests / replaced by the Gotenberg sink in S7b."""
    return _default_render_sink
