"""The ``kpi_measurement`` projection (S-obj-1, doc 14 §6 / §9.1.1) of a ``KPI_READING`` record. The
record (record_id) is the WORM evidence; this is the append-only queryable time-series. Insert-only
(REVOKE UPDATE,DELETE in 0049 — corrections create a new record + projection). ``target_at_capture``
freezes the objective's then-target so a later target edit can't rewrite a past verdict."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class KpiMeasurement(Base):
    __tablename__ = "kpi_measurement"
    __table_args__ = (Index("ix_kpi_measurement_objective_id", "objective_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_kpi_measurement_org_id_organization",
        ),
        nullable=False,
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "record.id",
            ondelete="RESTRICT",
            name="fk_kpi_measurement_record_id_record",
        ),
        nullable=False,
    )
    objective_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "quality_objective.id",
            ondelete="RESTRICT",
            name="fk_kpi_measurement_objective_id",
        ),
        nullable=True,
    )
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "process.id",
            ondelete="RESTRICT",
            name="fk_kpi_measurement_process_id_process",
        ),
        nullable=True,
    )
    period: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    target_at_capture: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
