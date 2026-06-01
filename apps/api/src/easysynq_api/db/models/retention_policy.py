"""Retention schedule catalog (doc 14 §10). FK target only in S3 — rows are a v1 concern
(D-1, records). The full policy columns (basis, duration, disposition_action, …) land with
records (S5); S3 needs just the table so ``document_type.default_retention_policy_id`` resolves.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class RetentionPolicy(Base):
    __tablename__ = "retention_policy"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
