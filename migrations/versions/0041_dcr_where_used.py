"""dcr where-used: document_link + impact_assessment + DOCUMENT_LINKED/UNLINKED events (S-dcr-2)

The where-used / impact slice of the v1 "Revision & change depth" family (doc 05 §7 + §5.3, doc 14
§5.6 + §7, doc 15 §8.7). Adds the document↔document reference graph + the DCR structured impact
assessment. NO new permission keys: where-used gates on `document.read`, link CRUD on
`document.manage_metadata`, assess on `changeRequest.assess`, impact on `changeRequest.read`/`.assess`
— all already in the catalog + granted (so no grant backfill).

1. **Two fresh enums** (CREATE TYPE → usable same-txn): `document_link_type`
   (parent_of/child_of/references/supersedes) + `impact_dimension` (the 7 doc 05 §5.3 dimensions).
   Value tuples sourced from the ORM `*_VALUES` so DDL + the SAEnum bindings never drift.
2. **event_type ADD VALUE** — DOCUMENT_LINKED / DOCUMENT_UNLINKED (object_type=document; none used by a
   row here → PG16 in-txn ADD-VALUE rule satisfied).
3. **document_link** — the doc↔doc graph. Editable metadata (GRANT SELECT,INSERT,DELETE — NOT
   append-only; the clause_mapping/process_link precedent). FK names given explicitly (<63-char PG
   limit); `ck_document_link_no_self`; UNIQUE(from,to,type); indexes on both FK columns.
4. **impact_assessment** — one row per DCR per dimension (UPSERT at assess). Mutable (GRANT
   SELECT,INSERT,UPDATE — auto_populated re-computed + requester_annotation edited).

Downgrade: drop impact_assessment + document_link; DROP the two fresh enums. The event_type ADD VALUEs
are irreversible in PostgreSQL → no-op. Round-trips up↔down↔check on PG16 incl. a populated-DB
downgrade.

Revision ID: 0041_dcr_where_used
Revises: 0040_dcr_core
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._dcr_enums import IMPACT_DIMENSION_VALUES
from easysynq_api.db.models._vault_enums import DOCUMENT_LINK_TYPE_VALUES

revision: str = "0041_dcr_where_used"
down_revision: str | None = "0040_dcr_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("DOCUMENT_LINKED", "DOCUMENT_UNLINKED")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The two fresh enums (CREATE TYPE → usable same-txn). Tuples from the ORM *_VALUES.
    postgresql.ENUM(*DOCUMENT_LINK_TYPE_VALUES, name="document_link_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*IMPACT_DIMENSION_VALUES, name="impact_dimension").create(bind, checkfirst=True)
    document_link_type = postgresql.ENUM(name="document_link_type", create_type=False)
    impact_dimension = postgresql.ENUM(name="impact_dimension", create_type=False)

    # 2. Extend event_type (additive; none used by a row here → in-txn safe).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 3. document_link — the doc↔doc reference graph (editable metadata, not append-only).
    op.create_table(
        "document_link",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("link_type", document_link_type, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_document_link_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_document_id"],
            ["documented_information.id"],
            name="fk_doc_link_from",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_document_id"],
            ["documented_information.id"],
            name="fk_doc_link_to",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_document_link_created_by_app_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_link"),
        sa.UniqueConstraint(
            "from_document_id", "to_document_id", "link_type", name="uq_document_link_from_to_type"
        ),
        # Bare token — the metadata ck naming convention expands it to ck_document_link_no_self,
        # matching the ORM __table_args__ (the 0040 create_iff_no_target precedent).
        sa.CheckConstraint("from_document_id <> to_document_id", name="no_self"),
    )
    op.create_index("ix_document_link_from_document_id", "document_link", ["from_document_id"])
    op.create_index("ix_document_link_to_document_id", "document_link", ["to_document_id"])

    # 4. impact_assessment — one row per DCR per dimension (UPSERT at assess; mutable).
    op.create_table(
        "impact_assessment",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dcr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", impact_dimension, nullable=False),
        sa.Column("auto_populated", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requester_annotation", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_impact_assessment_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dcr_id"], ["dcr.id"], name="fk_impact_assessment_dcr_id_dcr", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_impact_assessment"),
        sa.UniqueConstraint("dcr_id", "dimension", name="uq_impact_assessment_dcr_dimension"),
    )
    op.create_index("ix_impact_assessment_dcr_id", "impact_assessment", ["dcr_id"])

    # 5. Least-privilege grants. Both are mutable (NOT append-only): document_link is editable metadata
    #    (delete-not-update), impact_assessment is re-computed + annotated. pg_roles-guarded.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, DELETE ON document_link TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON impact_assessment TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_impact_assessment_dcr_id", table_name="impact_assessment")
    op.drop_table("impact_assessment")
    op.drop_index("ix_document_link_to_document_id", table_name="document_link")
    op.drop_index("ix_document_link_from_document_id", table_name="document_link")
    op.drop_table("document_link")
    for enum_name in ("impact_dimension", "document_link_type"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
