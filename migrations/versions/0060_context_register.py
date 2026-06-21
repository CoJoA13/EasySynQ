"""S-context-1 (doc 14 §6, clause 4.1, R50): the Context register family schema.

Creates ``context_issue`` (the 1:many register-row satellite of the ``kind=DOCUMENT`` ``CTX`` head),
the ``context_classification`` + ``context_category`` + ``context_issue_status`` enums, the additive
``CONTEXT_ISSUE_UPDATED`` event type, and seeds the ``CTX`` document_type (``is_singleton``).

⚠ Clause 4.1 "context of the organization" is ORG-LEVEL — there is NO ``process_id`` on the
satellite
(external/internal issues are strategic, org-wide). The register rides the **already-seeded**
``register.read`` / ``register.manage`` keys at the SYSTEM scope (the QMS-leadership steward), so —
unlike S-risk-1 (0058) — this migration adds **NO role grant**: the QMS Owner already holds
``register.*`` @ SYSTEM, and the 0058 Process-Owner ``register.manage`` @ PROCESS grant simply
matches no (process-less) context row. NO new permission key (catalog stays 102).

The single-non-Obsolete-``CTX``-head invariant is enforced in the **service**
(``services/context/service.resolve_or_create_head``, an advisory-lock-serialized get-or-create),
NOT
a DB partial-unique index (the S-risk-1 rationale verbatim: a ``CTX``-specific partial-unique can't
be
expressed and a generic ``is_singleton`` one would break the POL draft-successor flow).

Revision ID: 0060_context_register
Revises: 0059_risk_capa_spawn
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._context_enums import (
    CONTEXT_CATEGORY_VALUES,
    CONTEXT_CLASSIFICATION_VALUES,
    CONTEXT_ISSUE_STATUS_VALUES,
)

revision: str = "0060_context_register"
down_revision: str | None = "0059_risk_capa_spawn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("CONTEXT_ISSUE_UPDATED",)
# (code, name, document_level, is_singleton)
_CTX_TYPE = ("CTX", "Context Register", "L1_POLICY", True)


def upgrade() -> None:
    # 1. Additive event-type value (IF NOT EXISTS → idempotent; the autocommit_block matches 0058).
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The fresh enums (tuples from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*CONTEXT_CLASSIFICATION_VALUES, name="context_classification").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*CONTEXT_CATEGORY_VALUES, name="context_category").create(bind, checkfirst=True)
    postgresql.ENUM(*CONTEXT_ISSUE_STATUS_VALUES, name="context_issue_status").create(
        bind, checkfirst=True
    )
    classification = postgresql.ENUM(name="context_classification", create_type=False)
    category = postgresql.ENUM(name="context_category", create_type=False)
    issue_status = postgresql.ENUM(name="context_issue_status", create_type=False)

    # 3. context_issue — the 1:many register-row satellite (org-level: NO process_id). ``status`` is
    # NOT NULL with no server_default — the service always supplies ``active`` on insert
    # (greenfield;
    # the server_default alembic-check trap avoided).
    op.create_table(
        "context_issue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("register_doc_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classification", classification, nullable=False),
        sa.Column("category", category, nullable=True),
        sa.Column("status", issue_status, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_context_issue"),
        sa.ForeignKeyConstraint(
            ["register_doc_id"],
            ["documented_information.id"],
            name="fk_context_issue_register_doc_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_context_issue_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_context_issue_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["app_user.id"],
            name="fk_context_issue_updated_by_app_user",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_context_issue_register_doc_id", "context_issue", ["register_doc_id"])

    # 4. Least-privilege grant (pg_roles-guarded): context rows are mutable working content.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON context_issue TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 5. Seed the CTX document_type for EVERY org (idempotent; covers a renamed install such as
    # AHT).
    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    code, name, level, singleton = _CTX_TYPE
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


def downgrade() -> None:
    bind = op.get_bind()
    # 5. Drop the CTX document_type seed, RESTRICT-guarded (the 0023/0058 lesson): a populated DB
    # that
    # created a register has a base doc row referencing the CTX type → an unguarded DELETE aborts
    # the
    # whole rollback.
    bind.execute(
        sa.text(
            "DELETE FROM document_type dt WHERE dt.code = :c "
            "AND NOT EXISTS (SELECT 1 FROM documented_information di "
            "WHERE di.document_type_id = dt.id)"
        ),
        {"c": _CTX_TYPE[0]},
    )
    # 3. Drop the table + index.
    op.drop_index("ix_context_issue_register_doc_id", table_name="context_issue")
    op.drop_table("context_issue")
    # 2. Drop the enums (after the table that used them).
    op.execute("DROP TYPE IF EXISTS context_issue_status")
    op.execute("DROP TYPE IF EXISTS context_category")
    op.execute("DROP TYPE IF EXISTS context_classification")
    # 1. The event_type ADD VALUE is irreversible in PG → no-op (the 0058 precedent).
