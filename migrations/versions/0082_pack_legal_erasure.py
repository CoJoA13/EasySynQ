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
from sqlalchemy.dialects.postgresql import JSONB, UUID

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
    # Exact dossier dependency snapshot for new seals. Existing sealed packs remain NULL and use
    # the generated_at-bounded compatibility resolver; DRAFT/FAILED rows write the snapshot when
    # they eventually seal.
    op.add_column(
        "evidence_pack",
        sa.Column("embedded_record_ids_at_seal", JSONB(), nullable=True),
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
    # Refuse before mutating anything when rollback would discard the only durable information an
    # older application needs to finish legal erasure, or expose an enum value that it cannot
    # deserialize. Operators must reap derived work and retain 0082-compatible application code
    # once an immutable PACK_INVALIDATED event exists.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pending_blob_purge AS purge
                JOIN disposition_event AS event
                  ON event.id = purge.disposition_event_id
                WHERE event.derived_from_disposition_event_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0082: derived pending blob purges must be reaped first';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM audit_event
                WHERE event_type::text = 'PACK_INVALIDATED'
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0082: PACK_INVALIDATED audit events require compatible code';
            END IF;
        END $$;
        """
    )

    # A rollback cannot restore erased bytes. SEALED is the only pre-0082 terminal state, so retain
    # the missing artifact pointers and disposed pack Record as an undeliverable tombstone without
    # exposing the retry loop that FAILED would permit.
    op.drop_constraint(_INVALIDATION_CHECK, "evidence_pack", type_="check")
    op.execute(
        """
        UPDATE evidence_pack
        SET status = 'SEALED',
            error = COALESCE(
                error,
                'Pack invalidated by legal erasure; erased artifacts were not restored'
            )
        WHERE status::text = 'UNAVAILABLE'
        """
    )
    op.drop_constraint(_PACK_EVENT_FK, "evidence_pack", type_="foreignkey")
    op.drop_column("evidence_pack", "invalidated_by_disposition_event_id")
    op.drop_column("evidence_pack", "embedded_record_ids_at_seal")
    op.drop_column("evidence_pack", "invalidated_at")
    op.drop_constraint(_DERIVED_EVENT_FK, "disposition_event", type_="foreignkey")
    op.drop_column("disposition_event", "derived_from_disposition_event_id")
    # PostgreSQL cannot remove individual enum labels. The base enum types are rebuilt from the
    # ORM value tuples only when the original enum-owning migration is downgraded.
