"""seed: the ISO 9001:2015 clause catalog — the read-only clause spine (slice S9, doc 02 §2)

Idempotent. Seeds the ``clause`` reference tree (clauses 4-10 + sub-clauses) for the DEFAULT org's
seeded ``iso9001:2015`` framework, so documents can be mapped to clauses and the lifecycle
submit-review gate (≥1 clause_mapping) is satisfiable out of the box. The authoritative catalog data
lives in the reviewable, unit-tested ``easysynq_api.db.seeds.iso9001_clauses`` module (doc 02 §2/§2.1
/§3.2; the ★ set is Register R30, 20 rows incl. 8.5.6).

``clause`` is INSERT-by-seed only — there is no user-edit path (doc 07 §3.6). The self-referential
``parent_id`` is resolved in a second pass (insert all rows, then map each child to its parent by
clause number) so the tree (4 → 4.4 → 4.4.1) is built without ordering constraints.

Revision ID: 0018_seed_clauses
Revises: 0017_clause_ia
Create Date: 2026-06-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.seeds.iso9001_clauses import CLAUSES

revision: str = "0018_seed_clauses"
down_revision: str | None = "0017_clause_ia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Resolve the framework by its STABLE code, NOT a short_code='DEFAULT' org join: an OPERATIONAL
# install renames short_code away from 'DEFAULT' (the G-E gate requires it), and 0018 is the first
# seed migration that runs during an *upgrade of an already-finalized install* — a DEFAULT join would
# then return zero rows and abort the upgrade. Single-org per install (D1) + UNIQUE(org_id, code) make
# the code lookup unambiguous; scalar_one_or_none + skip keeps a not-yet-seeded install a clean no-op.
_FRAMEWORK_SQL = "SELECT id FROM framework WHERE code = 'iso9001:2015'"


def upgrade() -> None:
    bind = op.get_bind()
    framework_id = bind.execute(sa.text(_FRAMEWORK_SQL)).scalar_one_or_none()
    if framework_id is None:
        return  # framework not seeded (custom install) → nothing to seed

    clause_t = sa.table(
        "clause",
        sa.column("framework_id", postgresql.UUID(as_uuid=True)),
        sa.column("number", sa.Text),
        sa.column("title", sa.Text),
        sa.column("intent_text", sa.Text),
        sa.column("is_mandatory_star", sa.Boolean),
        sa.column("pdca_phase", postgresql.ENUM(name="pdca_phase", create_type=False)),
        sa.column("requirement_node", sa.Boolean),
    )
    rows = [
        {
            "framework_id": framework_id,
            "number": number,
            "title": title,
            "intent_text": intent_text,
            "is_mandatory_star": is_star,
            "pdca_phase": pdca_phase,
            "requirement_node": req_node,
        }
        for number, _parent, title, intent_text, is_star, pdca_phase, req_node in CLAUSES
    ]
    bind.execute(
        pg_insert(clause_t)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["framework_id", "number"])
    )

    # Second pass: resolve the self-referential parent_id by clause number.
    id_by_number = {
        number: cid
        for number, cid in bind.execute(
            sa.text("SELECT number, id FROM clause WHERE framework_id = :fw"),
            {"fw": framework_id},
        )
    }
    update = sa.text(
        "UPDATE clause SET parent_id = :pid WHERE framework_id = :fw AND number = :num"
    )
    for number, parent_number, *_rest in CLAUSES:
        if parent_number is not None:
            bind.execute(
                update,
                {"pid": id_by_number[parent_number], "fw": framework_id, "num": number},
            )


def downgrade() -> None:
    bind = op.get_bind()
    framework_id = bind.execute(sa.text(_FRAMEWORK_SQL)).scalar_one_or_none()
    if framework_id is None:
        return
    # Clear parent_id first (self-FK RESTRICT), then delete the seeded clauses.
    bind.execute(
        sa.text("UPDATE clause SET parent_id = NULL WHERE framework_id = :fw"),
        {"fw": framework_id},
    )
    bind.execute(sa.text("DELETE FROM clause WHERE framework_id = :fw"), {"fw": framework_id})
