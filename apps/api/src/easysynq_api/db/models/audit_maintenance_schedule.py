from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._worm_enums import (
    AuditMaintenanceJobKind,
    MaintenanceState,
    audit_maintenance_job_kind_enum,
    maintenance_state_enum,
)


class AuditMaintenanceSchedule(Base):
    __tablename__ = "audit_maintenance_schedule"
    __table_args__ = (
        UniqueConstraint("job_kind", name="uq_audit_maintenance_schedule_job_kind"),
        CheckConstraint("interval_seconds > 0", name="interval_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_kind: Mapped[AuditMaintenanceJobKind] = mapped_column(
        audit_maintenance_job_kind_enum, nullable=False
    )
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    next_due_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
    last_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_succeeded_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
