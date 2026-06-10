"""The PG-persisted mirror-build baseline (S-drift-2, doc 05 §9.2, R11).

One row per ``sync_mirror`` build, keyed by the ``.builds/<hex>`` dir name. ``manifest`` is the
build's file/symlink entry list (file entries carry additive ``document_id``/``version_id`` keys) —
the scan's expected-state AUTHORITY: the on-disk ``_meta/manifest.json`` is never trusted (its
deliberately non-deterministic ``generated_at`` also rules out recompute), only byte-verified
against ``manifest_sha256``. Inserted in the build txn (commit-then-swap: an orphan row for a
never-swapped build is harmless — the scan looks up by ``current``'s ACTUAL target); keep-last-20
pruned in the same txn. A regenerable registry, NOT an audit record (the visual_diff posture).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class MirrorBuild(Base):
    __tablename__ = "mirror_build"
    __table_args__ = (UniqueConstraint("build_name", name="uq_mirror_build_build_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    build_name: Mapped[str] = mapped_column(Text, nullable=False)
    built_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Stamped in a small post-swap commit — the pointer-integrity anchor (the S-drift-2 design
    # doc §11.1, docs/superpowers/specs/2026-06-09-s-drift-2-mirror-tamper-scan-design.md): the scan
    # verifies `current` against the newest SWAPPED row, so a repointed/rolled-back/planted tree
    # is MIRROR_TAMPER, never mistaken for the benign no-baseline state. NULL = built-not-swapped
    # (a commit-then-swap crash orphan, or the swap-then-crash window the scan self-heals).
    swapped_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # sha256 of the EXACT bytes written to _meta/manifest.json (generated_at is non-deterministic).
    manifest_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    documents: Mapped[int] = mapped_column(Integer, nullable=False)
    files: Mapped[int] = mapped_column(Integer, nullable=False)
    symlinks: Mapped[int] = mapped_column(Integer, nullable=False)
