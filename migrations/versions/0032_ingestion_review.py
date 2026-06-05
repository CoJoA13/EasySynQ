"""ingestion review: import_decision + Reviewing status + IMPORT_DECISION_RECORDED (S-ing-4)

Adds the v1 Ingestion engine's human-in-the-loop review stage (doc 09 §9/§11.3/§12-13, doc 14 §13) on
top of the S-ing-3 dedup/propose foundation (0031). It introduces ONLY transient ``import_*`` staging
(doc 14 §1.2); no vault table is touched (commit is S-ing-5).

1. **import_run_status ADD VALUE** — ``Reviewing`` (the lock-free, human-paced rest-state a reviewer
   enters on the first decision; it stays OUT of the in-progress/active sets so the lock-liveness
   reaper never FAILs a run mid-review). Additive ``ALTER TYPE … ADD VALUE`` (the 0011-0031 pattern);
   the value is NOT used by a row in THIS migration, so the PG16 in-txn rule holds.
2. **event_type ADD VALUE** — ``IMPORT_DECISION_RECORDED`` (each Mara accept/correct/merge/split/
   exclude/defer, USER actor, before→after; reuses object_type=import_run — no new audit_object_type).
   Additive (the 0029 event pattern).
3. **import_decision_action** — a fresh ``CREATE TYPE`` (accept/correct/merge/split/exclude/defer;
   usable same-txn). Tuple sourced from the ORM ``IMPORT_DECISION_ACTION_VALUES`` so the hand-authored
   DDL and the SAEnum binding never drift.
4. **import_decision** — the append-only human-in-the-loop log (doc 14 §13). Target is file XOR cluster
   (the ``ck_import_decision_exactly_one_target`` CHECK); ``cluster_id`` is polymorphic with NO FK (a
   dupe_cluster OR version_family id; the ``signature_event`` precedent). ``run_id``/``file_id`` FKs
   CASCADE (transient layer); ``org_id``/``decided_by`` RESTRICT (audit-like). Two plain b-tree indexes
   + a **partial UNIQUE** ``uq_import_decision_run_idem ... WHERE idempotency_key IS NOT NULL`` created
   as RAW DDL (a declarative partial index round-trips wrong — the 0024 ``ix_worm_destroy_request_open``
   lesson; excluded from ``alembic check`` via ``env.py._MIGRATION_MANAGED_INDEXES``).
5. **Explicit GRANTs** — **SELECT, INSERT only** on import_decision to ``easysynq_app`` (append-only —
   the human-intent log, no UPDATE/DELETE; CASCADE handles purge), pg_roles-guarded (the 0029-0031
   precedent). A deliberate tightening vs the full-DML sibling import_* tables.

Migration notes: the CHECK + the two plain indexes are modelled on the ORM and round-trip clean; the
partial-UNIQUE is raw DDL + env.py-excluded. The downgrade DELETEs import_decision first so a
POPULATED-DB downgrade does not depend on cascade ordering (it carries no inbound FK of its own), then
drops the table + the fresh ``import_decision_action`` TYPE; the import_run_status / event_type ADD
VALUEs are irreversible in PostgreSQL → no-op (0029 owns/DROPs those types on ITS downgrade; a
re-upgrade re-adds via ADD VALUE IF NOT EXISTS). Round-trips up↔down↔check on PG16 incl. a populated-DB
downgrade (a run → file → decision + a merged family with reconstruct=true).

Revision ID: 0032_ingestion_review
Revises: 0031_ingestion_dedup_propose
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._ingestion_enums import IMPORT_DECISION_ACTION_VALUES

revision: str = "0032_ingestion_review"
down_revision: str | None = "0031_ingestion_dedup_propose"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_IDEM_INDEX = "uq_import_decision_run_idem"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. + 2. Extend the run-status + event-type enums (additive; not used by a row here → in-txn safe).
    op.execute("ALTER TYPE import_run_status ADD VALUE IF NOT EXISTS 'Reviewing'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'IMPORT_DECISION_RECORDED'")

    # 3. The decision-action enum (CREATE TYPE → usable same-txn). Tuple from the ORM *_VALUES.
    postgresql.ENUM(*IMPORT_DECISION_ACTION_VALUES, name="import_decision_action").create(
        bind, checkfirst=True
    )
    import_decision_action = postgresql.ENUM(name="import_decision_action", create_type=False)

    # 4. import_decision — the append-only human-in-the-loop log. Target is file XOR cluster.
    op.create_table(
        "import_decision",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cluster_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_kind", sa.Text(), nullable=True),
        sa.Column("action", import_decision_action, nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # Bare token — op.create_table applies the db/base.py naming convention
        # (``ck_%(table_name)s_%(constraint_name)s`` → ``ck_import_decision_exactly_one_target``),
        # matching the ORM model (the 0024 ``approver_neq_requester`` precedent).
        sa.CheckConstraint(
            "(file_id IS NULL) <> (cluster_id IS NULL)",
            name="exactly_one_target",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_import_decision_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["import_run.id"],
            name="fk_import_decision_run_id_import_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["import_file.id"],
            name="fk_import_decision_file_id_import_file",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["app_user.id"],
            name="fk_import_decision_decided_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_decision"),
    )
    op.create_index(
        "ix_import_decision_run_file_decided",
        "import_decision",
        ["run_id", "file_id", "decided_at"],
    )
    op.create_index(
        "ix_import_decision_run_decided", "import_decision", ["run_id", "decided_at"]
    )
    # Partial UNIQUE idempotency index — raw DDL (a declarative partial index round-trips wrong;
    # excluded from alembic check via env.py._MIGRATION_MANAGED_INDEXES, the 0024 precedent).
    op.execute(
        f"CREATE UNIQUE INDEX {_IDEM_INDEX} ON import_decision (run_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )

    # 5. Least-privilege grant for the non-owner app role — append-only (SELECT, INSERT only);
    #    pg_roles-guarded (the 0029-0031 pattern).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT ON import_decision TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # DELETE first so a populated-DB downgrade does not depend on cascade ordering (import_decision
    # carries no inbound FK of its own). Then drop the index, the table, and the fresh
    # import_decision_action TYPE. The import_run_status / event_type ADD VALUEs are irreversible in
    # PostgreSQL → no-op (0029's downgrade DROPs those types wholesale; a re-upgrade re-adds the values
    # via ADD VALUE IF NOT EXISTS).
    op.execute("DELETE FROM import_decision")
    op.execute(f"DROP INDEX IF EXISTS {_IDEM_INDEX}")
    op.drop_table("import_decision")
    op.execute("DROP TYPE IF EXISTS import_decision_action")
