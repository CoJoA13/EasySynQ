"""The Complaint record subtype — a lightweight customer-complaint intake (slice S-capa-1; doc 02
Cl 8.2.1, doc 14 §6, decisions-register R16).

``complaint.id`` IS the ``record.id`` — a ``kind=RECORD`` shared-PK subtype (the ``audit`` /
``capa``
precedent). This is a **deliberate divergence** from doc 14 §6's literal ``id PK + record_id FK``
satellite phrasing (which the stateless ``kpi_measurement`` / ``satisfaction_survey`` satellites
use):
a complaint participates in the record-keyed subtype family (it is captured as immutable evidence
and
it spawns a CAPA that IS a shared-PK record), so a unified shared-PK id space is the consistent,
lower-friction model — the same kind of justified doc-14 divergence R39 made for ``audit_program``.
Recorded in decisions-register R39 (back-propagation to doc 14 §6).

``spawned_capa_id`` is the one-click-spawn idempotency latch (R16): a complaint spawns **at most
one**
CAPA. It is a nullable UNIQUE FK (multiple un-spawned complaints keep NULL, which Postgres treats as
distinct; once set it is unique). The spawn serializes on a ``SELECT … FOR UPDATE`` of the complaint
row; the UNIQUE is the secondary backstop.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._capa_enums import NcSeverity, nc_severity_enum


class Complaint(Base):
    __tablename__ = "complaint"
    __table_args__ = (UniqueConstraint("spawned_capa_id", name="uq_complaint_spawned_capa_id"),)

    # Shared primary key: complaint.id == record.id == documented_information.id (subtype link).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    customer: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable — a complaint may be captured un-triaged; severity is assigned (or overridden) at
    # spawn
    # time (the spawn requires a non-null severity for the CAPA it creates).
    severity: Mapped[NcSeverity | None] = mapped_column(nc_severity_enum, nullable=True)
    # The idempotency latch (R16) — set once at spawn; FK→capa, RESTRICT (a CAPA is never
    # hard-deleted).
    spawned_capa_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capa.id", ondelete="RESTRICT"), nullable=True
    )
