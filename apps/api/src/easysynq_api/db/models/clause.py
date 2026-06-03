"""The ISO clause spine — read-only, seeded reference data (slice S9, doc 02 §2, doc 14 §4).

A ``clause`` is a node of a standard's requirement tree (ISO 9001:2015 clauses 4-10 + sub-clauses),
hung off a ``framework`` row and self-nested via ``parent_id`` (``4 → 4.4 → 4.4.1``). It is
**INSERT-by-seed only**: orgs map their artifacts *to* clauses, they never edit the clause text —
so there is deliberately no ``clause.edit`` permission and no write endpoint (doc 07 §3.6; editing
clause text is reserved for the multi-standard ``framework.author`` extension, D3, not built in v1).

``framework_id`` (not a direct ``org_id``) is the org/tenant anchor: a clause belongs to exactly one
``framework`` row, and ``framework`` is itself org-scoped — so the catalog is per-org seed data
without duplicating ``org_id`` here (doc 14 §4's column list). ``is_mandatory_star`` marks the
doc 02 §2.1 ★ mandatory documented-information set (Register R30) that drives the compliance
checklist; ``pdca_phase`` places the clause on the PDCA axis (doc 02 §3.2, clause 7 split).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._clause_enums import PdcaPhase, pdca_phase_enum


class Clause(Base):
    __tablename__ = "clause"
    __table_args__ = (
        UniqueConstraint("framework_id", "number", name="uq_clause_framework_id_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    framework_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id", ondelete="RESTRICT"), nullable=False
    )
    # The ISO clause number as a stable text token, e.g. "4", "4.4", "8.5.6".
    number: Mapped[str] = mapped_column(Text, nullable=False)
    # Self-FK forming the requirement tree (NULL for the seven top-level clauses 4..10).
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clause.id", ondelete="RESTRICT"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    intent_text: Mapped[str] = mapped_column(Text, nullable=False)
    # ★ — in the doc 02 §2.1 mandatory documented-information set (R30; drives the checklist).
    is_mandatory_star: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pdca_phase: Mapped[PdcaPhase] = mapped_column(pdca_phase_enum, nullable=False)
    # A discrete requirement node (vs a pure section header like the bare "4"/"5").
    requirement_node: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
