"""dcr core + intake: dcr + dcr_stage_event + DCR_* events + grant backfill (S-dcr-1)

The core slice of the v1 "Revision & change depth" family (doc 05 §5, doc 14 §7, doc 15 §8.7,
decisions-register R22/R5). Per **R22** the DCR is a controlled WORKFLOW object with a *mutable*
``state`` column plus an append-only ``dcr_stage_event`` history — NOT a ``kind=RECORD`` artifact
(the
``worm_destroy_request`` mutable-state precedent, not the ``capa`` record-subtype).

1. **Four fresh enums** (``CREATE TYPE`` → usable same-txn): ``dcr_state`` / ``dcr_change_type`` /
   ``dcr_reason_class`` / ``dcr_source_link_type``. Value tuples sourced from the ORM
   ``_dcr_enums``
   ``*_VALUES`` so the DDL and the SAEnum bindings never drift (the 0036 precedent).
   ``change_significance``
   is REUSED (the existing vault enum), not re-created.
2. **event_type ADD VALUE** — DCR_RAISED / DCR_UPDATED / DCR_TRANSITIONED; **audit_object_type ADD
VALUE
   ``dcr``** (a DCR id is NOT a record id → cannot reuse ``record``). NONE is used by a row here
   (PG16
   in-txn ADD-VALUE rule satisfied — events are emitted at runtime).
3. **dcr** — an own table with a mutable ``state``. The ``ck_dcr_create_iff_no_target``
biconditional
   CHECK (bare token — the metadata convention expands it; the 0037 ``nc_has_severity`` precedent)
   enforces CREATE ⟺ no target. ``resulting_version_id`` is a nullable UUID with NO FK (the
   deferred
   cross-FK to ``document_version`` + the reverse ``document_version.dcr_id`` land in S-dcr-5 via
   ``use_alter``). ``source_link_id`` is a polymorphic UUID with NO FK (the signature_event
   precedent).
4. **dcr_stage_event** — the append-only state-transition trail (``REVOKE UPDATE, DELETE`` from the
   non-owner app role — the signature_event / capa_stage precedent). ``signed_event_id`` carries
   its FK
   to ``signature_event`` from day one (the capa_stage precedent; populated in S-dcr-4).
5. **Grant backfill** — the two orphaned-but-cataloged keys this slice surfaces (no new keys):
   ``changeRequest.assess`` (PATCH) + ``changeRequest.close`` (cancel) → Process Owner + QMS Owner,
   PROCESS-scoped (the seeded ``:assignment_process`` placeholder; rides SYSTEM overrides until
   owner-assignment binds it). The 0036 backfill recipe; idempotent. (``changeRequest.create`` /
   ``.read`` are already granted by 0004; ``.route`` / ``.approve`` / ``.implement`` are backfilled
   in
   their own later slices.)

Downgrade: delete the backfill grants; drop dcr_stage_event (FK→dcr) before dcr; DROP the four
fresh
enums. The event_type/audit_object_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's
downgrade DROPs those types; a re-upgrade re-adds via ADD VALUE IF NOT EXISTS). Round-trips
up↔down↔check on PG16 incl. a populated-DB downgrade.

Revision ID: 0040_dcr_core
Revises: 0039_pack_finding_capa_scope
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._dcr_enums import (
    DCR_CHANGE_TYPE_VALUES,
    DCR_REASON_CLASS_VALUES,
    DCR_SOURCE_LINK_TYPE_VALUES,
    DCR_STATE_VALUES,
)

revision: str = "0040_dcr_core"
down_revision: str | None = "0039_pack_finding_capa_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("DCR_RAISED", "DCR_UPDATED", "DCR_TRANSITIONED")
# The S-dcr-1 grant backfill (decisions-register R39/owner pattern): (role name, permission key).
# Both
# PROCESS-scoped (the family placeholder), consistent with the seeded changeRequest.*/capa.*
# grants.
_PROCESS_SCOPE: dict[str, Any] = {
    "level": "PROCESS",
    "selector": {"process_id": ":assignment_process"},
}
_BACKFILL: tuple[tuple[str, str], ...] = (
    ("Process Owner", "changeRequest.assess"),
    ("QMS Owner", "changeRequest.assess"),
    ("Process Owner", "changeRequest.close"),
    ("QMS Owner", "changeRequest.close"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The four fresh enums (CREATE TYPE → usable same-txn). Tuples from the ORM *_VALUES.
    postgresql.ENUM(*DCR_STATE_VALUES, name="dcr_state").create(bind, checkfirst=True)
    postgresql.ENUM(*DCR_CHANGE_TYPE_VALUES, name="dcr_change_type").create(bind, checkfirst=True)
    postgresql.ENUM(*DCR_REASON_CLASS_VALUES, name="dcr_reason_class").create(bind, checkfirst=True)
    postgresql.ENUM(*DCR_SOURCE_LINK_TYPE_VALUES, name="dcr_source_link_type").create(
        bind, checkfirst=True
    )
    dcr_state = postgresql.ENUM(name="dcr_state", create_type=False)
    dcr_change_type = postgresql.ENUM(name="dcr_change_type", create_type=False)
    dcr_reason_class = postgresql.ENUM(name="dcr_reason_class", create_type=False)
    dcr_source_link_type = postgresql.ENUM(name="dcr_source_link_type", create_type=False)
    change_significance = postgresql.ENUM(name="change_significance", create_type=False)

    # 2. Extend the audit-log enums (additive; none used by a row here → in-txn safe).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")
    op.execute("ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS 'dcr'")

    # 3. dcr — an own table with a mutable ``state``. CREATE ⟺ no-target CHECK; deferred cross-FK
    # seams.
    op.create_table(
        "dcr",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("target_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("change_type", dcr_change_type, nullable=False),
        sa.Column("change_significance", change_significance, nullable=False),
        sa.Column("reason_class", dcr_reason_class, nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("source_link_type", dcr_source_link_type, nullable=True),
        sa.Column("source_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("proposed_effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resulting_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", dcr_state, server_default=sa.text("'Open'"), nullable=False),
        sa.Column("decision", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], name="fk_dcr_org_id_organization", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_document_id"],
            ["documented_information.id"],
            name="fk_dcr_target_document_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["app_user.id"], name="fk_dcr_decided_by_app_user", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name="fk_dcr_created_by_app_user", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dcr"),
        sa.UniqueConstraint("org_id", "identifier", name="uq_dcr_org_id_identifier"),
        # Bare token — the metadata ck naming convention expands it to ck_dcr_create_iff_no_target,
        # matching the ORM __table_args__ (the 0037 nc_has_severity precedent).
        sa.CheckConstraint(
            "(change_type = 'CREATE') = (target_document_id IS NULL)",
            name="create_iff_no_target",
        ),
    )
    op.create_index("ix_dcr_org_id_state", "dcr", ["org_id", "state"])
    op.create_index("ix_dcr_target_document_id", "dcr", ["target_document_id"])

    # 4. dcr_stage_event — the append-only state-transition trail.
    op.create_table(
        "dcr_stage_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dcr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state", dcr_state, nullable=True),
        sa.Column("to_state", dcr_state, nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("signed_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_dcr_stage_event_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dcr_id"], ["dcr.id"], name="fk_dcr_stage_event_dcr_id_dcr", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["app_user.id"],
            name="fk_dcr_stage_event_actor_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["signed_event_id"],
            ["signature_event.id"],
            name="fk_dcr_stage_event_signed_event_id_signature_event",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dcr_stage_event"),
    )
    op.create_index("ix_dcr_stage_event_dcr_id", "dcr_stage_event", ["dcr_id"])

    # 5. Least-privilege grants; dcr_stage_event is append-only (REVOKE UPDATE,DELETE).
    # pg_roles-guarded.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON dcr TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON dcr_stage_event TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON dcr_stage_event FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 6. Grant backfill for the two orphaned-but-cataloged keys this slice surfaces (no new keys).
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
    # Remove exactly the backfilled grants (per (role name, permission key) pair).
    for role_name, perm_key in _BACKFILL:
        bind.execute(
            sa.text(
                "DELETE FROM role_grant "
                "WHERE permission_id = (SELECT id FROM permission WHERE key = :k) "
                "AND role_id IN (SELECT id FROM role WHERE name = :n)"
            ),
            {"k": perm_key, "n": role_name},
        )
    # Drop in reverse FK order: dcr_stage_event (FK→dcr) before dcr.
    op.drop_index("ix_dcr_stage_event_dcr_id", table_name="dcr_stage_event")
    op.drop_table("dcr_stage_event")
    op.drop_index("ix_dcr_target_document_id", table_name="dcr")
    op.drop_index("ix_dcr_org_id_state", table_name="dcr")
    op.drop_table("dcr")
    for enum_name in ("dcr_source_link_type", "dcr_reason_class", "dcr_change_type", "dcr_state"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
