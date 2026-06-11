"""The documented-information spine (doc 14 §5.1, doc 18 M13).

A single ``kind``-discriminated table; in S3 only ``kind=DOCUMENT`` rows exist. It carries the
document headline fields (``current_state``, ``is_singleton``, ``classification``) plus the
identity/scope fields (``identifier``, ``folder_path``, ``document_type_id``). The ``record``
extension table (record-only columns) lands in S5. ``current_effective_version_id`` is a
reserved S4 hook (the FK + single-Effective cutover land with the lifecycle slice).

``folder_path`` is stored as ltree-compatible dotted text in S3 (the PDP matches it in Python,
exactly as in S2); the real ``ltree`` column type + GiST index are an additive later change.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._vault_enums import (
    Classification,
    DocumentCurrentState,
    DocumentKind,
    classification_enum,
    document_current_state_enum,
    document_kind_enum,
)


class DocumentedInformation(Base):
    __tablename__ = "documented_information"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "identifier", name="uq_documented_information_org_id_identifier"
        ),
        # R25 singleton: at most one Effective instance per (org, document_type) AT A TIME
        # (Quality Policy / Scope Statement) — a draft successor may coexist while the current
        # governs. The predicate carries the explicit enum cast PG stores so alembic check is clean.
        Index(
            "uq_doc_info_singleton_effective",
            "org_id",
            "document_type_id",
            unique=True,
            postgresql_where=text(
                "current_state = 'Effective'::document_current_state AND is_singleton = true"
            ),
        ),
        Index(
            "ix_documented_information_next_review_due",
            "next_review_due",
            postgresql_where=text("next_review_due IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    framework_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("framework.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[DocumentKind] = mapped_column(document_kind_enum, nullable=False)
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    legacy_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    document_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_type.id", ondelete="RESTRICT"), nullable=True
    )
    area_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    folder_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_state: Mapped[DocumentCurrentState] = mapped_column(
        document_current_state_enum, default=DocumentCurrentState.Draft, nullable=False
    )
    is_singleton: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    current_effective_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # Explicit name: the convention's fk_{table}_{col}_{reftable} would be 71 chars (> PG's
        # 63-char identifier limit), so name it here and identically in 0007 (54 chars).
        ForeignKey(
            "document_version.id",
            ondelete="RESTRICT",
            name="fk_documented_information_current_effective_version_id",
            use_alter=True,  # cycle-closing back edge (doc↔version) — break the metadata sort cycle
        ),
        nullable=True,
    )  # S4: the single governing Effective version (set atomically at the release cutover)
    classification: Mapped[Classification] = mapped_column(
        classification_enum, default=Classification.Internal, nullable=False
    )
    # S-drift-1 (D5, doc 04 §9): periodic re-review. NULL review_period_months = not scheduled
    # (legacy/opt-out — the owner's no-backfill fork). next_review_due is STORED, not derived:
    # review-confirm resets it from the review date, not from effective_from.
    review_period_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_review_due: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    last_reviewed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # S-ack-1 (doc 04 §8.2, R43): the per-document master switch — obligations exist iff this AND
    # the entry's ack_required. Mutable working-row state; frozen into metadata_snapshot at checkin.
    acknowledgement_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=True
    )
    # S-ing-5 (doc 14 §5.1): durable import provenance folded onto the committed artifact (DOCUMENT
    # or
    # RECORD — both share this base table) so it survives the staging-layer TTL purge. Carries
    # {source_rel_path, source_sha256, run_id, classifier_version, confidence, decided_by}. NULL for
    # natively-authored documents/records; set only at import commit.
    import_provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
