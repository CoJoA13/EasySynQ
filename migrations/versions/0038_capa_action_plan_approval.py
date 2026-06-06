"""seed: the CAPA action-plan approval workflow + the Top-Management role (slice S-capa-2)

SEED-ONLY (no DDL / no enum / no new permission key). Seeds, for the DEFAULT org (single-tenant, D1 —
``scalar_one()`` fails fast if the baseline org from 0001 is missing):

- a reserved **"Top Management"** role (the Critical action-plan second-tier approver pool; resolved by
  ``Role.name`` via the candidate-pool seam — org-role-based resolution stays deferred, R39) + its one
  ``role_grant`` (``capa.read`` at SYSTEM scope, so an approver can read the CAPA it signs); and
- one effective ``workflow_definition`` ``capa_action_plan_approval`` (v1, subject CAPA) that the
  ``POST /capas/{id}/action-plan`` propose step instantiates, with a severity-conditional ROUTER entry
  (doc 10 §6.3): **Critical** → ``crit_qm`` (QMS-Owner, ANY) → ``crit_topmgmt`` (Top-Management, ANY)
  composed as SEQUENTIAL stages (the cross-role "QM **and** top-management" conjunction — a single
  merged-pool N_OF_M would false-PASS); **Major/Minor** → ``qm_approval`` (QMS-Owner, ANY). Every
  approval stage carries ``signature={"meaning":"approval"}`` and a uniform ≤5-business-day SLA
  (``default_sla`` 120h, doc 10 §6.2 — SLA is informational in v1, no escalation). A stage with no
  outward success transition implicitly advances to ``COMPLETED`` (the engine ``_transition_target``
  fallback).

Idempotent (``on_conflict_do_nothing``). The downgrade NOT-EXISTS-guards the RESTRICT children
(``workflow_instance`` for the definition; ``role_assignment`` for the role) so a populated-DB
downgrade leaves the seed intact rather than aborting (the 0023 precedent).

Revision ID: 0038_capa_action_plan_approval
Revises: 0037_audit_findings
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0038_capa_action_plan_approval"
down_revision: str | None = "0037_audit_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEF_KEY = "capa_action_plan_approval"
_TOPMGMT_ROLE = "Top Management"
_QM_ROLE = "QMS Owner"  # the seeded role for the Quality-Manager persona (0004)

# The 4 declarative stages (doc 10 §6.3). assignees.roles is resolved by ``users_with_roles`` (by
# Role.name). The ROUTER entry branches on the CAPA ``severity`` context discriminator.
_APPROVE: dict[str, Any] = {"task_type": "APPROVE", "action_expected": "approve_capa_action_plan"}
_SIGN: dict[str, Any] = {"meaning": "approval"}
_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "route_by_severity",
        "mode": "ROUTER",
        "transitions": [
            {"when": "severity == 'Critical'", "to": "crit_qm"},
            {"default": "qm_approval"},  # Major / Minor → single QMS-Owner sign-off
        ],
    },
    {
        "key": "crit_qm",
        "mode": "PARALLEL",
        "assignees": {"roles": [_QM_ROLE], **_APPROVE},
        "quorum": {"type": "ANY"},
        "transitions": [{"on": "satisfied", "to": "crit_topmgmt"}],
        "signature": _SIGN,
    },
    {
        "key": "crit_topmgmt",
        "mode": "PARALLEL",
        "assignees": {"roles": [_TOPMGMT_ROLE], **_APPROVE},
        "quorum": {"type": "ANY"},
        "transitions": [],  # no outward success edge → COMPLETED (engine _transition_target fallback)
        "signature": _SIGN,
    },
    {
        "key": "qm_approval",
        "mode": "PARALLEL",
        "assignees": {"roles": [_QM_ROLE], **_APPROVE},
        "quorum": {"type": "ANY"},
        "transitions": [],  # → COMPLETED
        "signature": _SIGN,
    },
)


def upgrade() -> None:
    bind = op.get_bind()
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one()

    # 1. The Top-Management role (additive; reserved governance role, no grants beyond capa.read).
    role_t = sa.table(
        "role",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.Text),
        sa.column("description", sa.Text),
        sa.column("is_reserved", sa.Boolean),
    )
    bind.execute(
        pg_insert(role_t)
        .values(
            org_id=org_id,
            name=_TOPMGMT_ROLE,
            description=(
                "Top management (ISO 9001 Clause 5). The second-tier approver of a Critical CAPA "
                "action plan; holds no QMS-content authority beyond reading the CAPA it signs."
            ),
            is_reserved=True,
        )
        .on_conflict_do_nothing(index_elements=["org_id", "name"])
    )
    topmgmt_id = bind.execute(
        sa.text("SELECT id FROM role WHERE org_id = :org AND name = :n"),
        {"org": org_id, "n": _TOPMGMT_ROLE},
    ).scalar_one()
    capa_read_id = bind.execute(
        sa.text("SELECT id FROM permission WHERE key = 'capa.read'")
    ).scalar_one()
    role_grant_t = sa.table(
        "role_grant",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_template", postgresql.JSONB),
    )
    bind.execute(
        pg_insert(role_grant_t)
        .values(
            org_id=org_id,
            role_id=topmgmt_id,
            permission_id=capa_read_id,
            scope_template={"level": "SYSTEM"},
        )
        .on_conflict_do_nothing(index_elements=["org_id", "role_id", "permission_id"])
    )

    # 2. The effective CAPA action-plan approval definition.
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
            subject_type="CAPA",
            stages={"entry": "route_by_severity"},
            default_sla={"hours": 120},  # ≤ 5 business days (doc 10 §6.2); informational in v1
        )
        .on_conflict_do_nothing(index_elements=["org_id", "key", "version"])
    )
    definition_id = bind.execute(
        sa.text(
            "SELECT id FROM workflow_definition "
            "WHERE org_id = :org AND key = :key AND version = 1"
        ),
        {"org": org_id, "key": _DEF_KEY},
    ).scalar_one()

    # 3. The 4 stages.
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

    # The definition + its stages — only if no workflow_instance references it (a populated-DB
    # downgrade with runtime/test instances leaves the seed intact rather than aborting on the
    # RESTRICT FK; the 0023 precedent).
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

    # The Top-Management role + its grant — only if no role_assignment references the role (a
    # populated-DB downgrade leaves the role AND its grant fully intact rather than half-stripping
    # it; role_grant deleted BEFORE role for the RESTRICT FK).
    has_assignments = bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM role_assignment ra "
            "JOIN role r ON ra.role_id = r.id WHERE r.name = :n)"
        ),
        {"n": _TOPMGMT_ROLE},
    ).scalar()
    if not has_assignments:
        bind.execute(
            sa.text("DELETE FROM role_grant WHERE role_id IN (SELECT id FROM role WHERE name = :n)"),
            {"n": _TOPMGMT_ROLE},
        )
        bind.execute(sa.text("DELETE FROM role WHERE name = :n"), {"n": _TOPMGMT_ROLE})
