"""Invalidate and purge sealed Evidence Pack derivatives under an R27 legal order.

Issue #361 closes the derivative-copy gap left by the original serve-time guard. A sealed pack can
now advance to terminal ``UNAVAILABLE`` with its artifact pointers cleared, while retaining the
header/seal as an audit tombstone. The source R27 disposition event is recorded on the pack and a
derived pack-record disposition event links back to it without fabricating another mutable destroy
request.

The new enum labels are additive and irreversible. The CHECK compares ``status::text`` rather than
constructing the newly-added enum value in this transaction, which keeps PostgreSQL's ADD VALUE
commit rule intact.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0082_pack_legal_erasure"
down_revision: str | None = "0081_pending_purge_authority"
branch_labels: str | None = None
depends_on: str | None = None

_PACK_EVENT_FK = "fk_evidence_pack_invalidation_event"
_DERIVED_EVENT_FK = "fk_disposition_event_derived_from_event"
_INVALIDATION_CHECK = "invalidation_shape"


def upgrade() -> None:
    # Additive enum values are not used as enum literals by any DML in this migration.
    op.execute("ALTER TYPE pack_status ADD VALUE IF NOT EXISTS 'UNAVAILABLE'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'PACK_INVALIDATED'")

    op.add_column(
        "disposition_event",
        sa.Column("derived_from_disposition_event_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        _DERIVED_EVENT_FK,
        "disposition_event",
        "disposition_event",
        ["derived_from_disposition_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "evidence_pack",
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evidence_pack",
        sa.Column("invalidated_by_disposition_event_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        _PACK_EVENT_FK,
        "evidence_pack",
        "disposition_event",
        ["invalidated_by_disposition_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        _INVALIDATION_CHECK,
        "evidence_pack",
        """
        (
            status::text = 'UNAVAILABLE'
            AND invalidated_at IS NOT NULL
            AND invalidated_by_disposition_event_id IS NOT NULL
            AND zip_blob_sha256 IS NULL
            AND portfolio_blob_sha256 IS NULL
        )
        OR (
            status::text <> 'UNAVAILABLE'
            AND invalidated_at IS NULL
            AND invalidated_by_disposition_event_id IS NULL
        )
        """,
    )


def downgrade() -> None:
    # A rollback cannot restore erased bytes. Keep those packs failed/undeliverable rather than
    # leaving a status label an older application cannot deserialize.
    op.drop_constraint(_INVALIDATION_CHECK, "evidence_pack", type_="check")
    op.execute(
        """
        UPDATE evidence_pack
        SET status = 'FAILED',
            error = COALESCE(
                error,
                'Pack invalidated by legal erasure; erased artifacts were not restored'
            )
        WHERE status::text = 'UNAVAILABLE'
        """
    )
    op.drop_constraint(_PACK_EVENT_FK, "evidence_pack", type_="foreignkey")
    op.drop_column("evidence_pack", "invalidated_by_disposition_event_id")
    op.drop_column("evidence_pack", "invalidated_at")
    op.drop_constraint(_DERIVED_EVENT_FK, "disposition_event", type_="foreignkey")
    op.drop_column("disposition_event", "derived_from_disposition_event_id")
    # PostgreSQL cannot remove individual enum labels. The base enum types are rebuilt from the
    # ORM value tuples only when the original enum-owning migration is downgraded.
