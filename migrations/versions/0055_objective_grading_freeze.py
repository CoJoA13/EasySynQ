"""S-obj-freeze (doc 14 §6, R44): freeze the FULL grading basis on each ``kpi_measurement``.

Only ``target_at_capture`` was frozen per reading; ``direction`` + ``at_risk_threshold`` were read
LIVE from the objective's governing commitment at serialize time, so a commitment revision that
flipped the direction or moved the amber band retroactively re-graded every historical reading (a
past green could read red). This adds the two missing frozen columns so the ENTIRE verdict basis is
snapshotted at capture.

Additive, backend-only. Two new ``kpi_measurement`` columns:
  - ``direction_at_capture`` (existing ``objective_direction`` enum, REUSED — ``create_type=False``;
    no ADD VALUE, so no ``autocommit_block``): added nullable → backfilled → ``SET NOT NULL`` (the
    3-step pattern so a *populated* DB doesn't abort — the 0023 fresh-DB blind-spot lesson).
  - ``at_risk_threshold_at_capture`` (Numeric, nullable — mirrors the nullable
    ``at_risk_threshold``; null = no amber band).

Backfill mirrors ``resolve_commitment`` EXACTLY (the read-back switch): each existing reading
freezes the GOVERNING Effective commitment's direction/threshold where one exists, else the working
``quality_objective`` row — i.e. the value it currently grades against, so the backfill is
behaviour-preserving (and fixes the re-grade hole for historical rows too). ``SET NOT NULL`` on
``direction_at_capture`` is safe: every existing row already has a NOT-NULL ``target_at_capture``
which ``record_measurement`` computes from the objective row, so every row provably has an
``objective_id`` → the backfill (keyed on ``objective_id``) covers all of them.

⚠ ``kpi_measurement`` carries ``REVOKE UPDATE,DELETE`` for the app role (0049); this backfill UPDATE
runs as the migration OWNER role (``DATABASE_URL_SYNC``), which is not subject to that REVOKE. The
WORM invariant is unchanged — no app path ever updates the new columns post-insert.

Downgrade: drop both columns (the frozen basis is regenerable-from-governing; no data dependency).

Revision ID: 0055_objective_grading_freeze
Revises: 0054_leadership_authorization
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055_objective_grading_freeze"
down_revision: str | None = "0054_leadership_authorization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The behaviour-preserving backfill — the SQL twin of resolve_commitment: the governing Effective
# commitment when one exists, else the working-row fields. BOTH columns gate on the SAME predicate
# (an Effective version whose snapshot carries an OBJECT ``objective_commitment`` fold), which
# mirrors the runtime ``governing = raw if isinstance(raw, dict) else None`` guard (service.py): a
# version present but fold absent / JSON-null / non-object falls back to the working row for BOTH
# columns. (A version-pointer-only gate would NULL a threshold the live grader still used — LESS
# defensive than the runtime it mirrors.) With the fold present, ``->>'at_risk_threshold'`` yields
# SQL NULL for a JSON null (no amber band) → a NULL Numeric, exactly "governing has no band".
_BACKFILL = """
    UPDATE kpi_measurement AS km
    SET direction_at_capture = (
            CASE
                WHEN di.current_effective_version_id IS NOT NULL
                     AND jsonb_typeof(dv.metadata_snapshot -> 'objective_commitment') = 'object'
                     AND dv.metadata_snapshot -> 'objective_commitment' ->> 'direction' IS NOT NULL
                THEN dv.metadata_snapshot -> 'objective_commitment' ->> 'direction'
                ELSE qo.direction::text
            END
        )::objective_direction,
        at_risk_threshold_at_capture = (
            CASE
                WHEN di.current_effective_version_id IS NOT NULL
                     AND jsonb_typeof(dv.metadata_snapshot -> 'objective_commitment') = 'object'
                THEN (
                    dv.metadata_snapshot -> 'objective_commitment' ->> 'at_risk_threshold'
                )::numeric
                ELSE qo.at_risk_threshold
            END
        )
    FROM quality_objective AS qo
    JOIN documented_information AS di ON di.id = qo.id
    LEFT JOIN document_version AS dv ON dv.id = di.current_effective_version_id
    WHERE km.objective_id = qo.id
"""


def upgrade() -> None:
    direction = postgresql.ENUM(name="objective_direction", create_type=False)
    # 1. Add nullable (so the backfill can populate before the NOT NULL is enforced).
    op.add_column(
        "kpi_measurement",
        sa.Column("direction_at_capture", direction, nullable=True),
    )
    op.add_column(
        "kpi_measurement",
        sa.Column("at_risk_threshold_at_capture", sa.Numeric(), nullable=True),
    )
    # 2. Behaviour-preserving backfill (governing-else-working — the resolve_commitment twin).
    op.execute(_BACKFILL)
    # 3. Enforce NOT NULL on the direction (every reading has a governing/working direction).
    op.alter_column("kpi_measurement", "direction_at_capture", nullable=False)


def downgrade() -> None:
    op.drop_column("kpi_measurement", "at_risk_threshold_at_capture")
    op.drop_column("kpi_measurement", "direction_at_capture")
