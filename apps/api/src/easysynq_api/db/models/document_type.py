"""Document-type catalog (doc 14 §6, doc 18 M06).

Carries the explicit ISO documentation level (the ``DOC_CLASS`` authz scope matches on it)
and the ``code`` that drives the ``{TYPE}`` token of an identifier (doc 04 §7). A few defaults
are seeded in ``0006_seed_vault``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._vault_enums import DocumentLevel, document_level_enum


class DocumentType(Base):
    __tablename__ = "document_type"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_document_type_org_id_code"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)  # the {TYPE} token, e.g. "SOP"
    name: Mapped[str] = mapped_column(Text, nullable=False)
    document_level: Mapped[DocumentLevel] = mapped_column(document_level_enum, nullable=False)
    is_singleton: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_retention_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("retention_policy.id", ondelete="RESTRICT"),
        nullable=True,
    )
