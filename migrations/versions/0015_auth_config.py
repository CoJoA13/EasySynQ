"""system_config auth columns + the AUTH_CONFIGURED / AUTH_TEST_LOGIN_OK / AUTH_TEST_LOGIN_FAILED
events — the G-D auth-config gate (slice S8c, doc 08 §9).

S8c lands the last blocking setup gate **G-D**: the wizard's auth step records a primary login
method and PROVES a non-bootstrap login works (the caller's JWKS-validated JWT + a live OIDC-issuer
reachability probe) before finalize is allowed — "never strand the org on a misconfigured IdP".
This migration adds the three nullable ``system_config`` columns the signal lives on
(``auth_test_login_ok``) + the three audit events the step emits.

Additive enum (the 0011–0014 precedent): ``ALTER TYPE event_type ADD VALUE`` is in-txn-safe on PG16
(no row USES the values here), irreversible → no-op enum downgrade (0001's downgrade DROP TYPEs
``event_type`` wholesale, so the up↔down round-trip still passes). The Python ``EventType`` carries
the three new members too (``_audit_enums.py``) so a from-scratch ``upgrade head`` — which rebuilds
the type from ``EVENT_TYPE_VALUES`` — matches a migrated DB.

The columns are nullable with no seed: a null ``auth_test_login_ok`` reads as G-D-unsatisfied, and
``/setup/configure-auth`` sets it. An already-OPERATIONAL install (upgraded) never re-finalizes, so
G-D is never re-checked for it — no brick risk, no back-fill needed.

Revision ID: 0015_auth_config
Revises: 0014_backup_policy
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_auth_config"
down_revision: str | None = "0014_backup_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'AUTH_CONFIGURED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'AUTH_TEST_LOGIN_OK'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'AUTH_TEST_LOGIN_FAILED'")

    op.add_column("system_config", sa.Column("auth_method", sa.Text(), nullable=True))
    op.add_column("system_config", sa.Column("auth_test_login_ok", sa.Boolean(), nullable=True))
    op.add_column(
        "system_config",
        sa.Column("auth_test_login_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # The ADD VALUEs on event_type are irreversible in PostgreSQL → no-op for the enum (0001's
    # downgrade DROP TYPEs event_type wholesale, so the round-trip still passes). Drop the columns.
    op.drop_column("system_config", "auth_test_login_at")
    op.drop_column("system_config", "auth_test_login_ok")
    op.drop_column("system_config", "auth_method")
