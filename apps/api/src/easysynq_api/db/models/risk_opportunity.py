"""The ``risk_opportunity`` register-row satellite (S-risk-1, doc 14 §6, R18/R49).

A **1:many satellite** of the ``kind=DOCUMENT`` ``RSK`` register head: ``id`` is its OWN uuid (NOT
a shared-PK subtype like ``quality_objective`` — a register has many rows, so they cannot share the
head's PK) and ``register_doc_id`` FKs to the head ``documented_information.id``. The rows are the
register version's controlled content (edited only while the head is Draft/UnderRevision; snapshot
into the version at publish — R49 / spec §3). ``risk_rating`` is STORED, re-derived from
``likelihood x severity`` on every write (never client-supplied); the displayed band is graded
against the GOVERNING version's frozen criteria, never live code (R49 derive-and-freeze).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._risk_enums import (
    RiskOpportunityType,
    ScoringMethod,
    risk_opportunity_type_enum,
    scoring_method_enum,
)


class RiskOpportunity(Base):
    __tablename__ = "risk_opportunity"
    __table_args__ = (
        Index("ix_risk_opportunity_register_doc_id", "register_doc_id"),
        Index("ix_risk_opportunity_process_id", "process_id"),
        # Bare tokens — the metadata ck convention expands them to ck_risk_opportunity_<token>,
        # matching the migration's same-token constraints (the 0040 create_iff_no_target precedent;
        # alembic check does NOT compare CHECK bodies, so the names must match by hand).
        CheckConstraint("likelihood BETWEEN 1 AND 5", name="likelihood_range"),
        CheckConstraint("severity BETWEEN 1 AND 5", name="severity_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    register_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_risk_opportunity_register_doc_id_documented_information",
        ),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_risk_opportunity_org_id_organization",
        ),
        nullable=False,
    )
    type: Mapped[RiskOpportunityType] = mapped_column(risk_opportunity_type_enum, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "process.id",
            ondelete="RESTRICT",
            name="fk_risk_opportunity_process_id_process",
        ),
        nullable=True,
    )
    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "clause.id",
            ondelete="RESTRICT",
            name="fk_risk_opportunity_clause_id_clause",
        ),
        nullable=True,
    )
    likelihood: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    # STORED, derived (likelihood x severity per scoring_method); re-derived on every write.
    risk_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_method: Mapped[ScoringMethod] = mapped_column(scoring_method_enum, nullable=False)
    treatment: Mapped[str | None] = mapped_column(Text, nullable=True)
    effectiveness: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_capa_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "capa.id",
            ondelete="RESTRICT",
            name="fk_risk_opportunity_linked_capa_id_capa",
        ),
        nullable=True,
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id",
            ondelete="RESTRICT",
            name="fk_risk_opportunity_created_by_app_user",
        ),
        nullable=False,
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "app_user.id",
            ondelete="RESTRICT",
            name="fk_risk_opportunity_updated_by_app_user",
        ),
        nullable=True,
    )
