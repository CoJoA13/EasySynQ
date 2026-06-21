"""S-interested-parties-1 (doc 14 §6, clause 4.2, R51): the Interested Parties register family schema.

Creates ``interested_party`` (the 1:many register-row satellite of the ``kind=DOCUMENT`` ``IPR``
head), the ``interested_party_type`` + ``interested_party_influence`` + ``interested_party_status``
enums, the additive ``INTERESTED_PARTY_UPDATED`` event type, and seeds the ``IPR`` document_type
(``is_singleton``).

⚠ Clause 4.2 "needs and expectations of interested parties" is ORG-LEVEL — there is NO ``process_id``
on the satellite (interested parties are strategic, org-wide; the Context register clone, 0060). The
register rides the **already-seeded** ``register.read`` / ``register.manage`` keys at the SYSTEM
scope (the QMS-leadership steward), so — like 0060 (CTX) and unlike S-risk-1 (0058) — this migration
adds **NO role grant**: the QMS Owner already holds ``register.*`` @ SYSTEM, and the 0058
Process-Owner ``register.manage`` @ PROCESS grant simply matches no (process-less) interested-party
row. NO new permission key (catalog stays 102).

``org_id`` is carried on the satellite per the §1.1 convention (the doc-14 §6 editorial-gap
correction R50 named — the only register satellite that omitted it).

The single-non-Obsolete-``IPR``-head invariant is enforced in the **service**
(``services/interested_parties/service.resolve_or_create_head``, an advisory-lock-serialized
get-or-create), NOT a DB partial-unique index (the S-risk-1/S-context-1 rationale verbatim).

Revision ID: 0061_interested_party_register
Revises: 0060_context_register
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._interested_party_enums import (
    INTERESTED_PARTY_INFLUENCE_VALUES,
    INTERESTED_PARTY_STATUS_VALUES,
    INTERESTED_PARTY_TYPE_VALUES,
)

revision: str = "0061_interested_party_register"
down_revision: str | None = "0060_context_register"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("INTERESTED_PARTY_UPDATED",)
# (code, name, document_level, is_singleton)
_IPR_TYPE = ("IPR", "Interested Parties Register", "L1_POLICY", True)


def upgrade() -> None:
    # 1. Additive event-type value (IF NOT EXISTS → idempotent; the autocommit_block matches 0060).
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The fresh enums (tuples from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*INTERESTED_PARTY_TYPE_VALUES, name="interested_party_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*INTERESTED_PARTY_INFLUENCE_VALUES, name="interested_party_influence").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*INTERESTED_PARTY_STATUS_VALUES, name="interested_party_status").create(
        bind, checkfirst=True
    )
    party_type = postgresql.ENUM(name="interested_party_type", create_type=False)
    influence = postgresql.ENUM(name="interested_party_influence", create_type=False)
    party_status = postgresql.ENUM(name="interested_party_status", create_type=False)

    # 3. interested_party — the 1:many register-row satellite (org-level: NO process_id). ``status``
    # is NOT NULL with no server_default — the service always supplies ``active`` on insert
    # (greenfield; the server_default alembic-check trap avoided).
    op.create_table(
        "interested_party",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("register_doc_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("party_type", party_type, nullable=False),
        sa.Column("party_name", sa.Text(), nullable=False),
        sa.Column("needs_expectations", sa.Text(), nullable=False),
        sa.Column("influence", influence, nullable=True),
        sa.Column("status", party_status, nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_interested_party"),
        sa.ForeignKeyConstraint(
            ["register_doc_id"],
            ["documented_information.id"],
            name="fk_interested_party_register_doc_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_interested_party_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_interested_party_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["app_user.id"],
            name="fk_interested_party_updated_by_app_user",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_interested_party_register_doc_id", "interested_party", ["register_doc_id"]
    )

    # 4. Least-privilege grant (pg_roles-guarded): interested-party rows are mutable working content.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON interested_party TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 5. Seed the IPR document_type for EVERY org (idempotent; covers a renamed install such as AHT).
    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    code, name, level, singleton = _IPR_TYPE
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
    # 5. Drop the IPR document_type seed, RESTRICT-guarded (the 0023/0058/0060 lesson): a populated
    # DB that created a register has a base doc row referencing the IPR type → an unguarded DELETE
    # aborts the whole rollback.
    bind.execute(
        sa.text(
            "DELETE FROM document_type dt WHERE dt.code = :c "
            "AND NOT EXISTS (SELECT 1 FROM documented_information di "
            "WHERE di.document_type_id = dt.id)"
        ),
        {"c": _IPR_TYPE[0]},
    )
    # 3. Drop the table + index.
    op.drop_index("ix_interested_party_register_doc_id", table_name="interested_party")
    op.drop_table("interested_party")
    # 2. Drop the enums (after the table that used them).
    op.execute("DROP TYPE IF EXISTS interested_party_status")
    op.execute("DROP TYPE IF EXISTS interested_party_influence")
    op.execute("DROP TYPE IF EXISTS interested_party_type")
    # 1. The event_type ADD VALUE is irreversible in PG → no-op (the 0060 precedent).
