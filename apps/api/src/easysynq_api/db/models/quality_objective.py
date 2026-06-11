"""The ``quality_objective`` subtype (S-obj-1, doc 14 §6, R3/R44). ``quality_objective.id`` IS the
``documented_information.id`` (kind=DOCUMENT, type OBJ) — the ``form_template`` precedent. The
commitment fields are the editable working copy frozen into ``metadata_snapshot`` at check-in;
``current_value`` is operational (rolled from KPI readings), never versioned. Owner = the BASE
``documented_information.owner_user_id`` (not duplicated)."""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._objective_enums import ObjectiveDirection, objective_direction_enum


class QualityObjective(Base):
    __tablename__ = "quality_objective"
    __table_args__ = (Index("ix_quality_objective_process_id", "process_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_quality_objective_id_documented_information",
        ),
        primary_key=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_quality_objective_org_id_organization",
        ),
        nullable=False,
    )
    target_value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    current_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    direction: Mapped[ObjectiveDirection] = mapped_column(objective_direction_enum, nullable=False)
    at_risk_threshold: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    due_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "process.id",
            ondelete="RESTRICT",
            name="fk_quality_objective_process_id_process",
        ),
        nullable=True,
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_quality_objective_policy_id_doc_info",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
