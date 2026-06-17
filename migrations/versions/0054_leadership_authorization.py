"""S-leadership-1: signed, engine-routed Top-Management *release authorization* for leadership
artifacts (Quality Policy POL §5.2, Quality Objectives OBJ §6.2, Management Review MR §9.3).

Additive enums + a seed-only workflow definition + one new ``system_config`` flag (NO new permission
key, NO new table — the signature binds to the existing ``document_version``; the "Top Management"
role already ships from 0038). Closes the document-backed half of the leadership-routing gap that
S-improvement-4 (the own-table Improvement-initiative authorization, 0053) deferred: a leadership
artifact may not be RELEASED until a Top-Management member signs ``meaning=verify`` on the Approved
``document_version`` — gated by the org opt-in flag (default OFF). The welded single-stage
``document_approval`` path is untouched / byte-identical.

1. **Two additive enum ADD VALUEs** (in an ``autocommit_block`` — so the new
   ``workflow_subject_type`` value is COMMITTED before the seed INSERT below uses it; the PG16 rule
   that a new enum value cannot be used in the same transaction it is added; the 0049/0052/0053
   shape):
   - ``workflow_subject_type`` → ``LEADERSHIP_AUTHORIZATION`` (the engine subject; USED by the seed).
   - ``event_type`` → ``LEADERSHIP_AUTHORIZED`` (the first-class audit of the leadership sign-off;
     runtime only).
   (``signed_object_type=document_version`` and the ``verify`` signature meaning already exist — no
   adds there.)

2. **The opt-in flag**: ``system_config.leadership_release_requires_top_management_authorization``
   (boolean NOT NULL DEFAULT false) — dormant on a fresh install; an operator flips it on (and
   assigns members to the member-less "Top Management" role) to activate the release gate.

3. **Seed (idempotent)** the effective ``leadership_release_authorization`` workflow_definition
   (subject LEADERSHIP_AUTHORIZATION) + its single ``leadership_authorization`` stage — a
   Top-Management ANY-quorum sign-off that signs ``meaning=verify`` and advances to ``COMPLETED``
   (no outward success edge → the engine ``_transition_target`` fallback). Reuses the reserved "Top
   Management" role seeded by 0038 (NOT reseeded).

Downgrade: delete the seeded stage + definition (NOT-EXISTS-guarding the ``workflow_instance``
RESTRICT child — a populated-DB downgrade leaves the seed intact rather than aborting; the
0038/0053 precedent), then DROP the flag column. The enum ADD VALUEs are irreversible in PostgreSQL
→ no-op (0001's downgrade DROPs the types wholesale; a re-upgrade rebuilds them from the ORM
``*_VALUES``). Round-trips up↔down↔check on PG16.

Revision ID: 0054_leadership_authorization
Revises: 0053_initiative_authorization
Create Date: 2026-06-17
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0054_leadership_authorization"
down_revision: str | None = "0053_initiative_authorization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEF_KEY = "leadership_release_authorization"
_TOPMGMT_ROLE = "Top Management"  # the reserved role seeded by 0038 (resolved by Role.name; R39)
_FLAG_COL = "leadership_release_requires_top_management_authorization"

# The single declarative stage (doc 10 §2.5). assignees.roles is resolved by ``users_with_roles``
# (by Role.name). A single Top-Management ANY stage — proportionate for a leadership release
# sign-off (no severity grade, so no ROUTER). No outward success edge → COMPLETED (engine
# _transition_target fallback). The signature ``verify`` = Top Management authorizes the release
# (R2 — reuse, no new meaning).
_STAGE: dict[str, Any] = {
    "key": "leadership_authorization",
    "mode": "PARALLEL",
    "assignees": {
        "roles": [_TOPMGMT_ROLE],
        "task_type": "VERIFY",
        "action_expected": "authorize_release",
    },
    "quorum": {"type": "ANY"},
    "transitions": [],  # no outward success edge → COMPLETED (engine fallback)
    "signature": {"meaning": "verify"},
}


def upgrade() -> None:
    # 1. Additive enum values. LEADERSHIP_AUTHORIZATION is USED by the seed below, so it MUST be
    # committed first — the autocommit_block commits the ALTER TYPE in its own transaction, so the
    # migration's main transaction (which the seed runs in) can then reference the new value (the
    # PG16 in-txn ADD-VALUE rule; the 0049/0052/0053 shape). IF NOT EXISTS → idempotent.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE workflow_subject_type ADD VALUE IF NOT EXISTS 'LEADERSHIP_AUTHORIZATION'"
        )
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'LEADERSHIP_AUTHORIZED'")

    # 2. The opt-in release-gate flag (default OFF — no behaviour change on a fresh install).
    op.add_column(
        "system_config",
        sa.Column(
            _FLAG_COL,
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    bind = op.get_bind()
    # Resilient org lookup (the 0038/0052/0053 trap): DEFAULT short_code else the sole org (D1).
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one_or_none()
    if org_id is None:
        return  # a fresh test DB without the org seed → nothing to seed

    # 3. The effective authorization definition.
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
            subject_type="LEADERSHIP_AUTHORIZATION",
            stages={"entry": "leadership_authorization"},
            default_sla={"hours": 120},  # ≤ 5 business days; informational in v1 (no escalation)
        )
        .on_conflict_do_nothing(index_elements=["org_id", "key", "version"])
    )
    definition_id = bind.execute(
        sa.text(
            "SELECT id FROM workflow_definition WHERE org_id = :org AND key = :key AND version = 1"
        ),
        {"org": org_id, "key": _DEF_KEY},
    ).scalar_one()

    # 4. The single Top-Management authorization stage.
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
    bind.execute(
        pg_insert(stage_t)
        .values(
            org_id=org_id,
            definition_id=definition_id,
            key=_STAGE["key"],
            mode=_STAGE["mode"],
            assignees=_STAGE["assignees"],
            quorum=_STAGE["quorum"],
            transitions=_STAGE["transitions"],
            signature=_STAGE["signature"],
        )
        .on_conflict_do_nothing(index_elements=["definition_id", "key"])
    )


def downgrade() -> None:
    bind = op.get_bind()

    # The definition + its stage — only if no workflow_instance references it (a populated-DB
    # downgrade with runtime/test instances leaves the seed intact rather than aborting on the
    # RESTRICT FK; the 0038/0053 precedent).
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

    op.drop_column("system_config", _FLAG_COL)

    # The "Top Management" role is owned by 0038 (NOT reseeded here) → left intact. The
    # workflow_subject_type / event_type ADD VALUEs are irreversible in PostgreSQL → no-op (0001's
    # downgrade DROPs the types wholesale; a re-upgrade rebuilds them from the ORM *_VALUES).
