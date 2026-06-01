"""audit_event (M18) + audit_checkpoint/_sink (M19) + DB role separation (slice S6).

The append-only, hash-chained, monthly-partitioned ``audit_event`` trail and its off-host
checkpoint anchors, plus the **role separation** that makes the trail structurally immutable.

Why role separation (the AC#6a foundation): ``REVOKE UPDATE/DELETE`` only bites a NON-OWNER,
NON-superuser role. The migration runs as the OWNER (``database_url_sync``); it creates
``easysynq_app`` (the runtime role for api/worker/beat — INSERT/SELECT-only on ``audit_event`` +
``signature_event``) and ``easysynq_linker`` (the chain-linker — the ONLY role with the
column-scoped ``UPDATE(prev_hash,row_hash,chained_at)``). doc 18 §136/§150.

Hand-authored (autogenerate cannot model these — doc 18 §148): ``CREATE ROLE``, ``GRANT/REVOKE``,
``PARTITION BY RANGE``, ``GENERATED ALWAYS AS IDENTITY``, ``BRIN``, and the SECURITY-DEFINER
partition-creation function the Beat ``roll_partitions`` job calls (so the non-owner app can add
months without holding ``CREATE`` on the schema). The composite PK ``(id, occurred_at)`` is a PG
partitioning requirement; the single parent IDENTITY sequence keeps ``id`` globally monotonic so a
gap is still the tamper signal (C4/R7). Monthly child partitions are excluded from ``alembic check``
in ``migrations/env.py``.

Revision ID: 0010_audit
Revises: 0009_seed_workflow_sod
Create Date: 2026-06-01
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.config import get_settings
from easysynq_api.db.models._audit_enums import (
    ACTOR_TYPE_VALUES,
    AUDIT_OBJECT_TYPE_VALUES,
    CHECKPOINT_SINK_KIND_VALUES,
    EVENT_TYPE_VALUES,
)

revision: str = "0010_audit"
down_revision: str | None = "0009_seed_workflow_sod"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "easysynq_app"
LINKER_ROLE = "easysynq_linker"

# Role names are interpolated into DDL/PL-pgSQL (CREATE ROLE / GRANT cannot bind a role as a
# parameter). They are module constants, but the helpers below validate against this pattern so
# they stay injection-safe even if a future edit passes a dynamic name — a role name must be a
# plain lowercase SQL identifier.
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

_ENUMS: dict[str, tuple[str, ...]] = {
    "actor_type": ACTOR_TYPE_VALUES,
    "audit_object_type": AUDIT_OBJECT_TYPE_VALUES,
    "event_type": EVENT_TYPE_VALUES,
    "checkpoint_sink_kind": CHECKPOINT_SINK_KIND_VALUES,
}

# The three months of runway created at install (today = 2026-06-01); the daily roll_partitions
# Beat job keeps ≥2 months ahead thereafter. Half-open [from, to) ranges at UTC month starts (R8).
_INITIAL_PARTITION_STARTS = ("2026-06-01", "2026-07-01", "2026-08-01")


def _lit(value: str) -> str:
    """Quote a Python string as a SQL string literal (doubling embedded quotes)."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _create_role(role: str, password: str) -> None:
    # Idempotent: create the login role if absent, then always (re)set the password so a rotated
    # secret is honoured on the next migrate. Roles are cluster-global, so guard the create.
    if not _SAFE_IDENT.match(role):
        raise ValueError(f"unsafe role identifier: {role!r}")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
            CREATE ROLE {role} LOGIN;
          END IF;
        END
        $$;
        """
    )
    op.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD {_lit(password)};")


def _drop_role(role: str) -> None:
    if not _SAFE_IDENT.match(role):
        raise ValueError(f"unsafe role identifier: {role!r}")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}';
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role}';
            EXECUTE 'DROP OWNED BY {role}';
            DROP ROLE {role};
          END IF;
        END
        $$;
        """
    )


# The SECURITY DEFINER partition factory: owned by the migration's owner role, so the non-owner
# app/beat can create + lock down a month's partition WITHOUT holding CREATE on the schema. Bounds
# are pinned to UTC (explicit +00) so the partition seam never shifts with the session timezone.
_PARTITION_FN = f"""
CREATE OR REPLACE FUNCTION easysynq_create_audit_partition(p_start date)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_end  date := (p_start + INTERVAL '1 month')::date;
    v_name text := 'audit_event_' || to_char(p_start, 'YYYY_MM');
    v_from text := to_char(p_start, 'YYYY-MM-DD') || ' 00:00:00+00';
    v_to   text := to_char(v_end,  'YYYY-MM-DD') || ' 00:00:00+00';
BEGIN
    IF NOT EXISTS (SELECT FROM pg_class WHERE relname = v_name AND relkind = 'r') THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF audit_event FOR VALUES FROM (%L) TO (%L)',
            v_name, v_from, v_to
        );
        -- Mirror the parent's least-privilege grants on the child (belt-and-suspenders: parent-
        -- routed DML checks the parent, but never grant the app UPDATE/DELETE on a child either).
        EXECUTE format('REVOKE ALL ON %I FROM {APP_ROLE}', v_name);
        EXECUTE format('GRANT SELECT, INSERT ON %I TO {APP_ROLE}', v_name);
        EXECUTE format('GRANT SELECT ON %I TO {LINKER_ROLE}', v_name);
        EXECUTE format(
            'GRANT UPDATE (prev_hash, row_hash, chained_at) ON %I TO {LINKER_ROLE}', v_name
        );
    END IF;
END
$fn$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    settings = get_settings()

    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # --- roles + base grants on the PRE-S6 schema -------------------------------------------
    _create_role(APP_ROLE, settings.app_db_password)
    _create_role(LINKER_ROLE, settings.linker_db_password)

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {LINKER_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    # Future tables (S7+) are covered automatically — keeps the MVP→v1 path purely additive.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )

    # --- audit_event: partitioned parent, IDENTITY id, composite PK, BRIN + btree -----------
    op.execute(
        """
        CREATE TABLE audit_event (
            id bigint GENERATED ALWAYS AS IDENTITY,
            org_id uuid NOT NULL,
            occurred_at timestamptz NOT NULL,
            actor_id uuid,
            actor_type actor_type NOT NULL,
            on_behalf_of uuid,
            event_type event_type NOT NULL,
            object_type audit_object_type NOT NULL,
            object_id uuid,
            scope_ref text,
            reason text,
            before jsonb,
            after jsonb,
            request_id uuid,
            client_ip inet,
            user_agent text,
            auth_context jsonb,
            prev_hash bytea,
            row_hash bytea,
            chained_at timestamptz,
            signature_event_id uuid,
            CONSTRAINT pk_audit_event PRIMARY KEY (id, occurred_at),
            CONSTRAINT fk_audit_event_org_id_organization
                FOREIGN KEY (org_id) REFERENCES organization (id) ON DELETE RESTRICT,
            CONSTRAINT fk_audit_event_actor_id_app_user
                FOREIGN KEY (actor_id) REFERENCES app_user (id) ON DELETE RESTRICT,
            CONSTRAINT fk_audit_event_on_behalf_of_app_user
                FOREIGN KEY (on_behalf_of) REFERENCES app_user (id) ON DELETE RESTRICT,
            CONSTRAINT fk_audit_event_signature_event_id_signature_event
                FOREIGN KEY (signature_event_id) REFERENCES signature_event (id) ON DELETE RESTRICT
        ) PARTITION BY RANGE (occurred_at);
        """
    )
    op.execute("CREATE INDEX brin_audit_event_occurred_at ON audit_event USING brin (occurred_at)")
    op.execute("CREATE INDEX ix_audit_event_object_id ON audit_event (object_id)")
    op.execute("CREATE INDEX ix_audit_event_actor_id ON audit_event (actor_id)")
    op.execute("CREATE INDEX ix_audit_event_event_type ON audit_event (event_type)")

    # The partition factory + the three months of runway (created via the factory so they get the
    # identical least-privilege grants as any future month).
    op.execute(_PARTITION_FN)
    op.execute(f"GRANT EXECUTE ON FUNCTION easysynq_create_audit_partition(date) TO {APP_ROLE}")
    for start in _INITIAL_PARTITION_STARTS:
        op.execute(
            sa.text("SELECT easysynq_create_audit_partition(CAST(:start AS date))").bindparams(
                start=start
            )
        )

    # Parent grants: INSERT/SELECT-only for the app (the append-only guarantee); column-scoped
    # UPDATE for the linker (the ONLY post-insert mutation, R12). All app DML is parent-routed.
    op.execute(f"REVOKE ALL ON audit_event FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_event TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON audit_event TO {LINKER_ROLE}")
    op.execute(f"GRANT UPDATE (prev_hash, row_hash, chained_at) ON audit_event TO {LINKER_ROLE}")
    # The linker also reads organization (to iterate the per-org chains) + system_config (the
    # bounded-lag alarm threshold) — SELECT-only, no other privileges.
    op.execute(f"GRANT SELECT ON organization TO {LINKER_ROLE}")
    op.execute(f"GRANT SELECT ON system_config TO {LINKER_ROLE}")

    # --- audit_checkpoint (append-only) + audit_checkpoint_sink (mutable config) -------------
    op.create_table(
        "audit_checkpoint",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("latest_id", sa.BigInteger(), nullable=False),
        sa.Column("latest_row_hash", sa.LargeBinary(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("app_signature", sa.LargeBinary(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_audit_checkpoint_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_checkpoint"),
    )
    op.create_index(
        "ix_audit_checkpoint_org_id_latest_id",
        "audit_checkpoint",
        ["org_id", "latest_id"],
    )
    op.create_table(
        "audit_checkpoint_sink",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(name="checkpoint_sink_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("connection", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_anchored_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_audit_checkpoint_sink_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_checkpoint_sink"),
    )
    # audit_checkpoint is append-only (doc 14 §15.2): app writes anchors, never edits them.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_checkpoint FROM {APP_ROLE}")

    # --- signature_event: the S5-promised structural REVOKE (append-only at the DB layer) ----
    op.execute(f"REVOKE UPDATE, DELETE ON signature_event FROM {APP_ROLE}")

    # --- system_config: the chain-linker bounded-lag alarm threshold -------------------------
    op.add_column(
        "system_config",
        sa.Column(
            "audit_chain_lag_alarm_seconds",
            sa.Integer(),
            server_default="60",
            nullable=False,
        ),
    )


def downgrade() -> None:
    # WARNING (ops): this DESTROYS all audit_event data — DROP TABLE … CASCADE drops every monthly
    # partition, and a re-upgrade starts with empty partitions + a fresh IDENTITY sequence. The
    # audit trail cannot be recovered without a backup restore. Downgrade is for dev/CI round-trips.
    op.drop_column("system_config", "audit_chain_lag_alarm_seconds")
    op.drop_index("ix_audit_checkpoint_org_id_latest_id", table_name="audit_checkpoint")
    op.drop_table("audit_checkpoint_sink")
    op.drop_table("audit_checkpoint")
    op.execute("DROP FUNCTION IF EXISTS easysynq_create_audit_partition(date)")
    # CASCADE drops the child partitions + their inherited indexes.
    op.execute("DROP TABLE IF EXISTS audit_event CASCADE")

    _drop_role(LINKER_ROLE)
    _drop_role(APP_ROLE)

    for name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
