"""seed: the document_approval workflow + the SoD-1/SoD-2 constraints (slice S5)

Idempotent. Seeds, for the DEFAULT org:
- one effective ``workflow_definition`` ``document_approval`` (v1, subject DOCUMENT) + its single
  ``quality_approval`` stage (SEQUENTIAL, quorum ANY, author excluded), which ``submit-review``
  instantiates; and
- the two separation-of-duties constraints the PDP enforces in S5 (doc 07 §7.1):
  **SoD-1** (a version's editor may not approve that same version — HARD_DENY, non-overridable) and
  **SoD-2** (the editor may never release their own edit; the sole approver may release only when
  ``allow_approver_release`` — HARD_DENY, org-overridable on the approver side only).

SoD-3 (auditor independence) is enforced structurally by the seeded "Internal Auditor" role (no
edit/approve/release grants), not a constraint row.

Revision ID: 0009_seed_workflow_sod
Revises: 0008_workflow_signature_record
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0009_seed_workflow_sod"
down_revision: str | None = "0008_workflow_signature_record"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEF_KEY = "document_approval"
_STAGE_KEY = "quality_approval"

# (description, duty_a, duty_b, target_binding, severity, org_overridable)
_SOD_CONSTRAINTS: tuple[tuple[str, dict, dict, str, str, bool], ...] = (
    (
        "SoD-1: a version's editor may not approve that same version",
        {"permission": "document.edit"},
        {"permission": "document.approve"},
        "SAME_VERSION",
        "HARD_DENY",
        False,
    ),
    (
        "SoD-2: the editor may never release their own edit (approver-release gated by flag)",
        {"permission": "document.edit"},
        {"permission": "document.release"},
        "SAME_VERSION",
        "HARD_DENY",
        True,
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one()

    definition_t = sa.table(
        "workflow_definition",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("effective", sa.Boolean),
        sa.column("subject_type", postgresql.ENUM(name="workflow_subject_type", create_type=False)),
        sa.column("stages", postgresql.JSONB),
    )
    bind.execute(
        pg_insert(definition_t)
        .values(
            org_id=org_id,
            key=_DEF_KEY,
            version=1,
            effective=True,
            subject_type="DOCUMENT",
            stages=[{"key": _STAGE_KEY, "mode": "SEQUENTIAL"}],
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

    stage_t = sa.table(
        "workflow_stage",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("definition_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("mode", postgresql.ENUM(name="workflow_stage_mode", create_type=False)),
        sa.column("assignees", postgresql.JSONB),
        sa.column("quorum", postgresql.JSONB),
        sa.column("sod_author_excluded", sa.Boolean),
        sa.column("signature", postgresql.JSONB),
    )
    bind.execute(
        pg_insert(stage_t)
        .values(
            org_id=org_id,
            definition_id=definition_id,
            key=_STAGE_KEY,
            mode="SEQUENTIAL",
            assignees={"roles": ["Approver", "QMS Owner"]},
            quorum={"type": "ANY"},
            sod_author_excluded=True,
            signature={"meaning": "approval"},
        )
        .on_conflict_do_nothing(index_elements=["definition_id", "key"])
    )

    # SoD constraints — guarded on (org_id, description) since the table has no natural unique key.
    existing = {
        row[0]
        for row in bind.execute(
            sa.text("SELECT description FROM sod_constraint WHERE org_id = :org"), {"org": org_id}
        )
    }
    sod_t = sa.table(
        "sod_constraint",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("description", sa.Text),
        sa.column("duty_a", postgresql.JSONB),
        sa.column("duty_b", postgresql.JSONB),
        sa.column("relation", sa.Text),
        sa.column("target_binding", postgresql.ENUM(name="sod_target_binding", create_type=False)),
        sa.column("severity", postgresql.ENUM(name="sod_severity", create_type=False)),
        sa.column("org_overridable", sa.Boolean),
    )
    rows = [
        {
            "org_id": org_id,
            "description": desc,
            "duty_a": duty_a,
            "duty_b": duty_b,
            "relation": "SAME_PRINCIPAL_FORBIDDEN",
            "target_binding": binding,
            "severity": severity,
            "org_overridable": overridable,
        }
        for desc, duty_a, duty_b, binding, severity, overridable in _SOD_CONSTRAINTS
        if desc not in existing
    ]
    if rows:
        bind.execute(sod_t.insert().values(rows))


def downgrade() -> None:
    bind = op.get_bind()
    descs = [desc for desc, *_ in _SOD_CONSTRAINTS]
    del_sod = sa.text("DELETE FROM sod_constraint WHERE description IN :descs").bindparams(
        sa.bindparam("descs", expanding=True)
    )
    bind.execute(del_sod, {"descs": descs})
    bind.execute(sa.text("DELETE FROM workflow_stage WHERE key = :k"), {"k": _STAGE_KEY})
    bind.execute(sa.text("DELETE FROM workflow_definition WHERE key = :k"), {"k": _DEF_KEY})
