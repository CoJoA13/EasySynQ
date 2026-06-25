"""S-notify-6: working_calendar table + per-org default seed (business-day escalation SLAs, R29).

Creates the working_calendar entity (doc 14 §line 131 + an additive ``timezone`` column per the
S-notify-6 design D-2). The timer_sweep resolves the org's is_default calendar to compute
business-day reminder/escalation thresholds (skip weekends + holidays).

* NEW TABLE working_calendar — operational config. The 0010 ALTER DEFAULT PRIVILEGES auto-grants
  full DML to easysynq_app; REVOKE DELETE only (keep INSERT/SELECT/UPDATE for the deferred editor) —
  the notification-ledger posture, NOT sla_policy's SELECT-only.
* Partial unique index uq_working_calendar_one_default (org_id) WHERE is_default — at most one
  default per org. Migration-managed (env.py _MIGRATION_MANAGED_INDEXES; absent from the ORM).
* Seed: one is_default Mon-Fri calendar per org, timezone = that org's organization.timezone.
  (NOTE: a fresh CI/throwaway DB has zero organization rows, so the seed loop is a no-op there —
  the seed insert path is exercised only on the populated dev DB during live-smoke.)

Revision ID: 0067_working_calendar
Revises: 0066_awareness_events
Create Date: 2026-06-25
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_working_calendar"
down_revision: str | None = "0066_awareness_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "working_calendar",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("working_days", postgresql.JSONB(), nullable=False),
        sa.Column("holidays", postgresql.JSONB(), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_working_calendar"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_working_calendar_org_id_organization",
            ondelete="RESTRICT",
        ),
    )

    # At most one default calendar per org (migration-managed partial unique index; env.py-excluded).
    op.create_index(
        "uq_working_calendar_one_default",
        "working_calendar",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    # working_calendar is operational config → keep INSERT/SELECT/UPDATE, REVOKE DELETE.
    # 0010's ALTER DEFAULT PRIVILEGES already GRANTed full DML to easysynq_app, so an explicit
    # GRANT is a no-op; only the REVOKE DELETE enforces the intended posture.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'REVOKE DELETE ON working_calendar FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # Seed one is_default Mon-Fri calendar per org (resilient multi-org loop; D1 single-org makes
    # this v1-moot but a from-scratch replay must be multi-org-safe). Explicit JSONB CAST + json.dumps
    # so the array serialization is correct without depending on op.bulk_insert's JSONB adaptation.
    org_rows = bind.execute(sa.text("SELECT id, timezone FROM organization")).all()
    for row in org_rows:
        bind.execute(
            sa.text(
                "INSERT INTO working_calendar"
                " (id, org_id, name, working_days, holidays, timezone, is_default)"
                " VALUES (:id, :org_id, :name, CAST(:working_days AS JSONB),"
                "         CAST(:holidays AS JSONB), :timezone, TRUE)"
            ),
            {
                "id": uuid.uuid4(),
                "org_id": row.id,
                "name": "Default",
                "working_days": json.dumps([1, 2, 3, 4, 5]),
                "holidays": json.dumps([]),
                "timezone": row.timezone or "UTC",
            },
        )


def downgrade() -> None:
    # No inbound FK references working_calendar.id → drop_table is clean on a populated DB (no
    # NOT-EXISTS seed-delete guard needed). Drop the migration-managed index first for parity with
    # 0066 (the cascade makes it optional).
    op.drop_index("uq_working_calendar_one_default", table_name="working_calendar")
    op.drop_table("working_calendar")
