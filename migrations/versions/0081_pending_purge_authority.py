"""Bind pending purge markers to lawful disposition authority.

``pending_blob_purge`` is a durable command consumed after the transaction that disposed the
record. Previously its target and ``bypass_governance`` flag were trusted directly, so an app-role
marker forged with a false SHA could evade the liveness check and drive an S3 erase.

New markers identify the disposed record and immutable disposition event; an R27 governance-bypass
marker also identifies the executed two-person request. Existing rows cannot be reconstructed after
their evidence links have been deleted, so they are explicitly marked legacy and may only be
replayed without bypass. Column-scoped privileges keep the legacy discriminator server-controlled
and prevent post-insert mutation of security-sensitive fields while retaining the minimal UPDATE
privilege PostgreSQL requires for ``SELECT ... FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0081_pending_purge_authority"
down_revision: str | None = "0080_schema_index_design"
branch_labels: str | None = None
depends_on: str | None = None

_APP_ROLE = "easysynq_app"
# Bare token: Alembic applies the metadata ck naming convention (``ck_pending_blob_purge_…``);
# the previously passed full name doubled on live databases (the 0019/0079 lesson; repaired by
# 0089). The ORM mirror (pending_blob_purge.py) already used the bare token.
_AUTHORITY_CHECK = "authority_shape"
_WORM_REQUEST_FK = "fk_pending_blob_purge_worm_request"


def upgrade() -> None:
    op.add_column(
        "pending_blob_purge",
        sa.Column("record_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "pending_blob_purge",
        sa.Column("disposition_event_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "pending_blob_purge",
        sa.Column("worm_destroy_request_id", UUID(as_uuid=True), nullable=True),
    )
    # Do not add the true default until existing rows have been explicitly classified as legacy.
    op.add_column(
        "pending_blob_purge",
        sa.Column("authority_bound", sa.Boolean(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE pending_blob_purge SET authority_bound = false WHERE authority_bound IS NULL"
        )
    )
    op.alter_column(
        "pending_blob_purge",
        "authority_bound",
        existing_type=sa.Boolean(),
        server_default=sa.text("true"),
        nullable=False,
    )

    op.create_foreign_key(
        "fk_pending_blob_purge_record_id_record",
        "pending_blob_purge",
        "record",
        ["record_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_pending_blob_purge_disposition_event_id_disposition_event",
        "pending_blob_purge",
        "disposition_event",
        ["disposition_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        _WORM_REQUEST_FK,
        "pending_blob_purge",
        "worm_destroy_request",
        ["worm_destroy_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        _AUTHORITY_CHECK,
        "pending_blob_purge",
        """
        NOT authority_bound
        OR (
            record_id IS NOT NULL
            AND disposition_event_id IS NOT NULL
            AND (NOT bypass_governance OR worm_destroy_request_id IS NOT NULL)
        )
        """,
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                REVOKE INSERT, UPDATE ON pending_blob_purge FROM {_APP_ROLE};
                GRANT SELECT, DELETE ON pending_blob_purge TO {_APP_ROLE};
                GRANT INSERT (
                    id,
                    org_id,
                    sha256,
                    bucket,
                    object_key,
                    bypass_governance,
                    record_id,
                    disposition_event_id,
                    worm_destroy_request_id
                ) ON pending_blob_purge TO {_APP_ROLE};
                -- A locking SELECT needs UPDATE on at least one column; no service path mutates id.
                GRANT UPDATE (id) ON pending_blob_purge TO {_APP_ROLE};
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                REVOKE INSERT, UPDATE ON pending_blob_purge FROM {_APP_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON pending_blob_purge TO {_APP_ROLE};
            END IF;
        END $$;
        """
    )
    # Spelling-tolerant (see 0078's downgrade note): drop whichever spelling this database stores.
    op.execute(
        "ALTER TABLE pending_blob_purge DROP CONSTRAINT IF EXISTS "
        "ck_pending_blob_purge_authority_shape"
    )
    op.execute(
        "ALTER TABLE pending_blob_purge DROP CONSTRAINT IF EXISTS "
        "ck_pending_blob_purge_ck_pending_blob_purge_authority_shape"
    )
    op.drop_constraint(
        _WORM_REQUEST_FK,
        "pending_blob_purge",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_pending_blob_purge_disposition_event_id_disposition_event",
        "pending_blob_purge",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_pending_blob_purge_record_id_record",
        "pending_blob_purge",
        type_="foreignkey",
    )
    op.drop_column("pending_blob_purge", "authority_bound")
    op.drop_column("pending_blob_purge", "worm_destroy_request_id")
    op.drop_column("pending_blob_purge", "disposition_event_id")
    op.drop_column("pending_blob_purge", "record_id")
