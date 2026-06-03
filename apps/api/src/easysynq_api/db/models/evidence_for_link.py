"""The audited *evidence-for* link — a Record promoted as evidence for a clause/process/document
(slice S-rec-1, doc 06 §6, doc 14 §5.5).

This is the records side of the traceability chain REQUIREMENT(clause)→PROCESS→DOCUMENT→RECORD→
EVIDENCE. The target is **polymorphic** (``target_type`` + ``target_id``, no FK on the id) — the
``signature_event.signed_object_type``/``signed_object_id`` precedent (doc 14 §8) — so it spans
tables that exist (clause/process/document) and future ones (finding/capa_stage). Each link is an
audited annotation (``RECORD_EVIDENCE_LINKED`` / ``RECORD_EVIDENCE_UNLINKED``, object_type
``record``), never a copy of bytes (doc 06 §1.3).

Framework-consistency (a record links only to a clause/process/document of its own framework, the
C5/clause-mapping defense-in-depth) is enforced at write time by comparing the record's
``framework_id`` to the target's — so no ``framework_id`` column is denormalised here (it is not in
the closed C5 set; doc 14 §15.3). ``UNIQUE(record_id, target_type, target_id)`` makes a link
idempotent; the ``(target_type, target_id)`` index serves the bottom-up "what evidence points here".
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._evidence_enums import EvidenceForTargetType, evidence_for_target_type_enum


class EvidenceForLink(Base):
    __tablename__ = "evidence_for_link"
    __table_args__ = (
        UniqueConstraint(
            "record_id",
            "target_type",
            "target_id",
            name="uq_evidence_for_link_record_id_target_type_target_id",
        ),
        Index("ix_evidence_for_link_target_type_target_id", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=False
    )
    target_type: Mapped[EvidenceForTargetType] = mapped_column(
        evidence_for_target_type_enum, nullable=False
    )
    # Polymorphic id — no FK (the signature_event precedent); spans tables not all present yet.
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    link_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
