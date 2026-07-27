"""Repair retention grants and the process-edge CHECK name on existing installations.

Migration 0028 resolved the bootstrap organization only by ``short_code='DEFAULT'`` even though
setup renames that code before an operational install upgrades. The permission rows still landed,
but the QMS Owner/Internal Auditor role grants could be skipped. Backfill those idempotently through
the org-scoped role rows, matching the corrected historical migration.

Migration 0019 passed an already-prefixed CHECK name through the metadata naming convention, so
deployed databases can carry ``ck_process_edge_ck_process_edge_no_self_loop`` while the ORM expects
``ck_process_edge_no_self_loop``. Normalize the legacy name, tolerate an already-correct database,
and collapse the harmless duplicate if both names exist.

Both repairs are deliberately retained on downgrade: a role grant cannot be distinguished from an
equivalent administrator-created grant, and reverting a constraint's spelling would reintroduce the
ORM mismatch without changing schema semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0079_migration_orm_coherence"
down_revision: str | None = "0078_record_content_hash_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RETENTION_KEYS = ("retention.read", "retention.manage")
_ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    "QMS Owner": ("retention.read", "retention.manage"),
    "Internal Auditor": ("retention.read",),
}
_CANONICAL_CHECK = "ck_process_edge_no_self_loop"
_LEGACY_CHECK = "ck_process_edge_ck_process_edge_no_self_loop"


def _backfill_retention_grants() -> None:
    bind = op.get_bind()
    permission_ids = {
        key: permission_id
        for key, permission_id in bind.execute(
            sa.text("SELECT key, id FROM permission WHERE key IN :keys").bindparams(
                sa.bindparam("keys", expanding=True)
            ),
            {"keys": list(_RETENTION_KEYS)},
        )
    }
    role_rows = bind.execute(
        sa.text("SELECT org_id, id, name FROM role WHERE name IN :names").bindparams(
            sa.bindparam("names", expanding=True)
        ),
        {"names": list(_ROLE_GRANTS)},
    ).mappings()

    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )
    grant_rows: list[dict[str, object]] = []
    for role in role_rows:
        role_name = str(role["name"])
        for key in _ROLE_GRANTS[role_name]:
            grant_rows.append(
                {
                    "org_id": role["org_id"],
                    "role_id": role["id"],
                    "permission_id": permission_ids[key],
                    "scope_template": {"level": "SYSTEM"},
                }
            )
    if grant_rows:
        bind.execute(
            pg_insert(role_grant_t)
            .values(grant_rows)
            .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
        )


def _normalize_process_edge_check() -> None:
    bind = op.get_bind()
    names = set(
        bind.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'process_edge'::regclass AND contype = 'c'"
            )
        ).scalars()
    )
    if _LEGACY_CHECK in names and _CANONICAL_CHECK in names:
        # ``_LEGACY_CHECK`` is already the physical database name. Mark it convention-final so
        # Alembic does not prepend ``ck_process_edge_`` for a second time.
        op.drop_constraint(op.f(_LEGACY_CHECK), "process_edge", type_="check")
    elif _LEGACY_CHECK in names:
        op.execute(
            f"ALTER TABLE process_edge RENAME CONSTRAINT {_LEGACY_CHECK} TO {_CANONICAL_CHECK}"
        )
    elif _CANONICAL_CHECK not in names:
        # A manually renamed/dropped constraint should not leave the self-loop invariant absent.
        op.create_check_constraint(
            "no_self_loop",
            "process_edge",
            "from_process_id <> to_process_id",
        )


def upgrade() -> None:
    _backfill_retention_grants()
    _normalize_process_edge_check()


def downgrade() -> None:
    # Data/coherence repair: intentionally irreversible (see module docstring).
    pass
