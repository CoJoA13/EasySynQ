"""The ``interested_party`` register-row satellite (S-interested-parties-1, doc 14 §6, clause 4.2,
R51).

A **1:many satellite** of the ``kind=DOCUMENT`` ``IPR`` register head: ``id`` is its OWN uuid (NOT a
shared-PK subtype like ``quality_objective`` — a register has many rows, so they cannot share the
head's PK) and ``register_doc_id`` FKs to the head ``documented_information.id``. The rows are the
register version's controlled content (edited only while the head is Draft/UnderRevision; snapshot
into the version at publish — R49/R50/R51 / spec §3). The Context register clone (clause 4.1).

⚠ Clause 4.2 "needs and expectations of interested parties" is ORG-LEVEL — interested parties are
strategic and org-wide (the standard's own examples: customers, regulators, suppliers, employees,
owners, the community). So — like ``context_issue`` and unlike ``risk_opportunity`` — there is **no
``process_id``**: the register rides ``register.*`` at the **SYSTEM** scope (the QMS-leadership
steward), the contracted org-level model (doc 14 §6). The enriched columns (``influence`` axis +
``status`` + ``last_reviewed_at``) extend the minimal contract per the owner decision (R51);
``party_type`` is the ISO spine. ``org_id`` is carried per the §1.1 convention (the doc-14 §6
editorial-gap correction R50 named).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._interested_party_enums import (
    InterestedPartyInfluence,
    InterestedPartyStatus,
    InterestedPartyType,
    interested_party_influence_enum,
    interested_party_status_enum,
    interested_party_type_enum,
)


class InterestedParty(Base):
    __tablename__ = "interested_party"
    __table_args__ = (Index("ix_interested_party_register_doc_id", "register_doc_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    register_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_interested_party_register_doc_id_documented_information",
        ),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_interested_party_org_id_organization",
        ),
        nullable=False,
    )
    # The ISO clause-4.2 spine (the relevant party category) — NOT NULL on every row.
    party_type: Mapped[InterestedPartyType] = mapped_column(
        interested_party_type_enum, nullable=False
    )
    party_name: Mapped[str] = mapped_column(Text, nullable=False)
    needs_expectations: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional relevance/influence axis (a party may be unrated) — the enriched model (R51).
    influence: Mapped[InterestedPartyInfluence | None] = mapped_column(
        interested_party_influence_enum, nullable=True
    )
    # Per-party lifecycle (a new party is always ``active``, set by the service on create; a
    # party is retired by closing it, never deleted). NOT NULL, NO server_default — the service
    # always supplies a value on insert (greenfield, the server_default alembic-check trap avoided).
    status: Mapped[InterestedPartyStatus] = mapped_column(
        interested_party_status_enum, nullable=False
    )
    last_reviewed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
            name="fk_interested_party_created_by_app_user",
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
            name="fk_interested_party_updated_by_app_user",
        ),
        nullable=True,
    )
