"""workflow engine: workflow_instance.context + TASK_DECIDED/STAGE_ADVANCED/STAGE_FAILED + workflow_instance audit type (S-wf-engine)

The declarative multi-stage workflow engine (doc 10 §2) on top of the S5 single-stage approval
tables. No new tables — the engine reuses workflow_definition/workflow_stage/workflow_instance/task/
task_outcome. Three additive changes:

1. **workflow_instance.context** (JSONB, nullable) — the entry-context snapshot (e.g.
   ``{"severity": "Critical"}``) the engine evaluates conditional quorum / assignees / routing
   against, frozen at ``instantiate()`` (``subject_id`` has no FK). The DOCUMENT single-stage path
   leaves it NULL. A column add needs no new GRANT (column grants inherit the table's).
2. **event_type ADD VALUE** — ``TASK_DECIDED`` / ``STAGE_ADVANCED`` / ``STAGE_FAILED`` (the
   per-transition in-txn audit rows, doc 10 §2.6). Additive ``ALTER TYPE … ADD VALUE`` (the 0034
   pattern); none is used by a row in this migration → the PG16 in-txn rule holds.
3. **audit_object_type ADD VALUE** — ``workflow_instance`` (the engine's per-flow anchor; subject is
   polymorphic-no-FK so events key on the instance, not the subject). Additive.

Members are declared in db/models/_audit_enums.py too, so a from-scratch ``upgrade head`` rebuilds
the types from EVENT_TYPE_VALUES / AUDIT_OBJECT_TYPE_VALUES identically. Downgrade drops the column;
the ADD VALUEs are irreversible in PostgreSQL → no-op (the 0001 DROP / re-add-IF-NOT-EXISTS
convention). Round-trips up↔down↔check on PG16 incl. a populated-DB downgrade.

Revision ID: 0035_workflow_engine
Revises: 0034_audit_records
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035_workflow_engine"
down_revision: str | None = "0034_audit_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_EVENT_TYPES = ("TASK_DECIDED", "STAGE_ADVANCED", "STAGE_FAILED")


def upgrade() -> None:
    op.add_column(
        "workflow_instance",
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")
    op.execute("ALTER TYPE audit_object_type ADD VALUE IF NOT EXISTS 'workflow_instance'")


def downgrade() -> None:
    # The event_type / audit_object_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's
    # downgrade DROPs those types wholesale; a re-upgrade re-adds via ADD VALUE IF NOT EXISTS). Only
    # the column is reversible.
    op.drop_column("workflow_instance", "context")
