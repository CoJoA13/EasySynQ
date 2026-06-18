"""The ``kpi_measurement`` projection (S-obj-1, doc 14 §6 / §9.1.1) of a ``KPI_READING`` record. The
record (record_id) is the WORM evidence; this is the append-only queryable time-series. Insert-only
(REVOKE UPDATE,DELETE in 0049 — corrections create a new record + projection).

``target_at_capture``, ``direction_at_capture`` and ``at_risk_threshold_at_capture`` freeze the
ENTIRE grading basis at capture (S-obj-freeze, 0055): a later commitment revision (target, direction
OR amber threshold) can no longer re-grade a historical verdict. All three are snapshotted from the
then-GOVERNING commitment (working-row fallback pre-first-release) in the same
``record_measurement`` transaction."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._objective_enums import ObjectiveDirection, objective_direction_enum


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
    # The grading basis frozen at capture (S-obj-freeze, 0055) — alongside target_at_capture, so a
    # later commitment revision cannot re-grade a historical reading. direction_at_capture is NOT
    # NULL (every reading is recorded against an objective, so a governing/working direction always
    # exists — mirrors target_at_capture); at_risk_threshold_at_capture is nullable (no amber band).
    direction_at_capture: Mapped[ObjectiveDirection] = mapped_column(
        objective_direction_enum, nullable=False
    )
    at_risk_threshold_at_capture: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
