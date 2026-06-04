"""ingestion run + scan/inventory foundation: import_run + import_file + IMPORT_RUN_* events (S-ing-1)

Stands up the first slice of the v1 Ingestion engine (doc 09, UJ-2) — the run + scan/inventory
foundation (doc 09 §3-4, doc 14 §13). It introduces ONLY the transient ``import_*`` staging layer; no
vault table is touched (commit is a later slice). All ``import_*`` tables are mutable/TTL-purgeable
(doc 14 §1.2).

1. **import_run_status** — the run state machine (``CREATE TYPE``, so its values are usable in the same
   transaction). S-ing-1's reachable subset: ``Created → Scanning → Scanned`` (+ ``Failed``/
   ``Cancelled``); later slices ADD VALUE their stages. The tuple is sourced from the ORM
   ``IMPORT_RUN_STATUS_VALUES`` so the hand-authored CREATE TYPE and the SAEnum binding never drift
   (alembic check cannot catch a CREATE-TYPE label change).
2. **import_run** — the run header / first-class audited object (doc 09 §3.2, doc 14 §546). Carries the
   validated ``source_root`` + ``source_root_hash`` (the Redis source-lock key hash), ``lock_token``
   (CAS-release mirror), ``scan_started_at`` (the stalled-scan reaper cutoff), the ``counts`` summary,
   and the run-config knobs (``ocr_enabled``/``classifier_version``/``committed_by`` are reserved per the
   §13 column list, NULL/unused until later slices).
3. **import_file** — one inventory row per walked path (doc 09 §4.1, doc 14 §547). ``UNIQUE (run_id,
   rel_path)`` is the §11.1 idempotency / upsert key. ``run_id`` is ``ON DELETE CASCADE`` — the one
   deliberate exception to RESTRICT-everywhere, justified by doc 14 §1.2 (pure derived inventory, no
   inbound FK, no independent bytes; the 0025 ``pack_item.pack_id`` CASCADE precedent).
4. **IMPORT_RUN_* event_type** (CREATED/STAGE_CHANGED/FAILED/CANCELLED) + the **import_run
   audit_object_type** — additive ``ALTER TYPE … ADD VALUE`` (the 0011-0028 pattern; the run-lifecycle
   events key on the ``import_run`` id; item/commit events defer to later slices). NO new permission
   keys (``import.execute``/``import.review``/``import.commit`` already exist since 0004; admins hold
   them via the System Administrator role bundle).
5. **Explicit GRANTs** — SELECT/INSERT/UPDATE/DELETE on the two new tables to ``easysynq_app`` (the
   scan worker + the API run on the non-owner app role). Belt-and-suspenders over 0010's ALTER DEFAULT
   PRIVILEGES (the 0024/0025 child-table precedent), guarded so a role-less CI DB doesn't error.

Migration notes: both new constraint/index objects are plain (composite b-tree, no expression/partial)
→ no ``env.py`` change, alembic check clean. The ADD VALUEs are never used by a row in THIS migration
(the PG16 in-txn rule is satisfied; the CREATE-TYPE ``import_run_status`` IS usable same-txn → the
``'Created'`` server_default is fine). The downgrade drops ``import_file`` before ``import_run`` (the
CASCADE FK), with an explicit DELETE first so a POPULATED-DB downgrade does not depend on cascade
ordering; the ADD VALUEs are irreversible → no-op (0001 DROPs the audit types wholesale, so the
up↔down round-trip still passes; a re-upgrade rebuilds them from the ORM *_VALUES). Round-trips
up↔down↔check on PG16 incl. a populated-DB downgrade (a run + a file row).

Revision ID: 0029_ingestion_scan
Revises: 0028_retention_policy_crud
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._ingestion_enums import IMPORT_RUN_STATUS_VALUES

revision: str = "0029_ingestion_scan"
down_revision: str | None = "0028_retention_policy_crud"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

_NEW_EVENT_TYPES = (
    "IMPORT_RUN_CREATED",
    "IMPORT_RUN_STAGE_CHANGED",
    "IMPORT_RUN_FAILED",
    "IMPORT_RUN_CANCELLED",
)
_NEW_OBJECT_TYPES = ("import_run",)


def _org_fk(table: str, column: str = "org_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["organization.id"], name=f"fk_{table}_{column}_organization", ondelete="RESTRICT"
    )


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["app_user.id"], name=f"fk_{table}_{column}_app_user", ondelete="RESTRICT"
    )


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The run-status enum (CREATE TYPE → usable same-txn). Sourced from the ORM tuple.
    postgresql.ENUM(*IMPORT_RUN_STATUS_VALUES, name="import_run_status").create(bind, checkfirst=True)
    import_run_status = postgresql.ENUM(name="import_run_status", create_type=False)

    # 2. import_run — the run header / state machine.
    op.create_table(
        "import_run",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_root", sa.Text(), nullable=False),
        sa.Column("source_root_hash", sa.Text(), nullable=False),
        sa.Column(
            "status", import_run_status, server_default=sa.text("'Created'"), nullable=False
        ),
        sa.Column("lock_token", sa.Text(), nullable=True),
        sa.Column("profile", sa.Text(), nullable=True),
        sa.Column("ocr_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("classifier_version", sa.Text(), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("committed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _org_fk("import_run"),
        _user_fk("import_run", "created_by"),
        _user_fk("import_run", "committed_by"),
        sa.PrimaryKeyConstraint("id", name="pk_import_run"),
    )
    op.create_index(
        "ix_import_run_status_scan_started_at",
        "import_run",
        ["status", "scan_started_at"],
    )

    # 3. import_file — one inventory row per walked path. UNIQUE(run_id, rel_path) = the §11.1
    #    idempotency key; run_id CASCADE (transient derived inventory, doc 14 §1.2).
    op.create_table(
        "import_file",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rel_path", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("ext", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ctime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("staged_blob_uri", sa.Text(), nullable=True),
        sa.Column(
            "scan_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("included_candidate", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("import_file"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["import_run.id"],
            name="fk_import_file_run_id_import_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("run_id", "rel_path", name="uq_import_file_run_id_rel_path"),
        sa.PrimaryKeyConstraint("id", name="pk_import_file"),
    )
    op.create_index("ix_import_file_run_id_sha256", "import_file", ["run_id", "sha256"])

    # 4. Additive enum values (never used by a row in this migration; PG16 in-txn rule satisfied).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")
    for value in _NEW_OBJECT_TYPES:
        op.execute(f"ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS '{value}'")

    # 5. Explicit least-privilege grants for the non-owner app role (scan worker + API). Guarded so a
    #    from-scratch CI DB without the role separation doesn't error.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON import_run TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON import_file TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # import_file → import_run (CASCADE) but clear explicitly so a populated-DB downgrade does not
    # depend on cascade ordering. import_file first (the child).
    op.execute("DELETE FROM import_file")
    op.execute("DELETE FROM import_run")
    op.drop_index("ix_import_file_run_id_sha256", table_name="import_file")
    op.drop_table("import_file")
    op.drop_index("ix_import_run_status_scan_started_at", table_name="import_run")
    op.drop_table("import_run")
    op.execute("DROP TYPE IF EXISTS import_run_status")
    # The event_type / audit_object_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's
    # downgrade DROPs those types wholesale, so the up↔down round-trip still passes; a re-upgrade
    # rebuilds them from the ORM *_VALUES).
