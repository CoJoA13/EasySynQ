"""The M:N record↔blob attachment store (slice S-rec-1, doc 06 §3/§4.4, doc 14 §5.5).

A Record's captured evidence is one or more content-addressed WORM blobs; ``evidence_blob`` is the
join. The blob lives once globally (``blob.sha256`` PK — re-attaching identical bytes is a no-op);
``record.content_hash`` seals the *manifest* of the attached digests (doc 06 §4.4). ``is_original``
distinguishes the as-uploaded artifact from a derived rendition (doc 06 §4.1/§4.2). Immutable —
INSERT-only, no UPDATE path (records are immutable post-capture, doc 06 §1.3).

``UNIQUE(record_id, blob_sha256)`` makes a (record, blob) attach idempotent ("never copies"). The
reverse index supports the disposition/GC "which records use this blob" lookup.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class EvidenceBlob(Base):
    __tablename__ = "evidence_blob"
    __table_args__ = (
        UniqueConstraint("record_id", "blob_sha256", name="uq_evidence_blob_record_id_blob_sha256"),
        Index("ix_evidence_blob_blob_sha256", "blob_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=False
    )
    blob_sha256: Mapped[str] = mapped_column(
        Text, ForeignKey("blob.sha256", ondelete="RESTRICT"), nullable=False
    )
    is_original: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), default=True, nullable=False
    )
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
