"""Establish exact WORM retention authority and container identity schema.

Revision ID: 0089_worm_retention_container_identity
Revises: 0088_bootstrap_credential
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models._r27_enums import R27_ENUM_VALUES
from easysynq_api.db.models._worm_enums import WORM_ENUM_VALUES

revision: str = "0089_worm_retention_container_identity"
down_revision: str | None = "0088_bootstrap_credential"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUM_VALUES = {**WORM_ENUM_VALUES, **R27_ENUM_VALUES}
_PARTIAL_INDEXES = (
    "uq_blob_worm_physical_identity",
    "uq_retention_revision_policy_proposed",
    "uq_retention_revision_config_proposed",
    "uq_r27_request_open",
)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _fk(
    table: str,
    column: str,
    target_table: str,
    target_column: str = "id",
    name: str | None = None,
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target_table}.{target_column}"],
        name=name or f"fk_{table}_{column}_{target_table}",
        ondelete="RESTRICT",
    )


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def _refuse_legacy_physical_owner_state(bind: sa.Connection) -> None:
    legacy_exists = bind.execute(
        sa.text(
            """
            SELECT
                EXISTS (SELECT 1 FROM blob LIMIT 1)
                OR EXISTS (SELECT 1 FROM document_version LIMIT 1)
                OR EXISTS (SELECT 1 FROM evidence_blob LIMIT 1)
                OR EXISTS (SELECT 1 FROM pending_blob_purge LIMIT 1)
                OR EXISTS (SELECT 1 FROM worm_destroy_request LIMIT 1)
                OR EXISTS (
                    SELECT 1 FROM evidence_pack
                    WHERE pack_record_id IS NOT NULL
                       OR zip_blob_sha256 IS NOT NULL
                       OR portfolio_blob_sha256 IS NOT NULL
                    LIMIT 1
                )
            """
        )
    ).scalar_one()
    if legacy_exists:
        raise RuntimeError("unsupported_legacy_physical_owner_state")


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_legacy_physical_owner_state(bind)
    # Alembic's historical default is VARCHAR(32); this approved revision id is longer.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )

    for name, values in _ENUM_VALUES.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    _upgrade_blob()
    _create_document_retention()
    _create_retention_operations()
    _create_hold_release()
    _replace_r27_request()
    _create_r27_authority()
    _upgrade_pending_blob_purge()
    _create_maintenance_intent()


def _upgrade_blob() -> None:
    for column in (
        sa.Column("object_version_id", sa.Text(), nullable=True),
        sa.Column("worm_enforced_mode", sa.Text(), nullable=True),
        sa.Column("worm_asserted_retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worm_asserted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worm_retention_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worm_legal_hold", sa.Boolean(), nullable=True),
        sa.Column("worm_legal_hold_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    ):
        op.add_column("blob", column)
    op.create_check_constraint(
        op.f("ck_blob_object_version_id_length"),
        "blob",
        "object_version_id IS NULL OR length(object_version_id) BETWEEN 1 AND 1024",
    )
    op.create_check_constraint(
        op.f("ck_blob_worm_assertion_shape"),
        "blob",
        """
        (worm_locked AND object_version_id IS NOT NULL
         AND worm_enforced_mode = 'GOVERNANCE'
         AND worm_asserted_retain_until IS NOT NULL AND worm_asserted_at IS NOT NULL
         AND worm_retain_until IS NOT NULL AND worm_retention_verified_at IS NOT NULL
         AND worm_retain_until >= worm_asserted_retain_until
         AND worm_legal_hold IS NOT NULL AND worm_legal_hold_verified_at IS NOT NULL)
        OR
        (NOT worm_locked AND worm_enforced_mode IS NULL
         AND worm_asserted_retain_until IS NULL AND worm_asserted_at IS NULL
         AND worm_retain_until IS NULL AND worm_retention_verified_at IS NULL
         AND worm_legal_hold IS NULL AND worm_legal_hold_verified_at IS NULL)
        """,
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_blob_worm_physical_identity "
        "ON blob (bucket, object_key, object_version_id) "
        "WHERE object_version_id IS NOT NULL"
    )


def _create_document_retention() -> None:
    op.create_table(
        "document_worm_config",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_period", sa.Text(), nullable=False),
        sa.Column("active_revision_no", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("document_worm_config", "org_id", "organization"),
        sa.PrimaryKeyConstraint("id", name="pk_document_worm_config"),
        sa.UniqueConstraint("org_id", name="uq_document_worm_config_org_id"),
        sa.CheckConstraint("btrim(active_period) <> ''", name="active_period_nonblank"),
        sa.CheckConstraint(
            "active_revision_no >= 1",
            name="active_revision_positive",
        ),
    )
    op.add_column(
        "retention_policy",
        sa.Column("active_revision_no", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_retention_policy_active_revision_positive"),
        "retention_policy",
        "active_revision_no >= 1",
    )
    op.add_column(
        "record",
        sa.Column(
            "retention_basis_provisional",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "document_version",
        sa.Column("retention_authority_kind", _enum("retention_authority_kind"), nullable=False),
    )
    op.add_column(
        "document_version",
        sa.Column("retention_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "document_version",
        sa.Column("document_worm_config_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("document_version", sa.Column("retention_basis_date", sa.Date(), nullable=False))
    op.create_foreign_key(
        "fk_document_version_retention_policy_id_retention_policy",
        "document_version",
        "retention_policy",
        ["retention_policy_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_document_version_worm_config_id_document_worm_config",
        "document_version",
        "document_worm_config",
        ["document_worm_config_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_document_version_retention_authority_shape"),
        "document_version",
        """
        (retention_authority_kind = 'POLICY' AND retention_policy_id IS NOT NULL
         AND document_worm_config_id IS NULL)
        OR
        (retention_authority_kind = 'INSTALLATION_MINIMUM' AND retention_policy_id IS NULL
         AND document_worm_config_id IS NOT NULL)
        """,
    )
    op.create_table(
        "retention_revision",
        _uuid_pk(),
        sa.Column("authority_kind", _enum("retention_authority_kind"), nullable=False),
        sa.Column("retention_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_worm_config_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("active_values", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_values", postgresql.JSONB(), nullable=True),
        sa.Column("state", _enum("retention_revision_state"), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("audit_event_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        _fk("retention_revision", "retention_policy_id", "retention_policy"),
        _fk(
            "retention_revision",
            "document_worm_config_id",
            "document_worm_config",
            name="fk_retention_revision_worm_config_id_config",
        ),
        _fk("retention_revision", "actor_user_id", "app_user"),
        sa.PrimaryKeyConstraint("id", name="pk_retention_revision"),
        sa.UniqueConstraint(
            "retention_policy_id",
            "revision_no",
            name="uq_retention_revision_retention_policy_id_revision_no",
        ),
        sa.UniqueConstraint(
            "document_worm_config_id",
            "revision_no",
            name="uq_retention_revision_document_worm_config_id_revision_no",
        ),
        sa.CheckConstraint(
            "(authority_kind = 'POLICY' AND retention_policy_id IS NOT NULL "
            "AND document_worm_config_id IS NULL) OR "
            "(authority_kind = 'INSTALLATION_MINIMUM' AND retention_policy_id IS NULL "
            "AND document_worm_config_id IS NOT NULL)",
            name="authority_shape",
        ),
        sa.CheckConstraint("revision_no >= 1", name="revision_positive"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_retention_revision_policy_proposed "
        "ON retention_revision (retention_policy_id) "
        "WHERE state = 'PROPOSED' AND retention_policy_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_retention_revision_config_proposed "
        "ON retention_revision (document_worm_config_id) "
        "WHERE state = 'PROPOSED' AND document_worm_config_id IS NOT NULL"
    )
    op.execute(
        """
        INSERT INTO retention_revision (
            id, authority_kind, retention_policy_id, revision_no,
            active_values, proposed_values, state, activated_at
        )
        SELECT
            gen_random_uuid(), 'POLICY'::retention_authority_kind, rp.id, 1,
            jsonb_build_object(
                'basis', rp.basis::text,
                'duration', rp.duration,
                'disposition_action', rp.disposition_action::text,
                'review_required', rp.review_required,
                'worm_lock_period', rp.worm_lock_period
            ),
            NULL, 'ACTIVE'::retention_revision_state, now()
        FROM retention_policy AS rp
        """
    )


def _create_retention_operations() -> None:
    op.create_table(
        "retention_operation",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state",
            _enum("retention_operation_state"),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("verified_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("retention_operation", "org_id", "organization"),
        _fk("retention_operation", "revision_id", "retention_revision"),
        sa.PrimaryKeyConstraint("id", name="pk_retention_operation"),
        sa.UniqueConstraint("revision_id", name="uq_retention_operation_revision_id"),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
        sa.CheckConstraint(
            "target_count >= 0 AND verified_count >= 0 AND failed_count >= 0 "
            "AND verified_count + failed_count <= target_count",
            name="progress_counts",
        ),
    )
    op.create_table(
        "retention_operation_target",
        _uuid_pk(),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blob_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("object_version_id", sa.Text(), nullable=False),
        sa.Column("required_retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("required_legal_hold", sa.Boolean(), nullable=False),
        sa.Column(
            "state",
            _enum("retention_target_state"),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("read_back_retain_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_back_legal_hold", sa.Boolean(), nullable=True),
        sa.Column("read_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("retention_operation_target", "operation_id", "retention_operation"),
        _fk("retention_operation_target", "blob_sha256", "blob", "sha256"),
        sa.PrimaryKeyConstraint("id", name="pk_retention_operation_target"),
        sa.UniqueConstraint(
            "operation_id",
            "blob_sha256",
            "object_version_id",
            name="uq_retention_operation_target_operation_blob_version",
        ),
        sa.CheckConstraint(
            "length(object_version_id) BETWEEN 1 AND 1024",
            name="object_version_id_length",
        ),
        sa.CheckConstraint(
            "blob_sha256 ~ '^[0-9a-f]{64}$'",
            name="blob_sha256_shape",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
    )


def _create_hold_release() -> None:
    op.create_table(
        "worm_hold_release_operation",
        _uuid_pk(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blob_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("object_version_id", sa.Text(), nullable=False),
        sa.Column("initiated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("normalized_release_basis", sa.Text(), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("canonical_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("owner_snapshot_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "state",
            _enum("hold_release_state"),
            server_default=sa.text("'PENDING_AUTHORIZATION'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("result_code", sa.String(length=64), nullable=True),
        sa.Column("result_detail", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("worm_hold_release_operation", "org_id", "organization"),
        _fk("worm_hold_release_operation", "record_id", "record"),
        _fk("worm_hold_release_operation", "blob_sha256", "blob", "sha256"),
        _fk("worm_hold_release_operation", "initiated_by_user_id", "app_user"),
        sa.PrimaryKeyConstraint("id", name="pk_worm_hold_release_operation"),
        sa.UniqueConstraint(
            "org_id",
            "idempotency_key",
            name="uq_worm_hold_release_operation_org_id_idempotency_key",
        ),
        sa.CheckConstraint(
            "length(object_version_id) BETWEEN 1 AND 1024",
            name="object_version_id_length",
        ),
        sa.CheckConstraint(
            "length(normalized_release_basis) BETWEEN 1 AND 4000",
            name="release_basis_length",
        ),
        sa.CheckConstraint(
            "blob_sha256 ~ '^[0-9a-f]{64}$'",
            name="blob_sha256_shape",
        ),
        sa.CheckConstraint(
            "canonical_sha256 ~ '^[0-9a-f]{64}$' AND owner_snapshot_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_shape",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
    )
    op.create_table(
        "worm_hold_release_authorization",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("host_operator_identity", sa.String(length=255), nullable=False),
        sa.Column("authorizing_audit_event_id", sa.BigInteger(), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorizer_role", sa.String(length=64), nullable=False),
        _fk(
            "worm_hold_release_authorization",
            "operation_id",
            "worm_hold_release_operation",
            name="fk_worm_hold_auth_operation_id_operation",
        ),
        sa.PrimaryKeyConstraint("operation_id", name="pk_worm_hold_release_authorization"),
    )
    op.execute(
        """
        CREATE FUNCTION authorize_worm_hold_release() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            operation_state hold_release_state;
            operation_digest char(64);
            record_hold boolean;
            policy_duration text;
            policy_worm_period text;
        BEGIN
            SELECT operation.state,
                   operation.canonical_sha256,
                   record.legal_hold,
                   policy.duration,
                   policy.worm_lock_period
            INTO operation_state,
                 operation_digest,
                 record_hold,
                 policy_duration,
                 policy_worm_period
            FROM worm_hold_release_operation AS operation
            JOIN record ON record.id = operation.record_id
            JOIN retention_policy AS policy ON policy.id = record.retention_policy_id
            WHERE operation.id = NEW.operation_id
            FOR UPDATE OF operation;

            IF NOT FOUND
               OR operation_state <> 'PENDING_AUTHORIZATION'
               OR operation_digest <> NEW.canonical_sha256
               OR record_hold
               OR policy_duration = 'PERMANENT'
               OR policy_worm_period = 'PERMANENT' THEN
                RAISE EXCEPTION 'hold_release_authorization_refused';
            END IF;

            UPDATE worm_hold_release_operation
            SET state = 'AUTHORIZED', updated_at = now()
            WHERE id = NEW.operation_id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_worm_hold_release_authorize "
        "BEFORE INSERT ON worm_hold_release_authorization "
        "FOR EACH ROW EXECUTE FUNCTION authorize_worm_hold_release()"
    )
    op.execute(
        """
        CREATE FUNCTION refuse_worm_hold_release_authorization_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'worm_hold_release_authorization_is_immutable';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_worm_hold_release_authorization_immutable "
        "BEFORE UPDATE OR DELETE ON worm_hold_release_authorization "
        "FOR EACH ROW EXECUTE FUNCTION refuse_worm_hold_release_authorization_change()"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'easysynq_app') THEN
                REVOKE INSERT, UPDATE, DELETE ON worm_hold_release_authorization FROM easysynq_app;
            END IF;
        END $$
        """
    )


def _replace_r27_request() -> None:
    op.drop_constraint(
        "ck_pending_blob_purge_authority_shape",
        "pending_blob_purge",
        type_="check",
    )
    op.drop_constraint(
        "fk_pending_blob_purge_worm_request", "pending_blob_purge", type_="foreignkey"
    )
    op.drop_index("ix_worm_destroy_request_open", table_name="worm_destroy_request")
    op.drop_index("ix_worm_destroy_request_record_id", table_name="worm_destroy_request")
    op.drop_constraint(
        op.f("ck_worm_destroy_request_approver_neq_requester"),
        "worm_destroy_request",
        type_="check",
    )
    for constraint in (
        "fk_worm_destroy_request_requested_by_app_user",
        "fk_worm_destroy_request_approved_by_app_user",
        "fk_worm_destroy_request_cancelled_by_app_user",
    ):
        op.drop_constraint(constraint, "worm_destroy_request", type_="foreignkey")
    for column in (
        "legal_basis",
        "requested_by",
        "requested_at",
        "approved_by",
        "executed_at",
        "cancelled_by",
        "cancelled_at",
        "created_at",
    ):
        op.drop_column("worm_destroy_request", column)
    op.rename_table("worm_destroy_request", "r27_request")
    op.execute(
        "ALTER TABLE r27_request RENAME CONSTRAINT pk_worm_destroy_request TO pk_r27_request"
    )
    op.execute(
        "ALTER TABLE r27_request RENAME CONSTRAINT "
        "fk_worm_destroy_request_org_id_organization TO fk_r27_request_org_id_organization"
    )
    op.execute(
        "ALTER TABLE r27_request RENAME CONSTRAINT "
        "fk_worm_destroy_request_record_id_record TO fk_r27_request_record_id_record"
    )
    for column in (
        sa.Column("normalized_legal_basis", sa.Text(), nullable=False),
        sa.Column("legal_basis_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("state", _enum("r27_request_state"), nullable=True),
        sa.Column("requester_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approver_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requester_audit_event_id", sa.BigInteger(), nullable=True),
        sa.Column("approver_audit_event_id", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancellation_audit_event_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ):
        op.add_column("r27_request", column)
    for column, target in (
        ("requester_user_id", "app_user"),
        ("approver_user_id", "app_user"),
        ("cancelled_by_user_id", "app_user"),
    ):
        op.create_foreign_key(
            f"fk_r27_request_{column}_{target}",
            "r27_request",
            target,
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_check_constraint(
        op.f("ck_r27_request_legal_basis_length"),
        "r27_request",
        "length(normalized_legal_basis) BETWEEN 1 AND 4000",
    )
    op.create_check_constraint(
        op.f("ck_r27_request_legal_basis_sha256_shape"),
        "r27_request",
        "legal_basis_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_r27_request_approver_neq_requester"),
        "r27_request",
        "approver_user_id IS NULL OR requester_user_id IS NULL "
        "OR approver_user_id <> requester_user_id",
    )
    op.create_check_constraint(
        op.f("ck_r27_request_state_requires_requester"),
        "r27_request",
        "state IS NULL OR requester_user_id IS NOT NULL",
    )
    op.create_index("ix_r27_request_record_id", "r27_request", ["record_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_r27_request_open ON r27_request (record_id) "
        "WHERE state IS NULL OR state NOT IN ('EXECUTED', 'CANCELLED', 'STALE')"
    )


def _create_r27_authority() -> None:
    op.create_table(
        "r27_manifest",
        _uuid_pk(),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("manifest_nonce", sa.String(length=43), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("excluded_set_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("expected_state", _enum("r27_request_state"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _fk("r27_manifest", "request_id", "r27_request"),
        sa.PrimaryKeyConstraint("id", name="pk_r27_manifest"),
        sa.UniqueConstraint("request_id", name="uq_r27_manifest_request_id"),
        sa.UniqueConstraint("manifest_nonce", name="uq_r27_manifest_manifest_nonce"),
        sa.CheckConstraint("manifest_nonce ~ '^[A-Za-z0-9_-]{43}$'", name="nonce_shape"),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$' AND excluded_set_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_shape",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
    )
    op.create_table(
        "r27_manifest_target",
        _uuid_pk(),
        sa.Column("manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_order", sa.Integer(), nullable=False),
        sa.Column("blob_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("object_version_id", sa.Text(), nullable=False),
        _fk("r27_manifest_target", "manifest_id", "r27_manifest"),
        sa.PrimaryKeyConstraint("id", name="pk_r27_manifest_target"),
        sa.UniqueConstraint(
            "manifest_id",
            "target_order",
            name="uq_r27_manifest_target_manifest_id_target_order",
        ),
        sa.UniqueConstraint(
            "manifest_id",
            "bucket",
            "object_key",
            "object_version_id",
            name="uq_r27_manifest_target_physical_identity",
        ),
        sa.CheckConstraint("target_order >= 0", name="target_order_nonnegative"),
        sa.CheckConstraint(
            "blob_sha256 ~ '^[0-9a-f]{64}$'",
            name="blob_sha256_shape",
        ),
        sa.CheckConstraint(
            "length(object_version_id) BETWEEN 1 AND 1024",
            name="object_version_id_length",
        ),
    )
    op.create_table(
        "r27_manifest_derivative",
        _uuid_pk(),
        sa.Column("manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("derivative_order", sa.Integer(), nullable=False),
        sa.Column("kind", _enum("r27_derivative_kind"), nullable=False),
        sa.Column("domain_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blob_sha256", sa.CHAR(length=64), nullable=True),
        _fk("r27_manifest_derivative", "manifest_id", "r27_manifest"),
        sa.PrimaryKeyConstraint("id", name="pk_r27_manifest_derivative"),
        sa.UniqueConstraint(
            "manifest_id",
            "derivative_order",
            name="uq_r27_manifest_derivative_manifest_id_derivative_order",
        ),
        sa.UniqueConstraint(
            "manifest_id",
            "kind",
            "domain_id",
            name="uq_r27_manifest_derivative_manifest_kind_domain_id",
        ),
        sa.CheckConstraint(
            "derivative_order >= 0",
            name="derivative_order_nonnegative",
        ),
        sa.CheckConstraint(
            "blob_sha256 IS NULL OR blob_sha256 ~ '^[0-9a-f]{64}$'",
            name="blob_sha256_shape",
        ),
    )
    _create_r27_challenge_and_keys()


def _create_r27_challenge_and_keys() -> None:
    op.create_table(
        "r27_action_challenge",
        _uuid_pk(),
        sa.Column("action", _enum("r27_action_kind"), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("token_jti", sa.String(length=255), nullable=False),
        sa.Column("action_nonce", sa.String(length=43), nullable=False),
        sa.Column("accepted_claims", postgresql.JSONB(), nullable=False),
        sa.Column("manifest_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("r27_action_challenge", "request_id", "r27_request"),
        _fk("r27_action_challenge", "record_id", "record"),
        sa.PrimaryKeyConstraint("id", name="pk_r27_action_challenge"),
        sa.UniqueConstraint("issuer", "token_jti", name="uq_r27_action_challenge_issuer_token_jti"),
        sa.UniqueConstraint("action_nonce", name="uq_r27_action_challenge_action_nonce"),
        sa.CheckConstraint(
            "action_nonce ~ '^[A-Za-z0-9_-]{43}$'",
            name="nonce_shape",
        ),
        sa.CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="manifest_sha256_shape",
        ),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_create"),
    )
    op.create_table(
        "r27_authorizer_key",
        _uuid_pk(),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("active_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_r27_authorizer_key"),
        sa.UniqueConstraint("key_id", name="uq_r27_authorizer_key_key_id"),
        sa.UniqueConstraint("fingerprint", name="uq_r27_authorizer_key_fingerprint"),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="fingerprint_shape",
        ),
    )
    op.create_table(
        "r27_attestation",
        _uuid_pk(),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", _enum("r27_action_kind"), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("canonical_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("authorizer_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("app_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("token_jti", sa.String(length=255), nullable=False),
        sa.Column("audience", postgresql.JSONB(), nullable=False),
        sa.Column("authorized_party", sa.String(length=255), nullable=False),
        sa.Column("acr", sa.String(length=255), nullable=False),
        sa.Column("auth_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amr", postgresql.JSONB(), nullable=False),
        sa.Column("permission_granted", sa.Boolean(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _fk("r27_attestation", "challenge_id", "r27_action_challenge"),
        _fk("r27_attestation", "request_id", "r27_request"),
        _fk("r27_attestation", "authorizer_key_id", "r27_authorizer_key"),
        _fk("r27_attestation", "app_user_id", "app_user"),
        sa.PrimaryKeyConstraint("id", name="pk_r27_attestation"),
        sa.UniqueConstraint("challenge_id", name="uq_r27_attestation_challenge_id"),
        sa.UniqueConstraint("request_id", "action", name="uq_r27_attestation_request_id_action"),
        sa.CheckConstraint(
            "canonical_sha256 ~ '^[0-9a-f]{64}$'",
            name="canonical_sha256_shape",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(audience) = 'array' "
            "AND jsonb_array_length(audience) > 0 "
            "AND NOT jsonb_path_exists("
            'audience, \'$[*] ? (@.type() != "string" || @ like_regex "^\\\\s*$")\''
            ")",
            name="audience_nonempty_string_array",
        ),
        sa.CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
    )
    op.create_table(
        "r27_execution",
        _uuid_pk(),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state",
            _enum("r27_execution_state"),
            server_default=sa.text("'CLAIMED'"),
            nullable=False,
        ),
        sa.Column("result_code", _enum("r27_result_code"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("source_committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purge_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("r27_execution", "request_id", "r27_request"),
        sa.PrimaryKeyConstraint("id", name="pk_r27_execution"),
        sa.UniqueConstraint("request_id", name="uq_r27_execution_request_id"),
        sa.UniqueConstraint("execution_id", name="uq_r27_execution_execution_id"),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
    )
    op.create_foreign_key(
        "fk_blob_purge_execution_id_r27_execution",
        "blob",
        "r27_execution",
        ["purge_execution_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    _create_recovery_authority()


def _create_recovery_authority() -> None:
    op.create_table(
        "recovery_generation_verifier_key",
        _uuid_pk(),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("algorithm", sa.String(length=16), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("installed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installed_audit_event_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk(
            "recovery_generation_verifier_key",
            "installed_by_user_id",
            "app_user",
            name="fk_recovery_verifier_key_installed_by_app_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_generation_verifier_key"),
        sa.UniqueConstraint("key_id", name="uq_recovery_generation_verifier_key_key_id"),
        sa.UniqueConstraint("fingerprint", name="uq_recovery_generation_verifier_key_fingerprint"),
        sa.CheckConstraint(
            "algorithm = 'ED25519'",
            name="algorithm_ed25519",
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="fingerprint_shape",
        ),
    )
    op.create_table(
        "recovery_generation_witness",
        _uuid_pk(),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("witness_nonce", sa.String(length=43), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manifest_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("generation_id", sa.String(length=255), nullable=False),
        sa.Column("generation_identity", sa.String(length=255), nullable=False),
        sa.Column("excluded_set_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("canonical_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        _fk(
            "recovery_generation_witness",
            "key_id",
            "recovery_generation_verifier_key",
            name="fk_recovery_witness_key_id_verifier_key",
        ),
        _fk("recovery_generation_witness", "request_id", "r27_request"),
        _fk(
            "recovery_generation_witness",
            "consumed_execution_id",
            "r27_execution",
            name="fk_recovery_witness_execution_id_r27_execution",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_generation_witness"),
        sa.UniqueConstraint(
            "key_id",
            "witness_nonce",
            name="uq_recovery_generation_witness_key_id_witness_nonce",
        ),
        sa.UniqueConstraint(
            "manifest_sha256",
            "generation_id",
            name="uq_recovery_generation_witness_manifest_generation",
        ),
        sa.UniqueConstraint("request_id", name="uq_recovery_generation_witness_request_id"),
        sa.CheckConstraint(
            "witness_nonce ~ '^[A-Za-z0-9_-]{43}$'",
            name="nonce_shape",
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$' AND excluded_set_sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_shape",
        ),
        sa.CheckConstraint(
            "generation_identity ~ '[^[:space:]]'",
            name="generation_identity_nonblank",
        ),
        sa.CheckConstraint("result = 'VERIFIED'", name="result_verified"),
    )


def _upgrade_pending_blob_purge() -> None:
    op.alter_column(
        "pending_blob_purge",
        "record_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "pending_blob_purge",
        "disposition_event_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("pending_blob_purge", "authority_bound")
    op.alter_column(
        "pending_blob_purge",
        "worm_destroy_request_id",
        new_column_name="r27_request_id",
        existing_type=postgresql.UUID(as_uuid=True),
        existing_nullable=True,
    )
    for column in (
        sa.Column("object_version_id", sa.Text(), nullable=False),
        sa.Column("r27_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "state",
            _enum("maintenance_state"),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ):
        op.add_column("pending_blob_purge", column)
    op.create_foreign_key(
        "fk_pending_blob_purge_r27_request_id_r27_request",
        "pending_blob_purge",
        "r27_request",
        ["r27_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_pending_blob_purge_r27_execution_id_r27_execution",
        "pending_blob_purge",
        "r27_execution",
        ["r27_execution_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_pending_blob_purge_authority_shape"),
        "pending_blob_purge",
        "record_id IS NOT NULL AND disposition_event_id IS NOT NULL "
        "AND (NOT bypass_governance OR "
        "(r27_request_id IS NOT NULL AND r27_execution_id IS NOT NULL))",
    )
    op.create_check_constraint(
        op.f("ck_pending_blob_purge_object_version_id_length"),
        "pending_blob_purge",
        "length(object_version_id) BETWEEN 1 AND 1024",
    )
    op.create_check_constraint(
        op.f("ck_pending_blob_purge_attempt_nonnegative"),
        "pending_blob_purge",
        "attempt_count >= 0",
    )


def _create_maintenance_intent() -> None:
    op.create_table(
        "audit_maintenance_schedule",
        _uuid_pk(),
        sa.Column("job_kind", _enum("audit_maintenance_job_kind"), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "state",
            _enum("maintenance_state"),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_maintenance_schedule"),
        sa.UniqueConstraint("job_kind", name="uq_audit_maintenance_schedule_job_kind"),
        sa.CheckConstraint("interval_seconds > 0", name="interval_positive"),
    )
    op.create_table(
        "backup_maintenance_operation",
        _uuid_pk(),
        sa.Column("kind", _enum("backup_maintenance_kind"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("backup_policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", _enum("maintenance_source"), nullable=False),
        sa.Column(
            "state",
            _enum("maintenance_state"),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("result_code", sa.String(length=64), nullable=True),
        sa.Column("result_detail", sa.String(length=512), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("backup_maintenance_operation", "org_id", "organization"),
        _fk("backup_maintenance_operation", "backup_policy_id", "backup_policy"),
        _fk("backup_maintenance_operation", "requested_by_user_id", "app_user"),
        sa.PrimaryKeyConstraint("id", name="pk_backup_maintenance_operation"),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_nonnegative"),
    )


def _refuse_populated_0089_downgrade(bind: sa.Connection) -> None:
    populated = bind.execute(
        sa.text(
            """
            SELECT
                EXISTS (
                    SELECT 1 FROM blob
                    WHERE worm_locked
                       OR purged_at IS NOT NULL
                       OR purge_execution_id IS NOT NULL
                    LIMIT 1
                )
                OR EXISTS (SELECT 1 FROM document_worm_config LIMIT 1)
                OR EXISTS (
                    SELECT 1
                    FROM retention_revision AS rr
                    LEFT JOIN retention_policy AS rp ON rp.id = rr.retention_policy_id
                    WHERE rr.authority_kind <> 'POLICY'
                       OR rr.retention_policy_id IS NULL
                       OR rr.document_worm_config_id IS NOT NULL
                       OR rr.revision_no <> 1
                       OR rr.state <> 'ACTIVE'
                       OR rr.proposed_values IS NOT NULL
                       OR rr.actor_user_id IS NOT NULL
                       OR rr.audit_event_id IS NOT NULL
                       OR rp.id IS NULL
                       OR rr.active_values <> jsonb_build_object(
                            'basis', rp.basis::text,
                            'duration', rp.duration,
                            'disposition_action', rp.disposition_action::text,
                            'review_required', rp.review_required,
                            'worm_lock_period', rp.worm_lock_period
                       )
                    LIMIT 1
                )
                OR EXISTS (SELECT 1 FROM retention_operation LIMIT 1)
                OR EXISTS (SELECT 1 FROM retention_operation_target LIMIT 1)
                OR EXISTS (SELECT 1 FROM worm_hold_release_operation LIMIT 1)
                OR EXISTS (SELECT 1 FROM worm_hold_release_authorization LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_request LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_manifest LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_manifest_target LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_manifest_derivative LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_action_challenge LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_authorizer_key LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_attestation LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_execution LIMIT 1)
                OR EXISTS (SELECT 1 FROM recovery_generation_verifier_key LIMIT 1)
                OR EXISTS (SELECT 1 FROM recovery_generation_witness LIMIT 1)
                OR EXISTS (SELECT 1 FROM pending_blob_purge LIMIT 1)
                OR EXISTS (SELECT 1 FROM audit_maintenance_schedule LIMIT 1)
                OR EXISTS (SELECT 1 FROM backup_maintenance_operation LIMIT 1)
            """
        )
    ).scalar_one()
    if populated:
        raise RuntimeError("populated_0089_downgrade_refused")


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_populated_0089_downgrade(bind)

    op.drop_table("backup_maintenance_operation")
    op.drop_table("audit_maintenance_schedule")
    _downgrade_pending_blob_purge()
    op.drop_table("recovery_generation_witness")
    op.drop_table("recovery_generation_verifier_key")
    op.drop_constraint("fk_blob_purge_execution_id_r27_execution", "blob", type_="foreignkey")
    op.drop_table("r27_execution")
    op.drop_table("r27_attestation")
    op.drop_table("r27_authorizer_key")
    op.drop_table("r27_action_challenge")
    op.drop_table("r27_manifest_derivative")
    op.drop_table("r27_manifest_target")
    op.drop_table("r27_manifest")
    _restore_worm_destroy_request()
    _drop_hold_release()
    op.drop_table("retention_operation_target")
    op.drop_table("retention_operation")
    _drop_document_retention()
    _downgrade_blob()

    for name in reversed(tuple(_ENUM_VALUES)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)


def _downgrade_pending_blob_purge() -> None:
    for constraint, constraint_type in (
        ("ck_pending_blob_purge_attempt_nonnegative", "check"),
        ("ck_pending_blob_purge_object_version_id_length", "check"),
        ("ck_pending_blob_purge_authority_shape", "check"),
        ("fk_pending_blob_purge_r27_execution_id_r27_execution", "foreignkey"),
        ("fk_pending_blob_purge_r27_request_id_r27_request", "foreignkey"),
    ):
        op.drop_constraint(
            op.f(constraint) if constraint_type == "check" else constraint,
            "pending_blob_purge",
            type_=constraint_type,
        )
    for column in (
        "updated_at",
        "completed_at",
        "claimed_at",
        "error_detail",
        "error_code",
        "attempt_count",
        "state",
        "r27_execution_id",
        "object_version_id",
    ):
        op.drop_column("pending_blob_purge", column)
    op.alter_column(
        "pending_blob_purge",
        "r27_request_id",
        new_column_name="worm_destroy_request_id",
        existing_type=postgresql.UUID(as_uuid=True),
        existing_nullable=True,
    )
    op.add_column(
        "pending_blob_purge",
        sa.Column(
            "authority_bound",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.alter_column(
        "pending_blob_purge",
        "disposition_event_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "pending_blob_purge",
        "record_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def _restore_worm_destroy_request() -> None:
    op.drop_index("uq_r27_request_open", table_name="r27_request")
    op.drop_index("ix_r27_request_record_id", table_name="r27_request")
    for constraint in (
        "ck_r27_request_state_requires_requester",
        "ck_r27_request_approver_neq_requester",
        "ck_r27_request_legal_basis_sha256_shape",
        "ck_r27_request_legal_basis_length",
    ):
        op.drop_constraint(op.f(constraint), "r27_request", type_="check")
    for column, target in (
        ("requester_user_id", "app_user"),
        ("approver_user_id", "app_user"),
        ("cancelled_by_user_id", "app_user"),
    ):
        op.drop_constraint(f"fk_r27_request_{column}_{target}", "r27_request", type_="foreignkey")
    for column in (
        "updated_at",
        "created_at",
        "failed_at",
        "stale_at",
        "cancelled_at",
        "approved_at",
        "requested_at",
        "error_detail",
        "error_code",
        "cancellation_audit_event_id",
        "cancelled_by_user_id",
        "approver_audit_event_id",
        "requester_audit_event_id",
        "approver_user_id",
        "requester_user_id",
        "state",
        "legal_basis_sha256",
        "normalized_legal_basis",
    ):
        op.drop_column("r27_request", column)
    op.rename_table("r27_request", "worm_destroy_request")
    op.execute(
        "ALTER TABLE worm_destroy_request RENAME CONSTRAINT "
        "pk_r27_request TO pk_worm_destroy_request"
    )
    op.execute(
        "ALTER TABLE worm_destroy_request RENAME CONSTRAINT "
        "fk_r27_request_org_id_organization TO fk_worm_destroy_request_org_id_organization"
    )
    op.execute(
        "ALTER TABLE worm_destroy_request RENAME CONSTRAINT "
        "fk_r27_request_record_id_record TO fk_worm_destroy_request_record_id_record"
    )
    for column in (
        sa.Column("legal_basis", sa.Text(), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ):
        op.add_column("worm_destroy_request", column)
    for column in ("requested_by", "approved_by", "cancelled_by"):
        op.create_foreign_key(
            f"fk_worm_destroy_request_{column}_app_user",
            "worm_destroy_request",
            "app_user",
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_check_constraint(
        op.f("ck_worm_destroy_request_approver_neq_requester"),
        "worm_destroy_request",
        "approved_by IS NULL OR approved_by <> requested_by",
    )
    op.create_index("ix_worm_destroy_request_record_id", "worm_destroy_request", ["record_id"])
    op.execute(
        "CREATE UNIQUE INDEX ix_worm_destroy_request_open "
        "ON worm_destroy_request (record_id) "
        "WHERE executed_at IS NULL AND cancelled_at IS NULL"
    )
    op.create_foreign_key(
        "fk_pending_blob_purge_worm_request",
        "pending_blob_purge",
        "worm_destroy_request",
        ["worm_destroy_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_pending_blob_purge_authority_shape",
        "pending_blob_purge",
        """
        NOT authority_bound
        OR (
            record_id IS NOT NULL
            AND disposition_event_id IS NOT NULL
            AND (NOT bypass_governance OR worm_destroy_request_id IS NOT NULL)
        )
        """,
    )


def _drop_hold_release() -> None:
    op.execute(
        "DROP TRIGGER trg_worm_hold_release_authorization_immutable "
        "ON worm_hold_release_authorization"
    )
    op.execute("DROP TRIGGER trg_worm_hold_release_authorize ON worm_hold_release_authorization")
    op.execute("DROP FUNCTION refuse_worm_hold_release_authorization_change()")
    op.execute("DROP FUNCTION authorize_worm_hold_release()")
    op.drop_table("worm_hold_release_authorization")
    op.drop_table("worm_hold_release_operation")


def _drop_document_retention() -> None:
    op.execute("DELETE FROM retention_revision")
    op.drop_index("uq_retention_revision_config_proposed", table_name="retention_revision")
    op.drop_index("uq_retention_revision_policy_proposed", table_name="retention_revision")
    op.drop_table("retention_revision")
    op.drop_constraint(
        op.f("ck_document_version_retention_authority_shape"),
        "document_version",
        type_="check",
    )
    op.drop_constraint(
        "fk_document_version_worm_config_id_document_worm_config",
        "document_version",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_document_version_retention_policy_id_retention_policy",
        "document_version",
        type_="foreignkey",
    )
    for column in (
        "retention_basis_date",
        "document_worm_config_id",
        "retention_policy_id",
        "retention_authority_kind",
    ):
        op.drop_column("document_version", column)
    op.drop_column("record", "retention_basis_provisional")
    op.drop_constraint(
        op.f("ck_retention_policy_active_revision_positive"),
        "retention_policy",
        type_="check",
    )
    op.drop_column("retention_policy", "active_revision_no")
    op.drop_table("document_worm_config")


def _downgrade_blob() -> None:
    op.drop_index("uq_blob_worm_physical_identity", table_name="blob")
    op.drop_constraint(op.f("ck_blob_worm_assertion_shape"), "blob", type_="check")
    op.drop_constraint(op.f("ck_blob_object_version_id_length"), "blob", type_="check")
    for column in (
        "purge_execution_id",
        "purged_at",
        "worm_legal_hold_verified_at",
        "worm_legal_hold",
        "worm_retention_verified_at",
        "worm_asserted_at",
        "worm_asserted_retain_until",
        "worm_enforced_mode",
        "object_version_id",
    ):
        op.drop_column("blob", column)
