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

# The CAPTURE-TIME backfill (Codex P1/P2): for each EXISTING reading, freeze the commitment that
# was the CURRENT effective version at its ``created_at`` — what ``record_measurement`` froze (it
# reads ``current_effective_version_id``, which only moves at ``_cutover``). So a version's
# governing window runs from when the pointer ACTUALLY moved to it: the PRIOR version's
# ``effective_to`` (stamped at the real cutover), or its own ``effective_from`` if first, up to its
# own ``effective_to``. Using bare ``effective_from`` would mis-seal a reading taken in the gap
# between a SCHEDULED future ``effective_from`` and the later ``release_due`` sweep that cuts over
# (the pointer was still on the prior version then). BOTH columns gate on the same OBJECT-fold
# predicate (the runtime ``isinstance(raw, dict)`` guard): no governing version (pre-first-release)
# or a fold-less version falls back to the working row for BOTH; a present fold with a JSON null
# threshold → NULL (no amber band).
_BACKFILL = """
    UPDATE kpi_measurement AS km
    SET direction_at_capture = (
            CASE
                WHEN g.commitment IS NOT NULL
                     AND jsonb_typeof(g.commitment) = 'object'
                     AND g.commitment ->> 'direction' IS NOT NULL
                THEN g.commitment ->> 'direction'
                ELSE g.working_direction
            END
        )::objective_direction,
        at_risk_threshold_at_capture = (
            CASE
                WHEN g.commitment IS NOT NULL AND jsonb_typeof(g.commitment) = 'object'
                THEN (g.commitment ->> 'at_risk_threshold')::numeric
                ELSE g.working_threshold
            END
        )
    FROM (
        SELECT
            m.id AS km_id,
            qo.direction::text AS working_direction,
            qo.at_risk_threshold AS working_threshold,
            (
                SELECT dv.metadata_snapshot -> 'objective_commitment'
                FROM document_version AS dv
                LEFT JOIN document_version AS prv ON prv.superseded_by_version_id = dv.id
                WHERE dv.document_id = qo.id
                  AND COALESCE(prv.effective_to, dv.effective_from) IS NOT NULL
                  AND COALESCE(prv.effective_to, dv.effective_from) <= m.created_at
                  AND (dv.effective_to IS NULL OR m.created_at < dv.effective_to)
                ORDER BY COALESCE(prv.effective_to, dv.effective_from) DESC
                LIMIT 1
            ) AS commitment
        FROM kpi_measurement AS m
        JOIN quality_objective AS qo ON qo.id = m.objective_id
    ) AS g
    WHERE km.id = g.km_id
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
