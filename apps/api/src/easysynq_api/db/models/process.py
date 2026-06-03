"""The QMS process node — the ISO 9001 Clause 4.4 process map (slice S9c, doc 02 §3.3, doc 14 §4).

A ``process`` is a node of the org's process landscape, self-nested via ``parent_id`` (subprocess),
tagged with a ``pdca_phase`` (reusing the clause cluster's enum), and carrying its ``state``
(``SEED`` from the wizard/API → ``ACTIVE`` once confirmed). ``owner_org_role_id`` points at the QMS
``org_role`` accountable for it (RACI, not authz — doc 02 §3.4); ``is_outsourced`` +
``outsourced_supplier_id`` model an externally-performed process (R17). Both FKs are **nullable** —
a process can exist before its owner/supplier is recorded (S9c has no org_role/supplier authoring).
Edges (``process_edge``) and document links (``process_link``) hang off this node.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._clause_enums import PdcaPhase, pdca_phase_enum
from ._process_enums import ProcessState, process_state_enum


class Process(Base):
    __tablename__ = "process"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_process_org_id_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Self-FK for subprocess nesting (NULL for a top-level process).
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=True
    )
    # RACI owner (an org_role, NOT a permission role); nullable until assigned.
    owner_org_role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("org_role.id", ondelete="RESTRICT"), nullable=True
    )
    pdca_phase: Mapped[PdcaPhase] = mapped_column(pdca_phase_enum, nullable=False)
    criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[ProcessState] = mapped_column(
        process_state_enum, default=ProcessState.SEED, nullable=False
    )
    # Excluded processes hide their IA sections but the row persists (doc 02 §2 note).
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_outsourced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outsourced_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier.id", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
