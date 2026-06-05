"""The NCR own table — a nonconforming-output report with its ISO 9001 8.7 disposition (slice
S-capa-1; doc 02 Cl 8.7/10.2, doc 14 §9, decisions-register R20).

Unlike ``capa`` / ``complaint`` (record subtypes), an NCR is its own lightweight table — it is a
working nonconformity record, not a captured immutable artifact, so its events key on
``object_type='ncr'`` (a reserved ``audit_object_type`` value; an own-table id is NOT a record id).
It carries a human ``identifier`` (``NCR-{SEQ}``) for cross-reference (the ``audit_program`` AUDPROG
precedent; doc 14 lists only ``id PK`` — the identifier is an additive usability call).

``disposition`` + ``disposition_authorized_by`` record the 8.7 decision and its authorizer (R20).
They are nullable: an NCR exists once raised; the disposition is a distinct, later, audited action
(``record_ncr_disposition``). An NCR may stand alone or be folded into a CAPA (doc 14 §14 R5); the
NCR→CAPA wiring is a later slice (S-capa-1 keeps NCRs standalone).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._capa_enums import (
    NcrDisposition,
    NcrSource,
    NcSeverity,
    nc_severity_enum,
    ncr_disposition_enum,
    ncr_source_enum,
)


class Ncr(Base):
    __tablename__ = "ncr"
    __table_args__ = (UniqueConstraint("org_id", "identifier", name="uq_ncr_org_id_identifier"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[NcrSource] = mapped_column(ncr_source_enum, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[NcSeverity] = mapped_column(nc_severity_enum, nullable=False)
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=True
    )
    # The 8.7 disposition decision + authorizer (R20). NULL until recorded via
    # record_ncr_disposition.
    disposition: Mapped[NcrDisposition | None] = mapped_column(ncr_disposition_enum, nullable=True)
    disposition_authorized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    disposition_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
