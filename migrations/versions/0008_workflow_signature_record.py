"""workflow + signature_event + record (slice S5)

Creates the minimal approval-workflow cluster (C7/M16: ``workflow_definition`` → ``workflow_stage``,
``workflow_instance`` pinning ``definition_version``, ``task`` / ``task_outcome``), the append-only
``signature_event`` table (doc 14 §8; polymorphic ``signed_object_type``/``signed_object_id``;
Part-11 columns present-but-NULL), and the ``record`` shared-PK subtype of
``documented_information`` (doc 14 §5.5). Adds the SoD-2 relaxation flag
``system_config.allow_approver_release``.

Partial-index note: the one-effective-definition-per-key index uses the standalone boolean predicate
``WHERE effective`` (PostgreSQL normalizes ``= true`` to the bare column reference), declared
identically in the ORM ``__table_args__`` so ``alembic check`` stays clean. The ``task`` containment
index is GIN (My-Tasks ``candidate_pool @> [me]``).

Revision ID: 0008_workflow_signature_record
Revises: 0007_lifecycle
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_workflow_signature_record"
down_revision: str | None = "0007_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS: dict[str, tuple[str, ...]] = {
    "workflow_subject_type": (
        "DOCUMENT",
        "DCR",
        "CAPA",
        "AUDIT",
        "MGMT_REVIEW",
        "PERIODIC_REVIEW",
    ),
    "workflow_stage_mode": ("SEQUENTIAL", "PARALLEL", "ROUTER"),
    "task_type": (
        "APPROVE",
        "REVIEW",
        "PERIODIC_REVIEW",
        "AUDIT_TASK",
        "FINDING_ACK",
        "CAPA_STAGE",
        "CAPA_ACTION",
        "VERIFY",
        "MR_INPUT",
        "MR_ACTION",
        "DCR_TRIAGE",
    ),
    "task_state": ("PENDING", "CLAIMED", "DONE", "SKIPPED", "ESCALATED", "EXPIRED"),
    "task_outcome_kind": (
        "approve",
        "reject",
        "acknowledge",
        "complete",
        "verify",
        "changes_requested",
    ),
    "signature_meaning": (
        "review",
        "approval",
        "release",
        "obsolete",
        "verify",
        "disposition",
        "import_baseline",
        "review_confirmed",
        "authored",
        "responsibility",
    ),
    "signature_method": ("app_click", "SESSION", "password_reauth", "mfa_totp", "mfa_webauthn"),
    "signed_object_type": ("document_version", "record", "capa_stage"),
    "record_type": (
        "AUDIT",
        "AUDIT_FINDING",
        "CAPA",
        "COMPETENCE",
        "CALIBRATION",
        "MGMT_REVIEW",
        "SUPPLIER_EVAL",
        "RELEASE",
        "KPI_READING",
        "SATISFACTION",
        "TRACEABILITY",
        "PROPERTY_EVENT",
        "CHANGE",
        "EVIDENCE",
        "FILLED_FORM",
        "COMPLAINT",
    ),
    "record_disposition_state": ("ACTIVE", "DUE_FOR_REVIEW", "ON_HOLD", "DISPOSED"),
}


def _org_fk(table: str, column: str = "org_id") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["organization.id"], name=f"fk_{table}_{column}_organization", ondelete="RESTRICT"
    )


def _user_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], ["app_user.id"], name=f"fk_{table}_{column}_app_user", ondelete="RESTRICT"
    )


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    workflow_subject_type = postgresql.ENUM(name="workflow_subject_type", create_type=False)
    workflow_stage_mode = postgresql.ENUM(name="workflow_stage_mode", create_type=False)
    task_type = postgresql.ENUM(name="task_type", create_type=False)
    task_state = postgresql.ENUM(name="task_state", create_type=False)
    task_outcome_kind = postgresql.ENUM(name="task_outcome_kind", create_type=False)
    signature_meaning = postgresql.ENUM(name="signature_meaning", create_type=False)
    signature_method = postgresql.ENUM(name="signature_method", create_type=False)
    signed_object_type = postgresql.ENUM(name="signed_object_type", create_type=False)
    record_type = postgresql.ENUM(name="record_type", create_type=False)
    record_disposition_state = postgresql.ENUM(name="record_disposition_state", create_type=False)

    # workflow_definition — declarative, versioned, data-not-code.
    op.create_table(
        "workflow_definition",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("subject_type", workflow_subject_type, nullable=False),
        sa.Column("stages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("entry_conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("default_sla", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _org_fk("workflow_definition"),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_definition"),
        sa.UniqueConstraint(
            "org_id", "key", "version", name="uq_workflow_definition_org_id_key_version"
        ),
    )

    # workflow_stage — ordered stages of a definition.
    op.create_table(
        "workflow_stage",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("mode", workflow_stage_mode, nullable=False),
        sa.Column("assignees", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("quorum", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("transitions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sla", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sod_author_excluded", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("signature", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        _org_fk("workflow_stage"),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["workflow_definition.id"],
            name="fk_workflow_stage_definition_id_workflow_definition",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_stage"),
        sa.UniqueConstraint("definition_id", "key", name="uq_workflow_stage_definition_id_key"),
    )

    # workflow_instance — a running approval; pins definition_version.
    op.create_table(
        "workflow_instance",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("subject_type", workflow_subject_type, nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_state", sa.Text(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        _org_fk("workflow_instance"),
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["workflow_definition.id"],
            name="fk_workflow_instance_definition_id_workflow_definition",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_instance"),
    )

    # task — the My-Tasks atom.
    op.create_table(
        "task",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_key", sa.Text(), nullable=False),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_pool", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("type", task_type, nullable=False),
        sa.Column("action_expected", sa.Text(), nullable=True),
        sa.Column("state", task_state, server_default="PENDING", nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_token", sa.Text(), nullable=True),
        _org_fk("task"),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instance.id"],
            name="fk_task_instance_id_workflow_instance",
            ondelete="RESTRICT",
        ),
        _user_fk("task", "assignee_user_id"),
        sa.PrimaryKeyConstraint("id", name="pk_task"),
    )

    # task_outcome — one decision per task (UNIQUE backstop).
    op.create_table(
        "task_outcome",
        _uuid_pk(),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", task_outcome_kind, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.id"], name="fk_task_outcome_task_id_task", ondelete="RESTRICT"
        ),
        _user_fk("task_outcome", "decided_by"),
        sa.PrimaryKeyConstraint("id", name="pk_task_outcome"),
        sa.UniqueConstraint("task_id", name="uq_task_outcome_task_id"),
    )

    # signature_event — append-only; polymorphic subject; Part-11 columns present-but-NULL.
    op.create_table(
        "signature_event",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("on_behalf_of", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("signed_object_type", signed_object_type, nullable=False),
        sa.Column("signed_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meaning", signature_meaning, nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("method", signature_method, nullable=False),
        sa.Column("content_digest", sa.Text(), nullable=True),
        sa.Column("auth_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reauth_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("crypto_signature", sa.LargeBinary(), nullable=True),
        sa.Column("prev_signature_hash", sa.LargeBinary(), nullable=True),
        sa.Column("signature_hash", sa.LargeBinary(), nullable=True),
        sa.Column("voided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("voided_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _org_fk("signature_event"),
        _user_fk("signature_event", "signer_user_id"),
        _user_fk("signature_event", "on_behalf_of"),
        _user_fk("signature_event", "voided_by"),
        sa.PrimaryKeyConstraint("id", name="pk_signature_event"),
    )

    # record — shared-PK subtype of documented_information (record.id == documented_information.id).
    op.create_table(
        "record",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_type", record_type, nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("captured_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("form_field_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("correction_of", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("superseded_by_correction", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("retention_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retention_basis_date", sa.Date(), nullable=True),
        sa.Column(
            "disposition_state", record_disposition_state, server_default="ACTIVE", nullable=False
        ),
        sa.Column("legal_hold", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(
            ["id"],
            ["documented_information.id"],
            name="fk_record_id_documented_information",
            ondelete="RESTRICT",
        ),
        _org_fk("record"),
        _user_fk("record", "captured_by"),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documented_information.id"],
            name="fk_record_source_document_id_documented_information",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["document_version.id"],
            name="fk_record_source_version_id_document_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["correction_of"],
            ["record.id"],
            name="fk_record_correction_of_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_correction"],
            ["record.id"],
            name="fk_record_superseded_by_correction_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["retention_policy_id"],
            ["retention_policy.id"],
            name="fk_record_retention_policy_id_retention_policy",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_record"),
    )

    # SoD-2 relaxation flag (org-level; defaults strict).
    op.add_column(
        "system_config",
        sa.Column(
            "allow_approver_release", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )

    # --- indexes (match the ORM __table_args__ byte-for-byte) -------------------------------
    op.create_index(
        "uq_workflow_definition_effective_per_key",
        "workflow_definition",
        ["org_id", "key"],
        unique=True,
        postgresql_where=sa.text("effective"),
    )
    op.create_index(
        "ix_workflow_instance_org_id_subject_type_subject_id",
        "workflow_instance",
        ["org_id", "subject_type", "subject_id"],
    )
    op.create_index("ix_task_assignee_user_id_state", "task", ["assignee_user_id", "state"])
    op.create_index("ix_task_instance_id", "task", ["instance_id"])
    op.create_index(
        "gin_task_candidate_pool", "task", ["candidate_pool"], postgresql_using="gin"
    )
    op.create_index(
        "ix_signature_event_signed_object_type_signed_object_id",
        "signature_event",
        ["signed_object_type", "signed_object_id"],
    )
    op.create_index(
        "ix_signature_event_org_id_signer_user_id",
        "signature_event",
        ["org_id", "signer_user_id"],
    )
    op.create_index("ix_record_source_version_id", "record", ["source_version_id"])
    op.create_index(
        "ix_record_retention_basis_date_disposition_state",
        "record",
        ["retention_basis_date", "disposition_state"],
    )


def downgrade() -> None:
    op.drop_index("ix_record_retention_basis_date_disposition_state", table_name="record")
    op.drop_index("ix_record_source_version_id", table_name="record")
    op.drop_index(
        "ix_signature_event_org_id_signer_user_id", table_name="signature_event"
    )
    op.drop_index(
        "ix_signature_event_signed_object_type_signed_object_id", table_name="signature_event"
    )
    op.drop_index("gin_task_candidate_pool", table_name="task")
    op.drop_index("ix_task_instance_id", table_name="task")
    op.drop_index("ix_task_assignee_user_id_state", table_name="task")
    op.drop_index(
        "ix_workflow_instance_org_id_subject_type_subject_id", table_name="workflow_instance"
    )
    op.drop_index(
        "uq_workflow_definition_effective_per_key", table_name="workflow_definition"
    )

    op.drop_column("system_config", "allow_approver_release")
    op.drop_table("record")
    op.drop_table("signature_event")
    op.drop_table("task_outcome")
    op.drop_table("task")
    op.drop_table("workflow_instance")
    op.drop_table("workflow_stage")
    op.drop_table("workflow_definition")
    for name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")
