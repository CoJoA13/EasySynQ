from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._r27_enums import R27RequestState, r27_request_state_enum


class R27Request(Base):
    __tablename__ = "r27_request"
    __table_args__ = (
        CheckConstraint(
            "length(normalized_legal_basis) BETWEEN 1 AND 4000",
            name="legal_basis_length",
        ),
        CheckConstraint(
            "legal_basis_sha256 ~ '^[0-9a-f]{64}$'",
            name="legal_basis_sha256_shape",
        ),
        CheckConstraint(
            "approver_user_id IS NULL OR requester_user_id IS NULL "
            "OR approver_user_id <> requester_user_id",
            name="approver_neq_requester",
        ),
        CheckConstraint(
            "(state IS NULL AND requester_user_id IS NULL AND requester_audit_event_id IS NULL "
            "AND requested_at IS NULL) OR "
            "(state='STALE' AND ((requester_user_id IS NULL "
            "AND requester_audit_event_id IS NULL AND requested_at IS NULL) OR "
            "(requester_user_id IS NOT NULL AND requester_audit_event_id IS NOT NULL "
            "AND requested_at IS NOT NULL))) OR "
            "(state IS NOT NULL AND state<>'STALE' AND requester_user_id IS NOT NULL "
            "AND requester_audit_event_id IS NOT NULL AND requested_at IS NOT NULL)",
            name="state_requires_requester",
        ),
        Index("ix_r27_request_record_id", "record_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=False
    )
    normalized_legal_basis: Mapped[str] = mapped_column(Text, nullable=False)
    legal_basis_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    state: Mapped[R27RequestState | None] = mapped_column(r27_request_state_enum, nullable=True)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    requester_audit_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approver_audit_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cancelled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    cancellation_audit_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requested_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stale_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
