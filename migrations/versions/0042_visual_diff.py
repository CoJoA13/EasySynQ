"""visual diff: the cached visual page-image diff result (S-dcr-3b)

The visual-diff slice of the v1 "Revision & change depth" family (doc 05 §8.1, worker-async). NO new
permission key (the endpoints ride `document.read_draft`, like the S-dcr-3a text/metadata diff); NO new
event type (the visual diff is a read-derived, regenerable cache — like a rendition — not an audited
domain action).

1. **One fresh enum** (`CREATE TYPE` → usable same-txn): `visual_diff_status`
   (Pending/Ready/Failed/Unavailable). Values from the ORM `VISUAL_DIFF_STATUS_VALUES`.
2. **visual_diff** — the cached comparison of two immutable versions of one document.
   `UNIQUE(from_version_id, to_version_id)` = the idempotency latch + cache key. `pages` JSONB holds the
   per-page result (page PNGs are content-addressed non-WORM `Blob`s in the renditions bucket). Mutable
   status (`GRANT SELECT,INSERT,UPDATE` — a regenerable cache, NOT append-only).

Downgrade: drop visual_diff; DROP the fresh enum. Round-trips up↔down↔check on PG16 incl. a populated-DB
downgrade.

Revision ID: 0042_visual_diff
Revises: 0041_dcr_where_used
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._dcr_enums import VISUAL_DIFF_STATUS_VALUES

revision: str = "0042_visual_diff"
down_revision: str | None = "0041_dcr_where_used"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The fresh enum (CREATE TYPE → usable same-txn). Tuple from the ORM *_VALUES.
    postgresql.ENUM(*VISUAL_DIFF_STATUS_VALUES, name="visual_diff_status").create(
        bind, checkfirst=True
    )
    visual_diff_status = postgresql.ENUM(name="visual_diff_status", create_type=False)

    # 2. visual_diff — the cached page-image comparison of two versions.
    op.create_table(
        "visual_diff",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", visual_diff_status, server_default=sa.text("'Pending'"), nullable=False
        ),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("pages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_visual_diff_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documented_information.id"],
            name="fk_visual_diff_document_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_version_id"],
            ["document_version.id"],
            name="fk_visual_diff_from_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_version_id"],
            ["document_version.id"],
            name="fk_visual_diff_to_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_visual_diff"),
        sa.UniqueConstraint("from_version_id", "to_version_id", name="uq_visual_diff_from_to"),
    )
    op.create_index("ix_visual_diff_document_id", "visual_diff", ["document_id"])

    # 3. Least-privilege grant (mutable status — re-computed cache, NOT append-only). pg_roles-guarded.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON visual_diff TO {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_visual_diff_document_id", table_name="visual_diff")
    op.drop_table("visual_diff")
    op.execute("DROP TYPE IF EXISTS visual_diff_status")
