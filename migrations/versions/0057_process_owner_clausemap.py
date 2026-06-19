"""Process Owner gains clauseMap.read @ SYSTEM (the create-in-process wizard's clause step)

S-records-C (R47, an R38-additive grant). Grants the seeded ``Process Owner`` permission-role a
SYSTEM-scoped ``clauseMap.read`` so a bound Process-Owner can read the org-wide ISO clause map
(``GET /clauses``) — the create-in-process wizard's clause step. The permission key ALREADY exists
(seeded in 0004), so this is an ADDITIVE role *grant*, not a new key: the catalog count stays 102
(R38 no-rename/removal holds; "additive" covers a new grant on an existing role). The companion
``authz/repository._grant_from_role`` change keeps a SYSTEM-finest grant from being clamped by a
bound owner's PROCESS ``bound_scope`` (a SYSTEM template has no placeholder to concretize), so the
grant is reachable.

Idempotent + multi-org: inserts the grant for EVERY org's ``Process Owner`` role (by role NAME, not
the ``DEFAULT`` org 0004 targets, so it reaches a renamed install such as ``AHT``) via
``on_conflict_do_nothing`` on ``(org_id, role_id, permission_id)``. Data-only — no schema change, so
``alembic check`` is unaffected and no ORM model changes.

Downgrade: delete just that role_grant (no permission is added, so none is removed). Round-trips
up/down/check on PG16 (a fresh DB seeds Process Owner via 0004, then this grant; downgrade removes
it).

Revision ID: 0057_process_owner_clausemap
Revises: 0056_org_role_assignment
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0057_process_owner_clausemap"
down_revision: str | None = "0056_org_role_assignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_NAME = "Process Owner"
_PERMISSION_KEY = "clauseMap.read"
_SYSTEM_SCOPE: dict[str, Any] = {"level": "SYSTEM"}


def upgrade() -> None:
    bind = op.get_bind()
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )
    # Resolve (org, Process Owner role, clauseMap.read permission) for EVERY org — the key already
    # exists (0004), so this is a CROSS JOIN of the role rows with the single permission row.
    rows = bind.execute(
        sa.text(
            "SELECT r.org_id AS org_id, r.id AS role_id, p.id AS permission_id "
            "FROM role r CROSS JOIN permission p "
            "WHERE r.name = :role AND p.key = :key"
        ),
        {"role": _ROLE_NAME, "key": _PERMISSION_KEY},
    ).all()
    if not rows:
        return
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
    bind.execute(
        sa.text(
            "DELETE FROM role_grant rg USING role r, permission p "
            "WHERE rg.role_id = r.id AND rg.permission_id = p.id "
            "AND r.name = :role AND p.key = :key"
        ),
        {"role": _ROLE_NAME, "key": _PERMISSION_KEY},
    )
