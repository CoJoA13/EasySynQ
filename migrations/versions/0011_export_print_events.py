"""event_type += EXPORTED, PRINTED — the controlled-copy export/print intent trail (slice S7d).

S7d serves a fresh, per-request "UNCONTROLLED IF PRINTED" / "CONTROLLED COPY" rendition on
``GET /documents/{id}/export`` + ``/print`` (doc 04 §11.2) and audits the intent (who/when/which
version). Those rows need two new ``event_type`` values. ``event_type`` is the one *extensible*
audit enum (doc 12 §4.2 / ``_audit_enums.py``); this is the **first** additive-enum migration in the
repo — every prior enum was created whole.

Shape notes (no in-repo template existed):

* ``ALTER TYPE … ADD VALUE`` is allowed inside a transaction on PG ≥ 12 (the stack is PG 16) **as
  long as the new value is not USED in the same transaction** — so this migration only adds the
  labels and inserts no row that references them (the rows are written at request time by the app).
  ``IF NOT EXISTS`` keeps it idempotent / re-run-safe.
* ``ADD VALUE`` is **irreversible** in PostgreSQL (a label cannot be dropped), so ``downgrade`` is a
  deliberate no-op. This is safe under CI's ``upgrade head → downgrade base → upgrade head`` gate
  because ``0010``'s own downgrade ``DROP TYPE``s ``event_type`` wholesale — ``downgrade base`` wipes
  the type regardless, and the round-trip back up recreates it from ``EVENT_TYPE_VALUES`` (which now
  includes both new labels).

The Python ``EventType`` enum carries ``EXPORTED``/``PRINTED`` too (``_audit_enums.py``) — mandatory,
not cosmetic: a from-scratch ``upgrade head`` builds the type from ``EVENT_TYPE_VALUES`` (not via
``ADD VALUE``), and the in-transaction audit writer does ``EventType(label)`` at write time. The
hash chain is unaffected: ``canonical_serialize`` v1 hashes ``event_type`` as its string value, so
new labels hash their own bytes and the golden vector still passes (no version bump).

Revision ID: 0011_export_print_events
Revises: 0010_audit
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_export_print_events"
down_revision: str | None = "0010_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literal, audited list — kept verbatim in sync with the new EventType members in _audit_enums.py.
_NEW_EVENT_TYPES: tuple[str, ...] = ("EXPORTED", "PRINTED")


def upgrade() -> None:
    for value in _NEW_EVENT_TYPES:
        # IF NOT EXISTS → idempotent; no row uses the value in this txn (PG16 in-txn ADD VALUE rule).
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Deliberate no-op: PostgreSQL cannot remove an enum value. The CI round-trip still passes
    # because 0010's downgrade DROP TYPEs event_type entirely (see module docstring).
    pass
