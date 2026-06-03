"""The M:N document↔clause join — an *audited link* (slice S9, doc 02 §2.1, doc 14 §4).

A ``clause_mapping`` records that a ``documented_information`` artifact (a Document or a Record)
*satisfies / addresses* an ISO clause. The relationship is many-to-many (one document can cover
several clauses; one clause can be covered by several documents), so the compliance checklist
computes coverage from these links rather than a 1:1 file-per-requirement. Mapping/unmapping is
audited (``CLAUSE_MAPPED`` / ``CLAUSE_UNMAPPED``, object_type ``document``).

``framework_id`` is carried here per the C5 canon (doc 14 §15.3 / doc 18 §1 — ``framework_id``
NOT NULL lives only on ``documented_information`` / ``clause`` / ``clause_mapping`` / ``scope``); it
is denormalised from the document + clause (which must agree) so multi-standard coverage stays
additive. The headline use: the lifecycle ``submit-review`` gate counts a document's mappings and
rejects a submit with zero (doc 15 §8.5 / doc 04 §6.1).

The ``documented_information_id`` FK is named explicitly — the naming-convention default
(``fk_clause_mapping_documented_information_id_documented_information``) exceeds PG's 63-char
identifier limit (the ``documented_information.py`` precedent).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ClauseMapping(Base):
    __tablename__ = "clause_mapping"
    __table_args__ = (
        UniqueConstraint(
            "documented_information_id", "clause_id", name="uq_clause_mapping_doc_clause"
        ),
        Index("ix_clause_mapping_documented_information_id", "documented_information_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    framework_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id", ondelete="RESTRICT"), nullable=False
    )
    clause_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clause.id", ondelete="RESTRICT"), nullable=False
    )
    documented_information_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_clause_mapping_documented_information_id",
        ),
        nullable=False,
    )
    # Marks the link as satisfying a discrete *requirement* node (vs a looser thematic mapping) —
    # used by the mandatory-coverage computation (doc 02 §2.1).
    is_requirement_level: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
