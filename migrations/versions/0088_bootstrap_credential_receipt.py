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
    # Bare token: Alembic applies the metadata ck naming convention; the full name previously
    # doubled AND hash-truncated on live databases (63-char identifier cap — the 0019/0079
    # lesson; repaired by 0089). The ORM mirrors this constraint in system_config.py.
    op.create_check_constraint(
        "bootstrap_credential_receipt_hash_hex",
        "system_config",
        "bootstrap_credential_receipt_hash IS NULL OR "
        "bootstrap_credential_receipt_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    # Spelling-tolerant (see 0078's downgrade note). The legacy spelling here is additionally
    # hash-truncated (63-char cap, opaque suffix), so it is matched by prefix, never by literal.
    op.execute(
        """
        DO $$
        DECLARE c record;
        BEGIN
            FOR c IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'system_config'::regclass AND contype = 'c'
                  AND (conname = 'ck_system_config_bootstrap_credential_receipt_hash_hex'
                       OR starts_with(conname, 'ck_system_config_ck_system_config_'))
            LOOP
                EXECUTE format('ALTER TABLE system_config DROP CONSTRAINT %I', c.conname);
            END LOOP;
        END $$;
        """
    )
    op.drop_column("system_config", "bootstrap_credential_receipt_hash")
