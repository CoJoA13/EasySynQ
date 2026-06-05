"""The CAPA record subtype — a corrective-action container with a lifecycle ``close_state`` (slice
S-capa-1; doc 02 Cl 10.2, doc 10 §6, doc 14 §9/§14).

``capa.id`` IS the ``record.id`` (= ``documented_information.id``): a ``kind=RECORD`` shared-PK
subtype (the ``audit.py`` / ``record.py`` precedent). The captured ``record`` row is immutable; only
the mutable ``close_state`` column advances through the FSM (the ``record.disposition_state`` /
``audit.state`` precedent — record-immutability governs captured content + the sealed ``capa_stage``
blocks, NOT the lifecycle column). Per-CAPA audit-LOG events reuse ``object_type='record'``
(``capa.id`` is a record id) so ``GET /documents/{id}/audit-events`` surfaces them — NO new
``audit_object_type`` value for CAPA (decisions-register R39).

Forward seams (left clean for later slices):
- ``origin_finding_id`` is a nullable UUID with **NO FK** — ``audit_finding`` does not exist yet;
  S-aud-2 creates that table, adds the FK, and wires the atomic NC→CAPA auto-link + the reverse
  ``audit_finding.auto_capa_id``. S-capa-1 only ever stores NULL here.
- ``cycle_marker`` is a forward-compat counter (the Verify→ActionPlan effectiveness loop); S-capa-1
  initializes it to 0 and never mutates it. S-capa-3 bumps it on the Verify→ActionPlan transition.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._capa_enums import (
    CapaCloseState,
    CapaSource,
    NcSeverity,
    capa_close_state_enum,
    capa_source_enum,
    nc_severity_enum,
)


class Capa(Base):
    __tablename__ = "capa"

    # Shared primary key: capa.id == record.id == documented_information.id (subtype link).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    # Nullable UUID, NO FK in S-capa-1 (audit_finding lands in S-aud-2, which adds the FK + the
    # reverse auto_capa_id). The S-capa-1 service MUST NOT write this — it is ALWAYS NULL here, so
    # S-aud-2's FK addition is safe (no orphaned values). A complaint-/process-raised CAPA has none.
    origin_finding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source: Mapped[CapaSource] = mapped_column(capa_source_enum, nullable=False)
    severity: Mapped[NcSeverity] = mapped_column(nc_severity_enum, nullable=False)
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("process.id", ondelete="RESTRICT"), nullable=True
    )
    # MUTABLE lifecycle column (the audit.state / record.disposition_state precedent). Advanced only
    # via the services/capa FSM under SELECT … FOR UPDATE.
    close_state: Mapped[CapaCloseState] = mapped_column(
        capa_close_state_enum,
        server_default=text("'Raised'"),
        default=CapaCloseState.Raised,
        nullable=False,
    )
    # Forward-compat: the Verify→ActionPlan effectiveness-loop counter. 0 in S-capa-1 (never
    # bumped);
    # each capa_stage carries the parent CAPA's cycle_marker at append time so auditors can group a
    # stage trail by loop iteration (S-capa-3 wires the bump).
    cycle_marker: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0, nullable=False
    )
