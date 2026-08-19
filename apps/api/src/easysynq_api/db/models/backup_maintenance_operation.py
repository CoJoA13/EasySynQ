from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._worm_enums import (
    BackupMaintenanceKind,
    MaintenanceSource,
    MaintenanceState,
    backup_maintenance_kind_enum,
    maintenance_source_enum,
    maintenance_state_enum,
)


class BackupMaintenanceOperation(Base):
    __tablename__ = "backup_maintenance_operation"
    __table_args__ = (CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[BackupMaintenanceKind] = mapped_column(
        backup_maintenance_kind_enum, nullable=False
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    backup_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backup_policy.id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    source: Mapped[MaintenanceSource] = mapped_column(maintenance_source_enum, nullable=False)
    state: Mapped[MaintenanceState] = mapped_column(
        maintenance_state_enum,
        server_default=text("'PENDING'"),
        default=MaintenanceState.PENDING,
        nullable=False,
    )
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    scheduled_for: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
