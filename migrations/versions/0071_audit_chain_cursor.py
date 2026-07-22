"""CR-2: the chain-linker safe-prefix cursor.

Adds ``audit_chain_cursor`` — a singleton the decoupled chain-linker OWNS so it can advance a proven
contiguous safe-``id``-prefix across ticks. The linker links each org's rows in id order only up to
``safe_watermark`` = the highest id whose predecessors are ALL decided (committed-visible or rolled
back), never linking a higher id ahead of a lower one still uncommitted in a long sweep (which would
permanently break ``verify_chain``'s id-order walk). ``stall_xmax``/``stall_ceiling`` carry the
two-snapshot rollback proof between ticks.

The linker role (``easysynq_linker``) is the sole reader/writer; new tables are default-REVOKEd
from it (migration 0010's ``ALTER DEFAULT PRIVILEGES``), so the ``SELECT/INSERT/UPDATE`` grant is
explicit. Seeded at the existing chained frontier so the fix is go-forward, not a re-walk of the
whole chain (fresh DB → ``MAX`` is NULL → 0, so the migrations CI job exercises the seed path).

⚠ QUIESCENCE. This migration runs in its own snapshot and CANNOT see in-flight (uncommitted) audit
rows, so audit writers must be quiesced across the upgrade — the norm for a schema migration. If a
long sweep is mid-flight while this runs, its low ids are invisible here and the seed is taken above
them; the go-forward linker still links each org in id order, but a row from a txn that spanned the
upgrade is the one case the seed alone cannot fence. Actively enforcing quiescence in the upgrade
orchestration (``services/upgrade.run_upgrade``, which today does pre-backup → alembic → health-gate
without pausing writers) is a tracked follow-up. Separately, a chain ALREADY reordered by the old
linker is pre-existing corruption that ``verify_chain`` already flags; the seed does not silently
repair it (a dedicated re-link remediation would) — out of scope for this go-forward fix.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0071_audit_chain_cursor"
down_revision: str | None = "0070_capa_overdue"
branch_labels: str | None = None
depends_on: str | None = None

LINKER_ROLE = "easysynq_linker"
APP_ROLE = "easysynq_app"


def upgrade() -> None:
    op.create_table(
        "audit_chain_cursor",
        # A singleton (id is always 1); no autoincrement so the ORM does not expect an IDENTITY.
        sa.Column("id", sa.SmallInteger(), autoincrement=False),
        # No server_default: the seed below and the linker's upsert always supply safe_watermark,
        # so there is no integer-default for alembic check to drift on.
        sa.Column("safe_watermark", sa.BigInteger(), nullable=False),
        # xid8 snapshot bounds (well below 2^63 for the DB's lifetime) — the rollback-proof marker.
        sa.Column("stall_xmax", sa.BigInteger(), nullable=True),
        sa.Column("stall_ceiling", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # Explicit PK name matching the ORM's naming convention (pk_%(table)s); a bare
        # primary_key=True would create audit_chain_cursor_pkey instead (0067 precedent).
        sa.PrimaryKeyConstraint("id", name="pk_audit_chain_cursor"),
    )
    # The cursor is linker-only. Migration 0010 GRANTs the app role DML on every FUTURE table via
    # ALTER DEFAULT PRIVILEGES, so on creation this table is already writable by easysynq_app — a
    # compromised app connection could then move/rewind safe_watermark and drive the linker to
    # reorder or stall the chain, defeating the role separation. Revoke the app role (and PUBLIC)
    # before granting the linker (CR-2 review, P1). Role-referencing statements are pg_roles-guarded
    # like the recent grant migrations (0063-0067); 0010 always creates both roles first, so this is
    # defensive consistency, not a functional requirement.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                EXECUTE 'REVOKE ALL ON audit_chain_cursor FROM {APP_ROLE}';
            END IF;
            EXECUTE 'REVOKE ALL ON audit_chain_cursor FROM PUBLIC';
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{LINKER_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON audit_chain_cursor TO {LINKER_ROLE}';
            END IF;
        END $$;
        """
    )
    # Seed the singleton at the current chained frontier (fresh DB → 0). Simple scalar seed — no
    # JSONB — so the COALESCE(MAX,0) path is safe on both a fresh and a populated database.
    op.execute(
        "INSERT INTO audit_chain_cursor (id, safe_watermark)"
        " SELECT 1, COALESCE(MAX(id), 0) FROM audit_event WHERE chained_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("audit_chain_cursor")
