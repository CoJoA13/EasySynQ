"""A reconstructed revision family in a run (slice S-ing-3, doc 09 §7.1/§7.3, doc 14 §13 §551).

``import_version_family`` is a Stage-4 output — one row per group of files recognized as one
document's revision history (``SOP-PUR-002_v1``, ``_v2``, ``_v3 FINAL`` → one family), grouped by
matching ``doc_code`` else normalized base-name (§7.1). Like all ``import_*`` it is **transient
staging** (doc 14 §1.2). It is a *suggestion only*: the **default** (R10) is that ONLY the canonical
(``effective_file_id``) becomes the controlled baseline and the older members are archived as
provenance — NOT approved revision history. ``reconstruct_revision_chain`` (default **OFF**, R10) is
the per-family opt-in a human sets at S-ing-4 review to materialize the older members as provenance
at
commit; it is never auto-set.

``ordered_member_file_ids`` is the full membership in §7.2 canonical order (newest/effective first),
under a TOTAL order (version-marker → mtime/embedded-modified → editable-source > PDF →
/Current//Released/ > /Archive//Old/ → lexically-lowest rel_path → id) so a re-run's
DELETE-then-INSERT
re-derives byte-identical ordering (the idempotency contract). ``effective_file_id`` is its head —
the
candidate Effective baseline. ``evidence`` carries the per-tie-break rationale +
``obsolete_candidate``
pre-flags (the ``import_classification.evidence`` shape).

``UNIQUE (run_id, family_key)`` is the idempotency key (``family_key`` = the ``doc_code`` else the
normalized base-name). ``run_id``/``effective_file_id`` FKs are ``ON DELETE CASCADE`` (the
transient-layer exception, doc 14 §1.2); ``ordered_member_file_ids`` is a plain UUID array kept
consistent by the whole-run CASCADE.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ImportVersionFamily(Base):
    __tablename__ = "import_version_family"
    __table_args__ = (
        # doc 09 §7 idempotency key — one family per (run, family_key); the DELETE-then-INSERT
        # re-run re-derives the same key (doc_code or normalized base-name, NFC-stable).
        UniqueConstraint("run_id", "family_key", name="uq_import_version_family_run_family_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"), nullable=False
    )
    # The grouping key: the recognized doc_code if present, else the normalized base-name.
    family_key: Mapped[str] = mapped_column(Text, nullable=False)
    base_name: Mapped[str] = mapped_column(Text, nullable=False)
    doc_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full membership in §7.2 canonical (newest-first) order; a plain UUID[] kept consistent by the
    # whole-run CASCADE. The §13 ``ordered_member_file_ids[]`` column.
    ordered_member_file_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    effective_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_file.id", ondelete="CASCADE"), nullable=False
    )
    # R10 opt-in: default OFF = current-only-as-provenance. A human sets it at S-ing-4 review to
    # reconstruct the older members as provenance (never approved history) at commit (S-ing-5).
    reconstruct_revision_chain: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    # The per-tie-break rationale + obsolete_candidate pre-flags shown to the reviewer.
    evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
