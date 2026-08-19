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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._worm_enums import RetentionTargetState, retention_target_state_enum


class RetentionOperationTarget(Base):
    __tablename__ = "retention_operation_target"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "blob_sha256",
            "object_version_id",
            name="uq_retention_operation_target_operation_blob_version",
        ),
        CheckConstraint(
            "length(object_version_id) BETWEEN 1 AND 1024",
            name="object_version_id_length",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retention_operation.id", ondelete="RESTRICT"),
        nullable=False,
    )
    blob_sha256: Mapped[str] = mapped_column(
        Text, ForeignKey("blob.sha256", ondelete="RESTRICT"), nullable=False
    )
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    object_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    required_retain_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    required_legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[RetentionTargetState] = mapped_column(
        retention_target_state_enum,
        server_default=text("'PENDING'"),
        default=RetentionTargetState.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    read_back_retain_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_back_legal_hold: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    read_back_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
