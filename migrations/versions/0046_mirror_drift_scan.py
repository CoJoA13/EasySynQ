"""mirror drift scan: the D2+D3 baseline + scan-summary tables (S-drift-2)

The thesis slice of the v1.x drift family (doc 05 §9.1 D2/D3, §9.2, R11). NO new permission key
(the scan is a system op; the admin read surface is S-drift-3); NO endpoint change.

1. **Two fresh enums** (CREATE TYPE → usable same-txn): ``drift_scan_kind`` (MIRROR; S-drift-3 adds
   BLOB_REHASH additively) + ``drift_scan_status`` (CLEAN/DIVERGENT/FAILED). Values from the ORM
   ``*_VALUES``.
2. **mirror_build** — the PG-persisted per-build manifest, the scan's expected-state authority
   (the on-disk manifest.json is never trusted; verified byte-wise via ``manifest_sha256``).
   Keyed UNIQUE(build_name) = the ``.builds/<hex>`` dir name. Mutable registry (SELECT/INSERT/
   DELETE — the keep-last-20 prune), NOT append-only.
3. **drift_scan** — one summary row per scan (doc 05 §9.2 "write scan summary"); write-once by code
   (SELECT/INSERT).
4. **event_type** += MIRROR_STALE, MIRROR_TAMPER (additive ADD VALUE; no-op downgrade — the 0011
   pattern).

Downgrade: drop both tables; DROP the two fresh enums; event values stay (PG cannot remove them).

Revision ID: 0046_mirror_drift_scan
Revises: 0045_periodic_review
Create Date: 2026-06-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._drift_enums import (
    DRIFT_SCAN_KIND_VALUES,
    DRIFT_SCAN_STATUS_VALUES,
)

revision: str = "0046_mirror_drift_scan"
down_revision: str | None = "0045_periodic_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = ("MIRROR_STALE", "MIRROR_TAMPER")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Event types (IF NOT EXISTS → idempotent; not used by any row in this txn).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 2. The fresh enums (CREATE TYPE → usable same-txn). Tuples from the ORM *_VALUES.
    postgresql.ENUM(*DRIFT_SCAN_KIND_VALUES, name="drift_scan_kind").create(bind, checkfirst=True)
    postgresql.ENUM(*DRIFT_SCAN_STATUS_VALUES, name="drift_scan_status").create(
        bind, checkfirst=True
    )
    kind_t = postgresql.ENUM(name="drift_scan_kind", create_type=False)
    status_t = postgresql.ENUM(name="drift_scan_status", create_type=False)

    # 3. mirror_build — the vault-side expected-state baseline.
    op.create_table(
        "mirror_build",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("build_name", sa.Text(), nullable=False),
        sa.Column(
            "built_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("swapped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_sha256", sa.Text(), nullable=False),
        sa.Column("documents", sa.Integer(), nullable=False),
        sa.Column("files", sa.Integer(), nullable=False),
        sa.Column("symlinks", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_mirror_build_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mirror_build"),
        sa.UniqueConstraint("build_name", name="uq_mirror_build_build_name"),
    )

    # 4. drift_scan — the per-scan summary (doc 05 §9.2).
    op.create_table(
        "drift_scan",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", kind_t, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", status_t, nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_drift_scan_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_drift_scan"),
    )
    op.create_index("ix_drift_scan_kind_started_at", "drift_scan", ["kind", "started_at"])

    # 5. Least-privilege grants (pg_roles-guarded — the 0042 pattern). mirror_build needs DELETE
    # for the keep-last-20 prune + UPDATE for the post-swap/self-heal ``swapped_at`` stamp.
    # drift_scan is write-once: the GRANT alone doesn't enforce that, because 0010 set ALTER
    # DEFAULT PRIVILEGES granting SELECT,INSERT,UPDATE,DELETE on future public tables to the app
    # role — so REVOKE UPDATE,DELETE explicitly, making the insert-only summary actually immutable
    # at the DB layer (a compromised worker / app-side SQL bug can't rewrite or delete summaries;
    # Codex P2). Not the tamper-evident record (that's the hash-chained audit_event), but a sound
    # least-privilege posture for an operational integrity table.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON mirror_build TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON drift_scan TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON drift_scan FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_drift_scan_kind_started_at", table_name="drift_scan")
    op.drop_table("drift_scan")
    op.drop_table("mirror_build")
    op.execute("DROP TYPE IF EXISTS drift_scan_status")
    op.execute("DROP TYPE IF EXISTS drift_scan_kind")
    # Event values: deliberate no-op (PG cannot remove an enum value; the 0011 precedent).
