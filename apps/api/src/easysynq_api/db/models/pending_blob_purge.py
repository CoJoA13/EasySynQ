"""A durable, authority-bound marker for reaper-driven S3 erasure.

When a record is disposed under a DESTROY action, its last-referenced evidence blob's ``blob`` row +
``evidence_blob`` links are deleted and the DISPOSED tombstone is committed FIRST; the physical S3
purge is a separate, idempotent, reaper-driven follow-up (Batch 5). This row is the to-be-purged
marker that survives a crash between that commit and the purge, so ``reap_pending_blob_purges`` can
finish the erasure. Deleting the ``blob`` row at commit (not after the purge) keeps
blob-row-iff-bytes safe for backups — a backup never sees a ``blob`` row whose bytes are gone; the
leaked bytes this marker tracks are reclaimed out-of-band.

Issue #360 binds every new marker to the exact immutable ``disposition_event`` that authorized the
DESTROY and, for the R27 legal-order hatch, its executed ``worm_destroy_request``. The reaper
derives governance-bypass authority from those rows; the marker's boolean is never authority by
itself. ``authority_bound=false`` is reserved for rows that predate migration 0081. The app role
has column-scoped INSERT/UPDATE grants that prevent it from selecting legacy mode or mutating any
security-sensitive field after creation while preserving the reaper's ``FOR UPDATE SKIP LOCKED``
claim.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class PendingBlobPurge(Base):
    __tablename__ = "pending_blob_purge"
    __table_args__ = (
        CheckConstraint(
            """
            NOT authority_bound
            OR (
                record_id IS NOT NULL
                AND disposition_event_id IS NOT NULL
                AND (NOT bypass_governance OR worm_destroy_request_id IS NOT NULL)
            )
            """,
            name="authority_shape",
        ),
    )

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
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=True
    )
    disposition_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("disposition_event.id", ondelete="RESTRICT"), nullable=True
    )
    worm_destroy_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "worm_destroy_request.id",
            ondelete="RESTRICT",
            name="fk_pending_blob_purge_worm_request",
        ),
        nullable=True,
    )
    # Server-controlled upgrade discriminator: existing rows are backfilled false by 0081; new
    # app-role inserts cannot name this column and therefore receive true.
    authority_bound: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
