"""S-obj-1 (doc 14 §6, R3/R44): the Quality Objectives family schema.

Creates ``quality_objective`` (kind=DOCUMENT subtype satellite), ``objective_plan`` (mutable action
rows), and the append-only ``kpi_measurement`` projection (REVOKE UPDATE,DELETE — the capa_stage/
acknowledgement house style). Adds the ``objective_direction`` enum + three additive OBJECTIVE_*
event types, and seeds the ``OBJ`` (Quality Objective) document_type. Rides the already-seeded
objective.*/kpi.* keys — NO new permission key, catalog stays 100.

Revision ID: 0049_quality_objectives
Revises: 0048_acknowledgements
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from easysynq_api.db.models._objective_enums import OBJECTIVE_DIRECTION_VALUES

revision: str = "0049_quality_objectives"
down_revision: str | None = "0048_acknowledgements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"
_NEW_EVENT_TYPES = (
    "OBJECTIVE_MEASUREMENT_RECORDED",
    "OBJECTIVE_PLAN_ADDED",
    "OBJECTIVE_PLAN_REMOVED",
)
# (code, name, document_level, is_singleton)
_OBJ_TYPE = ("OBJ", "Quality Objective", "L1_POLICY", False)


def upgrade() -> None:
    # 1. Additive event-type values (IF NOT EXISTS → idempotent; not USED in this txn's seeds,
    # but the autocommit_block is safe + matches the 0048 shape).
    with op.get_context().autocommit_block():
        for value in _NEW_EVENT_TYPES:
            op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    bind = op.get_bind()

    # 2. The fresh enum (tuple from the ORM *_VALUES — the 0010 rule).
    postgresql.ENUM(*OBJECTIVE_DIRECTION_VALUES, name="objective_direction").create(
        bind, checkfirst=True
    )
    direction = postgresql.ENUM(name="objective_direction", create_type=False)

    # 3. quality_objective — the kind=DOCUMENT subtype satellite.
    op.create_table(
        "quality_objective",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("baseline_value", sa.Numeric(), nullable=True),
        sa.Column("current_value", sa.Numeric(), nullable=True),
        sa.Column("direction", direction, nullable=False),
        sa.Column("at_risk_threshold", sa.Numeric(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_quality_objective"),
        sa.ForeignKeyConstraint(
            ["id"],
            ["documented_information.id"],
            name="fk_quality_objective_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_quality_objective_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["process.id"],
            name="fk_quality_objective_process_id_process",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["documented_information.id"],
            name="fk_quality_objective_policy_id_doc_info",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_quality_objective_process_id", "quality_objective", ["process_id"])

    # 4. objective_plan — mutable action rows.
    op.create_table(
        "objective_plan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("responsible_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_objective_plan"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_objective_plan_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["objective_id"],
            ["quality_objective.id"],
            name="fk_objective_plan_objective_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_user_id"],
            ["app_user.id"],
            name="fk_objective_plan_responsible_user_id_app_user",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_objective_plan_objective_id", "objective_plan", ["objective_id"])

    # 5. kpi_measurement — the append-only projection.
    op.create_table(
        "kpi_measurement",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("process_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("target_at_capture", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kpi_measurement"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_kpi_measurement_org_id_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["record.id"],
            name="fk_kpi_measurement_record_id_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["objective_id"],
            ["quality_objective.id"],
            name="fk_kpi_measurement_objective_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["process_id"],
            ["process.id"],
            name="fk_kpi_measurement_process_id_process",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_kpi_measurement_objective_id", "kpi_measurement", ["objective_id"])

    # 6. Least-privilege grants (pg_roles-guarded): kpi_measurement is append-only evidence.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON quality_objective TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON objective_plan TO {_APP_ROLE}';
                EXECUTE 'GRANT SELECT, INSERT ON kpi_measurement TO {_APP_ROLE}';
                EXECUTE 'REVOKE UPDATE, DELETE ON kpi_measurement FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # 7. Seed the OBJ document_type for the DEFAULT org (resilient lookup — the 0045/0048 trap).
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one_or_none()
    if org_id is None:
        org_id = bind.execute(sa.text("SELECT id FROM organization")).scalar_one()
    document_type_t = sa.table(
        "document_type",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.Text),
        sa.column("name", sa.Text),
        sa.column("document_level", postgresql.ENUM(name="document_level", create_type=False)),
        sa.column("is_singleton", sa.Boolean),
    )
    code, name, level, singleton = _OBJ_TYPE
    bind.execute(
        pg_insert(document_type_t)
        .values(org_id=org_id, code=code, name=name, document_level=level, is_singleton=singleton)
        .on_conflict_do_nothing(index_elements=["org_id", "code"])
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Guard the seed-delete against the RESTRICT FK documented_information.document_type_id ->
    # document_type.id: a populated DB that ever created an objective has base doc rows referencing
    # the OBJ type, so an unguarded DELETE aborts the whole rollback (the 0023 lesson). Leaving the
    # type in place when children exist is the correct, lossless downgrade.
    bind.execute(
        sa.text(
            "DELETE FROM document_type dt WHERE dt.code = :c "
            "AND NOT EXISTS (SELECT 1 FROM documented_information di "
            "WHERE di.document_type_id = dt.id)"
        ),
        {"c": _OBJ_TYPE[0]},
    )
    op.drop_index("ix_kpi_measurement_objective_id", table_name="kpi_measurement")
    op.drop_table("kpi_measurement")
    op.drop_index("ix_objective_plan_objective_id", table_name="objective_plan")
    op.drop_table("objective_plan")
    op.drop_index("ix_quality_objective_process_id", table_name="quality_objective")
    op.drop_table("quality_objective")
    op.execute("DROP TYPE IF EXISTS objective_direction")
    # The event_type ADD VALUEs are irreversible in PG → no-op (the 0011/0048 precedent).
