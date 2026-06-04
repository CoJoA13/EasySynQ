"""The ``evidence_pack`` header — a first-class, scope-limited, immutable audit bundle (S-pack-1).

doc 06 §7 (UJ-7): a pack assembles every record + its evidence + a manifest for a clause
or process (with an optional date overlay), then seals it. The header persists the scope definition,
the build status, and the gap/exclusion summaries; the ``pack_item`` rows are the resolved members.

On seal the generated ZIP is written to the WORM ``records`` bucket and **registered as an
EVIDENCE-type Record** (``pack_record_id``) pinned ``RETAIN_PERMANENT`` (doc 06 §7.4) — so "which
pack did we hand the auditor" is itself auditable. The ZIP blob is reached via
``pack_record_id → evidence_blob → blob``; ``zip_blob_sha256`` is a denormalised display pointer
with **no FK to ``blob``** — a RESTRICT FK would abort the EVIDENCE record's R27 WORM-destroy hatch
(``delete_blob_and_links``) and defeat the blob-row-iff-bytes invariant.

Per the every-table tenancy invariant (doc 14 §15.3) the header carries its own ``org_id``;
``framework_id`` is carried for gap-report clause scoping (the ``documented_information``/``clause``
C5 set). ``content_hash`` is the domain-separated *manifest* seal (the pack's "own SHA-256" on the
cover); the ZIP file digest is ``zip_blob_sha256``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._pack_enums import PackScopeKind, PackStatus, pack_scope_kind_enum, pack_status_enum


class EvidencePack(Base):
    __tablename__ = "evidence_pack"
    __table_args__ = (Index("ix_evidence_pack_org_id_status", "org_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    framework_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    scope_kind: Mapped[PackScopeKind] = mapped_column(pack_scope_kind_enum, nullable=False)
    # {"clause_ids": [...]} for CLAUSE, {"process_ids": [...]} for PROCESS (UUID strings).
    scope_selector: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    period_start: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[PackStatus] = mapped_column(
        pack_status_enum,
        server_default=text("'DRAFT'"),
        default=PackStatus.DRAFT,
        nullable=False,
    )
    # Set when the build is enqueued (status→BUILDING); the reaper's staleness basis.
    build_started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    item_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0"), default=0, nullable=False
    )
    gap_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    exclusion_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # The domain-separated manifest seal (sha256:…) — the pack's "own SHA-256" on the cover sheet.
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The sealed ZIP file digest (== blob.sha256). Plain Text, NO FK to blob (see module docstring).
    zip_blob_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    # S-pack-2: the cached single-PDF portfolio variant (cover + summaries + §11.3-stamped
    # renditions) — a DERIVED view in the non-WORM renditions bucket, NOT part of the seal
    # (content_hash is over the ZIP content list). Plain Text, NO FK to blob (the zip_blob_sha256
    # R27 rationale). NULL until Stage 2 of the build caches it (a Gotenberg outage leaves it NULL).
    portfolio_blob_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    pack_record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("record.id", ondelete="RESTRICT"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    generated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
