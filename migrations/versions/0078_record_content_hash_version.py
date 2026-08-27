"""Persist the algorithm version beside every record content seal.

The original v1 record-hash serializer collapsed an empty form object to ``null``. Existing rows
must therefore remain explicitly v1 while new application code stamps captures as v2, where an
empty structured form stays distinct from the ad-hoc ``null`` sentinel. The conservative database
default remains v1 for rolling-deploy compatibility with an older application process. Downgrade
fails closed once any v2 seal exists: dropping the selector and later re-adding it as v1 would make
those immutable records unverifiable.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0078_record_content_hash_version"
down_revision: str | None = "0077_sealed_pack_retention"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "record",
        sa.Column(
            "content_hash_version",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    # Bare token: Alembic applies the metadata ck naming convention, so the full name would be
    # stored as ``ck_record_ck_record_content_hash_version_supported`` (the 0019/0079 lesson).
    # Databases migrated before this correction are repaired by 0089.
    op.create_check_constraint(
        "content_hash_version_supported",
        "record",
        "content_hash_version IN (1, 2)",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM record
                    WHERE content_hash_version <> 1
                ) THEN
                    RAISE EXCEPTION
                        'cannot downgrade 0078: v2 record content seals would lose their version'
                        USING ERRCODE = 'check_violation';
                END IF;
            END
            $$;
            """
        )
    )
    # Spelling-tolerant: a database migrated before the bare-token correction (and not yet through
    # the 0089 repair) stores the doubled legacy name — a by-name drop of the canonical spelling
    # would abort such a rollback.
    op.execute(
        "ALTER TABLE record DROP CONSTRAINT IF EXISTS ck_record_content_hash_version_supported"
    )
    op.execute(
        "ALTER TABLE record DROP CONSTRAINT IF EXISTS "
        "ck_record_ck_record_content_hash_version_supported"
    )
    op.drop_column("record", "content_hash_version")
