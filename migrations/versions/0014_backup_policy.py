"""backup_policy + the BACKUP_CONFIGURED / RESTORE_TEST_PASSED / RESTORE_TEST_FAILED events —
the G-C backup/restore-drill gate (slice S8b2, doc 08 §8 / AC#5).

S8b2 lands the last blocking setup gate **G-C**: a backup→restore-into-scratch drill must PASS an
integrity triad (blob SHA-256 re-hash, per-table row-count parity, document_version→blob FK check)
before finalize is allowed — "configured but unverified" must NOT satisfy it (doc 18 §7
``[PROOF AC#5]``, test ``test_setup_finalize_requires_restore_pass``). This migration adds the
``backup_policy`` table that signal lives on (``last_restore_test_result``) + the three audit events
the config step and the drill emit.

Additive enum (the 0012/0013 precedent): ``ALTER TYPE event_type ADD VALUE`` is in-txn-safe on PG16
(no row USES the values here), irreversible → no-op enum downgrade (0001's downgrade DROP TYPEs
``event_type`` wholesale, so the up↔down round-trip still passes). The Python ``EventType`` carries
the three new members too (``_audit_enums.py``) so a from-scratch ``upgrade head`` — which rebuilds
the type from ``EVENT_TYPE_VALUES`` — matches a migrated DB.

``backup_policy`` is NOT seeded — a missing/null ``last_restore_test_result`` reads as
G-C-unsatisfied, and the ``/setup/configure-backup`` + ``/setup/run-restore-test`` steps populate it.
An already-OPERATIONAL install (upgraded) never re-finalizes, so G-C is never re-checked for it — no
brick risk, no back-fill needed. The owner-run ``ALTER DEFAULT PRIVILEGES`` from 0010 auto-grants the
non-owner ``easysynq_app`` role DML on this new table (same as 0013's storage_config — no explicit
grant needed).

Revision ID: 0014_backup_policy
Revises: 0013_storage_worm
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0014_backup_policy"
down_revision: str | None = "0013_storage_worm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BACKUP_CONFIGURED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'RESTORE_TEST_PASSED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'RESTORE_TEST_FAILED'")

    op.create_table(
        "backup_policy",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("encryption_key_ref", sa.Text(), nullable=True),
        sa.Column("cron", sa.Text(), nullable=False),
        sa.Column(
            "wal_pitr_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("retention_daily", sa.Integer(), server_default="7", nullable=False),
        sa.Column("retention_weekly", sa.Integer(), server_default="4", nullable=False),
        sa.Column("retention_monthly", sa.Integer(), server_default="6", nullable=False),
        sa.Column("alert_sink", sa.Text(), nullable=True),
        sa.Column("last_restore_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_restore_test_result", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_backup_policy_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_backup_policy"),
        sa.UniqueConstraint("org_id", name="uq_backup_policy_org_id"),
    )


def downgrade() -> None:
    # The ADD VALUEs on event_type are irreversible in PostgreSQL → no-op for the enum (0001's
    # downgrade DROP TYPEs event_type wholesale, so the round-trip still passes). Drop the table.
    op.drop_table("backup_policy")
