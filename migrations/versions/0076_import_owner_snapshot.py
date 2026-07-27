"""Persist reviewed import owners outside the operator decision history.

The ingestion commit worker needs a stable ``app_user.id`` after review closes: an owner can be
disabled or retired between the API transition and the detached worker, and a partial run may need
to resume much later. ``commit_owner_snapshot`` is transient run-level materialization state,
mapping ``import_file.id`` strings to validated ``app_user.id`` strings.

The nullable JSONB column keeps existing and already-partial runs migration-safe. Commit preflight
backfills the mapping lazily for remaining items when such a run resumes; no data rewrite is needed.
The existing ``import_run`` app-role DML grant covers the new column, so there is no permission-key
or database-role grant change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0076_import_owner_snapshot"
down_revision: str | None = "0075_audit_scope_ref_index"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "import_run",
        sa.Column("commit_owner_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_run", "commit_owner_snapshot")
