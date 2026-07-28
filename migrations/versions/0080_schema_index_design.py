"""Remove a dead version field and index two live query paths.

``document_version.change_summary`` was never part of the API or application workflow; INV-3 is
represented by the required ``change_reason`` and ``change_significance`` fields. Refuse to discard
unsupported manually inserted summary data, then remove the dead nullable column.

Records are looked up by their source document as well as their exact source version, and role
assignments are resolved by user. Add the missing plain indexes for those established query paths
without imposing new uniqueness semantics.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0080_schema_index_design"
down_revision: str | None = "0079_migration_orm_coherence"
branch_labels: str | None = None
depends_on: str | None = None

_RECORD_SOURCE_DOCUMENT_INDEX = "ix_record_source_document_id"
_ROLE_ASSIGNMENT_USER_INDEX = "ix_role_assignment_user_id"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM document_version
                    WHERE change_summary IS NOT NULL
                ) THEN
                    RAISE EXCEPTION
                        'cannot upgrade 0080: document_version.change_summary contains '
                        'unsupported data; migrate it deliberately before removing the dead column'
                        USING ERRCODE = 'check_violation';
                END IF;
            END
            $$;
            """
        )
    )
    op.drop_column("document_version", "change_summary")
    op.create_index(
        _RECORD_SOURCE_DOCUMENT_INDEX,
        "record",
        ["source_document_id"],
        unique=False,
    )
    op.create_index(
        _ROLE_ASSIGNMENT_USER_INDEX,
        "role_assignment",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(_ROLE_ASSIGNMENT_USER_INDEX, table_name="role_assignment")
    op.drop_index(_RECORD_SOURCE_DOCUMENT_INDEX, table_name="record")
    op.add_column(
        "document_version",
        sa.Column("change_summary", sa.Text(), nullable=True),
    )
