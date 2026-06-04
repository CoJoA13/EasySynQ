"""retention-policy CRUD + soft-archive + SoD-6 flag + the first additive catalog extension (R38)

Slice S-rec-4 (doc 06 §5, doc 07 §7, doc 15 §8.16) — the records-family close-out:

1. **system_config.allow_self_disposition** — the SoD-6 (creator≠disposer) relaxation flag (default
   OFF = enforced). NOT-NULL with a ``false`` server_default (safe on a populated table); flipped only
   via the SYSTEM-gated PATCH /admin/config.
2. **retention_policy** soft-archive + audit columns — ``active`` (NOT-NULL, default ``true``),
   ``archived_at``/``archived_by`` (the retire trail; a hard DELETE is blocked by 3 RESTRICT FKs), and
   ``created_at``/``updated_at``. Archiving hides a policy from NEW-capture resolution (the resolver's
   record_type/clause/process tiers filter ``active``) but never strands records already pinned to it
   (``due_active_records`` joins by id, no active filter) — the spec's shorten-for-future workflow.
3. **R38 — the first post-v1 ADDITIVE catalog extension.** Two new CONTENT-domain permission keys
   ``retention.read`` + ``retention.manage`` (is_system_domain=False, sig_hook=False, sod_sensitive=
   False, finest_scope=SYSTEM — retention policies are org-level, gated at SYSTEM scope like
   config.update). Seeded for the DEFAULT org's roles: ``retention.read`` + ``retention.manage`` →
   QMS Owner; ``retention.read`` → Internal Auditor (the checklist-read precedent). Idempotent
   (ON CONFLICT DO NOTHING). The closed-catalog rule (R5) is REFINED, not broken: no renaming/removal,
   additive growth allowed with a register entry.
4. **event_type / audit_object_type** additive ``ALTER TYPE … ADD VALUE`` (the 0011-0027 pattern):
   event_type += RETENTION_POLICY_CREATED/UPDATED/ARCHIVED + DISPOSITION_REFUSED_SOD; audit_object_type
   += retention_policy. A from-scratch ``upgrade head`` rebuilds the types from the ORM *_VALUES, so the
   members live in the ORM enums too.

No new GRANT: ``retention_policy`` already carries the app role's table grant (0010 line 160 + the
ALTER DEFAULT PRIVILEGES), and new columns inherit it.

Migration notes: the ADD VALUEs are never used by a row in THIS migration (the PG16 in-txn rule is
satisfied). The downgrade drops the two columns groups, then deletes the role_grant rows for the new
keys BEFORE the permission rows (role_grant.permission_id RESTRICT-FKs permission), so a POPULATED-DB
downgrade does not abort; the ADD VALUEs are irreversible in PostgreSQL → no-op (0001 DROPs the types
wholesale, so the up↔down round-trip still passes; a re-upgrade rebuilds them from the ORM values).
Round-trips up↔down↔check on PG16 incl. a populated-DB downgrade (a custom policy + a pinned record +
the seeded grants).

Revision ID: 0028_retention_policy_crud
Revises: 0027_structured_forms
Create Date: 2026-06-04
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0028_retention_policy_crud"
down_revision: str | None = "0027_structured_forms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Additive enum values — must match the ORM EventType / AuditObjectType members (they do); the
# CREATE-TYPE path (none here) is what sources tuples from the ORM *_VALUES.
_NEW_EVENT_TYPES = (
    "RETENTION_POLICY_CREATED",
    "RETENTION_POLICY_UPDATED",
    "RETENTION_POLICY_ARCHIVED",
    "DISPOSITION_REFUSED_SOD",
)
_NEW_OBJECT_TYPES = ("retention_policy",)

# R38: the two new CONTENT-domain keys (key, resource, action). is_system_domain/sig_hook/sod_sensitive
# are all False; finest_scope SYSTEM (org-level resource, the config.update precedent).
_NEW_KEYS = ("retention.read", "retention.manage")
# Grants per role name (DEFAULT org); retention.read also for the Internal Auditor (read-authority).
_ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    "QMS Owner": ("retention.read", "retention.manage"),
    "Internal Auditor": ("retention.read",),
}


def upgrade() -> None:
    bind = op.get_bind()

    # 1. SoD-6 relaxation flag (default OFF = creator≠disposer enforced).
    op.add_column(
        "system_config",
        sa.Column(
            "allow_self_disposition", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )

    # 2. retention_policy soft-archive + audit columns.
    op.add_column(
        "retention_policy",
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "retention_policy", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "retention_policy",
        sa.Column("archived_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_retention_policy_archived_by_app_user",
        "retention_policy",
        "app_user",
        ["archived_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "retention_policy",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column(
        "retention_policy",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # 3. Additive enum values (never used by a row here; PG16 in-txn rule satisfied).
    for value in _NEW_OBJECT_TYPES:
        op.execute(f"ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS '{value}'")
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 4. R38: seed the two new CONTENT keys + their role grants (idempotent).
    permission_t = sa.table(
        "permission",
        sa.column("key", sa.Text),
        sa.column("resource", sa.Text),
        sa.column("action", sa.Text),
        sa.column("is_system_domain", sa.Boolean),
        sa.column("sod_sensitive", sa.Boolean),
        sa.column("sig_hook", sa.Boolean),
        sa.column("finest_scope", postgresql.ENUM(name="scope_level", create_type=False)),
    )
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )

    bind.execute(
        pg_insert(permission_t)
        .values(
            [
                {
                    "key": key,
                    "resource": key.partition(".")[0],
                    "action": key.partition(".")[2],
                    "is_system_domain": False,
                    "sod_sensitive": False,
                    "sig_hook": False,
                    "finest_scope": "SYSTEM",
                }
                for key in _NEW_KEYS
            ]
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )

    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is not None:  # a fresh test DB without the DEFAULT org seed → nothing to grant
        perm_ids = {
            key: pid
            for key, pid in bind.execute(
                sa.text("SELECT key, id FROM permission WHERE key IN :keys").bindparams(
                    sa.bindparam("keys", expanding=True)
                ),
                {"keys": list(_NEW_KEYS)},
            )
        }
        role_ids = {
            name: rid
            for name, rid in bind.execute(
                sa.text("SELECT name, id FROM role WHERE org_id = :org"), {"org": org_id}
            )
        }
        grant_rows: list[dict[str, Any]] = []
        for role_name, keys in _ROLE_GRANTS.items():
            rid = role_ids.get(role_name)
            if rid is None:
                continue
            for key in keys:
                grant_rows.append(
                    {
                        "org_id": org_id,
                        "role_id": rid,
                        "permission_id": perm_ids[key],
                        "scope_template": {"level": "SYSTEM"},
                    }
                )
        if grant_rows:
            bind.execute(
                pg_insert(role_grant_t)
                .values(grant_rows)
                .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
            )


def downgrade() -> None:
    bind = op.get_bind()

    # Delete the role_grant rows for the new keys BEFORE the permission rows (role_grant.permission_id
    # RESTRICT-FKs permission) so a populated-DB downgrade does not abort.
    del_grants = sa.text(
        "DELETE FROM role_grant WHERE permission_id IN "
        "(SELECT id FROM permission WHERE key IN :keys)"
    ).bindparams(sa.bindparam("keys", expanding=True))
    bind.execute(del_grants, {"keys": list(_NEW_KEYS)})
    del_perms = sa.text("DELETE FROM permission WHERE key IN :keys").bindparams(
        sa.bindparam("keys", expanding=True)
    )
    bind.execute(del_perms, {"keys": list(_NEW_KEYS)})

    op.drop_column("retention_policy", "updated_at")
    op.drop_column("retention_policy", "created_at")
    op.drop_constraint(
        "fk_retention_policy_archived_by_app_user", "retention_policy", type_="foreignkey"
    )
    op.drop_column("retention_policy", "archived_by")
    op.drop_column("retention_policy", "archived_at")
    op.drop_column("retention_policy", "active")
    op.drop_column("system_config", "allow_self_disposition")
    # The event_type / audit_object_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's
    # downgrade DROPs the types wholesale, so the up↔down round-trip still passes; a re-upgrade
    # rebuilds them from the ORM *_VALUES).
