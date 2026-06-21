"""Seed the reserved Register Steward role (R52)

S-register-steward. Seeds a NEW reserved ``Register Steward`` role holding the full register
stewardship set at SYSTEM scope: ``register.read · register.manage · document.release ·
document.read · document.read_draft``. This is the FIRST seeded role to hold ``document.release``
(release was SYSTEM-override-only in v1) — so the three register-steward consoles (Risk 6.1 /
Context 4.1 / Interested Parties 4.2) become self-service without a SYSTEM override. The role
deliberately EXCLUDES ``document.approve`` (SoD: the approver stays a separate Approver / QMS-Owner;
register publish still routes its approval to that pool, and release stays releaser ≠ approver).

NO new permission key (every key already exists, seeded in 0004) → the catalog count stays 102
(R38: "additive" covers a new role + new grants on existing keys). Data-only — no schema change, so
``alembic check`` is unaffected and no ORM model changes.

Idempotent + multi-org by NAME (not the ``DEFAULT`` org 0004 targets, so it reaches a renamed
install such as ``AHT``): inserts the role for EVERY org via ``on_conflict_do_nothing`` on
``(org_id, name)``, then the 5 grants via a CROSS JOIN of the role rows with the permission rows
(``on_conflict_do_nothing`` on ``(org_id, role_id, permission_id)``). Returns early on an
uninitialized DB (no org).

Downgrade: both FKs to ``role.id`` are ``ondelete=RESTRICT``, so delete the steward's
``role_assignment`` rows → its ``role_grant`` rows → the ``role`` row (scoped by name). No permission
is added, so none is removed. Round-trips up/down/check on PG16.

Revision ID: 0062_register_steward_role
Revises: 0061_interested_party_register
Create Date: 2026-06-21
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0062_register_steward_role"
down_revision: str | None = "0061_interested_party_register"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_NAME = "Register Steward"
_ROLE_DESC = (
    "Stewards the org-level registers (Risk 6.1 / Context 4.1 / Interested Parties 4.2): "
    "start-revision, publish, and release the controlled register heads. Holds document.release "
    "(the releaser, distinct from the QMS-Owner/Approver) — SoD-2 still applies."
)
_KEYS: tuple[str, ...] = (
    "register.read",
    "register.manage",
    "document.release",
    "document.read",
    "document.read_draft",
)
_SYSTEM_SCOPE: dict[str, Any] = {"level": "SYSTEM"}


def upgrade() -> None:
    bind = op.get_bind()
    role_t = sa.table(
        "role",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("is_reserved", sa.Boolean),
    )
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )

    # 1. Seed the reserved role for EVERY org (single-org D1; by-org keeps the 0057 multi-org shape
    #    and is name-agnostic, so it reaches a renamed install such as AHT).
    org_ids = [row.id for row in bind.execute(sa.text("SELECT id FROM organization")).all()]
    if not org_ids:
        return  # uninitialized DB — no org yet
    bind.execute(
        pg_insert(role_t)
        .values(
            [
                {
                    "org_id": org_id,
                    "name": _ROLE_NAME,
                    "description": _ROLE_DESC,
                    "is_reserved": True,
                }
                for org_id in org_ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "name"])
    )

    # 2. Resolve (org, Register Steward role, key permission) for every (org, key) — a CROSS JOIN of
    #    the just-seeded role rows with the 5 permission rows (each key already exists from 0004).
    stmt = sa.text(
        "SELECT r.org_id AS org_id, r.id AS role_id, p.id AS permission_id "
        "FROM role r CROSS JOIN permission p "
        "WHERE r.name = :role AND p.key IN :keys"
    ).bindparams(sa.bindparam("keys", expanding=True))
    rows = bind.execute(stmt, {"role": _ROLE_NAME, "keys": list(_KEYS)}).all()
    bind.execute(
        pg_insert(role_grant_t)
        .values(
            [
                {
                    "org_id": row.org_id,
                    "role_id": row.role_id,
                    "permission_id": row.permission_id,
                    "scope_template": _SYSTEM_SCOPE,
                }
                for row in rows
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Both role_grant.role_id and role_assignment.role_id are ondelete=RESTRICT → delete the children
    # before the role. Scoped to the Register Steward role only (every other role untouched).
    bind.execute(
        sa.text(
            "DELETE FROM role_assignment WHERE role_id IN "
            "(SELECT id FROM role WHERE name = :role)"
        ),
        {"role": _ROLE_NAME},
    )
    bind.execute(
        sa.text(
            "DELETE FROM role_grant WHERE role_id IN (SELECT id FROM role WHERE name = :role)"
        ),
        {"role": _ROLE_NAME},
    )
    bind.execute(sa.text("DELETE FROM role WHERE name = :role"), {"role": _ROLE_NAME})
