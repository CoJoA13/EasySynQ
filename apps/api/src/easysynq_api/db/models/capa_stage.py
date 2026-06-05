"""The CAPA stage-block trail — an append-only sealed-content log (slice S-capa-1; doc 10 §6,
doc 14 §9).

Each transition of a CAPA's ``close_state`` appends one ``capa_stage`` row carrying a sealed
``content_block`` (the stage's recorded narrative/evidence-refs). Earlier blocks are NEVER rewritten
(doc 06 §2) — the table is **append-only**: the migration ``REVOKE UPDATE, DELETE`` from the
non-owner ``easysynq_app`` role makes immutability structural, not merely conventional (the
``signature_event`` precedent). There is no ``updated_at`` column.

Doc 14 §9 lists an ``attachments`` member; per decisions-register R39 this is realized as
``evidence_for_link(target_type=CAPA_STAGE)`` edges (Mode C, links-never-copy) rather than a column
—
the enum value is already reserved; the link validation is enabled by a later slice when stage
evidence is first consumed (S-aud-2/S-capa-3). S-capa-1 adds no evidence edges.

``signed_event_id`` is a forward seam: NULL in S-capa-1. S-capa-2 (engine-routed stage approval) /
S-capa-3 (capa.verify) write a ``signature_event(signed_object_type='capa_stage')`` row and populate
this FK at append time (signature meanings ``approval`` / ``verify``, both already in the v1 enum).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._capa_enums import CapaCloseState, capa_close_state_enum


class CapaStage(Base):
    __tablename__ = "capa_stage"
    __table_args__ = (Index("ix_capa_stage_capa_id", "capa_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    capa_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capa.id", ondelete="RESTRICT"), nullable=False
    )
    stage: Mapped[CapaCloseState] = mapped_column(capa_close_state_enum, nullable=False)
    # The sealed stage narrative (caller-constructed; doc 10 §6.2). Immutable once appended (the
    # REVOKE makes it structural). Shape is per-stage + free-form in v1, e.g. Raised carries the
    # problem statement + source; Containment the correction description.
    content_block: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Forward seam: NULL in S-capa-1; the S-capa-2 engine / S-capa-3 verify writer populates it (a
    # signature_event row signing this stage). See the module docstring.
    signed_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signature_event.id", ondelete="RESTRICT"), nullable=True
    )
    # The parent CAPA's cycle_marker at append time (forward-compat; the effectiveness-loop
    # iteration
    # this block belongs to). 0 in S-capa-1.
    cycle_marker: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0, nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
