"""baseline: extensions, setup_state enum, organization, system_config

Slice S0 baseline. Establishes the reversible-migration machinery and the
minimum schema the walking skeleton needs. The full enum inventory (doc 18 §4.2)
and the vault/authz/audit tables land in their owning slices (S2–S6).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    setup_state = postgresql.ENUM(
        "UNINITIALIZED", "IN_SETUP", "OPERATIONAL", name="setup_state"
    )
    setup_state.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "organization",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("short_code", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization"),
        sa.UniqueConstraint("short_code", name="uq_organization_short_code"),
    )

    op.create_table(
        "system_config",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "setup_state",
            postgresql.ENUM(name="setup_state", create_type=False),
            server_default="UNINITIALIZED",
            nullable=False,
        ),
        sa.Column(
            "canonical_serialize_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_system_config_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("org_id", name="pk_system_config"),
    )


def downgrade() -> None:
    op.drop_table("system_config")
    op.drop_table("organization")
    op.execute("DROP TYPE IF EXISTS setup_state")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
    op.execute("DROP EXTENSION IF EXISTS ltree")
