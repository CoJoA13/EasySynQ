"""records: disposition_event + worm_destroy_request + RECORD_* disposition events (S-rec-2)

Turns the inert disposition scaffolding (the ``record.disposition_state``/``legal_hold`` columns + the
``ix_record_retention_basis_date_disposition_state`` sweep index + the ``retention_policy``
``disposition_action``/``review_required``/``worm_lock_period`` fields, all shipped dead in 0023) into a
working retention end-of-life subsystem (doc 06 §5; doc 14 §10; R5/R27):

1. **disposition_event** — the immutable tombstone of each executed disposition act (doc 14 §10):
   the doc-mandated columns (``record_id``/``action``/``approved_by``/``executed_at``/``policy_id``/
   ``tombstone``) + the R27 dual-control fields (``is_worm_destroy``/``requested_by``/``legal_basis``).
   ``policy_id`` NULLABLE (non-policy legal-order destroy); ``approved_by`` NULLABLE (system sweep).
2. **worm_destroy_request** — the dual-control two-step workflow for the R27 destroy-under-legal-order
   hatch (the ``dcr`` mutable-state precedent, R22; state derived from nullable timestamps, no status
   enum). A ``CHECK(approved_by <> requested_by)`` backstops the in-service dual-control; a partial
   UNIQUE on ``record_id WHERE open`` permits one open request per record.
3. **RECORD_* event_type** — the 9 disposition/legal-hold/WORM-destroy/erasure-refusal events, additive
   ``ALTER TYPE event_type ADD VALUE`` (the 0011-0023 pattern; ``AuditObjectType.record`` already exists
   → no audit_object_type ALTER).
4. **Explicit GRANTs** — SELECT/INSERT/UPDATE/DELETE on the two new tables to ``easysynq_app`` (the Beat
   sweep + the disposition service run on the non-owner app role). 0010's ALTER DEFAULT PRIVILEGES
   already covers owner-created tables; these are belt-and-suspenders (the 0010 child-table precedent).

Migration notes: the partial UNIQUE index is **raw DDL** (a declarative partial index round-trips
wrong under ``alembic check`` — PG normalises the predicate; the 0020 FTS-GIN lesson) and is excluded
from autogenerate in ``env.py._MIGRATION_MANAGED_INDEXES``. No new native enum types (``DispositionAction``
is reused from 0023). FK names follow ``fk_{table}_{col}_{target}`` (all verified ≤63 chars). The
event_type ADD VALUEs are never used by a row in this migration (PG16 in-txn rule satisfied).

Revision ID: 0024_records_disposition
Revises: 0023_records_capture
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_records_disposition"
down_revision: str | None = "0023_records_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"

_OPEN_REQUEST_INDEX = "ix_worm_destroy_request_open"

_NEW_EVENT_TYPES = (
    "RECORD_DISPOSITION_DUE",
    "RECORD_DISPOSED",
    "RECORD_RETENTION_EXTENDED",
    "RECORD_LEGAL_HOLD_PLACED",
    "RECORD_LEGAL_HOLD_RELEASED",
    "RECORD_WORM_DESTROY_REQUESTED",
    "RECORD_WORM_DESTROY_CANCELLED",
    "RECORD_WORM_DESTROYED",
    "RECORD_ERASURE_REFUSED",
)


def _org_fk(table: str, column: str = "org_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["organization.id"], name=f"fk_{table}_{column}_organization", ondelete="RESTRICT"
    )


def _record_fk(table: str, column: str = "record_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["record.id"], name=f"fk_{table}_{column}_record", ondelete="RESTRICT"
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
    disposition_action = postgresql.ENUM(name="disposition_action", create_type=False)

    # 1. disposition_event — the immutable executed-disposition tombstone (doc 14 §10).
    op.create_table(
        "disposition_event",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", disposition_action, nullable=False),
        sa.Column("tombstone", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_worm_destroy", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=True),
        sa.Column(
            "executed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("disposition_event"),
        _record_fk("disposition_event"),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["retention_policy.id"],
            name="fk_disposition_event_policy_id_retention_policy",
            ondelete="RESTRICT",
        ),
        _user_fk("disposition_event", "approved_by"),
        _user_fk("disposition_event", "requested_by"),
        sa.PrimaryKeyConstraint("id", name="pk_disposition_event"),
    )
    op.create_index("ix_disposition_event_record_id", "disposition_event", ["record_id"])

    # 2. worm_destroy_request — the R27 dual-control two-step workflow (mutable; state from
    #    nullable timestamps). The CHECK backstops the in-service approver≠requester 409.
    op.create_table(
        "worm_destroy_request",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_basis", sa.Text(), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("worm_destroy_request"),
        _record_fk("worm_destroy_request"),
        _user_fk("worm_destroy_request", "requested_by"),
        _user_fk("worm_destroy_request", "approved_by"),
        _user_fk("worm_destroy_request", "cancelled_by"),
        sa.PrimaryKeyConstraint("id", name="pk_worm_destroy_request"),
        # Bare token — the metadata naming convention (db/base.py) wraps it to
        # ``ck_worm_destroy_request_approver_neq_requester`` (matches the ORM model).
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by",
            name="approver_neq_requester",
        ),
    )
    op.create_index("ix_worm_destroy_request_record_id", "worm_destroy_request", ["record_id"])
    # The "one open request per record" partial UNIQUE — raw DDL (declarative partial indexes drift
    # on alembic check; the 0020 lesson) + excluded in env.py._MIGRATION_MANAGED_INDEXES.
    op.execute(
        f"CREATE UNIQUE INDEX {_OPEN_REQUEST_INDEX} ON worm_destroy_request (record_id) "
        "WHERE executed_at IS NULL AND cancelled_at IS NULL"
    )

    # 3. RECORD_* disposition event_type values (additive; never used by a row in this migration).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 4. Explicit least-privilege grants for the non-owner app role (the sweep + disposition service
    #    run on database_url). Belt-and-suspenders over 0010's ALTER DEFAULT PRIVILEGES — guarded so
    #    a from-scratch CI DB without the role separation (0010 makes the role) doesn't error.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON disposition_event TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON worm_destroy_request TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Both new tables hold RESTRICT FKs to ``record`` — on a populated install an unguarded drop would
    # abort, so clear the rows first (they are this migration's own; the records they reference are
    # NOT deleted). The seeded System-Default policy stays one-way once records pin it (the 0023
    # guard is unchanged here).
    op.execute("DELETE FROM disposition_event")
    op.execute("DELETE FROM worm_destroy_request")
    op.execute(f"DROP INDEX IF EXISTS {_OPEN_REQUEST_INDEX}")
    op.drop_index("ix_worm_destroy_request_record_id", table_name="worm_destroy_request")
    op.drop_table("worm_destroy_request")
    op.drop_index("ix_disposition_event_record_id", table_name="disposition_event")
    op.drop_table("disposition_event")
    # The event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's downgrade DROPs
    # event_type wholesale, so the up↔down round-trip still passes; a re-upgrade rebuilds the type
    # from EVENT_TYPE_VALUES).
