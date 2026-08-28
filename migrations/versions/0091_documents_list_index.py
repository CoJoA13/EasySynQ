"""Index the documents-list scan (audit U16).

``GET /documents`` scans ``documented_information`` WHERE (org_id, kind) ORDER BY created_at
DESC over a bounded window; without a matching index every request sorted the whole shared
table (documents + records + register heads all live here). Plain composite btree — mirrored in
the ORM ``__table_args__`` so ``alembic check`` stays clean.
"""

from __future__ import annotations

from alembic import op

revision: str = "0091_documents_list_index"
down_revision: str | None = "0090_import_report_retention"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "ix_documented_information_org_id_kind_created_at",
        "documented_information",
        ["org_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_documented_information_org_id_kind_created_at",
        table_name="documented_information",
    )
