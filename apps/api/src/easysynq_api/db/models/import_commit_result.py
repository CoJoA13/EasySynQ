"""The idempotent commit ledger (slice S-ing-5, doc 09 §10.2/§13, doc 14 §13 §554).

``import_commit_result`` is the per-item record of what each commit-ready keep-item produced in the
vault. It is the **idempotency + per-item single-flight guard**: ``UNIQUE(run_id, file_id)`` means a
crash/retry re-commit of an already-committed item detects the existing row and no-ops (doc 09
§10.2 — "detects the existing ``(run_id, file_id)`` commit result … and no-ops, never a duplicate
Document"). ``result`` is ``success`` (a vault document/record was created in the item's own txn) /
``failed`` (an isolated per-item failure — the run continues + resumes the remaining queue, §11.2) /
``noop`` (the doc 14 §13 idempotent-re-commit value — RESERVED, not written in v1: a re-run /
peer-lost item is SKIPPED / rolled-back rather than re-stamped; the read-guards accept it).

Like all ``import_*`` it is **transient staging** (doc 14 §1.2, TTL-purged post-commit) — the
durable provenance lives on the committed artifact's ``documented_information.import_provenance``
(§5.1) and in the immutable Import Report record, so it survives the purge. ``run_id``/``file_id``
are ``ON DELETE CASCADE`` (the transient-layer exception, doc 14 §1.2); ``org_id`` is RESTRICT. The
vault back-pointers ``vault_document_id``/``vault_version_id`` are **ON DELETE SET NULL** — the
ledger is provenance, not an integrity anchor, so a later record-disposition / WORM-destroy purge of
a committed imported doc (which ``delete_blob_and_links`` drives) is never aborted by a RESTRICT FK
(the recurring blob-FK lesson).

GRANT is SELECT, INSERT, UPDATE (a failed item's row is upserted to success on resume; the upsert is
``ON CONFLICT(run_id, file_id) DO UPDATE`` so it never duplicates).
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ._ingestion_enums import ImportCommitResultStatus, import_commit_result_status_enum


class ImportCommitResult(Base):
    __tablename__ = "import_commit_result"
    __table_args__ = (
        # The per-item idempotency + single-flight key (doc 09 §11.1). A re-commit / peer worker
        # detects the existing row and no-ops; the upsert is ON CONFLICT on this constraint.
        UniqueConstraint("run_id", "file_id", name="uq_import_commit_result_run_file"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id",
            ondelete="RESTRICT",
            name="fk_import_commit_result_org_id_organization",
        ),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "import_run.id", ondelete="CASCADE", name="fk_import_commit_result_run_id_import_run"
        ),
        nullable=False,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "import_file.id", ondelete="CASCADE", name="fk_import_commit_result_file_id_import_file"
        ),
        nullable=False,
    )
    result: Mapped[ImportCommitResultStatus] = mapped_column(
        import_commit_result_status_enum, nullable=False
    )
    # The committed vault rows; SET NULL so a later disposition/WORM-destroy purge isn't FK-blocked.
    # vault_version_id is NULL for RECORD-kind items (records have no document_version). FK names
    # are
    # given explicitly — the convention default would exceed PG's 63-char identifier limit and the
    # truncation would drift alembic check (the clause_mapping/process_link lesson).
    vault_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "documented_information.id",
            ondelete="SET NULL",
            name="fk_import_commit_result_vault_document_id",
        ),
        nullable=True,
    )
    vault_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "document_version.id",
            ondelete="SET NULL",
            name="fk_import_commit_result_vault_version_id",
        ),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
