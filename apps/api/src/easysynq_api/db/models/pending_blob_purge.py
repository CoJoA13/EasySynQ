"""Exact-version, authority-bound durable physical purge marker."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._worm_enums import MaintenanceState, maintenance_state_enum


class PendingBlobPurge(Base):
    __tablename__ = "pending_blob_purge"
    __table_args__ = (
        CheckConstraint(
            "record_id IS NOT NULL AND disposition_event_id IS NOT NULL "
            "AND ((NOT bypass_governance AND r27_request_id IS NULL "
            "AND r27_execution_id IS NULL) OR "
            "(bypass_governance AND r27_request_id IS NOT NULL "
            "AND r27_execution_id IS NOT NULL))",
            name="authority_shape",
        ),
        CheckConstraint(
            "length(object_version_id) BETWEEN 1 AND 1024",
            name="object_version_id_length",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    object_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    bypass_governance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=False
    )
    disposition_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("disposition_event.id", ondelete="RESTRICT"), nullable=False
    )
    r27_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_request.id", ondelete="RESTRICT"), nullable=True
    )
    r27_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_execution.id", ondelete="RESTRICT"), nullable=True
    )
    state: Mapped[MaintenanceState] = mapped_column(
        maintenance_state_enum,
        server_default=text("'PENDING'"),
        default=MaintenanceState.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    claimed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
