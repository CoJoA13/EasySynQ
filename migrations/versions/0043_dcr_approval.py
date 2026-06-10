"""dcr approval: signed_object_type 'dcr' + the dcr_approval workflow + the route grant (S-dcr-4)

The routing + approval slice of the DCR family (doc 05 §5.4, the declarative engine — the S-capa-2
pattern for subject_type=DCR). Seeds, for the DEFAULT org (single-tenant D1):

1. **One enum value** — ``ALTER TYPE signed_object_type ADD VALUE IF NOT EXISTS 'dcr'`` (a DCR approval
   signature signs the DCR itself; per-approver, doc 05 §5.4 — ``signed_object_id`` = the DCR id). NOT
   used in this migration, so the same-txn ADD VALUE is safe. (``workflow_subject_type`` already carries
   ``DCR`` since 0008 — no ADD VALUE needed.)
2. **One ``workflow_definition`` ``dcr_approval``** (v1, subject DCR) that ``POST /dcrs/{id}/route``
   instantiates, with a **change_significance-conditional ROUTER** entry: **MAJOR** → ``major_proc``
   (Process Owner, ANY) → ``major_qms`` (QMS Owner, ANY) composed as SEQUENTIAL stages (the
   Process-Owner-**and**-QMS conjunction — a merged-pool N_OF_M would false-PASS); **MINOR** →
   ``minor_qms`` (QMS Owner, ANY) editorial sign-off. Every approval stage carries
   ``signature={"meaning":"approval"}`` + the uniform ≤5-business-day SLA. A stage with no outward
   success transition implicitly advances to COMPLETED (the engine ``_transition_target`` fallback).
   Reuses the EXISTING seeded Process Owner + QMS Owner roles (no new role).
3. **Grant-backfill** ``changeRequest.route`` → Process Owner + QMS Owner, PROCESS-scoped (the
   ``:assignment_process`` placeholder; the 0040 recipe). ``changeRequest.approve`` is NOT granted — the
   approval rides the candidate-pool authority (the CAPA precedent).

Idempotent (``on_conflict_do_nothing``). The downgrade NOT-EXISTS-guards the definition's
``workflow_instance`` children (the 0023/0038 precedent) + removes the route grant; the enum ADD VALUE
is a no-op downgrade (the 0011 precedent — a PG enum value cannot be dropped).

Revision ID: 0043_dcr_approval
Revises: 0042_visual_diff
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0043_dcr_approval"
down_revision: str | None = "0042_visual_diff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEF_KEY = "dcr_approval"
_PROC_ROLE = "Process Owner"
_QM_ROLE = "QMS Owner"

# The declarative stages (doc 05 §5.4, doc 10 §6.3). assignees.roles resolves by ``users_with_roles``
# (Role.name). The ROUTER entry branches on the DCR ``change_significance`` context discriminator.
_APPROVE: dict[str, Any] = {"task_type": "APPROVE", "action_expected": "approve_dcr"}
_SIGN: dict[str, Any] = {"meaning": "approval"}
_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "route_by_significance",
        "mode": "ROUTER",
        "transitions": [
            {"when": "change_significance == 'MAJOR'", "to": "major_proc"},
            {"default": "minor_qms"},  # MINOR → single QMS-Owner editorial sign-off
        ],
    },
    {
        "key": "major_proc",
        "mode": "PARALLEL",
        "assignees": {"roles": [_PROC_ROLE], **_APPROVE},
        "quorum": {"type": "ANY"},
        "transitions": [{"on": "satisfied", "to": "major_qms"}],
        "signature": _SIGN,
    },
    {
        "key": "major_qms",
        "mode": "PARALLEL",
        "assignees": {"roles": [_QM_ROLE], **_APPROVE},
        "quorum": {"type": "ANY"},
        "transitions": [],  # no outward success edge → COMPLETED (engine _transition_target fallback)
        "signature": _SIGN,
    },
    {
        "key": "minor_qms",
        "mode": "PARALLEL",
        "assignees": {"roles": [_QM_ROLE], **_APPROVE},
        "quorum": {"type": "ANY"},
        "transitions": [],  # → COMPLETED
        "signature": _SIGN,
    },
)

_PROCESS_SCOPE: dict[str, Any] = {"level": "PROCESS", "selector": {"process_id": ":assignment_process"}}
_BACKFILL: tuple[tuple[str, str], ...] = (
    ("Process Owner", "changeRequest.route"),
    ("QMS Owner", "changeRequest.route"),
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. The DCR-approval signature object type (not used here → same-txn ADD VALUE is safe).
    op.execute("ALTER TYPE signed_object_type ADD VALUE IF NOT EXISTS 'dcr'")

    # An OPERATIONAL install renames short_code away from 'DEFAULT' at setup G-E (the 0018/0021
    # trap) — a bare scalar_one() would abort an upgrade from a restored pre-0043 DB. D1 =
    # single-org, so fall back to the only row (the 0045 recipe).
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()

    # 2. The effective dcr_approval definition.
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
            subject_type="DCR",
            stages={"entry": "route_by_significance"},
            default_sla={"hours": 120},  # ≤ 5 business days; informational in v1
        )
        .on_conflict_do_nothing(index_elements=["org_id", "key", "version"])
    )
    definition_id = bind.execute(
        sa.text(
            "SELECT id FROM workflow_definition WHERE org_id = :org AND key = :key AND version = 1"
        ),
        {"org": org_id, "key": _DEF_KEY},
    ).scalar_one()

    # 3. The stages.
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

    # 4. Grant-backfill changeRequest.route → Process Owner + QMS Owner (PROCESS-scoped; the 0040 recipe).
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )
    perm_ids = {
        key: pid for key, pid in bind.execute(sa.text("SELECT key, id FROM permission")).all()
    }
    rows: list[dict[str, Any]] = []
    for role_name, perm_key in _BACKFILL:
        permission_id = perm_ids.get(perm_key)
        if permission_id is None:  # catalog always seeded by 0004 — defensive
            continue
        roles = bind.execute(
            sa.text("SELECT id, org_id FROM role WHERE name = :n"), {"n": role_name}
        ).all()
        rows.extend(
            {
                "org_id": role_org,
                "role_id": role_id,
                "permission_id": permission_id,
                "scope_template": _PROCESS_SCOPE,
            }
            for role_id, role_org in roles
        )
    if rows:
        bind.execute(
            pg_insert(role_grant_t)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
        )


def downgrade() -> None:
    bind = op.get_bind()

    # Remove the backfilled route grants (per (role name, permission key) pair).
    for role_name, perm_key in _BACKFILL:
        bind.execute(
            sa.text(
                "DELETE FROM role_grant "
                "WHERE permission_id = (SELECT id FROM permission WHERE key = :k) "
                "AND role_id IN (SELECT id FROM role WHERE name = :n)"
            ),
            {"k": perm_key, "n": role_name},
        )

    # The definition + its stages — only if no workflow_instance references it (a populated-DB
    # downgrade with runtime/test instances leaves the seed intact; the 0023/0038 precedent).
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
    # The signed_object_type 'dcr' enum value is a no-op downgrade (a PG enum value can't be dropped).
