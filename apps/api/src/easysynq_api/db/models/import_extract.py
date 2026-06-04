"""Per-file extraction output (slice S-ing-2, doc 09 §5, doc 14 §13 §548).

``import_extract`` is the Stage-2 output — one row per extracted ``import_file``, carrying the
text/metadata/OCR result the Stage-3 classifier consumes. Like all ``import_*`` it is **transient
staging** (doc 14 §1.2): mutable, TTL-purgeable, no inbound vault FK.

``full_text`` is stored **inline** (not a blob reference): the row is itself transient + TTL-purged,
so there is no blob-row-iff-bytes cost, PG TOAST handles size, and ``run_classify`` reads it in the
same transaction (S-ing-3 indexes it directly). It is **capped** at the configured byte limit with
``text_truncated`` set — keyword/clause matching only needs a bounded prefix.

``UNIQUE (run_id, file_id)`` is the doc 09 §3.1 idempotency key — a re-delivered / resumed extract
upserts the same row (and the index doubles as the ``run_id``-prefix lookup for batch resume). Both
``run_id`` and ``file_id`` FKs are ``ON DELETE CASCADE`` (the transient-layer exception, doc 14 §1.2
— the import_file CASCADE precedent), so a cancelled/re-run/TTL-purged run is a one-shot delete.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ingestion_enums import ImportExtractStatus, import_extract_status_enum


class ImportExtract(Base):
    __tablename__ = "import_extract"
    __table_args__ = (
        # doc 09 §3.1 idempotency / upsert key (+ the run_id-prefix index for batch resume).
        UniqueConstraint("run_id", "file_id", name="uq_import_extract_run_id_file_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_file.id", ondelete="CASCADE"), nullable=False
    )
    # Inline extracted text (capped; see module docstring). NULL when nothing was extractable.
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_truncated: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    # First N lines / first page + title/footer — the high-signal classification slice (§5.1).
    header_block: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Document properties: author, title, subject, created/modified, app (§5.1).
    embedded_props: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    # heading_count / table_count / has_revision_history / page_count … (§5.1).
    structure_hints: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ocr_used: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    # Best-effort: Tika may not expose per-doc Tesseract confidence (§5.2).
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[ImportExtractStatus] = mapped_column(import_extract_status_enum, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
