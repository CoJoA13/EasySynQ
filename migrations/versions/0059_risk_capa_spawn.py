"""S-risk-3 (clause 6.1, R49 §7): the Risk → CAPA treatment-spawn seam.

Two additive native-enum values for the one-click "treat this risk → spawn a CAPA" seam. There is
**no new table or column** — ``risk_opportunity.linked_capa_id`` (the latch) already exists from
0058, and the ``linked_capa_id → capa.id`` RESTRICT FK gates no erasure path (a CAPA is never
hard-deleted) — so the only change is the two enum values:

- ``capa_source`` += ``risk`` — the CAPA's origin tag (a CAPA spawned to treat a risk_opportunity
  row).
- ``event_type`` += ``RISK_SPAWNED_CAPA`` — the risk-side audit of the spawn (the CAPA emits its own
  ``CAPA_RAISED`` on the record side; this trails on the register head, the
  ``COMPLAINT_SPAWNED_CAPA`` / ``RISK_RESCORED`` shape).

Both are ``ALTER TYPE … ADD VALUE`` (the 0010/0058 additive pattern): an ADD VALUE cannot run inside
a transaction block → the ``autocommit_block``; ``IF NOT EXISTS`` → idempotent; irreversible in PG →
a no-op downgrade. The matching Python members live in ``_capa_enums.CapaSource`` /
``_audit_enums.EventType`` (a from-scratch ``upgrade head`` rebuilds each type from the ORM
``*_VALUES`` tuple, so the members must be present there too). No schema/table change → ``alembic
check`` stays clean.

Revision ID: 0059_risk_capa_spawn
Revises: 0058_risk_register
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0059_risk_capa_spawn"
down_revision: str | None = "0058_risk_register"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Hard-coded (not the full *_VALUES tuple): an ADD VALUE adds only the NEW member, vs CREATE TYPE
# which rebuilds the whole enum from the ORM tuple (the 0058 _NEW_EVENT_TYPES precedent).
_NEW_CAPA_SOURCES = ("risk",)
_NEW_EVENT_TYPES = ("RISK_SPAWNED_CAPA",)


def upgrade() -> None:
    # ADD VALUE must run outside a transaction block (the autocommit_block; matches 0058/0049).
    with op.get_context().autocommit_block():
        for value in _NEW_CAPA_SOURCES:
            op.execute(f"ALTER TYPE capa_source ADD VALUE IF NOT EXISTS '{value}'")
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # ALTER TYPE … ADD VALUE is irreversible in PostgreSQL → no-op (the 0011/0049/0058 precedent).
    pass
