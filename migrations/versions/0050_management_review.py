"""Management Review family (clause 9.3): management_review + review_input + review_output, new
enums, the MR document_type + workflow_definition seeds, and the system_config cadence columns.
(S-mr-1)

Revision ID: 0050_management_review
Revises: 0049_quality_objectives
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._mgmt_review_enums import (
    MANAGEMENT_REVIEW_CLOSE_STATE_VALUES,
    REVIEW_INPUT_TYPE_VALUES,
    REVIEW_OUTPUT_TYPE_VALUES,
)

revision: str = "0050_management_review"
down_revision: str | None = "0049_quality_objectives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_EVENT_TYPES = (
    "MGMT_REVIEW_INPUTS_COMPILED",
    "MGMT_REVIEW_OUTPUT_RECORDED",
    "MGMT_REVIEW_ACTION_SPAWNED",
    "MGMT_REVIEW_CLOSED",
)
# (code, name, document_level, is_singleton)
_MR_TYPE = ("MR", "Management Review", "L1_POLICY", False)
_DEF_KEY = "management_review"
# The container instance the MR_INPUT/MR_ACTION tasks hang off (added via the S5 direct-insert
# pattern, not the engine). Single stage; no sign-off signature on the container.
_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "prepare",
        "mode": "PARALLEL",
        "assignees": {
            "context_users": "owner_user_id",
            "task_type": "MR_INPUT",
            "action_expected": "prepare",
        },
        "quorum": {"type": "ANY"},
        "transitions": [],
        "signature": None,
    },
)


def _org_id(bind: Any) -> Any:
    # Resilient org lookup — an OPERATIONAL install renames short_code away from 'DEFAULT' at
    # setup G-E (the 0018/0021 trap; this live install's org is 'AHT'). D1 = single-org, so fall
    # back to the only row. NEVER bare scalar_one on 'DEFAULT'.
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()
    return org_id


def upgrade() -> None:
    # 1. Additive event-type values (IF NOT EXISTS → idempotent; in an autocommit_block — the
    # 0049 shape).
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The fresh enums (tuples from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*REVIEW_INPUT_TYPE_VALUES, name="review_input_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(*REVIEW_OUTPUT_TYPE_VALUES, name="review_output_type").create(
        bind, checkfirst=True
    )
    postgresql.ENUM(
        *MANAGEMENT_REVIEW_CLOSE_STATE_VALUES, name="management_review_close_state"
    ).create(bind, checkfirst=True)
    review_input_type = postgresql.ENUM(name="review_input_type", create_type=False)
    review_output_type = postgresql.ENUM(name="review_output_type", create_type=False)
    close_state = postgresql.ENUM(name="management_review_close_state", create_type=False)

    # 3. management_review — the kind=DOCUMENT shared-PK subtype (id IS
    # documented_information.id).
    op.create_table(
        "management_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_label", sa.Text(), nullable=True),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("attendees", postgresql.JSONB(), nullable=True),
        sa.Column("close_state", close_state, nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_management_review"),
        sa.ForeignKeyConstraint(
            ["id"],
            ["documented_information.id"],
            name="fk_management_review_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_management_review_org_id_organization",
            ondelete="RESTRICT",
        ),
    )

    # 4. review_input — the compiled 9.3.2 input rows (mutable working projection).
    op.create_table(
        "review_input",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("management_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_type", review_input_type, nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("source_ref", postgresql.JSONB(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_input"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_review_input_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["management_review_id"],
            ["management_review.id"],
            name="fk_review_input_management_review_id_management_review",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_review_input_management_review_id", "review_input", ["management_review_id"]
    )

    # 5. review_output — the 9.3.3 decisions/actions (spawned_* reserved-null, no FK on
    # capa/initiative).
    op.create_table(
        "review_output",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("management_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_type", review_output_type, nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("spawned_task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("spawned_capa_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("spawned_initiative_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_review_output"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_review_output_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["management_review_id"],
            ["management_review.id"],
            name="fk_review_output_management_review_id_management_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["app_user.id"],
            name="fk_review_output_owner_user_id_app_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["spawned_task_id"],
            ["task.id"],
            name="fk_review_output_spawned_task_id_task",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_review_output_management_review_id", "review_output", ["management_review_id"]
    )

    # 6. system_config cadence columns.
    op.add_column(
        "system_config",
        sa.Column(
            "mgmt_review_cadence_months",
            sa.Integer(),
            server_default=sa.text("12"),
            nullable=False,
        ),
    )
    op.add_column(
        "system_config",
        sa.Column("mgmt_review_owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_system_config_mgmt_review_owner_user_id_app_user",
        "system_config",
        "app_user",
        ["mgmt_review_owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 7. Seed the MR document_type for the org (resilient lookup — the 0045/0048/0049 trap).
    org_id = _org_id(bind)
    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    code, name, level, singleton = _MR_TYPE
    bind.execute(
        pg_insert(document_type_t)
        .values(org_id=org_id, code=code, name=name, document_level=level, is_singleton=singleton)
        .on_conflict_do_nothing(index_elements=["org_id", "code"])
    )

    # 8. The management_review workflow_definition — a CONTAINER for the MGMT_REVIEW instance the
    # MR_INPUT/MR_ACTION tasks hang off (tasks are added via the S5 direct-insert pattern, not the
    # engine). The 0045 periodic_review seed shape, swapping in the MGMT_REVIEW subject + prepare
    # stage (no sign-off signature on the container instance).
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
            subject_type="MGMT_REVIEW",
            stages={"entry": "prepare"},
            default_sla=None,
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
    # workflow seed (guarded by live instances), then document_type (guarded by live docs), then
    # tables.
    has_instances = bind.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM workflow_instance wi JOIN workflow_definition wd "
                "ON wi.definition_id = wd.id WHERE wd.key = :k)"),
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
    bind.execute(
        sa.text("DELETE FROM document_type dt WHERE dt.code = :c AND NOT EXISTS "
                "(SELECT 1 FROM documented_information di WHERE di.document_type_id = dt.id)"),
        {"c": _MR_TYPE[0]},
    )
    op.drop_constraint(
        "fk_system_config_mgmt_review_owner_user_id_app_user", "system_config", type_="foreignkey"
    )
    op.drop_column("system_config", "mgmt_review_owner_user_id")
    op.drop_column("system_config", "mgmt_review_cadence_months")
    op.drop_index("ix_review_output_management_review_id", table_name="review_output")
    op.drop_table("review_output")
    op.drop_index("ix_review_input_management_review_id", table_name="review_input")
    op.drop_table("review_input")
    op.drop_table("management_review")
    op.execute("DROP TYPE IF EXISTS management_review_close_state")
    op.execute("DROP TYPE IF EXISTS review_output_type")
    op.execute("DROP TYPE IF EXISTS review_input_type")
    # event_type ADD VALUEs are irreversible in PG → no-op (the 0011/0048 precedent).
