"""The AuditFinding record subtype — a retained Cl 9.2 audit finding (slice S-aud-2; doc 10 §5.3,
doc 14 §9).

``audit_finding.id`` IS the ``record.id`` (= ``documented_information.id``): a ``kind=RECORD``
shared-PK subtype (the ``audit`` / ``capa`` precedent). The captured ``record`` row is immutable; a
finding is never edited in place — it is corrected by capturing a successor finding
(``correction_of`` / ``superseded_by_correction`` on the ``record`` base) that supersedes it (the
records correction mechanic). Per-finding audit-LOG events reuse ``object_type='record'``
(``audit_finding.id`` IS a record id) so ``GET /documents/{id}/audit-events`` surfaces them — NO new
``audit_object_type`` value (decisions-register R39).

The NC -> CAPA auto-link (doc 02 / 06 §2 / 10 §5.3): an ``NC`` finding mandatorily auto-creates one
linked CAPA in the same transaction; ``auto_capa_id`` is the forward half of the bidirectional link
(``capa.origin_finding_id`` is the reverse). ``OBSERVATION`` / ``OFI`` findings carry no CAPA. The
``audit`` close gate keys off the *live* NC set (``finding_type='NC'`` AND not superseded), each
requiring a linked CAPA at ``close_state='Closed'`` (block-until-corrected, R39).

``severity`` reuses the shared ``nc_severity`` Critical/Major/Minor vocabulary (R39). It is nullable
in the column but a DB CHECK (``ck_audit_finding_nc_has_severity``) + the service layer require it
for ``NC`` (the auto-CAPA needs one; the close gate trusts "live NC ⇒ has CAPA ⇒ has severity").
``clause_ref`` / ``process_ref`` are soft TEXT refs; the finding's authz scope + the auto-CAPA's
``process_id`` derive from the audit's plan auditee process, not from ``process_ref``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._capa_enums import NcSeverity, nc_severity_enum
from ._iso_audit_enums import FindingType, finding_type_enum


class AuditFinding(Base):
    __tablename__ = "audit_finding"
    __table_args__ = (
        Index("ix_audit_finding_audit_id", "audit_id"),
        # The NC-needs-severity invariant the close gate trusts, enforced at the DB boundary (the
        # worm_destroy_request / import_decision CHECK precedent — a plain boolean CHECK round-trips
        # clean under alembic check, unlike a partial-index predicate).
        CheckConstraint("finding_type <> 'NC' OR severity IS NOT NULL", name="nc_has_severity"),
    )

    # Shared primary key: audit_finding.id == record.id == documented_information.id (subtype link).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit.id", ondelete="RESTRICT"), nullable=False
    )
    finding_type: Mapped[FindingType] = mapped_column(finding_type_enum, nullable=False)
    severity: Mapped[NcSeverity | None] = mapped_column(nc_severity_enum, nullable=True)
    clause_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The forward half of the NC→CAPA auto-link (the reverse is capa.origin_finding_id).
    # NULL for OBSERVATION / OFI; set in the same txn the NC finding is captured.
    auto_capa_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("capa.id", ondelete="RESTRICT"), nullable=True
    )
