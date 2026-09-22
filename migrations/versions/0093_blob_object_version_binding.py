"""Bind each blob row to the exact stored object version it was sealed as.

A recovery generation today references an object by ``bucket``/``object_key`` only, so every restore
path resolves whatever version is CURRENT at restore time. Because blobs are content-addressed and
only the sha256 is checked, an object overwritten after a generation was written would be accepted
silently: equal bytes hash identically, and a newer version of the same key is indistinguishable.

The value is already proven at the write path — WORM promotion pins the exact source version and
reads back the sealed ``target_version_id`` — it was simply discarded when the row was written.

``object_version_source`` records how the binding was obtained: ``promotion`` is the version the
verified WORM write returned, ``write`` one a direct server-side put returned, ``backfill`` one
observed later by ``backup bind-versions`` (attesting only what was current then), and
``unversioned`` records that the write returned no version because the bucket has none — the
``renditions`` bucket is created without object lock or versioning, so a deliberately absent binding
must stay distinguishable from a missing one. Existing rows predate all of this, so both columns are
nullable and a CHECK keeps the permitted combinations exact.

⚠ The CHECK is named with the BARE token: the ``ck_%(table_name)s_%(constraint_name)s`` convention
prepends the table, so passing ``blob_object_version_binding`` yields a doubled
``ck_blob_blob_object_version_binding`` that only an inspection of the live constraint name reveals.
The ORM carries the identical bare name, because ``alembic check`` compares constraint names but not
CHECK bodies.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0093_blob_object_version_binding"
down_revision: str | None = "0092_user_color_scheme"
branch_labels: str | None = None
depends_on: str | None = None

_CHECK = "object_version_binding"
# ⚠ CASE over COALESCE, not an OR-chain of IS NULL tests: with a NULL ``object_version_source``
# every branch of such a chain evaluates to NULL, the expression is NULL, and a CHECK ADMITS NULL —
# an id with no source passed. Caught by inserting that row against live PostgreSQL, not by review.
_BODY = (
    "CASE COALESCE(object_version_source, '')"
    " WHEN '' THEN object_version_id IS NULL"
    " WHEN 'unversioned' THEN object_version_id IS NULL"
    " WHEN 'promotion' THEN object_version_id IS NOT NULL"
    " WHEN 'write' THEN object_version_id IS NOT NULL"
    " WHEN 'backfill' THEN object_version_id IS NOT NULL"
    " ELSE false END"
)


def upgrade() -> None:
    op.add_column("blob", sa.Column("object_version_id", sa.Text(), nullable=True))
    op.add_column("blob", sa.Column("object_version_source", sa.Text(), nullable=True))
    op.create_check_constraint(_CHECK, "blob", _BODY)


def downgrade() -> None:
    # Bare token here too: drop_constraint re-tokenizes through the same convention, so a
    # "ck_blob_" prefix yields ck_blob_ck_blob_... and the DOWNGRADE fails on a populated or
    # empty database alike — invisible to an upgrade-only check.
    op.drop_constraint(_CHECK, "blob", type_="check")
    op.drop_column("blob", "object_version_source")
    op.drop_column("blob", "object_version_id")
