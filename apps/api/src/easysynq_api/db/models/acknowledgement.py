"""The read-and-understood acknowledgement — Clause 7.3 awareness evidence (slice S-ack-1;
doc 04 §8.2, doc 14 §5.6, R43).

An immutable, append-only row pinned to the exact ``document_version_id`` (acknowledging Rev C is
evidence about Rev C forever); the R43 carry-forward satisfaction rule lives in the COVERAGE
computation, never here. NOT a ``record`` subtype (no record_type member) and NOT a
``signature_event`` (``document.acknowledge`` is sig_hook=false; R2's enum has no acknowledge
meaning). DB-grant append-only: migration 0048 REVOKEs UPDATE, DELETE from the app role (the
``capa_stage`` house style — harder than doc 14 §1.2's "App" enforcement).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ack_enums import AckCreatedReason, ack_created_reason_enum


class Acknowledgement(Base):
    __tablename__ = "acknowledgement"
    __table_args__ = (
        # One ack per (user, version) — the decide leg's idempotency backstop.
        UniqueConstraint("user_id", "document_version_id", name="uq_acknowledgement_user_version"),
        # The satisfaction lookup (who acked which seq of this doc).
        Index("ix_acknowledgement_document_id_user_id", "document_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    # Denormalized for coverage queries (doc 14 §5.6 carries only the version FK; org_id +
    # document_id added per the §1.1 convention + the index plan — a spec-noted build divergence).
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_acknowledgement_document",
        ),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_version.id", ondelete="RESTRICT", name="fk_acknowledgement_version"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    acknowledged_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_reason: Mapped[AckCreatedReason] = mapped_column(
        ack_created_reason_enum, nullable=False
    )
