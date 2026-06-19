"""org_role_assignment + PROCESS_OWNER_ASSIGNED/_REVOKED events (owner-assignment binding)

S-owner-assignment-1 (doc 02 §3.4 / doc 14 §3). Creates the ERD-deferred ``org_role_assignment``
table — the RACI accountability carrier that binds a user to an ``org_role`` (optionally scoped to a
``process``; ``process_id`` nullable for a global org-role like Top Management). The owner-assignment
action records this row AND, separately, mints/extends the concrete PROCESS-scoped
``role_assignment.bound_scope`` (substituting the seeded ``:assignment_process`` placeholder) — but
that mint uses the EXISTING ``role``/``role_assignment`` tables, so there is no schema change there.

Adds two additive ``event_type`` values — PROCESS_OWNER_ASSIGNED / PROCESS_OWNER_REVOKED (the
binding's audit trail; not USED by a row here, so the ``autocommit_block`` satisfies PG16's in-txn
ADD-VALUE rule). **NO** new permission key (the action rides the seeded ``process.assign_owner`` —
catalog stays 102, R38 not engaged); **NO** new ``audit_object_type`` (the events key on the
existing ``process`` value). FK names are explicit (<63 chars) and match the Base naming convention
so ``alembic check`` does not phantom-DROP. The new ORM model is imported in
``db/models/__init__.py`` (the 0027 registration lesson).

Downgrade: drop ``org_role_assignment`` (no dependents). The ``event_type`` ADD VALUEs are
irreversible in PostgreSQL → no-op (0001's downgrade DROPs the type wholesale; a re-upgrade rebuilds
it from the ORM ``EVENT_TYPE_VALUES``). Round-trips up↔down↔check on PG16.

Revision ID: 0056_org_role_assignment
Revises: 0055_objective_grading_freeze
Create Date: 2026-06-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056_org_role_assignment"
down_revision: str | None = "0055_objective_grading_freeze"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("PROCESS_OWNER_ASSIGNED", "PROCESS_OWNER_REVOKED")


def upgrade() -> None:
    # 1. Additive event_type values (IF NOT EXISTS → idempotent; not USED in this txn — the
    # autocommit_block satisfies the PG16 in-txn ADD-VALUE rule, the 0052 shape).
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 2. org_role_assignment — the RACI accountability binding. process_id is nullable (a global
    # org-role has no process; a process owner carries the concrete id); UNIQUE makes the bind
    # idempotent (PG treats a NULL process_id as distinct — fine for the org-wide RACI case).
    op.create_table(
        "org_role_assignment",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_org_role_assignment"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_org_role_assignment_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_role_id"],
            ["org_role.id"],
            name="fk_org_role_assignment_org_role_id_org_role",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_org_role_assignment_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["process.id"],
            name="fk_org_role_assignment_process_id_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_org_role_assignment_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "org_role_id",
            "user_id",
            "process_id",
            name="uq_org_role_assignment_org_role_id_user_id_process_id",
        ),
    )

    # 3. Least-privilege grant (belt-and-suspenders over 0010's ALTER DEFAULT PRIVILEGES; guarded so
    # a role-less CI DB doesn't error — the 0024/0052 child-table precedent).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON org_role_assignment TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("org_role_assignment")
    # The event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's downgrade DROPs the
    # type wholesale; a re-upgrade rebuilds it from the ORM EVENT_TYPE_VALUES).
