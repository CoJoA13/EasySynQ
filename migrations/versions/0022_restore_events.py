"""RESTORE_* / UPGRADE_* event_type values — the operator-grade restore + upgrade CLIs
(slice S11, doc 18 §9, doc 12 §8.2 / R37).

S11 turns the S8b2 tested-restore drill (gate G-C, AC#5) into a live ``easysynq restore``
(WORM-aware, restore-to-verified-target: fresh non-WORM bucket, blob-snapshot alignment from the
archive manifest, the checkpoint-not-ahead tamper check, and a restored-chain re-verify) plus an
``easysynq upgrade`` (pre-backup → migrate → readiness health-gate). These eight events record that
trail (object_type ``config``).

Additive enum (the 0011-0021 precedent): ``ALTER TYPE event_type ADD VALUE`` is in-txn-safe on PG16
here (no row USES the values in this migration), and irreversible → a no-op enum downgrade (0001's
downgrade DROPs ``event_type`` wholesale, so the up↔down round-trip still passes). The Python
``EventType`` carries the eight members too (``_audit_enums.py``) so a from-scratch ``upgrade head``
— which rebuilds the type from ``EVENT_TYPE_VALUES`` — matches a migrated DB. No tables/columns →
``alembic check`` stays clean (the ``RESTORE_*`` audit rows are the record; no new schema needed).

Revision ID: 0022_restore_events
Revises: 0021_auditor_checklist_grant
Create Date: 2026-06-03
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_restore_events"
down_revision: str | None = "0021_auditor_checklist_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_EVENT_TYPES = (
    "RESTORE_STARTED",
    "RESTORE_VERIFIED",
    "RESTORE_FAILED",
    "RESTORE_CHECKPOINT_AHEAD",
    "RESTORE_CHECKPOINT_ACK",
    "UPGRADE_STARTED",
    "UPGRADE_COMPLETED",
    "UPGRADE_FAILED",
)


def upgrade() -> None:
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # The ADD VALUEs on event_type are irreversible in PostgreSQL → no-op (0001's downgrade DROPs
    # event_type wholesale, so the up↔down round-trip still passes). No tables/columns to drop.
    pass
