"""D1 blob verify + the admin drift surface: BLOB_REHASH kind + BLOB_INTEGRITY_FAILED + drift.read

Slice S-drift-3 (doc 03 §8.2, doc 05 §9.1 rows D1/D4, doc 07 §3.9, R38/R41) — no new tables:

1. **drift_scan_kind += BLOB_REHASH** — the S-drift-2 spec's declared seam (additive ADD VALUE,
   no-op downgrade; a from-scratch ``upgrade head`` rebuilds the type from the ORM
   DRIFT_SCAN_KIND_VALUES, which already carries the member).
2. **event_type += BLOB_INTEGRITY_FAILED** — the D1 mismatch alarm (one event type, owner fork;
   classification rides the payload).
3. **blob.verify_failed_at** (nullable timestamptz) — the D1 alarm LATCH: set on a finding,
   cleared on a passing re-hash, and sorted FIRST in the rotation sample. Without it a
   once-stamped-then-corrupted blob sorts behind every never-verified row, so a NULL-cursor
   influx larger than the sample size crowds a detected-but-unresolved corruption out of the
   daily sample and the latest-per-kind status read goes CLEAN over it (the diff-critic MAJOR).
   ``verified_at`` keeps its pure "last verified OK" meaning (the 0005 column).
4. **R38/R41: the drift.read SYSTEM-domain key** (is_system_domain=true, sod_sensitive=false,
   sig_hook=false, finest_scope=SYSTEM) + one role_grant to System Administrator. ⚠ Org lookup =
   the RESILIENT pattern (scalar_one_or_none on 'DEFAULT' + a fall-back to the only org — a
   deliberately SOFTENED variant of the 0043/0045 ``scalar_one()`` recipe, PR #107): setup G-E
   renames the short_code, so a DEFAULT-only lookup (the 0028 shape) silently skips an
   operational install. If the fallback finds ≠1 org, the GRANT is skipped (permission still
   seeded) — never abort the upgrade; a missing grant is operator-recoverable, unlike a
   load-bearing workflow seed (0045), where skipping would be wrong.

Neither new enum value is used by a row in THIS migration (the PG16 in-txn rule is satisfied).
Downgrade: role_grant rows BEFORE the permission row (the RESTRICT FK); the ADD VALUEs are
irreversible in PostgreSQL → no-op (0001/0046 drop the types wholesale, so up↔down still passes).

Revision ID: 0047_blob_verify_drift_read
Revises: 0046_mirror_drift_scan
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0047_blob_verify_drift_read"
down_revision: str | None = "0046_mirror_drift_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_KEY = "drift.read"


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Additive enum values (IF NOT EXISTS → idempotent; not used by any row in this txn).
    op.execute("ALTER TYPE drift_scan_kind ADD VALUE IF NOT EXISTS 'BLOB_REHASH'")
    op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BLOB_INTEGRITY_FAILED'")

    # 2. The D1 alarm latch (nullable, no default — NULL = never failed; the app role already
    # holds UPDATE on blob, the verified_at precedent).
    op.add_column(
        "blob", sa.Column("verify_failed_at", sa.DateTime(timezone=True), nullable=True)
    )

    # 3. R38/R41: seed the drift.read SYSTEM key (idempotent).
    permission_t = sa.table(
        "permission",
        sa.column("key", sa.Text),
        sa.column("resource", sa.Text),
        sa.column("action", sa.Text),
        sa.column("is_system_domain", sa.Boolean),
        sa.column("sod_sensitive", sa.Boolean),
        sa.column("sig_hook", sa.Boolean),
        sa.column("finest_scope", postgresql.ENUM(name="scope_level", create_type=False)),
    )
    bind.execute(
        pg_insert(permission_t)
        .values(
            [
                {
                    "key": _NEW_KEY,
                    "resource": "drift",
                    "action": "read",
                    "is_system_domain": True,
                    "sod_sensitive": False,
                    "sig_hook": False,
                    "finest_scope": "SYSTEM",
                }
            ]
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )

    # 4. Grant to System Administrator — resilient org lookup (#107: setup G-E renames the
    # short_code, so DEFAULT-only would skip an operational install). ⚠ Deliberately SOFTER than
    # the 0043/0045 ``scalar_one()`` recipe: on ≠1 orgs the GRANT is skipped (the permission row
    # is still seeded) rather than aborting — a missing role grant is operator-recoverable
    # (grant-role CLI / authz admin), unlike 0045's workflow seed whose absence 500s a sweep.
    # Do NOT copy this softened form for a seed whose absence is load-bearing.
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_rows = bind.execute(sa.text("SELECT id FROM organization")).fetchall()
        org_id = org_rows[0][0] if len(org_rows) == 1 else None
    if org_id is not None:
        perm_id = bind.execute(
            sa.text("SELECT id FROM permission WHERE key = :k"), {"k": _NEW_KEY}
        ).scalar_one()
        role_id = bind.execute(
            sa.text("SELECT id FROM role WHERE org_id = :o AND name = 'System Administrator'"),
            {"o": org_id},
        ).scalar_one_or_none()
        if role_id is not None:
            role_grant_t = sa.table(
                "role_grant",
                sa.column("org_id", postgresql.UUID(as_uuid=True)),
                sa.column("role_id", postgresql.UUID(as_uuid=True)),
                sa.column("permission_id", postgresql.UUID(as_uuid=True)),
                sa.column("scope_template", postgresql.JSONB),
            )
            bind.execute(
                pg_insert(role_grant_t)
                .values(
                    [
                        {
                            "org_id": org_id,
                            "role_id": role_id,
                            "permission_id": perm_id,
                            "scope_template": {"level": "SYSTEM"},
                        }
                    ]
                )
                .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
            )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_column("blob", "verify_failed_at")
    # role_grant BEFORE permission (the RESTRICT FK) so a populated-DB downgrade does not abort.
    bind.execute(
        sa.text(
            "DELETE FROM role_grant WHERE permission_id IN "
            "(SELECT id FROM permission WHERE key = :k)"
        ),
        {"k": _NEW_KEY},
    )
    bind.execute(sa.text("DELETE FROM permission WHERE key = :k"), {"k": _NEW_KEY})
    # The two ADD VALUEs are irreversible in PostgreSQL → no-op (0001/0046 DROP the types
    # wholesale, so the up↔down round-trip still passes; a re-upgrade rebuilds from the ORM
    # *_VALUES).
