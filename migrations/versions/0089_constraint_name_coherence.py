"""Normalize doubled CHECK-constraint names on existing installations (the 0079 pattern).

Migrations 0078, 0081, and 0088 passed already-``ck_``-prefixed names through the metadata naming
convention, so deployed databases carry doubled physical names while the ORM declares the canonical
single names:

- ``record``: ``ck_record_ck_record_content_hash_version_supported``
- ``pending_blob_purge``: ``ck_pending_blob_purge_ck_pending_blob_purge_authority_shape``
- ``system_config``: the doubled 72-char name additionally hash-truncated by SQLAlchemy under
  PostgreSQL's 63-char identifier cap (``ck_system_config_ck_system_config_bootstrap_credential_…``
  with an opaque 4-hex suffix), so it is matched by prefix, never by a hard-coded literal.

The three historical migrations are corrected to the bare tokens in the same change, so a fresh
chain creates the canonical names directly; this revision repairs databases migrated before the
correction. Per 0079: rename the legacy spelling, tolerate an already-correct database, collapse a
harmless duplicate if both names exist, and recreate the invariant if it is absent entirely.

The downgrade is deliberately retained: reverting a constraint's spelling would reintroduce the
ORM mismatch without changing schema semantics. The corrected historical downgrades drop BOTH
spellings tolerantly, so a pre-repair database (doubled names, stamped at or below 0088) can still
roll back through 0078/0081/0088 without upgrading through this revision first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0089_constraint_name_coherence"
down_revision: str | None = "0088_bootstrap_credential"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, canonical physical name, bare token, CHECK body — byte-matching the owning migration)
_REPAIRS: tuple[tuple[str, str, str, str], ...] = (
    (
        "record",
        "ck_record_content_hash_version_supported",
        "content_hash_version_supported",
        "content_hash_version IN (1, 2)",
    ),
    (
        "pending_blob_purge",
        "ck_pending_blob_purge_authority_shape",
        "authority_shape",
        """
        NOT authority_bound
        OR (
            record_id IS NOT NULL
            AND disposition_event_id IS NOT NULL
            AND (NOT bypass_governance OR worm_destroy_request_id IS NOT NULL)
        )
        """,
    ),
    (
        "system_config",
        "ck_system_config_bootstrap_credential_receipt_hash_hex",
        "bootstrap_credential_receipt_hash_hex",
        "bootstrap_credential_receipt_hash IS NULL OR "
        "bootstrap_credential_receipt_hash ~ '^[0-9a-f]{64}$'",
    ),
)


def _check_names(table: str) -> set[str]:
    bind = op.get_bind()
    return set(
        bind.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = CAST(:table_name AS regclass) AND contype = 'c'"
            ),
            {"table_name": table},
        ).scalars()
    )


def _normalize(table: str, canonical: str, bare_token: str, body: str) -> None:
    names = _check_names(table)
    # The doubled spelling starts with the canonical name repeated; system_config's is further
    # hash-truncated, so match legacy names by their doubled prefix instead of a stored literal.
    doubled_prefix = f"ck_{table}_ck_{table}_"
    # The identifier-charset guard keeps the raw RENAME/DROP interpolation closed even against a
    # hand-crafted quoted identifier that mimics the doubled prefix (requires DDL rights anyway).
    legacy_names = sorted(
        n
        for n in names
        if n.startswith(doubled_prefix)
        and all(c.isascii() and (c.isalnum() or c == "_") for c in n)
    )
    if canonical in names:
        for legacy in legacy_names:
            # The legacy spelling is already the physical name — mark it convention-final so
            # Alembic does not prepend the ck prefix a third time (the 0079 precedent).
            op.drop_constraint(op.f(legacy), table, type_="check")
    elif legacy_names:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {legacy_names[0]} TO {canonical}")
        for legacy in legacy_names[1:]:
            op.drop_constraint(op.f(legacy), table, type_="check")
    else:
        # A manually renamed/dropped constraint should not leave the invariant absent.
        op.create_check_constraint(bare_token, table, body)


def upgrade() -> None:
    for table, canonical, bare_token, body in _REPAIRS:
        _normalize(table, canonical, bare_token, body)


def downgrade() -> None:
    # Name-coherence repair: intentionally irreversible (see module docstring).
    pass
