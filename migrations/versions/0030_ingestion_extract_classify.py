"""ingestion extract + classify: import_extract + import_classification + Extracting/Classifying/Classified (S-ing-2)

Adds the v1 Ingestion engine's analytical stages (doc 09 §5-6, doc 14 §13) on top of the S-ing-1
scan foundation (0029). It introduces ONLY transient ``import_*`` staging (doc 14 §1.2); no vault
table is touched (commit is S-ing-5).

1. **import_run_status ADD VALUE** — ``Extracting`` → ``Classifying`` → ``Classified`` (the new
   resting checkpoint awaiting S-ing-4 review). Additive ``ALTER TYPE … ADD VALUE`` (the 0011-0029
   pattern); the new values are NOT used by a row in this migration, so the PG16 in-txn rule holds.
2. **import_extract_status / import_kind / import_confidence_band** — fresh ``CREATE TYPE``s (usable
   same-txn → the table columns below can reference them). Tuples sourced from the ORM ``*_VALUES``
   so the hand-authored DDL and the SAEnum bindings never drift. ``import_kind`` carries an UNKNOWN
   value the vault ``DocumentKind`` lacks — kind is always human-confirmed (R10), so the scorer may
   decline. The existing ``pdca_phase`` type (0017) is REUSED (``create_type=False``).
3. **import_extract** — Stage-2 output, one row per extracted file (doc 09 §5). ``full_text`` inline
   (transient row → no blob-row-iff-bytes cost). ``UNIQUE (run_id, file_id)`` = the §3.1 idempotency
   key (+ the run_id-prefix index for batch resume). ``run_id``/``file_id`` ``ON DELETE CASCADE``
   (the transient-layer exception, doc 14 §1.2; the import_file CASCADE precedent).
4. **import_classification** — Stage-3 scored proposal, one row per file (doc 09 §6). ``clause_numbers``
   are clause CODES (TEXT[]); ``pdca_phase`` is the reused enum (derived, nullable). ``UNIQUE
   (run_id, file_id, classifier_version)`` = the §3.1 idempotency key.
5. **Explicit GRANTs** — SELECT/INSERT/UPDATE/DELETE on both new tables to ``easysynq_app`` (the
   extract/classify worker + the API run as the non-owner app role), pg_roles-guarded so a role-less
   CI DB doesn't error (the 0029 precedent). NO new permission keys / event_type / audit_object_type
   (stage transitions reuse the 0029 ``IMPORT_RUN_STAGE_CHANGED`` event + ``import_run`` object_type).

Migration notes: both new UNIQUE constraints are plain (composite b-tree, no expression/partial) → no
``env.py`` change, alembic check clean. The downgrade DROPs the two tables (DELETE first so a
POPULATED-DB downgrade does not depend on cascade ordering) + the 3 fresh enum TYPEs; the ADD VALUEs
are irreversible in PostgreSQL → no-op (0001 DROPs ``import_run_status``? no — 0029 owns it and DROPs
it on ITS downgrade; this migration leaves the type's extra values behind on downgrade, which a
re-upgrade tolerates via ADD VALUE IF NOT EXISTS). Round-trips up↔down↔check on PG16 incl. a
populated-DB downgrade (a run → file → extract → classification chain).

Revision ID: 0030_ingestion_extract_classify
Revises: 0029_ingestion_scan
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._ingestion_enums import (
    IMPORT_CONFIDENCE_BAND_VALUES,
    IMPORT_EXTRACT_STATUS_VALUES,
    IMPORT_KIND_VALUES,
)

revision: str = "0030_ingestion_extract_classify"
down_revision: str | None = "0029_ingestion_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_RUN_STATUS = ("Extracting", "Classifying", "Classified")


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["org_id"], ["organization.id"], name=f"fk_{table}_org_id_organization", ondelete="RESTRICT"
    )


def _run_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["run_id"], ["import_run.id"], name=f"fk_{table}_run_id_import_run", ondelete="CASCADE"
    )


def _file_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["file_id"], ["import_file.id"], name=f"fk_{table}_file_id_import_file", ondelete="CASCADE"
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

    # 2. The Stage-2/3 staging enums (CREATE TYPE → usable same-txn). pdca_phase is reused.
    postgresql.ENUM(*IMPORT_EXTRACT_STATUS_VALUES, name="import_extract_status").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*IMPORT_KIND_VALUES, name="import_kind").create(bind, checkfirst=True)
    postgresql.ENUM(*IMPORT_CONFIDENCE_BAND_VALUES, name="import_confidence_band").create(
        bind, checkfirst=True
    )
    import_extract_status = postgresql.ENUM(name="import_extract_status", create_type=False)
    import_kind = postgresql.ENUM(name="import_kind", create_type=False)
    import_confidence_band = postgresql.ENUM(name="import_confidence_band", create_type=False)
    pdca_phase = postgresql.ENUM(name="pdca_phase", create_type=False)

    # 3. import_extract — Stage-2 output. UNIQUE(run_id, file_id) = §3.1 key; CASCADE children.
    op.create_table(
        "import_extract",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=True),
        sa.Column(
            "text_truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("header_block", sa.Text(), nullable=True),
        sa.Column("embedded_props", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("structure_hints", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ocr_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", import_extract_status, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("import_extract"),
        _run_fk("import_extract"),
        _file_fk("import_extract"),
        sa.UniqueConstraint("run_id", "file_id", name="uq_import_extract_run_id_file_id"),
        sa.PrimaryKeyConstraint("id", name="pk_import_extract"),
    )

    # 4. import_classification — Stage-3 scored proposal. UNIQUE(run_id, file_id, classifier_version).
    op.create_table(
        "import_classification",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classifier_version", sa.Text(), nullable=False),
        sa.Column("kind", import_kind, nullable=False),
        sa.Column("kind_conf", sa.Integer(), nullable=False),
        sa.Column("type_code", sa.Text(), nullable=True),
        sa.Column("type_conf", sa.Integer(), nullable=False),
        sa.Column(
            "clause_numbers",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("clause_conf", sa.Integer(), nullable=False),
        sa.Column("process_names", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("process_conf", sa.Integer(), nullable=False),
        sa.Column("pdca_phase", pdca_phase, nullable=True),
        sa.Column("band", import_confidence_band, nullable=False),
        sa.Column("ambiguous", sa.Boolean(), nullable=False),
        sa.Column("top2_margin", sa.Integer(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("import_classification"),
        _run_fk("import_classification"),
        _file_fk("import_classification"),
        sa.UniqueConstraint(
            "run_id",
            "file_id",
            "classifier_version",
            name="uq_import_classification_run_file_version",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_classification"),
    )

    # 5. Explicit least-privilege grants for the non-owner app role; pg_roles-guarded (0029 pattern).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON import_extract TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON import_classification TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # DELETE first so a populated-DB downgrade does not depend on cascade ordering (the children
    # carry no inbound FK of their own). Then drop the tables + the 3 fresh enum TYPEs. The
    # import_run_status ADD VALUEs are irreversible in PostgreSQL → no-op (0029's downgrade DROPs the
    # whole import_run_status type; a re-upgrade re-adds the values via ADD VALUE IF NOT EXISTS).
    op.execute("DELETE FROM import_classification")
    op.execute("DELETE FROM import_extract")
    op.drop_table("import_classification")
    op.drop_table("import_extract")
    op.execute("DROP TYPE IF EXISTS import_confidence_band")
    op.execute("DROP TYPE IF EXISTS import_kind")
    op.execute("DROP TYPE IF EXISTS import_extract_status")
