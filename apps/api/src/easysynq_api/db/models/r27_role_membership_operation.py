from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class R27RoleMembershipOperation(Base):
    __tablename__ = "r27_role_membership_operation"
    __table_args__ = (
        CheckConstraint("action IN ('ASSIGN','REVOKE')", name="action_closed"),
        CheckConstraint("state IN ('REQUESTED','AUDITED','FAILED')", name="state_closed"),
        CheckConstraint("operator_identity ~ '[^[:space:]]'", name="operator_identity_nonblank"),
        CheckConstraint(
            "(state='REQUESTED' AND audit_event_id IS NULL AND completed_at IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(state='AUDITED' AND audit_event_id IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(state='FAILED' AND audit_event_id IS NULL AND completed_at IS NOT NULL "
            "AND btrim(error_code)<>'' AND length(error_code)<=64 "
            "AND length(COALESCE(error_detail,''))<=512)",
            name="state_shape",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    operator_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requested_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    audit_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
