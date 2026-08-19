from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._worm_enums import RetentionOperationState, retention_operation_state_enum


class RetentionOperation(Base):
    __tablename__ = "retention_operation"
    __table_args__ = (
        UniqueConstraint("revision_id", name="uq_retention_operation_revision_id"),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        CheckConstraint(
            "target_count >= 0 AND verified_count >= 0 AND failed_count >= 0 "
            "AND verified_count + failed_count <= target_count",
            name="progress_counts",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retention_revision.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[RetentionOperationState] = mapped_column(
        retention_operation_state_enum,
        server_default=text("'PENDING'"),
        default=RetentionOperationState.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    target_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    verified_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
