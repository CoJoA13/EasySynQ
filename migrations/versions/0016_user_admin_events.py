"""USER_CREATED + USER_STATUS_CHANGED events — the user-lifecycle admin trail (slice S8d, doc 08 §10).

S8d lands the post-finalize Users & Roles admin surface (invite/enable/disable + role/override
management). The role/override grant trail already has its S2 events (ROLE_ASSIGN/REVOKE,
OVERRIDE_ADD/REMOVE); this migration adds the two events the **user-lifecycle** actions emit — an
invite (pre-creating an INVITED ``app_user``) and an enable/disable status change.

**No new columns:** ``UserStatus`` already carries ``INVITED`` and ``app_user.mfa_enrolled`` already
exists (both from S1's 0002), so S8d needs only these additive enum values.

Additive enum (the 0011-0015 precedent): ``ALTER TYPE event_type ADD VALUE`` is in-txn-safe on PG16
(no row USES the values here), irreversible → no-op enum downgrade (0001's downgrade DROP TYPEs
``event_type`` wholesale, so the up↔down round-trip still passes). The Python ``EventType`` carries
the two new members too (``_audit_enums.py``) so a from-scratch ``upgrade head`` — which rebuilds the
type from ``EVENT_TYPE_VALUES`` — matches a migrated DB.

Revision ID: 0016_user_admin_events
Revises: 0015_auth_config
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_user_admin_events"
down_revision: str | None = "0015_auth_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'USER_CREATED'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'USER_STATUS_CHANGED'")


def downgrade() -> None:
    # The ADD VALUEs on event_type are irreversible in PostgreSQL → no-op (0001's downgrade DROP
    # TYPEs event_type wholesale, so the round-trip still passes). No columns were added.
    pass
