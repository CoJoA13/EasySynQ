"""ingestion commit: import_commit_result + commit states + provenance + Import Report (S-ing-5)

Adds the v1 Ingestion engine's COMMIT stage (doc 09 §10/§12.1/§13, doc 14 §13/§5.1) on top of the
S-ing-4 review foundation (0032). This is the FIRST ingestion migration that touches the vault side: it
folds durable provenance onto ``documented_information`` and links the run to its Import Report record.

1. **import_run_status ADD VALUE** — ``Committing`` / ``Completed`` / ``PartiallyCommitted`` (the commit
   region). Additive ``ALTER TYPE … ADD VALUE`` (the 0011-0032 pattern); the values are NOT used by a row
   in THIS migration, so the PG16 in-txn rule holds. (No ``MirrorSync`` status — the post-commit mirror
   regen is a best-effort enqueue; ``Completed`` does not block on the regenerable mirror, R11/D2.)
2. **event_type ADD VALUE** — ``IMPORT_ITEM_COMMITTED`` / ``IMPORT_ITEM_FAILED`` /
   ``IMPORT_RUN_COMPLETED`` / ``IMPORT_RUN_PARTIAL`` (additive, the 0029 event pattern).
3. **import_commit_result_status** — a fresh ``CREATE TYPE`` (success/failed/noop; usable same-txn).
   Tuple sourced from the ORM ``IMPORT_COMMIT_RESULT_STATUS_VALUES`` so the DDL and the SAEnum never
   drift.
4. **import_commit_result** — the idempotent commit ledger (doc 14 §13). ``UNIQUE(run_id, file_id)`` is
   the per-item idempotency + single-flight guard. ``run_id``/``file_id`` FKs CASCADE (transient layer);
   ``org_id`` RESTRICT; the vault back-pointers ``vault_document_id``/``vault_version_id`` are **ON DELETE
   SET NULL** (the ledger is provenance, not an integrity anchor — a later record-disposition/WORM-destroy
   purge must not be FK-blocked; the recurring blob-FK lesson). FK names are explicit (the convention
   default for ``vault_document_id`` would exceed PG's 63-char limit — the clause_mapping/process_link
   lesson).
5. **documented_information.import_provenance** — ``ADD COLUMN`` jsonb null (doc 14 §5.1). The durable
   provenance fold ({source_rel_path, source_sha256, run_id, classifier_version, confidence, decided_by})
   on the committed artifact (DOCUMENT or RECORD — both share this base table) so it survives the staging
   TTL purge. A column, NOT a satellite table.
6. **import_run.committing_started_at** — ``ADD COLUMN`` timestamptz null (the commit-reaper progress
   anchor — commit holds no source-root lock, so the reaper uses progress-liveness over the ledger +
   this) and **import_run.report_record_id** — ``ADD COLUMN`` uuid null FK→documented_information RESTRICT
   (the §12.1 Import Report record; the mirror enumerates it to export ``_ImportReport/``; RESTRICT — the
   RETAIN_PERMANENT report is never disposed).
7. **Explicit GRANTs** — SELECT, INSERT, UPDATE on import_commit_result to ``easysynq_app`` (a failed
   item's row is upserted to success on resume), pg_roles-guarded (the 0029-0032 precedent).

Migration notes: the new table + the two ADD COLUMNs round-trip clean against the ORM. The downgrade
DELETEs import_commit_result first (populated-DB safety; it carries no inbound FK), drops the table + the
fresh ``import_commit_result_status`` TYPE, then drops the three ADD COLUMNs; the import_run_status /
event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0029 owns/DROPs those types on ITS
downgrade; a re-upgrade re-adds via ADD VALUE IF NOT EXISTS). Round-trips up↔down↔check on PG16 incl. a
populated-DB downgrade (a run → file → a committed documented_information + version + a commit_result +
the report record).

Revision ID: 0033_ingestion_commit
Revises: 0032_ingestion_review
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._ingestion_enums import IMPORT_COMMIT_RESULT_STATUS_VALUES

revision: str = "0033_ingestion_commit"
down_revision: str | None = "0032_ingestion_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. + 2. Extend the run-status + event-type enums (additive; not used by a row here → in-txn safe).
    op.execute("ALTER TYPE import_run_status ADD VALUE IF NOT EXISTS 'Committing'")
    op.execute("ALTER TYPE import_run_status ADD VALUE IF NOT EXISTS 'Completed'")
    op.execute("ALTER TYPE import_run_status ADD VALUE IF NOT EXISTS 'PartiallyCommitted'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'IMPORT_ITEM_COMMITTED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'IMPORT_ITEM_FAILED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'IMPORT_RUN_COMPLETED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'IMPORT_RUN_PARTIAL'")

    # 3. The commit-result enum (CREATE TYPE → usable same-txn). Tuple from the ORM *_VALUES.
    postgresql.ENUM(
        *IMPORT_COMMIT_RESULT_STATUS_VALUES, name="import_commit_result_status"
    ).create(bind, checkfirst=True)
    commit_result_status = postgresql.ENUM(
        name="import_commit_result_status", create_type=False
    )

    # 4. import_commit_result — the idempotent commit ledger.
    op.create_table(
        "import_commit_result",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result", commit_result_status, nullable=False),
        sa.Column("vault_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vault_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "committed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_import_commit_result_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["import_run.id"],
            name="fk_import_commit_result_run_id_import_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["import_file.id"],
            name="fk_import_commit_result_file_id_import_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vault_document_id"],
            ["documented_information.id"],
            name="fk_import_commit_result_vault_document_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["vault_version_id"],
            ["document_version.id"],
            name="fk_import_commit_result_vault_version_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_commit_result"),
        sa.UniqueConstraint("run_id", "file_id", name="uq_import_commit_result_run_file"),
    )

    # 5. documented_information.import_provenance — the durable provenance fold (doc 14 §5.1).
    op.add_column(
        "documented_information",
        sa.Column("import_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # 6. import_run commit-stage columns.
    op.add_column(
        "import_run",
        sa.Column("committing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "import_run",
        sa.Column("report_record_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_import_run_report_record_id_documented_information",
        "import_run",
        "documented_information",
        ["report_record_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 7. Least-privilege grant for the non-owner app role (SELECT, INSERT, UPDATE — the resume upsert),
    #    pg_roles-guarded (the 0029-0032 pattern).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON import_commit_result TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # DELETE first so a populated-DB downgrade does not depend on cascade ordering (import_commit_result
    # carries no inbound FK of its own). Drop the report-record FK + the three ADD COLUMNs, then the
    # ledger table + the fresh import_commit_result_status TYPE. The import_run_status / event_type ADD
    # VALUEs are irreversible in PostgreSQL → no-op (0029's downgrade DROPs those types wholesale; a
    # re-upgrade re-adds the values via ADD VALUE IF NOT EXISTS).
    op.execute("DELETE FROM import_commit_result")
    op.drop_constraint(
        "fk_import_run_report_record_id_documented_information",
        "import_run",
        type_="foreignkey",
    )
    op.drop_column("import_run", "report_record_id")
    op.drop_column("import_run", "committing_started_at")
    op.drop_column("documented_information", "import_provenance")
    op.drop_table("import_commit_result")
    op.execute("DROP TYPE IF EXISTS import_commit_result_status")
