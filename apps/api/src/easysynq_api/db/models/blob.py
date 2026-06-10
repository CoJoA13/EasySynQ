"""A content-addressed, deduplicated, WORM blob (doc 14 §5.4, doc 04 §2).

Identity **is** the content hash: ``sha256`` (lowercase hex) is the PK, so identical bytes
are one row globally — that is the point of content-addressing (re-upload of identical bytes
creates no new blob and, downstream, no new version). ``org_id`` is provenance. Content/identity
immutable — the ONLY updates are the two D1 operational stamps (``verified_at`` = last passing
re-hash, S-drift-3; ``verify_failed_at`` = the alarm latch, set on a finding / cleared on a pass,
sorted first in the rotation sample); WORM object-lock in MinIO backs the storage layer (the
``documents`` bucket's GOVERNANCE default retention auto-locks on PUT).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Blob(Base):
    __tablename__ = "blob"

    sha256: Mapped[str] = mapped_column(Text, primary_key=True)  # lowercase hex (64 chars)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    worm_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    worm_retain_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sse: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # S-drift-3 (mig 0047): the D1 alarm latch — set when a re-hash FAILS, cleared when it passes,
    # and sorted FIRST in the rotation sample so an unresolved finding is in EVERY rolling scan
    # regardless of how large the never-verified (NULL verified_at) backlog grows.
    verify_failed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
