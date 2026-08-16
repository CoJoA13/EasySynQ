"""Persist the active first-administrator credential receipt digest.

The receipt is volatile and its plaintext is never persisted. The nullable digest preserves existing
installations while later bootstrap flows bind acknowledgment to the active credential generation.

Revision ID: 0088_bootstrap_credential
Revises: 0087_first_admin_bootstrap
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088_bootstrap_credential"
down_revision: str | None = "0087_first_admin_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "system_config",
        sa.Column("bootstrap_credential_receipt_hash", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        "ck_system_config_bootstrap_credential_receipt_hash_hex",
        "system_config",
        "bootstrap_credential_receipt_hash IS NULL OR "
        "bootstrap_credential_receipt_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_system_config_bootstrap_credential_receipt_hash_hex",
        "system_config",
        type_="check",
    )
    op.drop_column("system_config", "bootstrap_credential_receipt_hash")
