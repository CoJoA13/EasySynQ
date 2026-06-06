"""dcr implement/close: the dcr↔document_version cross-FK + spawn idempotency + implement grant (S-dcr-5)

The FINAL DCR slice (doc 05 §5.5/§6/§7.3, decisions-register R40 S-dcr-5 addendum). Closes the
deferred ``dcr ↔ document_version`` 2-table cycle, adds the CAPA→DCR spawn idempotency key, and
grant-backfills the orphaned-but-cataloged ``changeRequest.implement`` key. No new permission key
(R5), no new enum value (``Implemented``/``Closed`` already exist in the 0040 ``dcr_state`` enum).

1. **The cross-FK** (the ``capa.origin_finding_id`` ↔ ``audit_finding`` /
   ``documented_information.current_effective_version_id`` precedent): a new
   ``document_version.dcr_id`` column + BOTH named FKs created via ``op.create_foreign_key`` AFTER
   the columns exist (so the metadata cycle breaks). The reverse edge
   ``fk_document_version_dcr_id_dcr`` carries ``use_alter`` in the ORM; the forward
   ``fk_dcr_resulting_version_id_document_version`` is on the pre-existing (0040) ``resulting_version_id``
   column. Both names are byte-identical to the ORM ``ForeignKey(name=…)`` so ``alembic check`` is clean.
2. **Spawn idempotency** — ``dcr.spawn_idempotency_key`` + a partial-UNIQUE
   ``uq_dcr_spawn_idempotency_key (org_id, spawn_idempotency_key) WHERE spawn_idempotency_key IS NOT
   NULL`` (the ``uq_import_decision_run_idem`` precedent; excluded from ``alembic check`` in
   ``env.py``). Makes ``POST /capas/{id}/raise-dcr`` retry-safe while preserving 1:N spawning.
3. **Grant-backfill** ``changeRequest.implement`` → Process Owner + QMS Owner, PROCESS-scoped (the
   0040/0043 recipe). NB the implement endpoint ALSO enforces ``document.release`` / ``document.obsolete``
   at runtime (no DCR side-door past document control) — this grant only opens the DCR gate.

No column-level GRANT needed (table grants cover the added columns; ``dcr`` already has
SELECT,INSERT,UPDATE, ``document_version`` already has UPDATE for the cutover). Downgrade drops the
FKs + index + the new column + the grant; the pre-existing ``resulting_version_id`` column stays.

Revision ID: 0044_dcr_implement
Revises: 0043_dcr_approval
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0044_dcr_implement"
down_revision: str | None = "0043_dcr_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROCESS_SCOPE: dict[str, Any] = {"level": "PROCESS", "selector": {"process_id": ":assignment_process"}}
_BACKFILL: tuple[tuple[str, str], ...] = (
    ("Process Owner", "changeRequest.implement"),
    ("QMS Owner", "changeRequest.implement"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The cross-FK columns (both nullable; no backfill needed — always NULL until an implement).
    op.add_column(
        "document_version",
        sa.Column("dcr_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("dcr", sa.Column("spawn_idempotency_key", sa.Text(), nullable=True))

    # 2. Both named cross-FKs, created AFTER the columns exist (the 0037/0007 cycle-break ordering).
    op.create_foreign_key(
        "fk_document_version_dcr_id_dcr",
        "document_version",
        "dcr",
        ["dcr_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_dcr_resulting_version_id_document_version",
        "dcr",
        "document_version",
        ["resulting_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 3. The spawn idempotency partial-UNIQUE (excluded from alembic check in env.py). Scoped to
    #    the originating object (source_link_id) so the same key on a DIFFERENT CAPA spawns fresh —
    #    the import-decision (run_id, key) precedent.
    op.create_index(
        "uq_dcr_spawn_idempotency_key",
        "dcr",
        ["org_id", "source_link_id", "spawn_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("spawn_idempotency_key IS NOT NULL"),
    )

    # 4. Grant-backfill changeRequest.implement → Process Owner + QMS Owner (PROCESS-scoped).
    _apply_backfill(bind)


def _apply_backfill(bind: sa.engine.Connection) -> None:
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )
    perm_ids = {
        key: pid for key, pid in bind.execute(sa.text("SELECT key, id FROM permission")).all()
    }
    rows: list[dict[str, Any]] = []
    for role_name, perm_key in _BACKFILL:
        permission_id = perm_ids.get(perm_key)
        if permission_id is None:  # catalog always seeded by 0004 — defensive
            continue
        roles = bind.execute(
            sa.text("SELECT id, org_id FROM role WHERE name = :n"), {"n": role_name}
        ).all()
        rows.extend(
            {
                "org_id": org_id,
                "role_id": role_id,
                "permission_id": permission_id,
                "scope_template": _PROCESS_SCOPE,
            }
            for role_id, org_id in roles
        )
    if rows:
        bind.execute(
            pg_insert(role_grant_t)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
        )


def downgrade() -> None:
    bind = op.get_bind()

    # Remove the backfilled implement grants (per (role name, permission key) pair).
    for role_name, perm_key in _BACKFILL:
        bind.execute(
            sa.text(
                "DELETE FROM role_grant "
                "WHERE permission_id = (SELECT id FROM permission WHERE key = :k) "
                "AND role_id IN (SELECT id FROM role WHERE name = :n)"
            ),
            {"k": perm_key, "n": role_name},
        )

    op.drop_index("uq_dcr_spawn_idempotency_key", table_name="dcr")
    # Drop both cross-FKs before dropping the back-edge column. resulting_version_id (the pre-0040
    # column) stays; only its FK is removed — a populated down→up re-adds the FK over the still-valid
    # version ids without dangling values.
    op.drop_constraint(
        "fk_dcr_resulting_version_id_document_version", "dcr", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_document_version_dcr_id_dcr", "document_version", type_="foreignkey"
    )
    op.drop_column("dcr", "spawn_idempotency_key")
    op.drop_column("document_version", "dcr_id")
