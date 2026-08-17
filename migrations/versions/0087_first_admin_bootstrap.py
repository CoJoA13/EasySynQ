"""Persist the staged first-administrator bootstrap claim.

ADR 0005 requires a durable, retry-safe claim because Keycloak identity provisioning and the
EasySynQ database cannot commit atomically. The fields are nullable so existing installations keep
their setup and identity state unchanged. The linked user is protected by a named RESTRICT FK, and
the lookup path has a named index.

``BOOTSTRAP_IDENTITY_CLAIMED`` is additive to PostgreSQL's ``event_type`` enum. PostgreSQL enum
value removal is unsafe, so downgrade removes only the normal relational storage; a re-upgrade
uses ``IF NOT EXISTS`` to restore the enum value safely.

Revision ID: 0087_first_admin_bootstrap
Revises: 0086_record_page_index
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0087_first_admin_bootstrap"
down_revision: str | None = "0086_record_page_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG16 permits this additive enum change in the migration transaction because no row uses the
    # value here. It intentionally remains on downgrade; the base migration drops the whole type.
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BOOTSTRAP_IDENTITY_CLAIMED'")

    op.add_column(
        "system_config",
        sa.Column("bootstrap_admin_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("system_config", sa.Column("bootstrap_admin_username", sa.Text(), nullable=True))
    op.add_column(
        "system_config",
        sa.Column("bootstrap_admin_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "system_config",
        sa.Column("bootstrap_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "system_config",
        sa.Column("bootstrap_credential_issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_system_config_bootstrap_admin_user_id_app_user",
        "system_config",
        "app_user",
        ["bootstrap_admin_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_system_config_bootstrap_admin_user_id",
        "system_config",
        ["bootstrap_admin_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_system_config_bootstrap_admin_user_id", table_name="system_config")
    op.drop_constraint(
        "fk_system_config_bootstrap_admin_user_id_app_user",
        "system_config",
        type_="foreignkey",
    )
    op.drop_column("system_config", "bootstrap_credential_issued_at")
    op.drop_column("system_config", "bootstrap_claimed_at")
    op.drop_column("system_config", "bootstrap_admin_user_id")
    op.drop_column("system_config", "bootstrap_admin_username")
    op.drop_column("system_config", "bootstrap_admin_claim_id")
    # PostgreSQL enum values are intentionally irreversible; 0001 drops event_type on full teardown.
