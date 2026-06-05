"""capa core + intake: capa + capa_stage + ncr + complaint + CAPA_* events + grant backfill (S-capa-1)

The CAPA slice of the v1 Audits/Findings/CAPA family (doc 02 Cl 8.7/10.2, doc 10 §6, doc 14 §9/§14).

1. **Five fresh enums** (``CREATE TYPE`` → usable same-txn): ``capa_source`` / ``ncr_source`` /
   ``nc_severity`` / ``capa_close_state`` / ``ncr_disposition``. Value tuples sourced from the ORM
   ``_capa_enums`` ``*_VALUES`` so DDL and the SAEnum bindings never drift. (``capa_source`` is
   all-lowercase — doc 14 §9's ``AUDIT`` is a spec typo, normalized per decisions-register R39; the
   R2/R16 lowercase precedent.)
2. **event_type ADD VALUE** — the six CAPA_*/COMPLAINT_*/NCR_* events; **audit_object_type ADD VALUE
   ``ncr``** (NCR is an own table → not a record id; CAPA + complaint reuse ``record``). NONE is used
   by a row here (PG16 in-txn ADD-VALUE rule satisfied — events are emitted at runtime).
3. **system_config.allow_capa_self_verify** — the severity-aware SoD-4 relaxation flag (default OFF;
   the ``allow_self_disposition`` precedent). A forward seam exposed via /admin/config now; enforced
   in S-capa-3.
4. **capa** — a ``kind=RECORD`` shared-PK subtype (``capa.id`` → ``record.id``) with a mutable
   ``close_state`` lifecycle column. ``origin_finding_id`` is a nullable UUID with NO FK (audit_finding
   lands in S-aud-2, which adds the FK + the reverse auto_capa_id).
5. **capa_stage** — the append-only sealed stage-block trail (``REVOKE UPDATE, DELETE`` from the
   non-owner app role — the signature_event precedent). doc 14's ``attachments`` member is realized as
   ``evidence_for_link(CAPA_STAGE)`` edges (R39), not a column.
6. **ncr** — an own table (ISO 9001 8.7 nonconforming output, R20) with a human ``NCR-{SEQ}`` identifier.
7. **complaint** — a lightweight ``kind=RECORD`` shared-PK subtype (R16); ``spawned_capa_id`` is the
   one-click-spawn idempotency latch (nullable UNIQUE).
8. **Grant backfill** — the three orphaned-but-cataloged keys (no new keys): ``capa.update`` →
   Process Owner; ``ncr.create`` → QMS Owner + Internal Auditor; ``ncr.record_correction`` → QMS Owner.
   PROCESS-scoped (the seeded ``:assignment_process`` placeholder, consistent with the family — rides
   SYSTEM overrides until owner-assignment binds it). The 0021 backfill recipe; idempotent.

Downgrade: delete the backfill grants; drop complaint + capa_stage (both FK→capa) → capa → ncr (reverse
FK order; wholesale DROP TABLE handles a populated DB — no inbound FK from another existing table);
drop the config column; DROP the five fresh enums. The event_type/audit_object_type ADD VALUEs are
irreversible in PostgreSQL → no-op (0001's downgrade DROPs those types; a re-upgrade re-adds via ADD
VALUE IF NOT EXISTS). Round-trips up↔down↔check on PG16 incl. a populated-DB downgrade.

Revision ID: 0036_capa_core
Revises: 0035_workflow_engine
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._capa_enums import (
    CAPA_CLOSE_STATE_VALUES,
    CAPA_SOURCE_VALUES,
    NC_SEVERITY_VALUES,
    NCR_DISPOSITION_VALUES,
    NCR_SOURCE_VALUES,
)

revision: str = "0036_capa_core"
down_revision: str | None = "0035_workflow_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = (
    "CAPA_RAISED",
    "CAPA_TRANSITIONED",
    "COMPLAINT_CAPTURED",
    "COMPLAINT_SPAWNED_CAPA",
    "NCR_CREATED",
    "NCR_DISPOSITIONED",
)
# The S-capa-1 grant backfill (decisions-register R39 / owner decision): (role name, permission key).
# All PROCESS-scoped (the family placeholder), consistent with the seeded capa.*/finding.* grants.
_PROCESS_SCOPE: dict[str, Any] = {
    "level": "PROCESS",
    "selector": {"process_id": ":assignment_process"},
}
_BACKFILL: tuple[tuple[str, str], ...] = (
    ("Process Owner", "capa.update"),
    ("QMS Owner", "ncr.create"),
    ("Internal Auditor", "ncr.create"),
    ("QMS Owner", "ncr.record_correction"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The five fresh enums (CREATE TYPE → usable same-txn). Tuples from the ORM *_VALUES.
    postgresql.ENUM(*CAPA_SOURCE_VALUES, name="capa_source").create(bind, checkfirst=True)
    postgresql.ENUM(*NCR_SOURCE_VALUES, name="ncr_source").create(bind, checkfirst=True)
    postgresql.ENUM(*NC_SEVERITY_VALUES, name="nc_severity").create(bind, checkfirst=True)
    postgresql.ENUM(*CAPA_CLOSE_STATE_VALUES, name="capa_close_state").create(bind, checkfirst=True)
    postgresql.ENUM(*NCR_DISPOSITION_VALUES, name="ncr_disposition").create(bind, checkfirst=True)
    capa_source = postgresql.ENUM(name="capa_source", create_type=False)
    ncr_source = postgresql.ENUM(name="ncr_source", create_type=False)
    nc_severity = postgresql.ENUM(name="nc_severity", create_type=False)
    capa_close_state = postgresql.ENUM(name="capa_close_state", create_type=False)
    ncr_disposition = postgresql.ENUM(name="ncr_disposition", create_type=False)

    # 2. Extend the audit-log enums (additive; none used by a row here → in-txn safe).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")
    op.execute("ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS 'ncr'")

    # 3. The severity-aware SoD-4 relaxation flag (default OFF; the allow_self_disposition precedent).
    op.add_column(
        "system_config",
        sa.Column(
            "allow_capa_self_verify",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )

    # 4. capa — a kind=RECORD shared-PK subtype (capa.id → record.id). Mutable ``close_state``.
    op.create_table(
        "capa",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", capa_source, nullable=False),
        sa.Column("severity", nc_severity, nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "close_state", capa_close_state, server_default=sa.text("'Raised'"), nullable=False
        ),
        sa.Column("cycle_marker", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["id"], ["record.id"], name="fk_capa_id_record", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_capa_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"], ["process.id"], name="fk_capa_process_id_process", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_capa"),
    )

    # 5. capa_stage — the append-only sealed stage-block trail.
    op.create_table(
        "capa_stage",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capa_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", capa_close_state, nullable=False),
        sa.Column("content_block", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signed_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cycle_marker", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_capa_stage_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["capa_id"], ["capa.id"], name="fk_capa_stage_capa_id_capa", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["signed_event_id"],
            ["signature_event.id"],
            name="fk_capa_stage_signed_event_id_signature_event",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_capa_stage_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_capa_stage"),
    )
    op.create_index("ix_capa_stage_capa_id", "capa_stage", ["capa_id"])

    # 6. ncr — an own table (ISO 9001 8.7) with a human NCR-{SEQ} identifier.
    op.create_table(
        "ncr",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("source", ncr_source, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", nc_severity, nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("disposition", ncr_disposition, nullable=True),
        sa.Column("disposition_authorized_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("disposition_notes", sa.Text(), nullable=True),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_ncr_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"], ["process.id"], name="fk_ncr_process_id_process", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["disposition_authorized_by"],
            ["app_user.id"],
            name="fk_ncr_disposition_authorized_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_ncr_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ncr"),
        sa.UniqueConstraint("org_id", "identifier", name="uq_ncr_org_id_identifier"),
    )

    # 7. complaint — a lightweight kind=RECORD shared-PK subtype (R16). spawned_capa_id = the latch.
    op.create_table(
        "complaint",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", nc_severity, nullable=True),
        sa.Column("spawned_capa_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["id"], ["record.id"], name="fk_complaint_id_record", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_complaint_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["spawned_capa_id"],
            ["capa.id"],
            name="fk_complaint_spawned_capa_id_capa",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_complaint"),
        sa.UniqueConstraint("spawned_capa_id", name="uq_complaint_spawned_capa_id"),
    )

    # 8. Least-privilege grants; capa_stage is append-only (REVOKE UPDATE,DELETE). pg_roles-guarded.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON capa TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON ncr TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON complaint TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON capa_stage TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON capa_stage FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 9. Grant backfill for the three orphaned-but-cataloged keys (no new keys; the 0021 recipe).
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
        key: pid
        for key, pid in bind.execute(sa.text("SELECT key, id FROM permission")).all()
    }
    rows: list[dict[str, Any]] = []
    for role_name, perm_key in _BACKFILL:
        permission_id = perm_ids.get(perm_key)
        if permission_id is None:  # catalog always seeded by 0004 — defensive
            continue
        # One row per org that has this role (single-org installs → one row).
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
    # Drop in reverse FK order: complaint + capa_stage (FK→capa) before capa; ncr is independent.
    op.drop_table("complaint")
    op.drop_index("ix_capa_stage_capa_id", table_name="capa_stage")
    op.drop_table("capa_stage")
    op.drop_table("capa")
    op.drop_table("ncr")
    op.drop_column("system_config", "allow_capa_self_verify")
    for enum_name in ("capa_close_state", "capa_source", "ncr_disposition", "ncr_source", "nc_severity"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
