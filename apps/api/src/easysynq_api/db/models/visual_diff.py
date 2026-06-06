"""The cached visual page-image diff result (slice S-dcr-3b; doc 05 §8.1, worker-async).

A `visual_diff` row is the deterministic, cached comparison of two immutable versions of one
document (`UNIQUE(from_version_id, to_version_id)` — the idempotency latch + cache key; the
versions never change → the result is cacheable forever). The worker task
(`easysynq.visual_diff`) renders + rasterizes both versions, computes per-page overlays, caches
the page PNGs as content-addressed non-WORM `Blob` rows in the renditions bucket, and flips
`status` Pending → Ready (or Failed / Unavailable). Mutable status (GRANT SELECT,INSERT,UPDATE —
a regenerable cache, NOT an audit record; no append-only REVOKE).

`pages` (JSONB) holds the per-page result: `[{page, changed, from_blob_sha, to_blob_sha,
diff_blob_sha}]` — the page PNGs are streamed via `GET …/visual-diff/page/{n}?layer=…`. The page
`Blob`s are derived + regenerable and NO disposal path deletes a `document_version`'s blobs
(verified S-dcr-3b), so they need no purge wiring (unlike the record structured-pdf rendition).
The blob-row-iff-bytes invariant holds — each PNG's `Blob` row + bytes are written together (the
`_cache_rendition` pattern).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._dcr_enums import VisualDiffStatus, visual_diff_status_enum


class VisualDiff(Base):
    __tablename__ = "visual_diff"
    __table_args__ = (
        UniqueConstraint("from_version_id", "to_version_id", name="uq_visual_diff_from_to"),
        Index("ix_visual_diff_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="RESTRICT", name="fk_visual_diff_from_version"),
        nullable=False,
    )
    to_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="RESTRICT", name="fk_visual_diff_to_version"),
        nullable=False,
    )
    status: Mapped[VisualDiffStatus] = mapped_column(
        visual_diff_status_enum, server_default=text("'Pending'"), nullable=False
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-page result: [{page, changed, from_blob_sha, to_blob_sha, diff_blob_sha}]. NULL until
    # Ready.
    pages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
