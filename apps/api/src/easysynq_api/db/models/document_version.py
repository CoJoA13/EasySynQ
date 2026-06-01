"""An immutable version snapshot (doc 14 §5.3) — the heart of what S3 produces.

A check-in freezes the working draft into one of these: content lives in ``source_blob``
(content-addressed), and ``metadata_snapshot`` captures title/type/owner/clauses AS THEY WERE.
There is no UPDATE path (immutability is app-enforced in S3; the DB-grant stripping that makes
it structural lands in S6). ``change_reason`` + ``change_significance`` are mandatory at
check-in (INV-3). Lifecycle/effectivity columns are reserved S4 hooks; the **INV-1
single-Effective partial unique index is created in S4** (no version is ``Effective`` in S3).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._vault_enums import (
    ChangeSignificance,
    VersionState,
    change_significance_enum,
    version_state_enum,
)


class DocumentVersion(Base):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "version_seq", name="uq_document_version_document_id_version_seq"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documented_information.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_label: Mapped[str] = mapped_column(Text, nullable=False)
    change_significance: Mapped[ChangeSignificance] = mapped_column(
        change_significance_enum, nullable=False
    )
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_state: Mapped[VersionState] = mapped_column(
        version_state_enum, default=VersionState.Draft, nullable=False
    )
    source_blob_sha256: Mapped[str] = mapped_column(
        Text, ForeignKey("blob.sha256", ondelete="RESTRICT"), nullable=False
    )
    metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rendition_blob_sha256: Mapped[str | None] = mapped_column(
        Text, ForeignKey("blob.sha256", ondelete="RESTRICT"), nullable=True
    )  # reserved (rendering = S7)
    effective_from: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # reserved (S4)
    effective_to: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # reserved (S4)
    superseded_by_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # reserved (S4): self-FK + supersession chain land with the lifecycle slice
    imported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
