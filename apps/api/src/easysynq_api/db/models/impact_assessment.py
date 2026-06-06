"""The DCR structured impact assessment — one row per dimension per DCR (slice S-dcr-2; doc 14 §7,
doc 05 §5.3).

At ``POST /dcrs/{id}/assess`` (Open→Assessed) the service auto-populates one row per
:class:`ImpactDimension` from the target document's where-used analysis — ``auto_populated`` is
the system-computed facts (JSONB), ``requester_annotation`` is the human's confirmation/note (set
via ``PUT /dcrs/{id}/impact``). The two-column split mirrors ``dcr.reason_class`` +
``dcr.reason_text`` (system tag + free text). Mutable: a re-assess re-computes ``auto_populated``
and the requester edits the annotation (GRANT SELECT,INSERT,UPDATE) — NOT an append-only trail;
the immutable record of the assessment transition is the ``dcr_stage_event`` row that the assess
writes.

``UNIQUE(dcr_id, dimension)`` makes the assess an UPSERT (one row per dimension); ``ix`` on
dcr_id for the per-DCR read.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._dcr_enums import ImpactDimension, impact_dimension_enum


class ImpactAssessment(Base):
    __tablename__ = "impact_assessment"
    __table_args__ = (
        UniqueConstraint("dcr_id", "dimension", name="uq_impact_assessment_dcr_dimension"),
        Index("ix_impact_assessment_dcr_id", "dcr_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    dcr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dcr.id", ondelete="RESTRICT"), nullable=False
    )
    dimension: Mapped[ImpactDimension] = mapped_column(impact_dimension_enum, nullable=False)
    # The system-computed facts for this dimension (e.g. affected_processes → {"applicable":
    # true, "processes": [{"id","name"}]}). For a CREATE DCR (no target) → {"applicable": false}.
    auto_populated: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requester_annotation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
