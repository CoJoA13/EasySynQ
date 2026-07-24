"""Batch 5 (2026-07-22 review): pending_blob_purge — the reaper marker for post-commit S3 erasure.

Retention disposition now COMMITs the DISPOSED tombstone + the blob-row delete FIRST, then purges the
S3 bytes as a separate idempotent step — so a crash can never leave bytes-deleted-with-DB-rolled-back
(the S-rec-2 blob-row-iff-bytes invariant, in the safe direction: a backup never sees a blob row whose
bytes are gone). This table is the durable to-be-purged marker a crash leaves behind, which
``reap_pending_blob_purges`` completes. NOT append-only — the app role INSERTs, SELECTs, DELETEs, and
UPDATEs it (the reaper's ``SELECT … FOR UPDATE SKIP LOCKED`` claim needs UPDATE); the grant lists all
four explicitly so it never depends on 0010's ``ALTER DEFAULT PRIVILEGES`` for the row lock, and so a
future maintainer can't mistake it for an append-only table and REVOKE UPDATE (which would silently
break the reaper). pg_roles-guarded (the 0048 house style).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0073_pending_blob_purge"
down_revision: str | None = "0072_disposition_append_only"
branch_labels: str | None = None
depends_on: str | None = None

_APP_ROLE = "easysynq_app"


def upgrade() -> None:
    op.create_table(
        "pending_blob_purge",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        # sha256 is log/dedup only — NOT an FK (the blob row is deleted in the same commit).
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        # No server_default — the ORM's Python-side default supplies it; keeps alembic check clean.
        sa.Column("bypass_governance", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_pending_blob_purge_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pending_blob_purge"),
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                -- UPDATE is required by the reaper's SELECT … FOR UPDATE SKIP LOCKED row lock.
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON pending_blob_purge TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("pending_blob_purge")
