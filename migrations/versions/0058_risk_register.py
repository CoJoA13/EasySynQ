"""S-risk-1 (doc 14 §6, R18/R49): the Risk & Opportunity register family schema.

Creates ``risk_opportunity`` (the 1:many register-row satellite of the ``kind=DOCUMENT`` ``RSK``
head), the ``risk_opportunity_type`` + ``scoring_method`` enums, the additive ``RISK_RESCORED`` event
type, seeds the ``RSK`` document_type (``is_singleton``), and grants the **already-seeded**
``register.manage`` permission to the ``Process Owner`` role — an **R38-additive role grant**, NO new
permission key (catalog stays 102).

⚠ ``register.manage`` is granted with the ``_PROCESS_SCOPE`` placeholder template, **NOT**
``_SYSTEM_SCOPE``: a SYSTEM ``scope_template`` is exempt from ``bound_scope`` clamping
(``authz/repository._grant_from_role``) and matches every resource (``pdp``), so a SYSTEM-scoped
``register.manage`` would let a bound Process-Owner manage **every** process's risks + org-level rows.
The PROCESS placeholder is clamped to the owner's bound processes (the 0004 Process-Owner-bundle
precedent). ``clauseMap.read`` in 0057 was SYSTEM only because *its* resource is org-level — the
opposite case; it is the wrong precedent to copy here.

The single-non-Obsolete-``RSK``-head invariant is enforced in the **service**
(``services/risk/service.resolve_or_create_head``, an advisory-lock-serialized get-or-create), NOT a
DB partial-unique index: an ``RSK``-specific partial-unique can't be expressed (the per-org ``RSK``
``document_type_id`` can't be filtered in an index predicate), and a generic ``WHERE is_singleton AND
current_state <> 'Obsolete'`` would break the POL draft-successor flow the existing
``uq_doc_info_singleton_effective`` (Effective-only) deliberately allows. ``RSK`` revisions in place
(one head doc row), so the only multi-head window is the concurrent *first* create — closed by the
advisory lock.

Revision ID: 0058_risk_register
Revises: 0057_process_owner_clausemap
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._risk_enums import (
    RISK_OPPORTUNITY_TYPE_VALUES,
    SCORING_METHOD_VALUES,
)

revision: str = "0058_risk_register"
down_revision: str | None = "0057_process_owner_clausemap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("RISK_RESCORED",)
# (code, name, document_level, is_singleton)
_RSK_TYPE = ("RSK", "Risk & Opportunity Register", "L1_POLICY", True)
_ROLE_NAME = "Process Owner"
_PERMISSION_KEY = "register.manage"
_PROCESS_SCOPE: dict[str, Any] = {
    "level": "PROCESS",
    "selector": {"process_id": ":assignment_process"},
}


def upgrade() -> None:
    # 1. Additive event-type value (IF NOT EXISTS → idempotent; the autocommit_block matches 0049).
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The fresh enums (tuples from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*RISK_OPPORTUNITY_TYPE_VALUES, name="risk_opportunity_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*SCORING_METHOD_VALUES, name="scoring_method").create(bind, checkfirst=True)
    ro_type = postgresql.ENUM(name="risk_opportunity_type", create_type=False)
    method = postgresql.ENUM(name="scoring_method", create_type=False)

    # 3. risk_opportunity — the 1:many register-row satellite. CHECK tokens are bare; the metadata
    # ck convention expands them to ck_risk_opportunity_<token>, matching the ORM __table_args__.
    op.create_table(
        "risk_opportunity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("register_doc_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", ro_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("clause_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("likelihood", sa.Integer(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("risk_rating", sa.Integer(), nullable=False),
        sa.Column("scoring_method", method, nullable=False),
        sa.Column("treatment", sa.Text(), nullable=True),
        sa.Column("effectiveness", sa.Text(), nullable=True),
        sa.Column("linked_capa_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_risk_opportunity"),
        sa.ForeignKeyConstraint(
            ["register_doc_id"],
            ["documented_information.id"],
            name="fk_risk_opportunity_register_doc_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_risk_opportunity_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["process.id"],
            name="fk_risk_opportunity_process_id_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["clause_id"],
            ["clause.id"],
            name="fk_risk_opportunity_clause_id_clause",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_capa_id"],
            ["capa.id"],
            name="fk_risk_opportunity_linked_capa_id_capa",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_risk_opportunity_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["app_user.id"],
            name="fk_risk_opportunity_updated_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("likelihood BETWEEN 1 AND 5", name="likelihood_range"),
        sa.CheckConstraint("severity BETWEEN 1 AND 5", name="severity_range"),
    )
    op.create_index(
        "ix_risk_opportunity_register_doc_id", "risk_opportunity", ["register_doc_id"]
    )
    op.create_index("ix_risk_opportunity_process_id", "risk_opportunity", ["process_id"])

    # 4. Least-privilege grant (pg_roles-guarded): risk rows are mutable working content.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON risk_opportunity TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 5. Seed the RSK document_type for EVERY org (idempotent; covers a renamed install such as AHT,
    # mirroring the role-grant's all-orgs reach below).
    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    code, name, level, singleton = _RSK_TYPE
    org_ids = bind.execute(sa.text("SELECT id FROM organization")).scalars().all()
    for org_id in org_ids:
        bind.execute(
            pg_insert(document_type_t)
            .values(
                org_id=org_id,
                code=code,
                name=name,
                document_level=level,
                is_singleton=singleton,
            )
            .on_conflict_do_nothing(index_elements=["org_id", "code"])
        )

    # 6. Grant the seeded register.manage to EVERY org's Process Owner role at PROCESS scope (an
    # R38-additive role grant — NO new key). ⚠ _PROCESS_SCOPE, NOT _SYSTEM_SCOPE (see the docstring).
    # The 0057 CROSS-JOIN-by-role-NAME form reaches a renamed install (AHT).
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )
    rows = bind.execute(
        sa.text(
            "SELECT r.org_id AS org_id, r.id AS role_id, p.id AS permission_id "
            "FROM role r CROSS JOIN permission p "
            "WHERE r.name = :role AND p.key = :key"
        ),
        {"role": _ROLE_NAME, "key": _PERMISSION_KEY},
    ).all()
    if rows:
        bind.execute(
            pg_insert(role_grant_t)
            .values(
                [
                    {
                        "org_id": row.org_id,
                        "role_id": row.role_id,
                        "permission_id": row.permission_id,
                        "scope_template": _PROCESS_SCOPE,
                    }
                    for row in rows
                ]
            )
            .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
        )


def downgrade() -> None:
    bind = op.get_bind()
    # 6. Drop the register.manage→Process-Owner grant (no permission was added → none removed).
    bind.execute(
        sa.text(
            "DELETE FROM role_grant rg USING role r, permission p "
            "WHERE rg.role_id = r.id AND rg.permission_id = p.id "
            "AND r.name = :role AND p.key = :key"
        ),
        {"role": _ROLE_NAME, "key": _PERMISSION_KEY},
    )
    # 5. Drop the RSK document_type seed, RESTRICT-guarded (the 0023/0049 lesson): a populated DB
    # that created a register has a base doc row referencing the RSK type → an unguarded DELETE
    # aborts the whole rollback.
    bind.execute(
        sa.text(
            "DELETE FROM document_type dt WHERE dt.code = :c "
            "AND NOT EXISTS (SELECT 1 FROM documented_information di "
            "WHERE di.document_type_id = dt.id)"
        ),
        {"c": _RSK_TYPE[0]},
    )
    # 3. Drop the table + indexes.
    op.drop_index("ix_risk_opportunity_process_id", table_name="risk_opportunity")
    op.drop_index("ix_risk_opportunity_register_doc_id", table_name="risk_opportunity")
    op.drop_table("risk_opportunity")
    # 2. Drop the enums (after the table that used them).
    op.execute("DROP TYPE IF EXISTS scoring_method")
    op.execute("DROP TYPE IF EXISTS risk_opportunity_type")
    # 1. The event_type ADD VALUE is irreversible in PG → no-op (the 0011/0049 precedent).
