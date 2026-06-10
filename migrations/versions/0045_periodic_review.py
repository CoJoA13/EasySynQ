"""S-drift-1 (D5, doc 04 §9): periodic re-review — review columns + the periodic_review seed.

Adds ``review_period_months`` / ``next_review_due`` / ``last_reviewed_at`` to
``documented_information`` (all nullable — NO backfill, the owner's opt-in fork), the partial
sweep index, the ``REVIEW_CONFIRMED`` / ``REVIEW_OVERDUE`` event types (additive ADD VALUE, no-op
downgrade — the 0011 pattern), and seeds the single-stage ``periodic_review`` workflow definition
(the 0043 recipe; assignee = the document owner via the ``context_users`` spec key).

Revision ID: 0045_periodic_review
Revises: 0044_dcr_implement
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0045_periodic_review"
down_revision: str | None = "0044_dcr_implement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEF_KEY = "periodic_review"
_NEW_EVENT_TYPES = ("REVIEW_CONFIRMED", "REVIEW_OVERDUE")
_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "review",
        "mode": "PARALLEL",
        "assignees": {
            "context_users": "owner_user_id",
            "task_type": "PERIODIC_REVIEW",
            "action_expected": "periodic_review",
        },
        "quorum": {"type": "ANY"},
        "transitions": [],
        "signature": {"meaning": "review_confirmed"},
    },
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Event types (IF NOT EXISTS → idempotent; not used by any row in this txn).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 2. The review columns (all nullable — no backfill).
    op.add_column(
        "documented_information",
        sa.Column("review_period_months", sa.Integer(), nullable=True),
    )
    op.add_column(
        "documented_information", sa.Column("next_review_due", sa.Date(), nullable=True)
    )
    op.add_column(
        "documented_information",
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_documented_information_next_review_due",
        "documented_information",
        ["next_review_due"],
        postgresql_where=sa.text("next_review_due IS NOT NULL"),
    )

    # 3. The periodic_review definition (the 0043 stage/seed shape, but NOT its org lookup:
    # an OPERATIONAL install renames short_code away from 'DEFAULT' at setup G-E — the 0018/0021
    # trap; this live install's org is 'AHT'. D1 = single-org, so fall back to the only row.
    # NEVER skip-if-absent: a missing seed makes the daily sweep raise 500 forever.
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()
    definition_t = sa.table(
        "workflow_definition",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("effective", sa.Boolean),
        sa.column("subject_type", postgresql.ENUM(name="workflow_subject_type", create_type=False)),
        sa.column("stages", postgresql.JSONB),
        sa.column("default_sla", postgresql.JSONB),
    )
    bind.execute(
        pg_insert(definition_t)
        .values(
            org_id=org_id,
            key=_DEF_KEY,
            version=1,
            effective=True,
            subject_type="PERIODIC_REVIEW",
            stages={"entry": "review"},
            default_sla=None,  # due_at is set by the sweep (= next_review_due), not an SLA
        )
        .on_conflict_do_nothing(index_elements=["org_id", "key", "version"])
    )
    definition_id = bind.execute(
        sa.text(
            "SELECT id FROM workflow_definition WHERE org_id = :org AND key = :key AND version = 1"
        ),
        {"org": org_id, "key": _DEF_KEY},
    ).scalar_one()
    stage_t = sa.table(
        "workflow_stage",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("definition_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("mode", postgresql.ENUM(name="workflow_stage_mode", create_type=False)),
        sa.column("assignees", postgresql.JSONB),
        sa.column("quorum", postgresql.JSONB),
        sa.column("transitions", postgresql.JSONB),
        sa.column("signature", postgresql.JSONB),
    )
    for st in _STAGES:
        bind.execute(
            pg_insert(stage_t)
            .values(
                org_id=org_id,
                definition_id=definition_id,
                key=st["key"],
                mode=st["mode"],
                assignees=st.get("assignees"),
                quorum=st.get("quorum"),
                transitions=st.get("transitions"),
                signature=st.get("signature"),
            )
            .on_conflict_do_nothing(index_elements=["definition_id", "key"])
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Seed delete guarded by child instances (the 0023/0043 precedent: a populated-DB downgrade
    # with runtime instances leaves the seed intact rather than aborting on RESTRICT).
    has_instances = bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM workflow_instance wi "
            "JOIN workflow_definition wd ON wi.definition_id = wd.id WHERE wd.key = :k)"
        ),
        {"k": _DEF_KEY},
    ).scalar()
    if not has_instances:
        bind.execute(
            sa.text(
                "DELETE FROM workflow_stage WHERE definition_id IN "
                "(SELECT id FROM workflow_definition WHERE key = :k)"
            ),
            {"k": _DEF_KEY},
        )
        bind.execute(sa.text("DELETE FROM workflow_definition WHERE key = :k"), {"k": _DEF_KEY})
    op.drop_index(
        "ix_documented_information_next_review_due", table_name="documented_information"
    )
    op.drop_column("documented_information", "last_reviewed_at")
    op.drop_column("documented_information", "next_review_due")
    op.drop_column("documented_information", "review_period_months")
    # Enum values: deliberate no-op (PG cannot remove an enum value; the 0011 precedent).
