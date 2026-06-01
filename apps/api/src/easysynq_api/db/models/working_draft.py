"""The check-out mirror (doc 14 §5.4, R8/R9) — the only mutable surface in the vault.

Exactly one active row per document (``document_id`` UNIQUE). **Redis holds the authoritative
runtime lock**; this row is the display/recovery mirror. ``scratch_blob_ref`` is preserved on
break-lock so the displaced editor's work survives (R9). Frozen into a ``document_version`` on
check-in. ``lock_ttl`` is 8h (R24).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Interval, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WorkingDraft(Base):
    __tablename__ = "working_draft"
    __table_args__ = (UniqueConstraint("document_id", name="uq_working_draft_document_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        nullable=False,
    )
    checked_out_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    checked_out_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=True
    )
    scratch_blob_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    lock_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # Redis lock token (CAS)
    lock_ttl: Mapped[datetime.timedelta] = mapped_column(
        Interval, server_default=text("'8 hours'"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
