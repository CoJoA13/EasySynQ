"""S-mr-3 — MR outputs → action systems.

Un-reserves ``review_output.spawned_capa_id`` (adds the FK → ``capa.id``, RESTRICT — the
``spawned_task_id`` precedent on the same table; the COLUMN already exists from 0050) and adds three
additive enum values: ``dcr_reason_class 'mgmt_review'`` (the MR→DCR justification) and the
``MGMT_REVIEW_CAPA_SPAWNED`` / ``MGMT_REVIEW_DCR_SPAWNED`` event types. No data, no seed.

Revision ID: 0051_mr_outputs_to_actions
Revises: 0050_management_review
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0051_mr_outputs_to_actions"
down_revision: str | None = "0050_management_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Additive enum values (IF NOT EXISTS → idempotent; autocommit_block — the 0048 pattern; a
    # from-scratch ``upgrade head`` already has them via the *_VALUES tuples, so these no-op there
    # and only really add on an incrementally-migrated production DB).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE dcr_reason_class ADD VALUE IF NOT EXISTS 'mgmt_review'")
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'MGMT_REVIEW_CAPA_SPAWNED'")
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'MGMT_REVIEW_DCR_SPAWNED'")
    # 2. Un-reserve spawned_capa_id: the FK on the EXISTING column (the 0044 create_foreign_key
    # precedent). RESTRICT — a CAPA the review spawned can't be row-deleted out from under it
    # (CAPAs are records whose lifecycle is state, never a row-delete). Acyclic (review_output→capa;
    # capa never points back), so no use_alter. The name MUST match the ORM constraint (else
    # ``alembic check`` phantom-DROPs it).
    op.create_foreign_key(
        "fk_review_output_spawned_capa_id_capa",
        "review_output",
        "capa",
        ["spawned_capa_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Drop the FK (always safe — an FK-drop never RESTRICT-aborts, even on a populated DB). The ADD
    # VALUEs are irreversible in PG → no-op (the 0011/0047/0048 precedent).
    op.drop_constraint("fk_review_output_spawned_capa_id_capa", "review_output", type_="foreignkey")
