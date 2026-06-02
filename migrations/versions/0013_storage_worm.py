"""storage_config + the WORM_VERIFIED event — the G-B WORM-verify gate (slice S8b, doc 08 §7).

S8b lands setup gate **G-B**: a setup step probes the object-locked vault bucket (write → confirm
retain-until → attempt an early delete → expect DENIAL), records ``worm_verified_at``, and finalize
re-checks it live. This migration adds the minimal storage_config table that signal lives on + the
audit event the step emits. The backup/restore drill (G-C / AC#5) and the rest of doc 14's
storage_config columns are **S8b2**.

Additive enum (the 0012 precedent): ``ALTER TYPE event_type ADD VALUE`` is allowed in-txn on PG16
(no row USES the value here), irreversible → no-op enum downgrade. The Python ``EventType`` carries
``WORM_VERIFIED`` too (``_audit_enums.py``) so a from-scratch ``upgrade head`` (which rebuilds the
type from ``EVENT_TYPE_VALUES``) matches a migrated DB.

``storage_config`` is NOT seeded — a missing/null ``worm_verified_at`` reads as G-B-unsatisfied, and
the ``/setup/verify-storage`` step upserts the row. An already-OPERATIONAL install (upgraded) never
re-finalizes, so G-B is never re-checked for it — no brick risk, no back-fill needed.

Revision ID: 0013_storage_worm
Revises: 0012_setup_spine
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0013_storage_worm"
down_revision: str | None = "0012_setup_spine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'WORM_VERIFIED'")

    op.create_table(
        "storage_config",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("worm_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "object_lock_mode", sa.Text(), server_default="GOVERNANCE", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_storage_config_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_storage_config"),
        sa.UniqueConstraint("org_id", name="uq_storage_config_org_id"),
    )


def downgrade() -> None:
    # The ADD VALUE on event_type is irreversible in PostgreSQL → no-op for the enum (0001's
    # downgrade DROP TYPEs event_type wholesale, so the round-trip still passes). Drop the table.
    op.drop_table("storage_config")
