"""The ``pack_item`` membership row — one resolved item of an evidence pack (S-pack-1, doc 06 §7).

A pack resolves to a set of items: the in-scope RECORDs and their PINNED DOCUMENT_VERSIONs.
Each candidate — **including the ones left out** — gets a row carrying its ``inclusion_status``
(R28: the exclusion report IS this table; a silently-dropped item is a spec-defined defect). A
DOCUMENT_VERSION item is a pinned ``document_version`` (``record.source_version_id``,
supersession-proof via its content-addressed blobs). ``content_hash_at_seal`` snapshots the record's
seal at build time.

Membership is rebuilt atomically (delete-all-then-reinsert) at preview and again at build, so there
is no DB UNIQUE backstop — the resolver de-duplicates record/version ids in Python and a single
worker owns the pack under ``FOR UPDATE`` while it (re)builds. ``pack_id`` is ON DELETE CASCADE
(derived membership, no independent WORM bytes — unlike a record); the outward FKs to ``record`` /
``document_version`` are RESTRICT.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._pack_enums import (
    PackInclusionStatus,
    PackItemType,
    pack_inclusion_status_enum,
    pack_item_type_enum,
)


class PackItem(Base):
    __tablename__ = "pack_item"
    __table_args__ = (Index("ix_pack_item_pack_id", "pack_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_pack.id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[PackItemType] = mapped_column(pack_item_type_enum, nullable=False)
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=True
    )
    inclusion_status: Mapped[PackInclusionStatus] = mapped_column(
        pack_inclusion_status_enum, nullable=False
    )
    exclusion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash_at_seal: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
