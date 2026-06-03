"""authz backfill: grant report.compliance_checklist.read to the Internal Auditor role (slice S10)

The org-wide Compliance Checklist (★ mandatory-item coverage) is gated on the already-seeded SYSTEM
key ``report.compliance_checklist.read``, which 0004 bundled into **QMS Owner only**. Internal Auditor
(Ingrid) is the natural consumer of a coverage view, so S10 adds the grant to the Internal Auditor
bundle for every org — a deliberate seed-bundle change (owner decision), not a new permission key.

This is the first **backfill** of seeded authz after 0004. It resolves by **stable identifiers only**
— role by ``name='Internal Auditor'`` (per org) + permission by ``key`` — never by ``short_code`` (a
finalized install renames the org's short_code; the role name is stable). Idempotent
(``ON CONFLICT DO NOTHING`` on ``uq_role_grant_org_id_role_id_permission_id``) and forward-safe: on a
fresh install 0004 seeds the role first, then this grants it; on an upgrade the renamed org's role is
found by name. The grant scope mirrors the auditor's other SYSTEM reads (``report.read`` /
``clauseMap.read``): ``scope_template = {"level": "SYSTEM"}``. Downgrade removes exactly this grant.

Revision ID: 0021_auditor_checklist_grant
Revises: 0020_search_fts
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0021_auditor_checklist_grant"
down_revision: str | None = "0020_search_fts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_NAME = "Internal Auditor"
_PERMISSION_KEY = "report.compliance_checklist.read"
_SYSTEM_SCOPE = {"level": "SYSTEM"}


def upgrade() -> None:
    bind = op.get_bind()
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )

    permission_id = bind.execute(
        sa.text("SELECT id FROM permission WHERE key = :k"), {"k": _PERMISSION_KEY}
    ).scalar_one_or_none()
    if permission_id is None:  # the catalog is always seeded by 0004 — defensive skip
        return

    # One row per org that has an Internal Auditor role (single-org installs → one row).
    auditor_roles = bind.execute(
        sa.text("SELECT id, org_id FROM role WHERE name = :n"), {"n": _ROLE_NAME}
    ).all()
    values = [
        {
            "org_id": org_id,
            "role_id": role_id,
            "permission_id": permission_id,
            "scope_template": _SYSTEM_SCOPE,
        }
        for role_id, org_id in auditor_roles
    ]
    if values:
        bind.execute(
            pg_insert(role_grant_t)
            .values(values)
            .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_grant "
            "WHERE permission_id = (SELECT id FROM permission WHERE key = :k) "
            "AND role_id IN (SELECT id FROM role WHERE name = :n)"
        ),
        {"k": _PERMISSION_KEY, "n": _ROLE_NAME},
    )
