"""A duplicate cluster found in a run (slice S-ing-3, doc 09 §7.1, doc 14 §13 §550).

``import_dupe_cluster`` is a Stage-4 output — one row per detected cluster of duplicate files,
tagged
by the detector that found it (``method`` = ``exact`` byte-identical SHA-256, or ``near`` content
shingling + MinHash/Jaccard ≥ 0.85 over normalized text). Like all ``import_*`` it is **transient
staging** (doc 14 §1.2). It is a *suggestion only* — nothing is committed here; the redundant copies
are merely recorded so "nothing silently vanishes" (doc 09 §7.3/§11.3): a non-canonical member is a
"redundant copy of <canonical>". Version FAMILIES (a document's revision history) are a distinct
concern and live in ``import_version_family`` — only true duplicates land here.

``member_file_ids`` is the full set of files in the cluster (≥2); ``canonical_file_id`` is the §7.2
deterministic keep-pick (a TOTAL order — version-marker → mtime/embedded-modified → editable-source
>
PDF → /Current//Released/ > /Archive//Old/ → lexically-lowest rel_path → id — so an all-tie
exact-dup
resolves identically across re-deliveries, the DELETE-then-INSERT idempotency contract). ``jaccard``
is the cluster's representative similarity (1.0 for exact; the min pairwise exact-Jaccard for near).
``evidence`` carries review caveats — notably ``truncated_comparison`` when any near member's
extracted text was capped (doc 09 §5.3/§7.3 "nothing silently vanishes").

``UNIQUE (run_id, method, canonical_file_id)`` is the idempotency key — a re-run's
DELETE-then-INSERT
re-derives the same clusters; the constraint backstops a racing-twin re-delivery. Both ``run_id``
and
``canonical_file_id`` FKs are ``ON DELETE CASCADE`` (the transient-layer exception, doc 14 §1.2 —
the
import_classification precedent); ``member_file_ids`` is a plain UUID array (no FK), kept consistent
by the whole-run CASCADE.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ingestion_enums import ImportDupeMethod, import_dupe_method_enum


class ImportDupeCluster(Base):
    __tablename__ = "import_dupe_cluster"
    __table_args__ = (
        # doc 09 §7 idempotency key — one cluster per (run, method, canonical). The canonical is the
        # deterministic §7.2 pick, so a re-run's DELETE-then-INSERT converges on the same key.
        UniqueConstraint(
            "run_id", "method", "canonical_file_id", name="uq_import_dupe_cluster_run_method_canon"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[ImportDupeMethod] = mapped_column(import_dupe_method_enum, nullable=False)
    # The full membership (≥2 file ids); a plain UUID[] (arrays can't FK) kept consistent by the
    # whole-run CASCADE. The §13 ``member_file_ids[]`` column.
    member_file_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    canonical_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_file.id", ondelete="CASCADE"), nullable=False
    )
    # 1.0 for exact; the min pairwise exact-Jaccard for near (NULL only defensively).
    jaccard: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Review caveats: {"truncated_comparison": true} when a near member rode a capped text prefix.
    evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
