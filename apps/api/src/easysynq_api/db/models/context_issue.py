"""The ``context_issue`` register-row satellite (S-context-1, doc 14 §6, clause 4.1, R50).

A **1:many satellite** of the ``kind=DOCUMENT`` ``CTX`` register head: ``id`` is its OWN uuid (NOT a
shared-PK subtype like ``quality_objective`` — a register has many rows, so they cannot share the
head's PK) and ``register_doc_id`` FKs to the head ``documented_information.id``. The rows are the
register version's controlled content (edited only while the head is Draft/UnderRevision; snapshot
into the version at publish — R49/R50 / spec §3).

⚠ Clause 4.1 "context of the organization" is ORG-LEVEL — external/internal issues are strategic
and org-wide (the standard's own examples: legal/market/cultural environment; org values/knowledge).
So — unlike ``risk_opportunity`` — there is **no ``process_id``**: the register rides ``register.*``
at the **SYSTEM** scope (the QMS-leadership steward), the contracted org-level model (doc 14 §6, the
internal/external dichotomy). The enriched columns (``category`` SWOT + ``status`` +
``last_reviewed_at``)
extend the minimal contract per the owner decision (R50); ``classification`` is the ISO spine.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._context_enums import (
    ContextCategory,
    ContextClassification,
    ContextIssueStatus,
    context_category_enum,
    context_classification_enum,
    context_issue_status_enum,
)


class ContextIssue(Base):
    __tablename__ = "context_issue"
    __table_args__ = (Index("ix_context_issue_register_doc_id", "register_doc_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    register_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="RESTRICT",
            name="fk_context_issue_register_doc_id_documented_information",
        ),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_context_issue_org_id_organization",
        ),
        nullable=False,
    )
    # The ISO clause-4.1 spine (external/internal) — NOT NULL on every row.
    classification: Mapped[ContextClassification] = mapped_column(
        context_classification_enum, nullable=False
    )
    # Optional SWOT framing (an issue may be unclassified-by-SWOT) — the enriched model (R50).
    category: Mapped[ContextCategory | None] = mapped_column(context_category_enum, nullable=True)
    # Per-issue lifecycle (a new issue is always ``active``, set by the service on create; an issue
    # is retired by closing it, never deleted). NOT NULL, NO server_default — the service always
    # supplies a value on insert (greenfield, the server_default alembic-check trap avoided).
    status: Mapped[ContextIssueStatus] = mapped_column(context_issue_status_enum, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
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
            name="fk_context_issue_created_by_app_user",
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
            name="fk_context_issue_updated_by_app_user",
        ),
        nullable=True,
    )
