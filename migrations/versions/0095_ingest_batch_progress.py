"""Record completed ingestion batches without fabricating progress for existing runs.

Downgrade drops only the operational watermark; legacy stage rows remain progress evidence.
"""

import sqlalchemy as sa
from alembic import op

revision = "0095_ingest_batch_progress"
down_revision = "0094_backup_schedule_attempt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_run",
        sa.Column("last_batch_progress_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_run", "last_batch_progress_at")
