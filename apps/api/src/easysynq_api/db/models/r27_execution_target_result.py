from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._r27_enums import R27ResultCode, r27_result_code_enum


class R27ExecutionTargetResult(Base):
    __tablename__ = "r27_execution_target_result"
    __table_args__ = (
        UniqueConstraint(
            "execution_id", "manifest_target_id", name="uq_r27_execution_target_result"
        ),
        CheckConstraint(
            "(result_code='PHYSICAL_ERASED' AND purge_marker_id IS NOT NULL "
            "AND surviving_owner_kind IS NULL AND surviving_owner_id IS NULL) OR "
            "(result_code='LOGICAL_ONLY_SURVIVING_OWNER' AND purge_marker_id IS NULL "
            "AND surviving_owner_kind IN ('DOCUMENT_VERSION','EVIDENCE_BLOB') "
            "AND surviving_owner_id IS NOT NULL)",
            name="authority_shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("r27_execution.id", ondelete="RESTRICT"), nullable=False
    )
    manifest_target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("r27_manifest_target.id", ondelete="RESTRICT"),
        nullable=False,
    )
    result_code: Mapped[R27ResultCode] = mapped_column(r27_result_code_enum, nullable=False)
    verified_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purge_marker_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pending_blob_purge.id", ondelete="RESTRICT"), nullable=True
    )
    surviving_owner_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    surviving_owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
