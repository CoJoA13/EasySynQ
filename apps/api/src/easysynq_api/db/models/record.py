"""The ``record`` extension table — a shared-PK subtype of ``documented_information`` (doc 14 §5.5).

``record.id`` IS the base row's id (one-to-one with the abstract base): the base carries the
universal control fields (org/identifier/kind=RECORD), ``record`` adds the retain-specific columns.
S5 brings the table forward so the schema is final and ``signature_event.signed_object_type =
'record'`` has a real target; record capture/correction/disposition flows + the satellite tables
land with the records slice (doc 06). Records are immutable post-capture (corrections via
``correction_of``; only ``disposition_state`` advances) — no UPDATE path in S5.

Per the every-table tenancy invariant (doc 14 §15.3) ``record`` carries its own ``org_id`` FK in
addition to the shared-PK link; ``source_version_id`` pins the exact version the record was produced
under and survives that version's later supersession (INV-7).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._record_enums import (
    RecordDispositionState,
    RecordType,
    record_disposition_state_enum,
    record_type_enum,
)


class Record(Base):
    __tablename__ = "record"
    __table_args__ = (
        Index("ix_record_source_version_id", "source_version_id"),
        # Beat retention sweep (doc 14 §15.1).
        Index(
            "ix_record_retention_basis_date_disposition_state",
            "retention_basis_date",
            "disposition_state",
        ),
    )

    # Shared primary key: record.id == documented_information.id (subtype link).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    record_type: Mapped[RecordType] = mapped_column(record_type_enum, nullable=False)
    captured_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    captured_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=True
    )
    form_field_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    correction_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=True
    )
    superseded_by_correction: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=True
    )
    retention_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("retention_policy.id", ondelete="RESTRICT"), nullable=False
    )
    retention_basis_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    disposition_state: Mapped[RecordDispositionState] = mapped_column(
        record_disposition_state_enum,
        server_default=text("'ACTIVE'"),
        default=RecordDispositionState.ACTIVE,
        nullable=False,
    )
    legal_hold: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False, nullable=False
    )
