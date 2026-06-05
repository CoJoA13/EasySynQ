"""The Audit Plan — one scheduled audit of a process under a programme (slice S-aud-1; doc 10 §5.2,
doc 14 §9).

An own-table (no shared PK): a plan is a scheduling row, not a retained record. ``program_id`` is
RESTRICT (a programme with plans cannot be dropped from under them). ``auditee_process_id`` /
``lead_auditor_user_id`` / ``scheduled_date`` are nullable so a plan can be drafted before it is
fully specified. doc 14 cardinality: one plan → one audit (``audit.plan_id`` NOT NULL).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AuditPlan(Base):
    __tablename__ = "audit_plan"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_program.id", ondelete="RESTRICT"), nullable=False
    )
    auditee_process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=True
    )
    lead_auditor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    scheduled_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    checklist_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
