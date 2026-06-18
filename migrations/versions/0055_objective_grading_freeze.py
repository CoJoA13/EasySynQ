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

# The CAPTURE-TIME backfill (Codex P1): for each EXISTING reading, freeze the commitment that was
# GOVERNING at its ``created_at`` — the document_version whose effective window contains it (the
# latest ``effective_from <= created_at``) — exactly what ``record_measurement`` froze for a NEW
# reading at that instant. Freezing the CURRENT commitment instead would re-stamp a reading
# captured BEFORE a direction/threshold revision with the post-revision basis, permanently sealing
# the very retroactive re-grade this slice removes. BOTH columns gate on the SAME predicate — the
# capture-time version's snapshot carries an OBJECT ``objective_commitment`` fold — mirroring the
# runtime ``governing = raw if isinstance(raw, dict) else None`` guard (service.py): a reading
# captured pre-first-release (no applicable Effective version) or a fold-less version falls back
# to the working row for BOTH columns. With the fold present, ``->>'at_risk_threshold'`` is SQL NULL
# for a JSON null (no amber band) → a NULL Numeric. ``effective_from``/``effective_to`` are set at
# every cutover (lifecycle ``_cutover``; the DCR/import/ingestion go-live paths likewise).
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
                WHERE dv.document_id = qo.id
                  AND dv.effective_from IS NOT NULL
                  AND dv.effective_from <= m.created_at
                ORDER BY dv.effective_from DESC
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
