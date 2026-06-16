"""improvement initiatives: improvement_initiative + _stage_event + INITIATIVE_* events + R46 keys

The backend core of the Improvement Initiatives family (clause 10.3 continual improvement; doc 02
Cl 10.3, doc 14 §9, decisions-register R46/R22/R5). Per **R46** an improvement initiative is a
controlled WORKFLOW object with a *mutable* ``stage`` column plus an append-only
``improvement_initiative_stage_event`` history — NOT a ``kind=RECORD`` artifact and NOT a
``documented_information`` subtype (the ``dcr`` mutable-state precedent, a register-sanctioned
deviation from doc 02's RECORD framing).

1. **Two fresh enums** (``CREATE TYPE`` → usable same-txn): ``improvement_stage`` /
   ``improvement_source``. Value tuples sourced from the ORM ``_improvement_enums`` ``*_VALUES`` so
   the DDL and the SAEnum bindings never drift (the 0010/0040 rule).
2. **event_type ADD VALUE** — INITIATIVE_RAISED / INITIATIVE_UPDATED / INITIATIVE_TRANSITIONED +
   MGMT_REVIEW_INITIATIVE_SPAWNED (the MR-side spawn act, first emitted in slice 2 — defined here so
   slice 2 is zero-migration); **audit_object_type ADD VALUE ``improvement_initiative``** (an
   initiative id is NOT a record id → cannot reuse ``record``). NONE is used by a row here (PG16
   in-txn ADD-VALUE rule satisfied — wrapped in an autocommit_block, the 0049 shape).
3. **improvement_initiative** — an own table with a mutable ``stage``. UNIQUE(org_id, identifier) +
   three indexes; the spawn-seam columns (``source``/``source_link_id``/``spawn_idempotency_key``)
   ship here so slice 2 is zero-migration; the partial-UNIQUE ``uq_improvement_initiative_spawn``
   (excluded from alembic check in env.py).
4. **improvement_initiative_stage_event** — the append-only state-transition trail (``REVOKE UPDATE,
   DELETE`` from the non-owner app role — the signature_event / capa_stage / dcr_stage_event
   precedent; no ``updated_at``). ``signed_event_id`` carries its FK to ``signature_event`` from
   day one (NULL/unsigned in v1.x — the D3 Part-11 reserved hook).
5. **R46 — the additive catalog extension.** Two new CONTENT-domain keys ``improvement.read`` +
   ``improvement.manage`` (is_system_domain=False, sod_sensitive=False, sig_hook=False,
   finest_scope PROCESS). Granted: ``improvement.read`` + ``improvement.manage`` → QMS Owner
   (org/QMS) and Process Owner (PROCESS-scoped, the ``:assignment_process`` placeholder — rides
   SYSTEM overrides until
   owner-assignment binds); ``improvement.read`` → Internal Auditor (the checklist-read precedent).
   Catalog 100 → 102.

Downgrade: NOT-EXISTS-guarded seed-deletes (none here — no seed children); delete
``permission_override → role_grant → permission`` for the 2 keys (the RESTRICT-FK order); drop
improvement_initiative_stage_event (FK→improvement_initiative) before improvement_initiative; DROP
the two fresh enums. The event_type/audit_object_type ADD VALUEs are irreversible in PostgreSQL →
no-op (0001's downgrade DROPs those types). Round-trips up↔down↔check on PG16.

Revision ID: 0052_improvement_initiatives
Revises: 0051_mr_outputs_to_actions
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._improvement_enums import (
    IMPROVEMENT_SOURCE_VALUES,
    IMPROVEMENT_STAGE_VALUES,
)

revision: str = "0052_improvement_initiatives"
down_revision: str | None = "0051_mr_outputs_to_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = (
    "INITIATIVE_RAISED",
    "INITIATIVE_UPDATED",
    "INITIATIVE_TRANSITIONED",
    "MGMT_REVIEW_INITIATIVE_SPAWNED",
)
_NEW_OBJECT_TYPES = ("improvement_initiative",)

# R46: the two new CONTENT-domain keys (is_system_domain/sig_hook/sod_sensitive all False;
# finest_scope PROCESS — the family is PROCESS-scoped, the objective.*/changeRequest.* precedent).
_NEW_KEYS = ("improvement.read", "improvement.manage")
# PROCESS-scope template (the seeded :assignment_process placeholder — rides SYSTEM overrides until
# owner-assignment binds; the DCR/objectives backfill recipe).
_PROCESS_SCOPE: dict[str, Any] = {
    "level": "PROCESS",
    "selector": {"process_id": ":assignment_process"},
}
# Grants per role name (DEFAULT org): QMS Owner gets both; Process Owner gets both (PROCESS-scoped);
# Internal Auditor gets read (the checklist-read precedent — the auditor raises OFIs + reads the
# improvement pipeline but does not drive initiatives).
_ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    "QMS Owner": ("improvement.read", "improvement.manage"),
    "Process Owner": ("improvement.read", "improvement.manage"),
    "Internal Auditor": ("improvement.read",),
}


def upgrade() -> None:
    # 1. Additive enum values (IF NOT EXISTS → idempotent; not USED in this txn's seeds, but the
    # autocommit_block satisfies the PG16 in-txn ADD-VALUE rule — the 0049 shape).
    with op.get_context().autocommit_block():
        for value in _NEW_OBJECT_TYPES:
            op.execute(f"ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS '{value}'")
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The two fresh enums (CREATE TYPE → usable same-txn). Tuples from the ORM *_VALUES.
    postgresql.ENUM(*IMPROVEMENT_STAGE_VALUES, name="improvement_stage").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*IMPROVEMENT_SOURCE_VALUES, name="improvement_source").create(
        bind, checkfirst=True
    )
    improvement_stage = postgresql.ENUM(name="improvement_stage", create_type=False)
    improvement_source = postgresql.ENUM(name="improvement_source", create_type=False)

    # 3. improvement_initiative — an own table with a mutable ``stage``; spawn-seam columns ship
    # now (so slice 2 is zero-migration).
    op.create_table(
        "improvement_initiative",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_outcome", sa.Text(), nullable=True),
        sa.Column("source", improvement_source, nullable=False),
        sa.Column("source_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("spawn_idempotency_key", sa.Text(), nullable=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stage", improvement_stage, server_default=sa.text("'Open'"), nullable=False),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_improvement_initiative"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_improvement_initiative_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["process.id"],
            name="fk_improvement_initiative_process_id_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["app_user.id"],
            name="fk_improvement_initiative_owner_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_improvement_initiative_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "org_id", "identifier", name="uq_improvement_initiative_org_id_identifier"
        ),
    )
    op.create_index(
        "ix_improvement_initiative_org_id_stage", "improvement_initiative", ["org_id", "stage"]
    )
    op.create_index(
        "ix_improvement_initiative_source_link_id", "improvement_initiative", ["source_link_id"]
    )
    op.create_index(
        "ix_improvement_initiative_process_id", "improvement_initiative", ["process_id"]
    )
    # The spawn idempotency partial-UNIQUE (excluded from alembic check in env.py). Scoped to the
    # originating object (source_link_id) so the same key on a DIFFERENT source spawns fresh — the
    # dcr / import-decision precedent. NULL spawn_idempotency_key (every manual create) is exempt.
    op.create_index(
        "uq_improvement_initiative_spawn",
        "improvement_initiative",
        ["org_id", "source_link_id", "spawn_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("spawn_idempotency_key IS NOT NULL"),
    )

    # 4. improvement_initiative_stage_event — the append-only state-transition trail.
    op.create_table(
        "improvement_initiative_stage_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initiative_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state", improvement_stage, nullable=True),
        sa.Column("to_state", improvement_stage, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("signed_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_improvement_initiative_stage_event"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_imp_init_stage_event_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["initiative_id"],
            ["improvement_initiative.id"],
            name="fk_imp_init_stage_event_initiative_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["app_user.id"],
            name="fk_imp_init_stage_event_actor_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signed_event_id"],
            ["signature_event.id"],
            name="fk_imp_init_stage_event_signed_event_id_signature_event",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_improvement_initiative_stage_event_initiative_id",
        "improvement_initiative_stage_event",
        ["initiative_id"],
    )

    # 5. Least-privilege grants (pg_roles-guarded): improvement_initiative is mutable (stage / owner
    # / closed_at), improvement_initiative_stage_event is append-only (REVOKE UPDATE,DELETE — the
    # dcr_stage_event precedent; without the REVOKE, immutability is merely conventional).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON improvement_initiative TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON improvement_initiative_stage_event TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON improvement_initiative_stage_event '
                        'FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 6. R46: seed the two new CONTENT keys + their role grants (idempotent).
    _seed_authz(bind)


def _seed_authz(bind: sa.engine.Connection) -> None:
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
                    "finest_scope": "PROCESS",
                }
                for key in _NEW_KEYS
            ]
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )

    # Resilient org lookup (the 0028/0049 trap): DEFAULT short_code else the sole org. A fresh test
    # DB without the org seed → nothing to grant.
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one_or_none()
    if org_id is None:
        return

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
                    "scope_template": _PROCESS_SCOPE,
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

    # permission_override + role_grant BEFORE permission (both RESTRICT FKs) — a populated-DB
    # downgrade must not abort on a live-smoke per-user override (the 0028/0049 shape).
    del_overrides = sa.text(
        "DELETE FROM permission_override WHERE permission_id IN "
        "(SELECT id FROM permission WHERE key IN :keys)"
    ).bindparams(sa.bindparam("keys", expanding=True))
    bind.execute(del_overrides, {"keys": list(_NEW_KEYS)})
    del_grants = sa.text(
        "DELETE FROM role_grant WHERE permission_id IN "
        "(SELECT id FROM permission WHERE key IN :keys)"
    ).bindparams(sa.bindparam("keys", expanding=True))
    bind.execute(del_grants, {"keys": list(_NEW_KEYS)})
    del_perms = sa.text("DELETE FROM permission WHERE key IN :keys").bindparams(
        sa.bindparam("keys", expanding=True)
    )
    bind.execute(del_perms, {"keys": list(_NEW_KEYS)})

    # Drop in reverse FK order: the stage_event (FK→improvement_initiative) before the parent.
    op.drop_index(
        "ix_improvement_initiative_stage_event_initiative_id",
        table_name="improvement_initiative_stage_event",
    )
    op.drop_table("improvement_initiative_stage_event")
    op.drop_index("uq_improvement_initiative_spawn", table_name="improvement_initiative")
    op.drop_index("ix_improvement_initiative_process_id", table_name="improvement_initiative")
    op.drop_index("ix_improvement_initiative_source_link_id", table_name="improvement_initiative")
    op.drop_index("ix_improvement_initiative_org_id_stage", table_name="improvement_initiative")
    op.drop_table("improvement_initiative")
    for enum_name in ("improvement_source", "improvement_stage"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
    # The event_type / audit_object_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's
    # downgrade DROPs the types wholesale; a re-upgrade rebuilds them from the ORM *_VALUES).
