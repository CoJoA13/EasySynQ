"""Move clause 7 (Support) and its whole subtree to the DO PDCA phase (R62).

The seeded catalog split clause 7 across PLAN (the 7.1-7.4 resourcing subtree) and DO (7.5).
ISO 9001:2015's own PDCA figure places Support wholly in Do; R62 corrects the catalog. Fresh
installs seed the corrected values through 0018 (its data module now carries DO); this
migration flips the thirteen rows a live install already holds. ``clause`` is INSERT-by-seed
only (doc 07 §3.6) — overwriting pdca_phase/intent_text cannot clobber user data.

Revision ID: 0084_clause7_support_do
Revises: 0083_pack_build_principal
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0084_clause7_support_do"
down_revision: str | None = "0083_pack_build_principal"
branch_labels: str | None = None
depends_on: str | None = None

# Resolve the framework by its STABLE code (the 0018 pattern) — an operational install renames
# the org short_code, so a DEFAULT-org join would silently no-op the flip on exactly the
# installs this migration exists for.
_FRAMEWORK_SQL = "SELECT id FROM framework WHERE code = 'iso9001:2015'"

_FLIPPED = (
    "7",
    "7.1",
    "7.1.1",
    "7.1.2",
    "7.1.3",
    "7.1.4",
    "7.1.5",
    "7.1.5.1",
    "7.1.5.2",
    "7.1.6",
    "7.2",
    "7.3",
    "7.4",
)
# The clause-7 header intent, verbatim from db/seeds/iso9001_clauses.py at each side of R62
# (frozen ISO-text reference data carries en-dashes — the seed module's noqa precedent).
_NEW_INTENT_7 = (
    "Resources, competence, awareness, communication, and the control of documented "
    "information itself. Section header for clauses 7.1–7.5."  # noqa: RUF001
)
_OLD_INTENT_7 = (
    "Resources, competence, awareness, communication, and the control of documented "
    "information itself. Section header for clauses 7.1–7.5; split across PLAN (resourcing) "  # noqa: RUF001
    "and DO (operating/document control)."
)


def _set_phase(phase: str, intent_7: str) -> None:
    bind = op.get_bind()
    framework_id = bind.execute(sa.text(_FRAMEWORK_SQL)).scalar_one_or_none()
    if framework_id is None:
        return  # framework not seeded (custom install) → nothing to flip
    bind.execute(
        sa.text(
            "UPDATE clause SET pdca_phase = CAST(:phase AS pdca_phase) "
            "WHERE framework_id = :fw AND number IN :numbers"
        ).bindparams(sa.bindparam("numbers", expanding=True)),
        {"phase": phase, "fw": framework_id, "numbers": list(_FLIPPED)},
    )
    bind.execute(
        sa.text(
            "UPDATE clause SET intent_text = :intent WHERE framework_id = :fw AND number = '7'"
        ),
        {"intent": intent_7, "fw": framework_id},
    )


def upgrade() -> None:
    _set_phase("DO", _NEW_INTENT_7)


def downgrade() -> None:
    _set_phase("PLAN", _OLD_INTENT_7)
