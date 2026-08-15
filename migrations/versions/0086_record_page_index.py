"""Add the tenant-first index for deterministic records candidate paging.

Revision ID: 0086_record_page_index
Revises: 0085_user_credential_issued
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0086_record_page_index"
down_revision: str | None = "0085_user_credential_issued"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_record_org_id_captured_at_id_desc "
        "ON record (org_id, captured_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.drop_index("ix_record_org_id_captured_at_id_desc", table_name="record")
