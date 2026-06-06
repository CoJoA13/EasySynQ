"""The document↔document reference graph — the where-used substrate (slice S-dcr-2; doc 14 §5.6,
doc 05 §7.1).

A directed link ``from_document_id → to_document_id`` of a typed relationship (``parent_of`` /
``child_of`` / ``references`` / ``supersedes``). Unlike the append-only stage trails, a link is
editable **metadata** — it is created and removed (NOT versioned), so the table grants ``SELECT,
INSERT, DELETE`` (the ``clause_mapping`` / ``process_link`` precedent), NOT the append-only
``REVOKE``. The where-used engine (S-dcr-2) traverses it in both directions (indexes on both FK
columns); a DCR's impact assessment reads it to surface dependent / referenced-by documents (doc
05 §7.2).

FK constraint names are given explicitly (``fk_doc_link_from`` / ``fk_doc_link_to``) — the
SQLAlchemy-convention default would exceed PostgreSQL's 63-char identifier limit (the
``clause_mapping.documented_information_id`` precedent).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._vault_enums import DocumentLinkType, document_link_type_enum


class DocumentLink(Base):
    __tablename__ = "document_link"
    __table_args__ = (
        UniqueConstraint(
            "from_document_id", "to_document_id", "link_type", name="uq_document_link_from_to_type"
        ),
        # No self-link. Bare token — the metadata ck naming convention expands it to
        # ck_document_link_no_self, matching the migration's same-token constraint.
        CheckConstraint("from_document_id <> to_document_id", name="no_self"),
        Index("ix_document_link_from_document_id", "from_document_id"),
        Index("ix_document_link_to_document_id", "to_document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    from_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT", name="fk_doc_link_from"),
        nullable=False,
    )
    to_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT", name="fk_doc_link_to"),
        nullable=False,
    )
    link_type: Mapped[DocumentLinkType] = mapped_column(document_link_type_enum, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
