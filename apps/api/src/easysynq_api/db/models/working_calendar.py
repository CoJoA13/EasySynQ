"""Per-org working calendar (S-notify-6, doc 14 §line 131, R29). A working-day week mask + holiday
list the ``timer_sweep`` resolves to compute business-day reminder/escalation thresholds.

Operational config: the app role keeps INSERT/SELECT/UPDATE; migration 0067 REVOKEs DELETE.
The at-most-one-default-per-org partial unique index ``uq_working_calendar_one_default`` is
migration-managed (``migrations/env.py``) and intentionally NOT modelled here (the
``ix_task_timer_pending`` precedent — Alembic reflects predicate indexes, so an ORM copy would
phantom-DROP/CREATE)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, false, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WorkingCalendar(Base):
    __tablename__ = "working_calendar"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id", ondelete="RESTRICT", name="fk_working_calendar_org_id_organization"
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # ISO weekday ints (1=Mon..7=Sun) that are working days. JSONB; NO server_default (the seed +
    # the future editor always supply it — avoids the JSONB server_default alembic-check trap).
    working_days: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    # Array of "YYYY-MM-DD" holiday dates. JSONB; NO server_default (seed supplies []).
    holidays: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
