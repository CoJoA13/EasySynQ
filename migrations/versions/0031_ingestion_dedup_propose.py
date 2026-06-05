"""ingestion dedup + propose: import_dupe_cluster + import_version_family + import_proposal_node + Deduping/Proposing/Proposed (S-ing-3)

Adds the v1 Ingestion engine's dedup + proposal stages (doc 09 §7-8, doc 14 §13) on top of the
S-ing-2 extract/classify foundation (0030). It introduces ONLY transient ``import_*`` staging (doc 14
§1.2); no vault table is touched (commit is S-ing-5).

1. **import_run_status ADD VALUE** — ``Deduping`` → ``Proposing`` → ``Proposed`` (the new resting
   terminal awaiting S-ing-4 review; ``Classified`` consequently stops being terminal — a code-level
   change, no DDL). Additive ``ALTER TYPE … ADD VALUE`` (the 0011-0030 pattern); the new values are
   NOT used by a row in this migration, so the PG16 in-txn rule holds.
2. **import_dupe_method** — a fresh ``CREATE TYPE`` (``exact``/``near``; usable same-txn). Tuple
   sourced from the ORM ``*_VALUES`` so the hand-authored DDL and the SAEnum binding never drift.
3. **import_dupe_cluster** — Stage-4 output, one row per detected duplicate cluster (doc 09 §7.1).
   ``member_file_ids`` / (in §4) ``ordered_member_file_ids`` are ``UUID[]`` — the FIRST array-of-UUID
   in the schema (only ARRAY(Text) existed before, 0030); declared identically here and on the ORM so
   ``alembic check`` stays clean. ``canonical_file_id`` FK → import_file CASCADE. UNIQUE(run_id,
   method, canonical_file_id) = the §7 idempotency key.
4. **import_version_family** — Stage-4 reconstructed revision family (doc 09 §7.3). ``reconstruct_
   revision_chain`` Boolean default false (the R10 opt-in; set at S-ing-4). UNIQUE(run_id, family_key).
5. **import_proposal_node** — Stage-5 per-keep-item proposal (doc 09 §8). UNIQUE(run_id, file_id).
6. **Explicit GRANTs** — SELECT/INSERT/UPDATE/DELETE on the three new tables to ``easysynq_app`` (the
   dedup/propose worker + the API run as the non-owner app role), pg_roles-guarded so a role-less CI
   DB doesn't error (the 0029/0030 precedent). NO new permission keys / event_type / audit_object_type
   (stage transitions reuse the 0029 ``IMPORT_RUN_STAGE_CHANGED`` event + ``import_run`` object_type).

Migration notes: all new UNIQUE constraints are plain (composite b-tree, no expression/partial) → no
``env.py`` change, alembic check clean. The ``UUID[]`` columns round-trip with ``compare_type`` clean
when declared identically on both sides (the load-bearing check; verify on PG16). The downgrade DROPs
the three tables (DELETE first so a POPULATED-DB downgrade does not depend on cascade ordering — they
carry no inbound FK of their own) + the fresh ``import_dupe_method`` TYPE; the import_run_status ADD
VALUEs are irreversible in PostgreSQL → no-op (0029 owns/DROPs that type on ITS downgrade; a re-upgrade
re-adds via ADD VALUE IF NOT EXISTS). Round-trips up↔down↔check on PG16 incl. a populated-DB downgrade
(a run → file → cluster/family/node chain with real ``uuid[]`` members).

Revision ID: 0031_ingestion_dedup_propose
Revises: 0030_ingestion_extract_classify
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._ingestion_enums import IMPORT_DUPE_METHOD_VALUES

revision: str = "0031_ingestion_dedup_propose"
down_revision: str | None = "0030_ingestion_extract_classify"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_RUN_STATUS = ("Deduping", "Proposing", "Proposed")
_NEW_TABLES = ("import_proposal_node", "import_version_family", "import_dupe_cluster")


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id"], ["organization.id"], name=f"fk_{table}_org_id_organization", ondelete="RESTRICT"
    )


def _run_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["run_id"], ["import_run.id"], name=f"fk_{table}_run_id_import_run", ondelete="CASCADE"
    )


def _file_fk(table: str, column: str, constraint: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["import_file.id"], name=constraint, ondelete="CASCADE"
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

    # 1. Extend the run-status state machine (additive; not used by a row here → PG16 in-txn safe).
    for value in _NEW_RUN_STATUS:
        op.execute(f"ALTER TYPE import_run_status ADD VALUE IF NOT EXISTS '{value}'")

    # 2. The dedup-method enum (CREATE TYPE → usable same-txn). Tuple from the ORM *_VALUES.
    postgresql.ENUM(*IMPORT_DUPE_METHOD_VALUES, name="import_dupe_method").create(
        bind, checkfirst=True
    )
    import_dupe_method = postgresql.ENUM(name="import_dupe_method", create_type=False)

    # 3. import_dupe_cluster — Stage-4 duplicate clusters. UNIQUE(run_id, method, canonical_file_id).
    #    member_file_ids is the FIRST UUID[] in the schema — declared identically to the ORM model.
    op.create_table(
        "import_dupe_cluster",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", import_dupe_method, nullable=False),
        sa.Column("member_file_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False),
        sa.Column("canonical_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jaccard", sa.Float(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("import_dupe_cluster"),
        _run_fk("import_dupe_cluster"),
        _file_fk(
            "import_dupe_cluster",
            "canonical_file_id",
            "fk_import_dupe_cluster_canonical_file_id_import_file",
        ),
        sa.UniqueConstraint(
            "run_id", "method", "canonical_file_id", name="uq_import_dupe_cluster_run_method_canon"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_dupe_cluster"),
    )

    # 4. import_version_family — Stage-4 reconstructed revision families. UNIQUE(run_id, family_key).
    op.create_table(
        "import_version_family",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("family_key", sa.Text(), nullable=False),
        sa.Column("base_name", sa.Text(), nullable=False),
        sa.Column("doc_code", sa.Text(), nullable=True),
        sa.Column(
            "ordered_member_file_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
        ),
        sa.Column("effective_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "reconstruct_revision_chain",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("import_version_family"),
        _run_fk("import_version_family"),
        _file_fk(
            "import_version_family",
            "effective_file_id",
            "fk_import_version_family_effective_file_id_import_file",
        ),
        sa.UniqueConstraint(
            "run_id", "family_key", name="uq_import_version_family_run_family_key"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_version_family"),
    )

    # 5. import_proposal_node — Stage-5 per-keep-item proposal. UNIQUE(run_id, file_id).
    op.create_table(
        "import_proposal_node",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_identifier", sa.Text(), nullable=True),
        sa.Column("identifier_source", sa.Text(), nullable=True),
        sa.Column("target_ia_path", sa.Text(), nullable=True),
        sa.Column("proposed_owner", sa.Text(), nullable=True),
        sa.Column("owner_source", sa.Text(), nullable=True),
        sa.Column(
            "conflict_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("import_proposal_node"),
        _run_fk("import_proposal_node"),
        _file_fk(
            "import_proposal_node", "file_id", "fk_import_proposal_node_file_id_import_file"
        ),
        sa.UniqueConstraint("run_id", "file_id", name="uq_import_proposal_node_run_file"),
        sa.PrimaryKeyConstraint("id", name="pk_import_proposal_node"),
    )

    # 6. Explicit least-privilege grants for the non-owner app role; pg_roles-guarded (0030 pattern).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON import_dupe_cluster TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON import_version_family TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON import_proposal_node TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # DELETE first so a populated-DB downgrade does not depend on cascade ordering (the children carry
    # no inbound FK of their own). Then drop the tables + the fresh import_dupe_method TYPE. The
    # import_run_status ADD VALUEs are irreversible in PostgreSQL → no-op (0029's downgrade DROPs the
    # whole import_run_status type; a re-upgrade re-adds the values via ADD VALUE IF NOT EXISTS).
    for table in _NEW_TABLES:
        op.execute(f"DELETE FROM {table}")
    for table in _NEW_TABLES:
        op.drop_table(table)
    op.execute("DROP TYPE IF EXISTS import_dupe_method")
