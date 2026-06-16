"""The improvement-initiative stage-event trail — an append-only state-transition history (slice
S-improvement-1; doc 02 Cl 10.3, doc 14 §9, decisions-register R46/R22).

Each transition of an initiative's mutable ``stage`` appends one
``improvement_initiative_stage_event`` row. Earlier events are NEVER rewritten — the table is
**append-only**: the migration ``REVOKE
UPDATE, DELETE`` from the non-owner ``easysynq_app`` role makes immutability structural, not merely
conventional (the ``signature_event`` / ``capa_stage`` / ``dcr_stage_event`` precedent). There is
**no ``updated_at``** column. The mutable ``improvement_initiative.stage`` is the headline; this
table is the immutable trail (R46).

``from_state`` is NULL on the genesis (raise/create) event — an initiative is born at ``Open`` with
no predecessor.

``signed_event_id`` is a forward seam: it **ships day-one but stays NULL/unsigned in v1.x** — the D3
Part-11 reserved hook (the ``dcr_stage_event.signed_event_id`` precedent; ``signature_event`` is
shipped substrate, so no dependency cycle and no ``use_alter``). The clause-10.3 lifecycle mandates
no per-initiative sign-off; ``SignatureMeaning`` stays closed (R2). **If** the §11-deferred
effectiveness review later signs a stage, it MUST use the pre-generated-UUID + flush + two
mutually-referencing INSERTs seam (never an UPDATE — the table is REVOKE-immutable).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._improvement_enums import ImprovementStage, improvement_stage_enum


class ImprovementInitiativeStageEvent(Base):
    __tablename__ = "improvement_initiative_stage_event"
    __table_args__ = (
        Index("ix_improvement_initiative_stage_event_initiative_id", "initiative_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    initiative_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("improvement_initiative.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # NULL on the genesis (raise/create) event — an initiative is born at Open with no predecessor.
    from_state: Mapped[ImprovementStage | None] = mapped_column(
        improvement_stage_enum, nullable=True
    )
    to_state: Mapped[ImprovementStage] = mapped_column(improvement_stage_enum, nullable=False)
    # NULL for a future system/Beat move; always a user in v1.x.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The sealed per-transition narrative (e.g. the Closed outcome / realized-benefit note — the
    # lightweight 10.3 continual-improvement evidence, frozen into this REVOKE-immutable row).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Forward seam: ships day-one, stays NULL/unsigned in v1.x — the D3 Part-11 reserved hook (the
    # dcr_stage_event.signed_event_id precedent). signature_event is shipped substrate → no cycle.
    signed_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signature_event.id", ondelete="RESTRICT"), nullable=True
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
