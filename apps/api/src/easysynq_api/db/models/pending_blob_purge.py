"""A durable 'purge these bytes' marker for reaper-driven S3 erasure (disposition txn integrity).

When a record is disposed under a DESTROY action, its last-referenced evidence blob's ``blob`` row +
``evidence_blob`` links are deleted and the DISPOSED tombstone is committed FIRST; the physical S3
purge is a separate, idempotent, reaper-driven follow-up (Batch 5). This row is the to-be-purged
marker that survives a crash between that commit and the purge, so ``reap_pending_blob_purges`` can
finish the erasure. Deleting the ``blob`` row at commit (not after the purge) keeps
blob-row-iff-bytes safe for backups — a backup never sees a ``blob`` row whose bytes are gone; the
leaked bytes this marker tracks are reclaimed out-of-band. The app role INSERTs, SELECTs, UPDATEs,
and DELETEs these rows (the UPDATE is for the reaper's ``FOR UPDATE`` claim); each is deleted once
its bytes are purged.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PendingBlobPurge(Base):
    __tablename__ = "pending_blob_purge"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    # The destroyed blob's hash — log/dedup only; NOT an FK (its blob row is deleted at the same
    # commit that inserts this marker).
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    bypass_governance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
