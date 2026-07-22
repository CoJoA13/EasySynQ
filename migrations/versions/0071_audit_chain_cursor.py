"""CR-2: the chain-linker safe-prefix cursor.

Adds ``audit_chain_cursor`` — a singleton the decoupled chain-linker OWNS so it can advance a proven
contiguous safe-``id``-prefix across ticks. The linker links each org's rows in id order only up to
``safe_watermark`` = the highest id whose predecessors are ALL decided (committed-visible or rolled
back), never linking a higher id ahead of a lower one still uncommitted in a long sweep (which would
permanently break ``verify_chain``'s id-order walk). ``stall_xmax``/``stall_ceiling`` carry the
two-snapshot rollback proof between ticks.

The linker role (``easysynq_linker``) is the sole reader/writer; new tables are default-REVOKEd from
it (migration 0010's ``ALTER DEFAULT PRIVILEGES``), so the ``SELECT/INSERT/UPDATE`` grant is explicit.
Seeded at the existing chained frontier so the fix applies go-forward without re-walking the whole
existing chain (fresh DB → ``MAX`` is NULL → 0, so the migrations CI job exercises the seed path).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0071_audit_chain_cursor"
down_revision: str | None = "0070_capa_overdue"
branch_labels: str | None = None
depends_on: str | None = None

LINKER_ROLE = "easysynq_linker"


def upgrade() -> None:
    op.create_table(
        "audit_chain_cursor",
        # A singleton (id is always 1); no autoincrement so the ORM does not expect an IDENTITY.
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        # No server_default: the seed below and the linker's upsert always supply safe_watermark,
        # so there is no integer-default for alembic check to drift on.
        sa.Column("safe_watermark", sa.BigInteger(), nullable=False),
        # xid8 snapshot bounds (well below 2^63 for the DB's lifetime) — the rollback-proof marker.
        sa.Column("stall_xmax", sa.BigInteger(), nullable=True),
        sa.Column("stall_ceiling", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON audit_chain_cursor TO {LINKER_ROLE}")
    # Seed the singleton at the current chained frontier (fresh DB → 0). Simple scalar seed — no
    # JSONB — so the COALESCE(MAX,0) path is safe on both a fresh and a populated database.
    op.execute(
        "INSERT INTO audit_chain_cursor (id, safe_watermark)"
        " SELECT 1, COALESCE(MAX(id), 0) FROM audit_event WHERE chained_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("audit_chain_cursor")
