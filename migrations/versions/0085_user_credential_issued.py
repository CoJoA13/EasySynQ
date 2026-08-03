"""USER_CREDENTIAL_ISSUED event — in-app Keycloak credential issuance (slice S-user-create).

S-user-create adds one-step user creation from the Admin SPA: the API provisions the Keycloak
account and issues a temporary password. Creating the user rides the existing ``USER_CREATED``
event; issuing a credential to an EXISTING user has no honest existing event, so this migration adds
one. Reusing ``USER_STATUS_CHANGED`` was rejected — no status changes, and a misleading audit record
is worse than a migration.

The event records THAT a credential was issued. The password itself is never persisted, logged, or
placed in an audit payload.

**No new columns.** ``app_user`` already carries ``keycloak_subject``/``display_name``/``email``/
``status``, so this slice needs only the additive enum value.

Additive enum (the 0011-0016 precedent): ``ALTER TYPE event_type ADD VALUE`` is in-txn-safe on PG16
(no row USES the value here), irreversible → no-op enum downgrade (0010's downgrade DROP TYPEs
``event_type`` wholesale, so the up↔down round-trip still passes). The Python ``EventType`` carries
the new member too (``_audit_enums.py``) so a from-scratch ``upgrade head`` — which rebuilds the type
from ``EVENT_TYPE_VALUES`` — matches a migrated DB.

Revision ID: 0085_user_credential_issued
Revises: 0084_clause7_support_do
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0085_user_credential_issued"
down_revision: str | None = "0084_clause7_support_do"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'USER_CREDENTIAL_ISSUED'")


def downgrade() -> None:
    # The ADD VALUE on event_type is irreversible in PostgreSQL → no-op (0010's downgrade DROP TYPEs
    # event_type wholesale, so the round-trip still passes). No columns were added.
    pass
