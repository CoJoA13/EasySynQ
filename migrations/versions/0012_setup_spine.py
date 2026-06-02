"""setup spine — bootstrap-secret columns, org timezone, the system_config row + setup events (S8a).

S8a turns the reserved ``setup_state`` latch into a real first-run flow (doc 08): an operator-minted
single-use bootstrap secret gates the public ``/setup/bootstrap`` → first-admin grant, then the
wizard sets the org profile and finalizes to ``OPERATIONAL``. This migration adds the storage that
flow needs and the audit ``event_type`` values it emits.

Three things to know:

* **Additive enum (the 0011 precedent).** ``ALTER TYPE event_type ADD VALUE`` for the four setup
  events. PG16 allows it in-transaction because no row *uses* the value in this migration; it is
  irreversible, so ``downgrade`` is a no-op for the enum (safe — ``0001``'s downgrade drops the type
  wholesale). The Python ``EventType`` carries the same members (``_audit_enums.py``).
* **The ``system_config`` row never existed.** No migration seeded it; the table is empty on every
  install to date. We seed exactly one row (for the singleton org) so the latch always has a state
  to read.
* **Don't brick a running install.** A brand-new deploy must start ``UNINITIALIZED`` (latch on), but
  an *already-running* instance (upgraded into S8a) must NOT suddenly lock behind the wizard. We seed
  ``OPERATIONAL`` iff a ``role_assignment`` already exists (the box has been bootstrapped/used), else
  ``UNINITIALIZED``. ``role_assignment`` is only ever written by the grant-role CLI or the wizard, so
  its presence is a sound "this install is past setup" signal.

Revision ID: 0012_setup_spine
Revises: 0011_export_print_events
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_setup_spine"
down_revision: str | None = "0011_export_print_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_EVENT_TYPES: tuple[str, ...] = (
    "BOOTSTRAP_CONSUMED",
    "ADMIN_BOOTSTRAPPED",
    "ORG_PROFILE_SET",
    "SETUP_FINALIZED",
)


def upgrade() -> None:
    for value in _NEW_EVENT_TYPES:
        # IF NOT EXISTS → idempotent; not USED in this txn (PG16 in-txn ADD VALUE rule).
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    op.add_column(
        "organization",
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
    )
    op.add_column(
        "system_config", sa.Column("bootstrap_secret_hash", sa.Text(), nullable=True)
    )
    op.add_column(
        "system_config",
        sa.Column("bootstrap_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "system_config",
        sa.Column("bootstrap_consumed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Seed the singleton system_config row. canonical_serialize_version has no server default
    # (it is an ORM-side default), so set it explicitly here; the rest carry server defaults.
    # OPERATIONAL iff the install is already in use (any role_assignment), else UNINITIALIZED.
    op.execute(
        """
        INSERT INTO system_config (org_id, setup_state, canonical_serialize_version, finalized_at)
        SELECT o.id,
               CASE WHEN EXISTS (SELECT 1 FROM role_assignment)
                    THEN 'OPERATIONAL'::setup_state
                    ELSE 'UNINITIALIZED'::setup_state END,
               1,
               CASE WHEN EXISTS (SELECT 1 FROM role_assignment) THEN now() ELSE NULL END
        FROM organization o
        ON CONFLICT (org_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # The ADD VALUE on event_type is irreversible in PostgreSQL → deliberate no-op for the enum
    # (0001's downgrade DROP TYPEs event_type wholesale, so the round-trip still passes).
    # Delete the row we seeded: system_config was empty before 0012, and the row's FK to organization
    # (ON DELETE RESTRICT) would otherwise block 0002's later `DELETE FROM organization` on a full
    # downgrade. Then reverse the columns.
    op.execute("DELETE FROM system_config")
    op.drop_column("system_config", "bootstrap_consumed_at")
    op.drop_column("system_config", "bootstrap_expires_at")
    op.drop_column("system_config", "bootstrap_secret_hash")
    op.drop_column("organization", "timezone")
