"""Persist scheduled backup attempts independently of the Beat process lifetime.

Existing policies begin at created_at. The nullable watermark records both successful and handled
failed attempts; manual backups and skipped/busy claims do not consume it. Downgrade retains policy
configuration and archives but necessarily drops this scheduling history.
"""

import sqlalchemy as sa
from alembic import op

revision = "0094_backup_schedule_attempt"
down_revision = "0093_blob_object_version_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backup_policy",
        sa.Column("last_scheduled_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backup_policy", "last_scheduled_attempt_at")
