"""The human-in-the-loop review decision log (slice S-ing-4, doc 09 §9/§12.2, doc 14 §13 §553).

``import_decision`` is the **append-only** record of every reviewer act over a ``Proposed`` import
run (and the future ML-label set, doc 09 §6.6). One row per decision; ``before``/``after`` jsonb
capture the dimensional change. It is the SINGLE source of human **dimensional** intent (kind
/ type / clause / process / owner / identifier corrections + the accept/exclude/defer disposition) —
folded at read time over the engine's ``import_classification`` / ``import_proposal_node`` (R10: the
confirmed kind is NEVER written back to ``import_classification``; the classification stays the
immutable engine proposal). **Structural** intent (merge/split) additionally live-mutates the
``import_dupe_cluster`` / ``import_version_family`` rows and is recorded here too (cluster-keyed).

Like all ``import_*`` it is **transient staging** (doc 14 §1.2, TTL-purged post-commit) and writes
NOTHING to the vault.

Target is **file XOR cluster** (the ``ck_import_decision_exactly_one_target`` CHECK): a dimensional
decision targets ``file_id`` (FK CASCADE); a structural merge/split targets ``cluster_id`` — a
POLYMORPHIC id (an ``import_dupe_cluster.id`` OR an ``import_version_family.id``, disambiguated by
``target_kind``), carried with **NO FK** (the ``signature_event.signed_object_id`` precedent), as a
merge may reference a family created in the same operation.

``idempotency_key`` (optional, from the ``Idempotency-Key`` header) backs a partial UNIQUE
``(run_id, idempotency_key) WHERE idempotency_key IS NOT NULL`` (created as raw DDL in 0032 +
excluded from ``alembic check`` via ``env.py._MIGRATION_MANAGED_INDEXES`` — a declarative partial
index round-trips wrong; the 0024 ``ix_worm_destroy_request_open`` precedent). A replay returns the
existing decision (no duplicate row, no re-mutation) so the log stays clean under retries.

GRANT is **SELECT, INSERT only** to the app role (append-only — no UPDATE/DELETE; CASCADE handles
purge), a deliberate tightening vs the full-DML sibling tables. ``run_id``/``file_id`` FKs are
``ON DELETE CASCADE`` (the transient-layer exception, doc 14 §1.2); ``org_id``/``decided_by`` are
RESTRICT (audit-like — a populated org/user cannot be deleted out from under its decision log).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ingestion_enums import ImportDecisionAction, import_decision_action_enum


class ImportDecision(Base):
    __tablename__ = "import_decision"
    __table_args__ = (
        # Exactly one target: a dimensional decision on a file, OR a structural one on a cluster/
        # family. The convention (db/base.py) wraps the bare token to
        # ``ck_import_decision_exactly_one_target``.
        CheckConstraint(
            "(file_id IS NULL) <> (cluster_id IS NULL)",
            name="exactly_one_target",
        ),
        Index("ix_import_decision_run_file_decided", "run_id", "file_id", "decided_at"),
        Index("ix_import_decision_run_decided", "run_id", "decided_at"),
        # NOTE: the partial UNIQUE idempotency index
        # (``uq_import_decision_run_idem ... WHERE idempotency_key IS NOT NULL``) is created as raw
        # DDL in migration 0032 and excluded from ``alembic check`` via
        # ``env.py._MIGRATION_MANAGED_INDEXES`` — a declarative partial index round-trips wrong (PG
        # normalises the predicate; the 0024 ``ix_worm_destroy_request_open`` lesson). Not modelled
        # here on purpose.
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"), nullable=False
    )
    # Dimensional decision → file_id set; structural (merge/split) → cluster_id set (the CHECK).
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_file.id", ondelete="CASCADE"), nullable=True
    )
    # Polymorphic: an import_dupe_cluster.id OR import_version_family.id; NO FK (a merge may
    # reference a family created in the same op); target_kind disambiguates.
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 'file' | 'dupe_cluster' | 'version_family'.
    target_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[ImportDecisionAction] = mapped_column(
        import_decision_action_enum, nullable=False
    )
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False
    )
    decided_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
