from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._worm_enums import HoldReleaseState, hold_release_state_enum


class WormHoldReleaseOperation(Base):
    __tablename__ = "worm_hold_release_operation"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "idempotency_key",
            name="uq_worm_hold_release_operation_org_id_idempotency_key",
        ),
        CheckConstraint(
            "length(object_version_id) BETWEEN 1 AND 1024",
            name="object_version_id_length",
        ),
        CheckConstraint(
            "length(normalized_release_basis) BETWEEN 1 AND 4000",
            name="release_basis_length",
        ),
        CheckConstraint(
            "canonical_sha256 ~ '^[0-9a-f]{64}$' AND owner_snapshot_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_shape",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
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
    object_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    initiated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_release_basis: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    owner_snapshot_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    state: Mapped[HoldReleaseState] = mapped_column(
        hold_release_state_enum,
        server_default=text("'PENDING_AUTHORIZATION'"),
        default=HoldReleaseState.PENDING_AUTHORIZATION,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
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
