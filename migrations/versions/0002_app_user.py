"""app_user: identity rows mapped from Keycloak subjects (slice S1)

Adds the user_status enum and the app_user table, plus a single-org default
organization row (v1 is single-org; first-run setup configures it later, S8).
Users are JIT-provisioned from a validated Keycloak access token (sub -> keycloak_subject).

Revision ID: 0002_app_user
Revises: 0001_baseline
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_app_user"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    user_status = postgresql.ENUM(
        "INVITED", "ACTIVE", "LOCKED", "DISABLED", "RETIRED", name="user_status"
    )
    user_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "app_user",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("keycloak_subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="user_status", create_type=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column(
            "mfa_enrolled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "is_guest", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("manager_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_invalidated_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_app_user_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["app_user.id"],
            name="fk_app_user_manager_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
        sa.UniqueConstraint("keycloak_subject", name="uq_app_user_keycloak_subject"),
    )

    # Single-org bootstrap (v1 is single-tenant; setup S8 configures the real name).
    op.execute(
        """
        INSERT INTO organization (legal_name, short_code)
        VALUES ('EasySynQ (configure in setup)', 'DEFAULT')
        ON CONFLICT (short_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM organization WHERE short_code = 'DEFAULT'")
    op.drop_table("app_user")
    op.execute("DROP TYPE IF EXISTS user_status")
