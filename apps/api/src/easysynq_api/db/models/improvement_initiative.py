"""The Improvement Initiative — an own table with a *mutable* ``stage`` lifecycle column (slice
S-improvement-1; doc 02 Cl 10.3, doc 14 §9, doc 15, decisions-register R46/R22/R5).

Per **R46** an improvement initiative is a controlled **workflow object**, NOT a ``kind=RECORD``
immutable artifact and NOT a ``documented_information`` subtype (the DCR / **R22** doctrine, and a
register-sanctioned deviation from doc 02's RECORD framing — see R46). Clause 10.3 is non-★, so
there is no checklist ★ node to flip and no DOCUMENT-path rationale (cf. R44/R45): an initiative is
purely operational PostgreSQL state, never entering the mirror. The mutable ``stage`` is the
headline; the append-only ``improvement_initiative_stage_event`` trail is the immutable history.
Because an initiative id is NOT a record id, its events key on a fresh
``audit_object_type='improvement_initiative'`` (the ``ncr``/``dcr`` own-table precedent).

It carries a human ``IMP-{YYYY}-{NNNN}`` identifier (4-digit SEQ; allocated from the per-(org,
"IMP", year) numbering counter via ``allocate_seq`` + ``format_identifier(pad=4)``).

Spawn-seam columns ship in slice 1 so slice 2 (the OFI-finding / MR-output spawn) is
**zero-migration** (the DCR ``source_link_id`` / ``spawn_idempotency_key`` precedent):
- ``source`` (``OFI``/``review``/``manual``) — where the initiative was raised from.
- ``source_link_id`` — the polymorphic origin id (a ``finding.id`` for OFI, a ``review_output.id``
  for review, NULL for manual). NO FK (the ``dcr.source_link_id`` / ``signature_event`` precedent).
- ``spawn_idempotency_key`` — the ``Idempotency-Key`` header value; a migration-managed
  partial-UNIQUE ``(org_id, source_link_id, spawn_idempotency_key) WHERE spawn_idempotency_key IS
  NOT NULL`` makes a 1:N retry return the SAME initiative (the ``dcr`` spawn precedent).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._improvement_enums import (
    ImprovementSource,
    ImprovementStage,
    improvement_source_enum,
    improvement_stage_enum,
)


class ImprovementInitiative(Base):
    __tablename__ = "improvement_initiative"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "identifier", name="uq_improvement_initiative_org_id_identifier"
        ),
        Index("ix_improvement_initiative_org_id_stage", "org_id", "stage"),
        Index("ix_improvement_initiative_source_link_id", "source_link_id"),
        Index("ix_improvement_initiative_process_id", "process_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The intended improvement / success measure (the 10.3 "opportunity").
    target_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[ImprovementSource] = mapped_column(improvement_source_enum, nullable=False)
    # Polymorphic origin id (finding.id for OFI / review_output.id for review; NULL for manual).
    # NO FK (the dcr.source_link_id / signature_event precedent). NULL in slice 1's manual create.
    source_link_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # The Idempotency-Key header value (1:N spawn retry-safety; see the partial-UNIQUE in 0052).
    spawn_idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # PROCESS-scoped authz selector.
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=True
    )
    # The accountable owner.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    stage: Mapped[ImprovementStage] = mapped_column(
        improvement_stage_enum, server_default=text("'Open'"), nullable=False
    )
    opened_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set at the Closed / Cancelled transition (the terminal moves).
    closed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # The headline is mutable (stage / owner / metadata edits) — onupdate now().
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )
