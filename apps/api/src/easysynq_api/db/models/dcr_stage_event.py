"""The DCR stage-event trail — an append-only state-transition history (slice S-dcr-1; doc 05 §5.5,
doc 14 §7, decisions-register R22).

Each transition of a DCR's mutable ``state`` appends one ``dcr_stage_event`` row. Earlier events
are NEVER rewritten — the table is **append-only**: the migration ``REVOKE UPDATE, DELETE`` from
the non-owner ``easysynq_app`` role makes immutability structural, not merely conventional (the
``signature_event`` / ``capa_stage`` precedent). There is no ``updated_at`` column. The mutable
``dcr.state`` is the headline; this table is the immutable trail (R22).

``from_state`` is NULL on the genesis (intake) event — a DCR is born at ``Open`` with no
predecessor.

``signed_event_id`` is a forward seam: NULL in S-dcr-1. S-dcr-4 (the engine-routed DCR approval)
writes a ``signature_event(signed_object_type='dcr_stage', meaning='approval')`` and populates
this FK at append time. The FK is present from day one (the ``capa_stage.signed_event_id``
precedent — 0036 adds the nullable column AND its FK in the introducing migration;
``signature_event`` is shipped substrate, so there is no dependency cycle and no ``use_alter``).
NB ``signed_object_type`` gains ``dcr_stage`` in S-dcr-4, not here — S-dcr-1 writes no signature
rows.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._dcr_enums import DcrState, dcr_state_enum


class DcrStageEvent(Base):
    __tablename__ = "dcr_stage_event"
    __table_args__ = (Index("ix_dcr_stage_event_dcr_id", "dcr_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    dcr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dcr.id", ondelete="RESTRICT"), nullable=False
    )
    # NULL on the genesis (intake) event — a DCR is born at Open with no predecessor.
    from_state: Mapped[DcrState | None] = mapped_column(dcr_state_enum, nullable=True)
    to_state: Mapped[DcrState] = mapped_column(dcr_state_enum, nullable=False)
    # NULL for system/Beat-driven transitions (none in S-dcr-1; all S-dcr-1 events are
    # user-driven).
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form structured detail for the transition (e.g. the cancel reason, the intake
    # snapshot).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Forward seam: NULL in S-dcr-1; the S-dcr-4 approval writer populates it (a signature_event
    # row signing this stage). See the module docstring (the capa_stage.signed_event_id
    # precedent).
    signed_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signature_event.id", ondelete="RESTRICT"), nullable=True
    )
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
