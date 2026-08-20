"""Establish exact WORM retention authority and container identity schema.

Revision ID: 0089_worm_retention_container_identity
Revises: 0088_bootstrap_credential
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from psycopg import sql as psycopg_sql
from sqlalchemy.dialects import postgresql

from easysynq_api.config import get_settings
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

APP_ROLE = "easysynq_app"
LINKER_ROLE = "easysynq_linker"
R27_CLIENT_ID = "easysynq-r27-authorizer"
R27_ACR = "urn:easysynq:acr:r27-webauthn"
_APP_BLOB_INSERT_COLUMNS = (
    "sha256",
    "org_id",
    "size_bytes",
    "mime_type",
    "bucket",
    "object_key",
    "object_version_id",
    "worm_locked",
    "worm_enforced_mode",
    "worm_asserted_retain_until",
    "worm_asserted_at",
    "worm_retain_until",
    "worm_retention_verified_at",
    "worm_legal_hold",
    "worm_legal_hold_verified_at",
    "sse",
)
_APP_RECORD_UPDATE_COLUMNS = (
    "content_hash",
    "form_field_values",
    "superseded_by_correction",
    "disposition_state",
    "legal_hold",
    "structured_pdf_blob_sha256",
)
_APP_RETENTION_POLICY_UPDATE_COLUMNS = (
    "name",
    "applies_to",
    "basis",
    "duration",
    "disposition_action",
    "review_required",
    "worm_lock_period",
    "active",
    "archived_at",
    "archived_by",
    "updated_at",
)
_TASK4_RECORD_UPDATE_COLUMNS = (
    *_APP_RECORD_UPDATE_COLUMNS,
    "retention_basis_date",
    "retention_basis_provisional",
)
_TASK4_RETENTION_POLICY_UPDATE_COLUMNS = (
    *_APP_RETENTION_POLICY_UPDATE_COLUMNS,
    "active_revision_no",
)
_AUTHORITY_ROLE_PASSWORD_FIELDS = {
    APP_ROLE: "app_db_password",
    LINKER_ROLE: "linker_db_password",
    "easysynq_retention": "retention_db_password",
    "easysynq_hold_authorizer": "hold_authorizer_db_password",
    "easysynq_hold_maintenance": "hold_maintenance_db_password",
    "easysynq_r27_authorizer": "r27_authorizer_db_password",
    "easysynq_r27_maintenance": "r27_maintenance_db_password",
    "easysynq_r27_authorizer_key_manager": "r27_authorizer_key_manager_db_password",
    "easysynq_recovery_key_manager": "recovery_key_manager_db_password",
    "easysynq_r27_role_manager": "r27_role_manager_db_password",
    "easysynq_audit_signer": "audit_signer_db_password",
    "easysynq_backup": "backup_db_password",
}
_NEW_AUTHORITY_ROLES = tuple(_AUTHORITY_ROLE_PASSWORD_FIELDS)[2:]
_AUTHORITY_FUNCTION_NAMES = (
    "easysynq_assert_worm_record_live",
    "easysynq_lock_document_worm_config",
    "easysynq_lock_worm_blob",
    "easysynq_lock_worm_owners",
    "easysynq_record_worm_assertion",
    "easysynq_claim_retention_targets",
    "easysynq_fail_retention_target",
    "easysynq_ratchet_worm_assertion",
    "easysynq_enqueue_ordinary_exact_purge",
    "easysynq_claim_ordinary_exact_purges",
    "easysynq_fail_ordinary_exact_purge",
    "easysynq_record_ordinary_exact_purge",
    "easysynq_authorize_hold_release",
    "easysynq_claim_hold_releases",
    "easysynq_fail_hold_release",
    "easysynq_record_ordinary_hold_release",
    "easysynq_accept_r27_request",
    "easysynq_accept_r27_approval",
    "easysynq_cancel_r27_request",
    "easysynq_mark_r27_stale",
    "easysynq_claim_r27_finalizations",
    "easysynq_fail_r27_execution",
    "easysynq_record_r27_hold_release",
    "easysynq_claim_r27_exact_purges",
    "easysynq_fail_r27_exact_purge",
    "easysynq_record_r27_purge",
    "easysynq_record_r27_surviving_owner",
    "easysynq_install_r27_authorizer_key",
    "easysynq_retire_r27_authorizer_key",
    "easysynq_revoke_r27_authorizer_key",
    "easysynq_install_recovery_verifier_key",
    "easysynq_retire_recovery_verifier_key",
    "easysynq_revoke_recovery_verifier_key",
    "easysynq_begin_r27_role_membership",
    "easysynq_complete_r27_role_membership",
    "easysynq_fail_r27_role_membership",
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


def _execute_composed(
    bind: sa.Connection,
    statement: psycopg_sql.Composed,
    parameters: tuple[object, ...] = (),
) -> None:
    """Execute composed DDL directly through psycopg, preserving bound secrets."""
    with bind.connection.driver_connection.cursor() as cursor:
        cursor.execute(statement, parameters)


def _preflight_database_authority(bind: sa.Connection) -> str:
    owner = bind.execute(sa.text("SELECT current_user")).scalar_one()
    schema_owner = bind.execute(
        sa.text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'")
    ).scalar_one()
    database_owner = bind.execute(
        sa.text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()")
    ).scalar_one()
    schema_owner_is_database_owner = schema_owner == "pg_database_owner" and owner == database_owner
    if schema_owner != owner and not schema_owner_is_database_owner:
        raise RuntimeError("database_authority_wrong_public_schema_owner")

    unexpected_default_grantors = (
        bind.execute(
            sa.text(
                """
            SELECT DISTINCT grantor.rolname
            FROM pg_default_acl defaults
            JOIN pg_roles grantor ON grantor.oid=defaults.defaclrole
            JOIN pg_namespace namespace ON namespace.oid=defaults.defaclnamespace
            CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl
            LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee
            WHERE namespace.nspname='public'
              AND grantee.rolname=:app_role
              AND grantor.rolname<>:owner
            """
            ),
            {"app_role": APP_ROLE, "owner": owner},
        )
        .scalars()
        .all()
    )
    if unexpected_default_grantors:
        raise RuntimeError("database_authority_wrong_default_acl_grantor")

    memberships = bind.execute(
        sa.text(
            """
            SELECT member.rolname, granted.rolname
            FROM pg_auth_members membership
            JOIN pg_roles member ON member.oid=membership.member
            JOIN pg_roles granted ON granted.oid=membership.roleid
            WHERE member.rolname=ANY(:roles) OR granted.rolname=ANY(:roles)
            LIMIT 1
            """
        ),
        {"roles": list(_AUTHORITY_ROLE_PASSWORD_FIELDS)},
    ).first()
    if memberships is not None:
        raise RuntimeError("database_authority_role_membership_refused")

    preexisting_purpose_roles = bind.execute(
        sa.text("SELECT rolname FROM pg_roles WHERE rolname=ANY(:roles) ORDER BY rolname"),
        {"roles": list(_NEW_AUTHORITY_ROLES)},
    ).scalars()
    if next(iter(preexisting_purpose_roles), None) is not None:
        raise RuntimeError("database_authority_preexisting_purpose_role_refused")
    return owner


def _create_and_normalize_authority_roles(bind: sa.Connection) -> str:
    owner = _preflight_database_authority(bind)
    settings = get_settings()
    for role, password_field in _AUTHORITY_ROLE_PASSWORD_FIELDS.items():
        if not bind.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"),
            {"role": role},
        ).scalar_one():
            _execute_composed(
                bind,
                psycopg_sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOBYPASSRLS").format(
                    psycopg_sql.Identifier(role)
                ),
            )
        _execute_composed(
            bind,
            psycopg_sql.SQL(
                "ALTER ROLE {} WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(
                psycopg_sql.Identifier(role),
                psycopg_sql.Literal(getattr(settings, password_field)),
            ),
        )
        _execute_composed(
            bind,
            psycopg_sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                psycopg_sql.Identifier(role)
            ),
        )

    database_name = bind.execute(sa.text("SELECT current_database()"), {}).scalar_one()
    _execute_composed(
        bind,
        psycopg_sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            psycopg_sql.Identifier(database_name), psycopg_sql.Identifier("easysynq_backup")
        ),
    )

    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    op.execute(
        f"REVOKE ALL ON blob, document_version, evidence_blob, pending_blob_purge FROM {APP_ROLE}"
    )
    op.execute(
        f"REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER "
        f"ON audit_checkpoint,audit_checkpoint_sink FROM {APP_ROLE}"
    )
    op.execute(f"GRANT SELECT ON audit_checkpoint,audit_checkpoint_sink TO {APP_ROLE}")
    op.execute(f"REVOKE EXECUTE ON FUNCTION easysynq_create_audit_partition(date) FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {LINKER_ROLE}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {LINKER_ROLE}")
    op.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {LINKER_ROLE}")
    _execute_composed(
        bind,
        psycopg_sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {}"
        ).format(psycopg_sql.Identifier(owner), psycopg_sql.Identifier(APP_ROLE)),
    )
    _execute_composed(
        bind,
        psycopg_sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "REVOKE USAGE, SELECT ON SEQUENCES FROM {}"
        ).format(psycopg_sql.Identifier(owner), psycopg_sql.Identifier(APP_ROLE)),
    )
    _execute_composed(
        bind,
        psycopg_sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
        ).format(psycopg_sql.Identifier(owner)),
    )
    _execute_composed(
        bind,
        psycopg_sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
        ).format(psycopg_sql.Identifier(owner)),
    )
    return owner


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_legacy_physical_owner_state(bind)
    _create_and_normalize_authority_roles(bind)
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
    op.execute(
        "ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'RECORD_LEGAL_HOLD_RELEASE_AUTHORIZED'"
    )

    _upgrade_blob()
    _create_document_retention()
    _create_retention_operations()
    _create_hold_release()
    _replace_r27_request()
    _create_r27_authority()
    _upgrade_pending_blob_purge()
    _create_maintenance_intent()
    _create_database_authority()


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
    op.create_check_constraint(
        op.f("ck_blob_purge_provenance_shape"),
        "blob",
        "purge_execution_id IS NULL OR purged_at IS NOT NULL",
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
    hold_authority = _ordinary_hold_authority_sql(
        operation_sql="operation",
        record_sql="source_record",
        blob_sql="source_blob",
        user_sql="initiator",
        edge_sql="source_edge",
    )
    hold_obligation = _hold_obligation_exists_sql(
        org_sql="locked_org",
        blob_sha_sql="locked_sha",
    )
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
        f"""
        CREATE FUNCTION public.easysynq_guard_hold_release_authorization_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            operation_id uuid;
            locked_org uuid;
            locked_sha text;
        BEGIN
            SELECT operation.id,operation.org_id,operation.blob_sha256
            INTO operation_id,locked_org,locked_sha
            FROM public.worm_hold_release_operation AS operation
            JOIN public.record AS source_record
              ON source_record.id=operation.record_id
            JOIN public.app_user AS initiator
              ON initiator.id=operation.initiated_by_user_id
            JOIN public.blob AS source_blob
              ON source_blob.sha256=operation.blob_sha256
            JOIN public.evidence_blob AS source_edge
              ON source_edge.record_id=operation.record_id
             AND source_edge.blob_sha256=operation.blob_sha256
            WHERE operation.id = NEW.operation_id
              AND operation.state='PENDING_AUTHORIZATION'
              AND operation.canonical_sha256=NEW.canonical_sha256
              AND {hold_authority}
            FOR UPDATE OF operation
            FOR SHARE OF source_record,initiator,source_blob;

            IF NOT FOUND
               OR {hold_obligation} THEN
                RAISE EXCEPTION 'hold_release_authorization_refused';
            END IF;

            UPDATE public.worm_hold_release_operation
            SET state = 'AUTHORIZED', updated_at = now()
            WHERE id = NEW.operation_id;
            RETURN NEW;
        END;
        $$;
        REVOKE ALL ON FUNCTION
          public.easysynq_guard_hold_release_authorization_insert() FROM PUBLIC
        """
    )
    op.execute(
        "CREATE TRIGGER trg_worm_hold_release_authorize "
        "BEFORE INSERT ON public.worm_hold_release_authorization "
        "FOR EACH ROW EXECUTE FUNCTION "
        "public.easysynq_guard_hold_release_authorization_insert()"
    )
    op.execute(
        """
        CREATE FUNCTION public.easysynq_guard_hold_release_authorization_history()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            RAISE EXCEPTION 'worm_hold_release_authorization_is_immutable';
        END;
        $$;
        REVOKE ALL ON FUNCTION
          public.easysynq_guard_hold_release_authorization_history() FROM PUBLIC
        """
    )
    op.execute(
        "CREATE TRIGGER trg_worm_hold_release_authorization_immutable "
        "BEFORE UPDATE OR DELETE ON public.worm_hold_release_authorization "
        "FOR EACH ROW EXECUTE FUNCTION "
        "public.easysynq_guard_hold_release_authorization_history()"
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
        "(state IS NULL AND requester_user_id IS NULL AND requester_audit_event_id IS NULL "
        "AND requested_at IS NULL) OR "
        "(state='STALE' AND ((requester_user_id IS NULL "
        "AND requester_audit_event_id IS NULL AND requested_at IS NULL) OR "
        "(requester_user_id IS NOT NULL AND requester_audit_event_id IS NOT NULL "
        "AND requested_at IS NOT NULL))) OR "
        "(state IS NOT NULL AND state<>'STALE' AND requester_user_id IS NOT NULL "
        "AND requester_audit_event_id IS NOT NULL AND requested_at IS NOT NULL)",
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
        sa.Column("installed_by_identity", sa.String(length=255), nullable=False),
        sa.Column("installed_audit_event_id", sa.BigInteger(), nullable=False),
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
        sa.CheckConstraint(
            "installed_by_identity ~ '[^[:space:]]'",
            name="installed_by_identity_nonblank",
        ),
        sa.CheckConstraint(
            "(retired_at IS NULL OR retired_at>=active_at) "
            "AND (revoked_at IS NULL OR revoked_at>=active_at) "
            "AND (retired_at IS NULL OR revoked_at IS NULL OR revoked_at>=retired_at)",
            name="lifecycle_monotone",
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
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
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
    for column in (
        sa.Column("r27_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("r27_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
    ):
        op.add_column("disposition_event", column)
    op.create_foreign_key(
        "fk_disposition_event_r27_request_id_r27_request",
        "disposition_event",
        "r27_request",
        ["r27_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_disposition_event_r27_execution_id_r27_execution",
        "disposition_event",
        "r27_execution",
        ["r27_execution_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_disposition_event_r27_authority_shape",
        "disposition_event",
        "(is_worm_destroy AND r27_request_id IS NOT NULL AND r27_execution_id IS NOT NULL) "
        "OR (NOT is_worm_destroy AND r27_request_id IS NULL AND r27_execution_id IS NULL)",
    )


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
        sa.Column("installed_by_identity", sa.String(length=255), nullable=False),
        sa.Column("installed_audit_event_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
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
        sa.CheckConstraint(
            "installed_by_identity ~ '[^[:space:]]'",
            name="installed_by_identity_nonblank",
        ),
        sa.CheckConstraint(
            "(retired_at IS NULL OR retired_at>=not_before) "
            "AND (revoked_at IS NULL OR revoked_at>=not_before) "
            "AND (retired_at IS NULL OR revoked_at IS NULL OR revoked_at>=retired_at)",
            name="lifecycle_monotone",
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
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_audit_event_id", sa.BigInteger(), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=64), nullable=True),
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
        sa.CheckConstraint(
            "(invalidated_at IS NULL AND invalidation_audit_event_id IS NULL "
            "AND invalidation_reason IS NULL) OR (invalidated_at IS NOT NULL "
            "AND invalidation_audit_event_id IS NOT NULL AND invalidation_reason='KEY_REVOKED')",
            name="invalidation_shape",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_recovery_generation_witness_active_request "
        "ON recovery_generation_witness(request_id) WHERE invalidated_at IS NULL"
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
        "AND ((NOT bypass_governance AND r27_request_id IS NULL "
        "AND r27_execution_id IS NULL) OR "
        "(bypass_governance AND r27_request_id IS NOT NULL "
        "AND r27_execution_id IS NOT NULL))",
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
    op.create_table(
        "r27_execution_target_result",
        _uuid_pk(),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manifest_target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_code", _enum("r27_result_code"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_marker_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("surviving_owner_kind", sa.String(length=32), nullable=True),
        sa.Column("surviving_owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        _fk(
            "r27_execution_target_result",
            "execution_id",
            "r27_execution",
            name="fk_r27_target_result_execution",
        ),
        _fk(
            "r27_execution_target_result",
            "manifest_target_id",
            "r27_manifest_target",
            name="fk_r27_target_result_manifest_target",
        ),
        _fk(
            "r27_execution_target_result",
            "purge_marker_id",
            "pending_blob_purge",
            name="fk_r27_target_result_purge_marker",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_r27_execution_target_result"),
        sa.UniqueConstraint(
            "execution_id", "manifest_target_id", name="uq_r27_execution_target_result"
        ),
        sa.CheckConstraint(
            "(result_code='PHYSICAL_ERASED' AND purge_marker_id IS NOT NULL "
            "AND surviving_owner_kind IS NULL AND surviving_owner_id IS NULL) OR "
            "(result_code='LOGICAL_ONLY_SURVIVING_OWNER' AND purge_marker_id IS NULL "
            "AND surviving_owner_kind IN "
            "('DOCUMENT_VERSION','EVIDENCE_BLOB','SEALED_PACK') "
            "AND surviving_owner_id IS NOT NULL)",
            name="authority_shape",
        ),
    )


def _create_maintenance_intent() -> None:
    op.create_table(
        "r27_role_membership_operation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("operator_identity", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=512), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_event_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _fk("r27_role_membership_operation", "user_id", "app_user"),
        _fk("r27_role_membership_operation", "org_id", "organization"),
        sa.PrimaryKeyConstraint("id", name="pk_r27_role_membership_operation"),
        sa.CheckConstraint("action IN ('ASSIGN','REVOKE')", name="action_closed"),
        sa.CheckConstraint("state IN ('REQUESTED','AUDITED','FAILED')", name="state_closed"),
        sa.CheckConstraint("operator_identity ~ '[^[:space:]]'", name="operator_identity_nonblank"),
        sa.CheckConstraint(
            "(state='REQUESTED' AND audit_event_id IS NULL AND completed_at IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(state='AUDITED' AND audit_event_id IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(state='FAILED' AND audit_event_id IS NULL AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL AND btrim(error_code)<>'' "
            "AND length(error_code)<=64 "
            "AND length(COALESCE(error_detail,''))<=512)",
            name="state_shape",
        ),
    )
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


def _create_definer_function(
    declaration: str,
    identity: str,
    role: str,
    body: str,
) -> None:
    parameter_list = declaration.split("(", 1)[1].split(")", 1)[0]
    required_parameters = tuple(
        parameter.strip().split()[0]
        for parameter in parameter_list.split(",")
        if parameter.strip() and parameter.strip().split()[0] != "p_detail"
    )
    if required_parameters:
        required_guard = " OR ".join(f"{parameter} IS NULL" for parameter in required_parameters)
        body = body.replace(
            "BEGIN",
            "BEGIN\n"
            f"            IF {required_guard} THEN\n"
            "                RAISE EXCEPTION 'required_argument_is_null';\n"
            "            END IF;",
            1,
        )
    for observation_parameter in (
        "p_at",
        "p_active_at",
        "p_claimed_at",
        "p_failed_at",
        "p_verified_at",
    ):
        if f"{observation_parameter} timestamptz" not in declaration:
            continue
        observation_guard = f"""
            IF {observation_parameter} IS NULL
               OR {observation_parameter} < clock_timestamp() - interval '5 minutes'
               OR {observation_parameter} > clock_timestamp() + interval '5 minutes' THEN
                RAISE EXCEPTION 'observation_time_refused';
            END IF;
        """
        body = body.replace("BEGIN", f"BEGIN\n{observation_guard}", 1)
    op.execute(
        f"""
        CREATE FUNCTION public.{declaration}
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $function$
        {body}
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{identity} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{identity} TO {role}")


def _create_worm_guard_triggers() -> None:
    op.execute(
        f"""
        CREATE FUNCTION public.easysynq_guard_app_disposition_insert() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp AS $function$
        BEGIN
            IF session_user='{APP_ROLE}'
               AND (NEW.is_worm_destroy
                    OR NEW.r27_request_id IS NOT NULL
                    OR NEW.r27_execution_id IS NOT NULL) THEN
                RAISE EXCEPTION 'app_r27_disposition_insert_refused';
            END IF;
            RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.easysynq_guard_app_disposition_insert() FROM PUBLIC;
        CREATE TRIGGER trg_app_disposition_insert_guard
        BEFORE INSERT ON public.disposition_event
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_app_disposition_insert();
        """
    )


def _valid_destructive_event_exists_sql(*, record_sql: str) -> str:
    """Return the immutable ordinary-or-R27 destructive-event predicate for one Record."""
    return f"""
        EXISTS (
            SELECT 1
            FROM public.disposition_event owner_destroy
            WHERE owner_destroy.org_id={record_sql}.org_id
              AND owner_destroy.record_id={record_sql}.id
              AND owner_destroy.action='DESTROY'
              AND owner_destroy.tombstone
              AND (
                  (
                      NOT owner_destroy.is_worm_destroy
                      AND owner_destroy.r27_request_id IS NULL
                      AND owner_destroy.r27_execution_id IS NULL
                      AND owner_destroy.derived_from_disposition_event_id IS NULL
                      AND owner_destroy.policy_id={record_sql}.retention_policy_id
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM public.r27_request owner_request
                      JOIN public.r27_execution owner_execution
                        ON owner_execution.id=owner_destroy.r27_execution_id
                       AND owner_execution.request_id=owner_request.id
                       AND owner_execution.source_committed_at IS NOT NULL
                      JOIN public.disposition_event owner_source
                        ON owner_source.id=COALESCE(
                            owner_destroy.derived_from_disposition_event_id,
                            owner_destroy.id
                        )
                      WHERE owner_request.id=owner_destroy.r27_request_id
                        AND owner_request.org_id={record_sql}.org_id
                        AND owner_source.org_id=owner_request.org_id
                        AND owner_source.record_id=owner_request.record_id
                        AND owner_source.action='DESTROY'
                        AND owner_source.tombstone
                        AND owner_source.is_worm_destroy
                        AND owner_source.policy_id IS NULL
                        AND owner_source.derived_from_disposition_event_id IS NULL
                        AND owner_source.r27_request_id=owner_request.id
                        AND owner_source.r27_execution_id=owner_execution.id
                        AND owner_source.requested_by=owner_request.requester_user_id
                        AND owner_source.approved_by=owner_request.approver_user_id
                        AND owner_source.legal_basis=owner_request.normalized_legal_basis
                        AND owner_destroy.is_worm_destroy
                        AND owner_destroy.policy_id IS NULL
                        AND owner_destroy.r27_request_id=owner_request.id
                        AND owner_destroy.r27_execution_id=owner_execution.id
                        AND owner_destroy.requested_by=owner_request.requester_user_id
                        AND owner_destroy.approved_by=owner_request.approver_user_id
                        AND owner_destroy.legal_basis=owner_request.normalized_legal_basis
                        AND (
                            (
                                owner_destroy.id=owner_source.id
                                AND owner_request.record_id={record_sql}.id
                            )
                            OR (
                                owner_destroy.derived_from_disposition_event_id=owner_source.id
                                AND owner_request.record_id<>{record_sql}.id
                            )
                        )
                  )
              )
        )
    """


def _create_task4_worm_functions() -> None:
    live_record = f"NOT {_valid_destructive_event_exists_sql(record_sql='owner_record')}"
    _create_definer_function(
        "easysynq_assert_worm_record_live(p_org_id uuid,p_record_id uuid) RETURNS void",
        "easysynq_assert_worm_record_live(uuid,uuid)",
        APP_ROLE,
        f"""
        DECLARE owner_record public.record%ROWTYPE;
        BEGIN
            SELECT record.* INTO owner_record
            FROM public.record record
            WHERE record.id=p_record_id AND record.org_id=p_org_id
            FOR UPDATE OF record;
            IF NOT FOUND
               OR {_valid_destructive_event_exists_sql(record_sql="owner_record")} THEN
                RAISE EXCEPTION 'worm_proposed_owner_liveness_refused';
            END IF;
        END
        """,
    )
    _create_definer_function(
        "easysynq_lock_document_worm_config(p_org_id uuid,p_config_id uuid) "
        "RETURNS TABLE(id uuid,org_id uuid,active_period text,active_revision_no integer)",
        "easysynq_lock_document_worm_config(uuid,uuid)",
        "easysynq_app",
        """
        BEGIN
            RETURN QUERY
            SELECT config.id,config.org_id,config.active_period,config.active_revision_no
            FROM public.document_worm_config config
            WHERE config.id=p_config_id AND config.org_id=p_org_id
            FOR UPDATE OF config;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'document_worm_config_lock_refused';
            END IF;
        END
        """,
    )
    _create_definer_function(
        "easysynq_lock_worm_blob(p_org_id uuid,p_blob_sha256 text) "
        "RETURNS TABLE(blob_sha256 text,org_id uuid,bucket text,object_key text,"
        "object_version_id text,worm_locked boolean,worm_enforced_mode text,"
        "worm_asserted_retain_until timestamptz,worm_asserted_at timestamptz,"
        "worm_retain_until timestamptz,worm_retention_verified_at timestamptz,"
        "worm_legal_hold boolean,worm_legal_hold_verified_at timestamptz,"
        "purged_at timestamptz,purge_execution_id uuid)",
        "easysynq_lock_worm_blob(uuid,text)",
        "easysynq_app,easysynq_retention",
        """
        BEGIN
            PERFORM pg_advisory_xact_lock(1163087698,hashtext(p_org_id::text));
            PERFORM pg_advisory_xact_lock(1163088712,hashtext(p_blob_sha256));
            RETURN QUERY
            SELECT blob.sha256,blob.org_id,blob.bucket,blob.object_key,
                   blob.object_version_id,blob.worm_locked,blob.worm_enforced_mode,
                   blob.worm_asserted_retain_until,blob.worm_asserted_at,
                   blob.worm_retain_until,blob.worm_retention_verified_at,
                   blob.worm_legal_hold,blob.worm_legal_hold_verified_at,
                   blob.purged_at,blob.purge_execution_id
            FROM public.blob
            WHERE blob.sha256=p_blob_sha256 AND blob.org_id=p_org_id
            FOR UPDATE OF blob;
            IF FOUND THEN RETURN; END IF;
            IF EXISTS (
                SELECT 1 FROM public.blob foreign_blob
                WHERE foreign_blob.sha256=p_blob_sha256
            ) THEN
                RAISE EXCEPTION 'worm_blob_lock_refused';
            END IF;
        END
        """,
    )
    _create_definer_function(
        "easysynq_lock_worm_owners(p_org_id uuid,p_blob_sha256 text) "
        "RETURNS TABLE(owner_kind text,owner_id uuid,org_id uuid,blob_sha256 text,"
        "basis_date date,duration text,domain_hold boolean,permanent boolean,"
        "worm_lock_period text)",
        "easysynq_lock_worm_owners(uuid,text)",
        "easysynq_app,easysynq_retention",
        f"""
        DECLARE
            candidate record;
            owner_record record;
            owner_parent record;
            owner_policy record;
            owner_config record;
            owner_edge record;
            owner_duration text;
            owner_worm_period text;
        BEGIN
            PERFORM pg_advisory_xact_lock(1163087698,hashtext(p_org_id::text));
            PERFORM pg_advisory_xact_lock(1163088712,hashtext(p_blob_sha256));
            PERFORM 1 FROM public.blob registry_blob
            WHERE registry_blob.sha256=p_blob_sha256
              AND registry_blob.org_id=p_org_id
              AND registry_blob.worm_locked
              AND registry_blob.object_version_id IS NOT NULL
              AND btrim(registry_blob.object_version_id)<>''
              AND registry_blob.object_version_id<>'null'
              AND registry_blob.worm_enforced_mode='GOVERNANCE'
              AND registry_blob.worm_asserted_retain_until IS NOT NULL
              AND registry_blob.worm_asserted_at IS NOT NULL
              AND registry_blob.worm_retain_until IS NOT NULL
              AND registry_blob.worm_retention_verified_at IS NOT NULL
              AND registry_blob.worm_legal_hold IS NOT NULL
              AND registry_blob.worm_legal_hold_verified_at IS NOT NULL
              AND registry_blob.purged_at IS NULL
              AND registry_blob.purge_execution_id IS NULL
            FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'invalid_worm_owner_state'; END IF;

            FOR candidate IN
                SELECT version.* FROM public.document_version version
                WHERE version.source_blob_sha256=p_blob_sha256
                ORDER BY version.id FOR UPDATE
            LOOP
                IF candidate.org_id<>p_org_id OR candidate.retention_basis_date IS NULL THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT parent.* INTO owner_parent
                FROM public.documented_information parent
                WHERE parent.id=candidate.document_id FOR UPDATE;
                IF NOT FOUND OR owner_parent.org_id<>p_org_id OR owner_parent.kind<>'DOCUMENT' THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                IF candidate.retention_authority_kind='POLICY' THEN
                    IF candidate.retention_policy_id IS NULL
                       OR candidate.document_worm_config_id IS NOT NULL THEN
                        RAISE EXCEPTION 'invalid_worm_owner_state';
                    END IF;
                    SELECT policy.* INTO owner_policy FROM public.retention_policy policy
                    WHERE policy.id=candidate.retention_policy_id FOR UPDATE;
                    IF NOT FOUND OR owner_policy.org_id<>p_org_id THEN
                        RAISE EXCEPTION 'invalid_worm_owner_state';
                    END IF;
                    owner_duration := owner_policy.duration;
                    owner_worm_period := owner_policy.worm_lock_period;
                ELSIF candidate.retention_authority_kind='INSTALLATION_MINIMUM' THEN
                    IF candidate.document_worm_config_id IS NULL
                       OR candidate.retention_policy_id IS NOT NULL THEN
                        RAISE EXCEPTION 'invalid_worm_owner_state';
                    END IF;
                    SELECT config.* INTO owner_config FROM public.document_worm_config config
                    WHERE config.id=candidate.document_worm_config_id FOR UPDATE;
                    IF NOT FOUND OR owner_config.org_id<>p_org_id THEN
                        RAISE EXCEPTION 'invalid_worm_owner_state';
                    END IF;
                    owner_duration := owner_config.active_period;
                    owner_worm_period := NULL;
                ELSE
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                owner_kind := 'DOCUMENT_VERSION'; owner_id := candidate.id;
                org_id := candidate.org_id; blob_sha256 := p_blob_sha256;
                basis_date := candidate.retention_basis_date; duration := owner_duration;
                domain_hold := false;
                permanent := upper(btrim(owner_duration))='PERMANENT'
                    OR upper(btrim(COALESCE(owner_worm_period,'')))='PERMANENT';
                worm_lock_period := owner_worm_period;
                RETURN NEXT;
            END LOOP;

            FOR candidate IN
                SELECT edge.* FROM public.evidence_blob edge
                WHERE edge.blob_sha256=p_blob_sha256
                ORDER BY edge.id FOR UPDATE
            LOOP
                IF candidate.org_id<>p_org_id THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT record.* INTO owner_record FROM public.record record
                WHERE record.id=candidate.record_id FOR UPDATE;
                IF NOT FOUND OR owner_record.org_id<>p_org_id THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT parent.* INTO owner_parent FROM public.documented_information parent
                WHERE parent.id=owner_record.id FOR UPDATE;
                IF NOT FOUND OR owner_parent.org_id<>p_org_id OR owner_parent.kind<>'RECORD' THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT policy.* INTO owner_policy FROM public.retention_policy policy
                WHERE policy.id=owner_record.retention_policy_id FOR UPDATE;
                IF NOT FOUND OR owner_policy.org_id<>p_org_id THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                IF {_valid_destructive_event_exists_sql(record_sql="owner_record")} THEN
                    CONTINUE;
                END IF;
                IF owner_record.retention_basis_date IS NULL THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                owner_kind := 'RECORD_EVIDENCE'; owner_id := candidate.id;
                org_id := candidate.org_id; blob_sha256 := candidate.blob_sha256;
                basis_date := owner_record.retention_basis_date;
                duration := owner_policy.duration; domain_hold := owner_record.legal_hold;
                permanent := upper(btrim(owner_policy.duration))='PERMANENT'
                    OR upper(btrim(COALESCE(owner_policy.worm_lock_period,'')))='PERMANENT';
                worm_lock_period := owner_policy.worm_lock_period;
                RETURN NEXT;
            END LOOP;

            FOR candidate IN
                SELECT pack.* FROM public.evidence_pack pack
                WHERE pack.zip_blob_sha256=p_blob_sha256
                  AND pack.status='SEALED'
                  AND pack.invalidated_at IS NULL
                ORDER BY pack.id FOR UPDATE
            LOOP
                IF candidate.org_id<>p_org_id OR candidate.pack_record_id IS NULL THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT record.* INTO owner_record FROM public.record record
                WHERE record.id=candidate.pack_record_id FOR UPDATE;
                IF NOT FOUND OR owner_record.org_id<>p_org_id
                   OR owner_record.retention_basis_date IS NULL THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT parent.* INTO owner_parent FROM public.documented_information parent
                WHERE parent.id=owner_record.id FOR UPDATE;
                IF NOT FOUND OR owner_parent.org_id<>p_org_id OR owner_parent.kind<>'RECORD' THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT policy.* INTO owner_policy FROM public.retention_policy policy
                WHERE policy.id=owner_record.retention_policy_id FOR UPDATE;
                IF NOT FOUND OR owner_policy.org_id<>p_org_id
                   OR NOT (
                       upper(btrim(owner_policy.duration))='PERMANENT'
                       OR upper(btrim(COALESCE(owner_policy.worm_lock_period,'')))='PERMANENT'
                   ) THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                SELECT edge.* INTO owner_edge FROM public.evidence_blob edge
                WHERE edge.record_id=owner_record.id AND edge.blob_sha256=p_blob_sha256
                FOR UPDATE;
                IF NOT FOUND OR owner_edge.org_id<>p_org_id OR NOT ({live_record}) THEN
                    RAISE EXCEPTION 'invalid_worm_owner_state';
                END IF;
                owner_kind := 'SEALED_PACK'; owner_id := candidate.id;
                org_id := candidate.org_id; blob_sha256 := p_blob_sha256;
                basis_date := owner_record.retention_basis_date;
                duration := owner_policy.duration; domain_hold := owner_record.legal_hold;
                permanent := true; worm_lock_period := owner_policy.worm_lock_period;
                RETURN NEXT;
            END LOOP;
        END
        """,
    )
    _create_definer_function(
        "easysynq_record_worm_assertion(p_org_id uuid,p_blob_sha256 text,p_bucket text,"
        "p_object_key text,p_object_version_id text,p_retain_until timestamptz,"
        "p_legal_hold boolean,p_verified_at timestamptz) RETURNS void",
        "easysynq_record_worm_assertion(uuid,text,text,text,text,timestamptz,boolean,timestamptz)",
        APP_ROLE,
        """
        DECLARE current_blob public.blob%ROWTYPE;
        BEGIN
            SELECT blob.* INTO current_blob FROM public.blob
            WHERE blob.sha256=p_blob_sha256 FOR UPDATE;
            IF NOT FOUND
               OR current_blob.org_id<>p_org_id
               OR current_blob.bucket<>p_bucket
               OR current_blob.object_key<>p_object_key
               OR current_blob.object_version_id<>p_object_version_id
               OR NOT current_blob.worm_locked
               OR current_blob.worm_enforced_mode<>'GOVERNANCE'
               OR current_blob.worm_asserted_retain_until IS NULL
               OR current_blob.worm_asserted_at IS NULL
               OR current_blob.worm_retain_until IS NULL
               OR current_blob.worm_retention_verified_at IS NULL
               OR current_blob.worm_legal_hold IS NULL
               OR current_blob.worm_legal_hold_verified_at IS NULL
               OR current_blob.purged_at IS NOT NULL
               OR current_blob.purge_execution_id IS NOT NULL
               OR p_retain_until<current_blob.worm_retain_until
               OR p_retain_until<current_blob.worm_asserted_retain_until
               OR (current_blob.worm_legal_hold AND NOT p_legal_hold)
               OR p_verified_at<current_blob.worm_retention_verified_at
               OR p_verified_at<current_blob.worm_legal_hold_verified_at THEN
                RAISE EXCEPTION 'worm_assertion_record_refused';
            END IF;
            UPDATE public.blob SET
                worm_retain_until=p_retain_until,
                worm_retention_verified_at=p_verified_at,
                worm_legal_hold=(current_blob.worm_legal_hold OR p_legal_hold),
                worm_legal_hold_verified_at=p_verified_at
            WHERE sha256=p_blob_sha256;
        END
        """,
    )
    op.execute(
        """
        CREATE FUNCTION public.easysynq_guard_blob_worm_identity() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp AS $function$
        DECLARE v_owned boolean;
        BEGIN
            IF TG_OP = 'UPDATE' AND NOT OLD.worm_locked AND NEW.worm_locked THEN
                RAISE EXCEPTION 'worm_blob_conversion_requires_insert';
            END IF;
            SELECT OLD.worm_locked
                OR EXISTS (SELECT 1 FROM public.document_version v
                           WHERE v.source_blob_sha256=OLD.sha256)
                OR EXISTS (SELECT 1 FROM public.evidence_blob e
                           WHERE e.blob_sha256=OLD.sha256)
            INTO v_owned;
            IF TG_OP = 'DELETE' AND v_owned THEN
                RAISE EXCEPTION 'worm_blob_identity_is_immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND v_owned AND (
                NEW.sha256 IS DISTINCT FROM OLD.sha256
                OR NEW.org_id IS DISTINCT FROM OLD.org_id
                OR NEW.bucket IS DISTINCT FROM OLD.bucket
                OR NEW.object_key IS DISTINCT FROM OLD.object_key
                OR NEW.object_version_id IS DISTINCT FROM OLD.object_version_id
                OR NEW.worm_locked IS DISTINCT FROM OLD.worm_locked
                OR NEW.worm_enforced_mode IS DISTINCT FROM OLD.worm_enforced_mode
                OR NEW.worm_asserted_retain_until IS DISTINCT FROM OLD.worm_asserted_retain_until
                OR NEW.worm_asserted_at IS DISTINCT FROM OLD.worm_asserted_at
            ) THEN
                RAISE EXCEPTION 'worm_blob_identity_is_immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.easysynq_guard_blob_worm_identity() FROM PUBLIC;
        CREATE TRIGGER trg_blob_worm_identity
        BEFORE UPDATE OR DELETE ON public.blob
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_blob_worm_identity();
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.easysynq_guard_worm_owner_pointer() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp AS $function$
        DECLARE
            v_org uuid;
            v_parent_org uuid;
            v_locked boolean;
            v_complete boolean;
            v_sha text;
            v_policy_id uuid;
        BEGIN
            IF TG_TABLE_NAME='retention_policy' THEN
                IF session_user='easysynq_app'
                   AND (NEW.duration IS DISTINCT FROM OLD.duration
                        OR NEW.worm_lock_period IS DISTINCT FROM OLD.worm_lock_period) THEN
                    PERFORM 1 FROM public.retention_policy policy
                    WHERE policy.id=OLD.id
                    FOR UPDATE;
                    IF EXISTS (
                           SELECT 1 FROM public.record owner_record
                           WHERE owner_record.retention_policy_id=OLD.id
                       ) OR EXISTS (
                           SELECT 1 FROM public.document_version owner_version
                           WHERE owner_version.retention_policy_id=OLD.id
                       ) THEN
                        RAISE EXCEPTION 'worm_pinned_policy_is_immutable';
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME='record' THEN
                IF TG_OP='INSERT'
                   OR NEW.retention_policy_id IS DISTINCT FROM OLD.retention_policy_id THEN
                    v_policy_id := NEW.retention_policy_id;
                    IF v_policy_id IS NOT NULL THEN
                        PERFORM 1 FROM public.retention_policy policy
                        WHERE policy.id=v_policy_id
                        FOR UPDATE;
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'worm_owner_pointer_is_immutable';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.org_id IS DISTINCT FROM OLD.org_id THEN
                    RAISE EXCEPTION 'worm_owner_pointer_is_immutable';
                ELSIF TG_TABLE_NAME='document_version'
                   AND ((to_jsonb(NEW)->>'source_blob_sha256')
                        IS DISTINCT FROM (to_jsonb(OLD)->>'source_blob_sha256')
                        OR (to_jsonb(NEW)->>'document_id')
                           IS DISTINCT FROM (to_jsonb(OLD)->>'document_id')
                        OR (to_jsonb(NEW)->>'retention_authority_kind')
                           IS DISTINCT FROM (to_jsonb(OLD)->>'retention_authority_kind')
                        OR (to_jsonb(NEW)->>'retention_policy_id')
                           IS DISTINCT FROM (to_jsonb(OLD)->>'retention_policy_id')
                        OR (to_jsonb(NEW)->>'document_worm_config_id')
                           IS DISTINCT FROM (to_jsonb(OLD)->>'document_worm_config_id')
                        OR (to_jsonb(NEW)->>'retention_basis_date')
                           IS DISTINCT FROM (to_jsonb(OLD)->>'retention_basis_date')) THEN
                    RAISE EXCEPTION 'worm_owner_pointer_is_immutable';
                ELSIF TG_TABLE_NAME='evidence_blob'
                   AND ((to_jsonb(NEW)->>'blob_sha256')
                        IS DISTINCT FROM (to_jsonb(OLD)->>'blob_sha256')
                        OR (to_jsonb(NEW)->>'record_id')
                           IS DISTINCT FROM (to_jsonb(OLD)->>'record_id')) THEN
                    RAISE EXCEPTION 'worm_owner_pointer_is_immutable';
                END IF;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF TG_TABLE_NAME='document_version' THEN
                    IF NEW.retention_authority_kind='POLICY'
                       AND NEW.retention_policy_id IS NOT NULL THEN
                        PERFORM 1 FROM public.retention_policy policy
                        WHERE policy.id=NEW.retention_policy_id
                        FOR UPDATE;
                    END IF;
                    v_sha := to_jsonb(NEW)->>'source_blob_sha256';
                    SELECT parent.org_id INTO v_parent_org
                    FROM public.documented_information parent
                    WHERE parent.id=(to_jsonb(NEW)->>'document_id')::uuid
                    FOR SHARE;
                ELSE
                    v_sha := to_jsonb(NEW)->>'blob_sha256';
                    SELECT parent.org_id INTO v_parent_org
                    FROM public.record parent
                    WHERE parent.id=(to_jsonb(NEW)->>'record_id')::uuid
                    FOR SHARE;
                END IF;
                IF NOT FOUND OR v_parent_org IS DISTINCT FROM NEW.org_id THEN
                    RAISE EXCEPTION 'worm_owner_requires_complete_assertion';
                END IF;
                SELECT b.org_id,b.worm_locked,
                       b.object_version_id IS NOT NULL
                       AND b.worm_enforced_mode='GOVERNANCE'
                       AND b.worm_asserted_retain_until IS NOT NULL
                       AND b.worm_asserted_at IS NOT NULL
                       AND b.worm_retention_verified_at IS NOT NULL
                       AND b.worm_legal_hold IS NOT NULL
                       AND b.worm_legal_hold_verified_at IS NOT NULL
                       AND b.purged_at IS NULL
                       AND b.purge_execution_id IS NULL
                INTO v_org,v_locked,v_complete
                FROM public.blob b WHERE b.sha256=v_sha FOR KEY SHARE;
                IF NOT FOUND OR NOT v_locked OR NOT v_complete
                   OR v_org IS DISTINCT FROM NEW.org_id
                   OR v_org IS DISTINCT FROM v_parent_org THEN
                    RAISE EXCEPTION 'worm_owner_requires_complete_assertion';
                END IF;
            END IF;
            RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.easysynq_guard_worm_owner_pointer() FROM PUBLIC;
        CREATE TRIGGER trg_document_version_worm_owner
        BEFORE INSERT OR UPDATE OR DELETE ON public.document_version
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_worm_owner_pointer();
        CREATE TRIGGER trg_record_retention_policy_pin
        BEFORE INSERT OR UPDATE ON public.record
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_worm_owner_pointer();
        CREATE TRIGGER trg_retention_policy_worm_owner
        BEFORE UPDATE OF duration,worm_lock_period ON public.retention_policy
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_worm_owner_pointer();
        CREATE TRIGGER trg_evidence_blob_worm_owner
        BEFORE INSERT OR UPDATE OR DELETE ON public.evidence_blob
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_worm_owner_pointer();
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.easysynq_guard_key_registry_history() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp AS $function$
        BEGIN
            IF TG_OP='DELETE'
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.key_id IS DISTINCT FROM OLD.key_id
               OR NEW.public_key IS DISTINCT FROM OLD.public_key
               OR NEW.fingerprint IS DISTINCT FROM OLD.fingerprint
               OR NEW.installed_by_identity IS DISTINCT FROM OLD.installed_by_identity
               OR NEW.installed_audit_event_id IS DISTINCT FROM OLD.installed_audit_event_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR ((to_jsonb(NEW)->'active_at') IS DISTINCT FROM
                   (to_jsonb(OLD)->'active_at'))
               OR (TG_TABLE_NAME='recovery_generation_verifier_key'
                   AND ((to_jsonb(NEW)->'algorithm') IS DISTINCT FROM
                        (to_jsonb(OLD)->'algorithm')
                        OR (to_jsonb(NEW)->'not_before') IS DISTINCT FROM
                           (to_jsonb(OLD)->'not_before')))
               OR (OLD.retired_at IS NOT NULL AND NEW.retired_at IS DISTINCT FROM OLD.retired_at)
               OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
               OR (NEW.retired_at IS NOT NULL AND NEW.retired_at<OLD.created_at)
               OR (NEW.revoked_at IS NOT NULL AND NEW.revoked_at<OLD.created_at) THEN
                RAISE EXCEPTION 'key_registry_history_is_immutable';
            END IF;
            RETURN NEW;
        END
        $function$;
        REVOKE ALL ON FUNCTION public.easysynq_guard_key_registry_history() FROM PUBLIC;
        CREATE TRIGGER trg_r27_authorizer_key_history
        BEFORE UPDATE OR DELETE ON public.r27_authorizer_key
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_key_registry_history();
        CREATE TRIGGER trg_recovery_verifier_key_history
        BEFORE UPDATE OR DELETE ON public.recovery_generation_verifier_key
        FOR EACH ROW EXECUTE FUNCTION public.easysynq_guard_key_registry_history();
        """
    )


def _create_retention_functions() -> None:
    _create_definer_function(
        "easysynq_claim_retention_targets(p_limit integer,p_claimed_at timestamptz) "
        "RETURNS TABLE(target_id uuid,operation_id uuid,blob_sha256 text,bucket text,"
        "object_key text,object_version_id text,required_retain_until timestamptz,"
        "required_legal_hold boolean)",
        "easysynq_claim_retention_targets(integer,timestamptz)",
        "easysynq_retention",
        """
        BEGIN
            IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
                RAISE EXCEPTION 'retention_claim_limit_refused';
            END IF;
            RETURN QUERY
            WITH candidates AS (
                SELECT target.id FROM public.retention_operation_target target
                JOIN public.retention_operation operation ON operation.id=target.operation_id
                WHERE target.state IN ('PENDING','FAILED')
                  AND operation.state IN ('PENDING','FAILED','RUNNING')
                ORDER BY target.created_at,target.id
                FOR UPDATE OF target,operation SKIP LOCKED LIMIT p_limit
            ), claimed AS (
                UPDATE public.retention_operation_target target
                SET state='RUNNING',attempt_count=target.attempt_count+1,error_code=NULL,
                    error_detail=NULL,updated_at=p_claimed_at
                FROM candidates WHERE target.id=candidates.id
                RETURNING target.*
            ), parents AS (
                UPDATE public.retention_operation operation
                SET state='RUNNING',started_at=COALESCE(operation.started_at,p_claimed_at),
                    updated_at=p_claimed_at
                WHERE operation.id IN (SELECT claimed.operation_id FROM claimed)
                  AND operation.state IN ('PENDING','FAILED','RUNNING')
                RETURNING operation.id
            )
            SELECT claimed.id,claimed.operation_id,claimed.blob_sha256::text,
                   claimed.bucket,claimed.object_key,claimed.object_version_id,
                   claimed.required_retain_until,claimed.required_legal_hold
            FROM claimed ORDER BY claimed.created_at,claimed.id;
        END
        """,
    )
    _create_definer_function(
        "easysynq_fail_retention_target(p_target_id uuid,p_code text,p_detail text,"
        "p_failed_at timestamptz) RETURNS void",
        "easysynq_fail_retention_target(uuid,text,text,timestamptz)",
        "easysynq_retention",
        """
        BEGIN
            IF p_target_id IS NULL OR p_code IS NULL THEN
                RAISE EXCEPTION 'required_argument_is_null';
            END IF;
            IF btrim(p_code)='' OR length(p_code)>64 OR length(COALESCE(p_detail,''))>512 THEN
                RAISE EXCEPTION 'retention_failure_detail_refused';
            END IF;
            UPDATE public.retention_operation_target SET state='FAILED',error_code=p_code,
                error_detail=p_detail,updated_at=p_failed_at
            WHERE id=p_target_id AND state='RUNNING';
            IF NOT FOUND THEN RAISE EXCEPTION 'retention_target_not_running'; END IF;
            UPDATE public.retention_operation operation SET
                failed_count=(SELECT count(*) FROM public.retention_operation_target target
                              WHERE target.operation_id=operation.id AND target.state='FAILED'),
                verified_count=(SELECT count(*) FROM public.retention_operation_target target
                                WHERE target.operation_id=operation.id AND target.state='VERIFIED'),
                state='FAILED',updated_at=p_failed_at
            WHERE operation.id=(SELECT operation_id FROM public.retention_operation_target
                                WHERE id=p_target_id);
        END
        """,
    )
    _create_definer_function(
        "easysynq_ratchet_worm_assertion(p_blob_sha256 text,p_object_version_id text,"
        "p_retain_until timestamptz,p_legal_hold boolean,p_verified_at timestamptz,"
        "p_operation_id uuid) RETURNS void",
        "easysynq_ratchet_worm_assertion(text,text,timestamptz,boolean,timestamptz,uuid)",
        "easysynq_retention",
        """
        DECLARE v_target uuid;
        BEGIN
            IF p_blob_sha256 IS NULL OR p_object_version_id IS NULL
               OR p_retain_until IS NULL OR p_legal_hold IS NULL
               OR p_operation_id IS NULL THEN
                RAISE EXCEPTION 'required_argument_is_null';
            END IF;
            SELECT target.id INTO v_target FROM public.retention_operation_target target
            JOIN public.blob blob ON blob.sha256=target.blob_sha256
            JOIN public.retention_operation operation ON operation.id=target.operation_id
            WHERE target.operation_id=p_operation_id AND target.blob_sha256=p_blob_sha256
              AND target.object_version_id=p_object_version_id AND target.state='RUNNING'
              AND target.bucket=blob.bucket AND target.object_key=blob.object_key
              AND target.object_version_id=blob.object_version_id
              AND operation.org_id=blob.org_id
              AND p_retain_until>=COALESCE(blob.worm_retain_until,p_retain_until)
              AND p_retain_until>=COALESCE(target.required_retain_until,p_retain_until)
              AND (blob.worm_legal_hold IS NOT TRUE OR p_legal_hold)
              AND (NOT target.required_legal_hold OR p_legal_hold)
            FOR UPDATE OF operation,target,blob;
            IF NOT FOUND THEN RAISE EXCEPTION 'worm_retention_ratchet_refused'; END IF;
            UPDATE public.blob blob SET worm_retain_until=p_retain_until,
                worm_retention_verified_at=p_verified_at,worm_legal_hold=p_legal_hold,
                worm_legal_hold_verified_at=p_verified_at
            FROM public.retention_operation_target target
            JOIN public.retention_operation operation ON operation.id=target.operation_id
            WHERE target.id=v_target AND target.operation_id=p_operation_id
              AND blob.sha256=target.blob_sha256 AND blob.sha256=p_blob_sha256
              AND blob.bucket=target.bucket AND blob.object_key=target.object_key
              AND blob.object_version_id=target.object_version_id
              AND operation.org_id=blob.org_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'worm_retention_ratchet_refused'; END IF;
            UPDATE public.retention_operation_target SET state='VERIFIED',
                read_back_retain_until=p_retain_until,read_back_legal_hold=p_legal_hold,
                read_back_at=p_verified_at,error_code=NULL,error_detail=NULL,updated_at=p_verified_at
            WHERE id=v_target;
            UPDATE public.retention_operation operation SET
                verified_count=(SELECT count(*) FROM public.retention_operation_target target
                                WHERE target.operation_id=operation.id AND target.state='VERIFIED'),
                failed_count=(SELECT count(*) FROM public.retention_operation_target target
                              WHERE target.operation_id=operation.id AND target.state='FAILED'),
                state=CASE WHEN operation.target_count=(
                    SELECT count(*) FROM public.retention_operation_target target
                    WHERE target.operation_id=operation.id
                ) AND NOT EXISTS (
                    SELECT 1 FROM public.retention_operation_target target
                    WHERE target.operation_id=operation.id AND target.state<>'VERIFIED'
                ) THEN 'VERIFIED'::retention_operation_state ELSE operation.state END,
                completed_at=CASE WHEN operation.target_count=(
                    SELECT count(*) FROM public.retention_operation_target target
                    WHERE target.operation_id=operation.id
                ) AND NOT EXISTS (
                    SELECT 1 FROM public.retention_operation_target target
                    WHERE target.operation_id=operation.id AND target.state<>'VERIFIED'
                ) THEN p_verified_at ELSE operation.completed_at END,updated_at=p_verified_at
            WHERE operation.id=p_operation_id;
        END
        """,
    )


def _key_install_body(table: str, algorithm_column: str, scope: str) -> str:
    return f"""
        DECLARE v_id uuid:=gen_random_uuid(); v_org uuid; v_audit bigint;
        BEGIN
            IF p_key_id IS NULL OR p_public_key IS NULL OR length(p_public_key)=0
               OR p_fingerprint IS NULL OR p_operator_identity IS NULL
               OR btrim(p_key_id)='' OR btrim(p_operator_identity)=''
               OR p_fingerprint !~ '^[0-9a-f]{{64}}$' THEN
                RAISE EXCEPTION 'key_install_refused';
            END IF;
            SELECT id INTO v_org FROM public.organization ORDER BY id LIMIT 1;
            IF NOT FOUND THEN RAISE EXCEPTION 'key_install_requires_organization'; END IF;
            INSERT INTO public.audit_event
                (org_id,occurred_at,actor_type,event_type,object_type,object_id,scope_ref,reason,after)
            VALUES (v_org,p_active_at,'system','CONFIG_UPDATED','config',v_id,
                    '{scope}','{scope}-installed',
                    jsonb_build_object('operator_identity',p_operator_identity,
                                       'key_id',p_key_id,'operation','install'))
            RETURNING id INTO v_audit;
            INSERT INTO public.{table}
                (id,key_id,{algorithm_column}public_key,fingerprint,{("not_before" if table.startswith("recovery") else "active_at")},
                 installed_by_identity,installed_audit_event_id)
            VALUES (v_id,p_key_id,{("'ED25519'," if algorithm_column else "")}p_public_key,p_fingerprint,p_active_at,
                    p_operator_identity,v_audit);
            RETURN v_id;
        END
    """


def _create_key_functions() -> None:
    _create_definer_function(
        "easysynq_install_r27_authorizer_key(p_key_id text,p_public_key bytea,"
        "p_fingerprint text,p_active_at timestamptz,p_operator_identity text) RETURNS uuid",
        "easysynq_install_r27_authorizer_key(text,bytea,text,timestamptz,text)",
        "easysynq_r27_authorizer_key_manager",
        _key_install_body("r27_authorizer_key", "", "r27-authorizer-key"),
    )
    _create_definer_function(
        "easysynq_install_recovery_verifier_key(p_key_id text,p_public_key bytea,"
        "p_fingerprint text,p_active_at timestamptz,p_operator_identity text) RETURNS uuid",
        "easysynq_install_recovery_verifier_key(text,bytea,text,timestamptz,text)",
        "easysynq_recovery_key_manager",
        _key_install_body(
            "recovery_generation_verifier_key", "algorithm,", "recovery-generation-verifier-key"
        ),
    )
    for prefix, table, role, scope in (
        (
            "r27_authorizer",
            "r27_authorizer_key",
            "easysynq_r27_authorizer_key_manager",
            "r27-authorizer-key",
        ),
        (
            "recovery_verifier",
            "recovery_generation_verifier_key",
            "easysynq_recovery_key_manager",
            "recovery-generation-verifier-key",
        ),
    ):
        lifecycle_start = "not_before" if table.startswith("recovery") else "active_at"
        for verb, column in (("retire", "retired_at"), ("revoke", "revoked_at")):
            lifecycle_guard = "AND revoked_at IS NULL" if verb == "retire" else ""
            prior_lifecycle_guard = (
                "" if verb == "retire" else "AND (retired_at IS NULL OR p_at>=retired_at)"
            )
            downstream = ""
            if verb == "revoke" and prefix == "recovery_verifier":
                downstream = """
                    UPDATE public.recovery_generation_witness w
                    SET invalidated_at=p_at,invalidation_audit_event_id=v_audit,invalidation_reason='KEY_REVOKED'
                    WHERE w.key_id=v_id AND w.invalidated_at IS NULL
                      AND (w.consumed_execution_id IS NULL OR EXISTS (
                          SELECT 1 FROM public.r27_execution e
                          WHERE e.id=w.consumed_execution_id AND e.state<>'EXECUTED'));
                    UPDATE public.r27_request r SET state='WAITING_FOR_RECOVERY_GENERATION',updated_at=clock_timestamp()
                    WHERE r.id IN (SELECT w.request_id FROM public.recovery_generation_witness w WHERE w.key_id=v_id AND w.invalidated_at=p_at)
                      AND r.state IN ('READY_FOR_FINALIZATION','FINALIZING');
                    UPDATE public.r27_execution e SET state='FAILED',error_code='RECOVERY_KEY_REVOKED',
                        error_detail='active recovery witness key revoked',next_attempt_at=NULL,updated_at=clock_timestamp()
                    WHERE e.request_id IN (SELECT w.request_id FROM public.recovery_generation_witness w WHERE w.key_id=v_id AND w.invalidated_at=p_at)
                      AND e.state<>'EXECUTED';
                """
            elif verb == "revoke" and prefix == "r27_authorizer":
                downstream = """
                    UPDATE public.r27_request r SET state='STALE',error_code='AUTHORIZER_KEY_REVOKED',
                        error_detail='authorizer trust root revoked',stale_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE r.id IN (SELECT a.request_id FROM public.r27_attestation a WHERE a.authorizer_key_id=v_id)
                      AND (r.state IS NULL OR r.state IN ('WAITING_FOR_SECOND_APPROVER','WAITING_FOR_RECOVERY_GENERATION','READY_FOR_FINALIZATION','FINALIZING'))
                      AND NOT EXISTS (SELECT 1 FROM public.r27_execution e
                                      WHERE e.request_id=r.id AND e.source_committed_at IS NOT NULL);
                    UPDATE public.r27_execution e SET state='FAILED',error_code='AUTHORIZER_KEY_REVOKED',
                        error_detail='authorizer trust root revoked',next_attempt_at=NULL,updated_at=clock_timestamp()
                    WHERE e.request_id IN (SELECT a.request_id FROM public.r27_attestation a WHERE a.authorizer_key_id=v_id)
                      AND e.state<>'EXECUTED';
                    UPDATE public.r27_request r SET state='FAILED',error_code='AUTHORIZER_KEY_REVOKED',
                        error_detail='authorizer trust root revoked',failed_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE r.id IN (SELECT a.request_id FROM public.r27_attestation a WHERE a.authorizer_key_id=v_id)
                      AND EXISTS (SELECT 1 FROM public.r27_execution e
                                  WHERE e.request_id=r.id AND e.source_committed_at IS NOT NULL
                                    AND e.state='FAILED');
                """
            _create_definer_function(
                f"easysynq_{verb}_{prefix}_key(p_key_id text,p_at timestamptz,"
                "p_operator_identity text) RETURNS void",
                f"easysynq_{verb}_{prefix}_key(text,timestamptz,text)",
                role,
                f"""
                DECLARE v_id uuid; v_org uuid; v_audit bigint;
                BEGIN
                    IF p_key_id IS NULL OR p_at IS NULL OR p_operator_identity IS NULL OR btrim(p_operator_identity)='' THEN RAISE EXCEPTION 'key_lifecycle_refused'; END IF;
                    UPDATE public.{table} SET {column}=p_at
                    WHERE key_id=p_key_id AND {column} IS NULL
                      AND ({"TRUE" if column == "retired_at" else "revoked_at IS NULL"}) {lifecycle_guard}
                      AND p_at>={lifecycle_start} {prior_lifecycle_guard}
                      AND p_at>=created_at AND p_at<=clock_timestamp()+interval '5 minutes'
                    RETURNING id INTO v_id;
                    IF NOT FOUND THEN RAISE EXCEPTION 'key_lifecycle_refused'; END IF;
                    SELECT id INTO v_org FROM public.organization ORDER BY id LIMIT 1;
                    INSERT INTO public.audit_event
                        (org_id,occurred_at,actor_type,event_type,object_type,object_id,scope_ref,reason,after)
                    VALUES (v_org,p_at,'system','CONFIG_UPDATED','config',v_id,'{scope}',
                            '{scope}-{verb}d',jsonb_build_object(
                                'operator_identity',p_operator_identity,'key_id',p_key_id,
                                'operation','{verb}')) RETURNING id INTO v_audit;
                    {downstream}
                END
                """,
            )


def _live_owner_exists_sql(*, org_sql: str, blob_sha_sql: str, target_record_sql: str) -> str:
    """Return the one reviewed live-owner predicate used by every purge boundary."""
    live_record = f"NOT {_valid_destructive_event_exists_sql(record_sql='live_record')}"
    return f"""
        (
            EXISTS (
                SELECT 1
                FROM public.document_version live_version
                JOIN public.documented_information live_document
                  ON live_document.id=live_version.document_id
                 AND live_document.org_id=live_version.org_id
                WHERE live_version.org_id={org_sql}
                  AND live_version.source_blob_sha256={blob_sha_sql}
            )
            OR EXISTS (
                SELECT 1
                FROM public.evidence_blob live_evidence
                JOIN public.record live_record
                  ON live_record.id=live_evidence.record_id
                 AND live_record.org_id=live_evidence.org_id
                WHERE live_evidence.org_id={org_sql}
                  AND live_evidence.blob_sha256={blob_sha_sql}
                  AND live_evidence.record_id<>{target_record_sql}
                  AND {live_record}
            )
            OR EXISTS (
                SELECT 1
                FROM public.evidence_pack live_pack
                WHERE live_pack.org_id={org_sql}
                  AND live_pack.zip_blob_sha256={blob_sha_sql}
                  AND live_pack.status='SEALED'
                  AND live_pack.invalidated_at IS NULL
            )
        )
    """


def _hold_obligation_exists_sql(*, org_sql: str, blob_sha_sql: str) -> str:
    """Return the reviewed current hold-obligation predicate."""
    live_record = f"NOT {_valid_destructive_event_exists_sql(record_sql='hold_record')}"
    return f"""
        (
            EXISTS (
                SELECT 1
                FROM public.evidence_blob hold_evidence
                JOIN public.record hold_record
                  ON hold_record.id=hold_evidence.record_id
                 AND hold_record.org_id=hold_evidence.org_id
                LEFT JOIN public.retention_policy hold_policy
                  ON hold_policy.id=hold_record.retention_policy_id
                 AND hold_policy.org_id=hold_record.org_id
                WHERE hold_evidence.org_id={org_sql}
                  AND hold_evidence.blob_sha256={blob_sha_sql}
                  AND {live_record}
                  AND (
                      hold_record.legal_hold
                      OR upper(btrim(hold_policy.duration))='PERMANENT'
                      OR upper(btrim(COALESCE(hold_policy.worm_lock_period,'')))='PERMANENT'
                  )
            )
            OR EXISTS (
                SELECT 1
                FROM public.document_version hold_version
                JOIN public.documented_information hold_document
                  ON hold_document.id=hold_version.document_id
                 AND hold_document.org_id=hold_version.org_id
                LEFT JOIN public.retention_policy hold_policy
                  ON hold_policy.id=hold_version.retention_policy_id
                 AND hold_policy.org_id=hold_version.org_id
                LEFT JOIN public.document_worm_config hold_config
                  ON hold_config.id=hold_version.document_worm_config_id
                 AND hold_config.org_id=hold_version.org_id
                WHERE hold_version.org_id={org_sql}
                  AND hold_version.source_blob_sha256={blob_sha_sql}
                  AND (
                      (
                          hold_version.retention_authority_kind='POLICY'
                          AND (
                              upper(btrim(hold_policy.duration))='PERMANENT'
                              OR upper(btrim(COALESCE(hold_policy.worm_lock_period,'')))='PERMANENT'
                          )
                      )
                      OR (
                          hold_version.retention_authority_kind='INSTALLATION_MINIMUM'
                          AND upper(btrim(hold_config.active_period))='PERMANENT'
                      )
                  )
            )
            OR EXISTS (
                SELECT 1
                FROM public.evidence_pack hold_pack
                WHERE hold_pack.org_id={org_sql}
                  AND hold_pack.zip_blob_sha256={blob_sha_sql}
                  AND hold_pack.status='SEALED'
                  AND hold_pack.invalidated_at IS NULL
            )
        )
    """


def _ordinary_hold_authority_sql(
    *, operation_sql: str, record_sql: str, blob_sql: str, user_sql: str, edge_sql: str
) -> str:
    """Return the exact same-tenant ordinary hold-release tuple."""
    return f"""
        {operation_sql}.org_id={record_sql}.org_id
        AND {operation_sql}.org_id={blob_sql}.org_id
        AND {operation_sql}.org_id={user_sql}.org_id
        AND {operation_sql}.record_id={record_sql}.id
        AND {operation_sql}.initiated_by_user_id={user_sql}.id
        AND {operation_sql}.blob_sha256={blob_sql}.sha256
        AND {operation_sql}.object_version_id={blob_sql}.object_version_id
        AND {edge_sql}.org_id={operation_sql}.org_id
        AND {edge_sql}.record_id={operation_sql}.record_id
        AND {edge_sql}.blob_sha256={operation_sql}.blob_sha256
        AND {blob_sql}.worm_locked
        AND {blob_sql}.worm_enforced_mode='GOVERNANCE'
        AND {blob_sql}.object_version_id IS NOT NULL
        AND {blob_sql}.worm_asserted_retain_until IS NOT NULL
        AND {blob_sql}.worm_asserted_at IS NOT NULL
        AND {blob_sql}.worm_retention_verified_at IS NOT NULL
        AND {blob_sql}.worm_legal_hold IS NOT NULL
        AND {blob_sql}.worm_legal_hold_verified_at IS NOT NULL
        AND {blob_sql}.purged_at IS NULL
        AND {blob_sql}.purge_execution_id IS NULL
    """


def _read_committed_guard_sql() -> str:
    return """
        IF current_setting('transaction_isolation')<>'read committed' THEN
            RAISE EXCEPTION 'authority_requires_read_committed';
        END IF;
    """


def _r27_accepted_action_sql(
    *,
    action: str,
    request_sql: str,
    manifest_sql: str,
    user_sql: str,
    audit_sql: str,
    accepted_at_sql: str,
    reason: str,
) -> str:
    """Return the exact persisted acceptance relation for one R27 action."""
    return f"""
        EXISTS (
            SELECT 1
            FROM public.r27_attestation accepted_attestation
            JOIN public.r27_action_challenge accepted_challenge
              ON accepted_challenge.id=accepted_attestation.challenge_id
            JOIN public.r27_authorizer_key accepted_key
              ON accepted_key.id=accepted_attestation.authorizer_key_id
            JOIN public.app_user accepted_user
              ON accepted_user.id=accepted_attestation.app_user_id
            WHERE accepted_attestation.request_id={request_sql}.id
              AND accepted_attestation.action='{action}'
              AND accepted_attestation.app_user_id={user_sql}
              AND accepted_attestation.permission_granted
              AND accepted_user.org_id={request_sql}.org_id
              AND accepted_user.keycloak_subject=accepted_attestation.subject
              AND accepted_attestation.subject<>''
              AND accepted_attestation.issuer=accepted_challenge.issuer
              AND accepted_attestation.token_jti=accepted_challenge.token_jti
              AND accepted_attestation.action=accepted_challenge.action
              AND accepted_challenge.request_id={request_sql}.id
              AND accepted_challenge.record_id={request_sql}.record_id
              AND accepted_challenge.manifest_sha256={manifest_sql}.sha256
              AND accepted_challenge.accepted_claims->>'iss'=
                  accepted_attestation.issuer
              AND accepted_challenge.accepted_claims->>'sub'=
                  accepted_attestation.subject
              AND accepted_challenge.accepted_claims->>'sid'=
                  accepted_attestation.session_id
              AND accepted_challenge.accepted_claims->>'jti'=
                  accepted_attestation.token_jti
              AND accepted_challenge.accepted_claims->>'azp'=
                  accepted_attestation.authorized_party
              AND accepted_challenge.accepted_claims->>'acr'=accepted_attestation.acr
              AND accepted_attestation.audience @> '["{R27_CLIENT_ID}"]'::jsonb
              AND accepted_attestation.authorized_party='{R27_CLIENT_ID}'
              AND accepted_attestation.acr='{R27_ACR}'
              AND accepted_attestation.auth_time>=
                  accepted_attestation.issued_at-interval '120 seconds'
              AND accepted_attestation.auth_time<=
                  accepted_attestation.issued_at+interval '30 seconds'
              AND accepted_attestation.expires_at-
                  accepted_attestation.issued_at<=interval '120 seconds'
              AND accepted_challenge.expires_at-
                  accepted_challenge.created_at<=interval '120 seconds'
              AND accepted_attestation.issued_at<={accepted_at_sql}
              AND accepted_challenge.created_at<={accepted_at_sql}
              AND {accepted_at_sql}<accepted_attestation.expires_at
              AND {accepted_at_sql}<accepted_challenge.expires_at
              AND accepted_challenge.consumed_at={accepted_at_sql}
              AND accepted_key.revoked_at IS NULL
              AND accepted_key.active_at<=accepted_attestation.issued_at
              AND (
                  accepted_key.retired_at IS NULL
                  OR accepted_attestation.issued_at<=accepted_key.retired_at
              )
              AND EXISTS (
                  SELECT 1
                  FROM public.audit_event accepted_audit
                  WHERE accepted_audit.id={audit_sql}
                    AND accepted_audit.occurred_at={accepted_at_sql}
                    AND accepted_audit.org_id={request_sql}.org_id
                    AND accepted_audit.actor_type='user'
                    AND accepted_audit.actor_id={user_sql}
                    AND accepted_audit.on_behalf_of IS NULL
                    AND accepted_audit.event_type='RECORD_WORM_DESTROY_REQUESTED'
                    AND accepted_audit.object_type='record'
                    AND accepted_audit.object_id={request_sql}.record_id
                    AND accepted_audit.scope_ref='record'
                    AND accepted_audit.reason='{reason}'
                    AND accepted_audit.after->>'request_id'={request_sql}.id::text
                    AND accepted_audit.after->>'attestation_id'=
                        accepted_attestation.id::text
              )
        )
    """


def _r27_pending_action_sql(*, action: str, now_sql: str) -> str:
    """Return the exact unconsumed action-token relation checked at acceptance."""
    return f"""
        a.action='{action}'
        AND a.permission_granted
        AND a.request_id=r.id
        AND a.app_user_id=action_user.id
        AND action_user.org_id=r.org_id
        AND action_user.keycloak_subject=a.subject
        AND a.subject<>''
        AND source_record.id=r.record_id
        AND source_record.org_id=r.org_id
        AND c.id=a.challenge_id
        AND c.action=a.action
        AND c.request_id=a.request_id
        AND c.record_id=r.record_id
        AND c.manifest_sha256=m.sha256
        AND c.issuer=a.issuer
        AND c.token_jti=a.token_jti
        AND c.accepted_claims->>'iss'=a.issuer
        AND c.accepted_claims->>'sub'=a.subject
        AND c.accepted_claims->>'sid'=a.session_id
        AND c.accepted_claims->>'jti'=a.token_jti
        AND c.accepted_claims->>'azp'=a.authorized_party
        AND c.accepted_claims->>'acr'=a.acr
        AND a.audience @> '["{R27_CLIENT_ID}"]'::jsonb
        AND a.authorized_party='{R27_CLIENT_ID}'
        AND a.acr='{R27_ACR}'
        AND a.auth_time>=a.issued_at-interval '120 seconds'
        AND a.auth_time<=a.issued_at+interval '30 seconds'
        AND a.expires_at-a.issued_at<=interval '120 seconds'
        AND c.expires_at-c.created_at<=interval '120 seconds'
        AND a.issued_at<={now_sql}
        AND c.created_at<={now_sql}
        AND a.expires_at>{now_sql}
        AND c.expires_at>{now_sql}
        AND c.consumed_at IS NULL
        AND m.request_id=r.id
        AND m.schema_version=1
        AND m.expected_state='WAITING_FOR_SECOND_APPROVER'
        AND m.expires_at>{now_sql}
        AND k.id=a.authorizer_key_id
        AND k.revoked_at IS NULL
        AND k.active_at<=a.issued_at
        AND (k.retired_at IS NULL OR a.issued_at<=k.retired_at)
    """


def _r27_human_authority_sql(
    *, request_sql: str, manifest_sql: str, now_sql: str = "clock_timestamp()"
) -> str:
    """Return the exact persisted two-person human authority relation."""
    request_action = _r27_accepted_action_sql(
        action="REQUEST",
        request_sql=request_sql,
        manifest_sql=manifest_sql,
        user_sql=f"{request_sql}.requester_user_id",
        audit_sql=f"{request_sql}.requester_audit_event_id",
        accepted_at_sql=f"{request_sql}.requested_at",
        reason="r27-requester-authorized",
    )
    approval_action = _r27_accepted_action_sql(
        action="APPROVE",
        request_sql=request_sql,
        manifest_sql=manifest_sql,
        user_sql=f"{request_sql}.approver_user_id",
        audit_sql=f"{request_sql}.approver_audit_event_id",
        accepted_at_sql=f"{request_sql}.approved_at",
        reason="r27-second-approval-authorized",
    )
    return f"""
        {request_sql}.requester_user_id<>{request_sql}.approver_user_id
        AND {request_sql}.requested_at<={request_sql}.approved_at
        AND {manifest_sql}.request_id={request_sql}.id
        AND {manifest_sql}.schema_version=1
        AND {manifest_sql}.expected_state='WAITING_FOR_SECOND_APPROVER'
        AND {manifest_sql}.expires_at>{now_sql}
        AND EXISTS (
            SELECT 1 FROM public.record current_record
            WHERE current_record.id={request_sql}.record_id
              AND current_record.org_id={request_sql}.org_id
        )
        AND {request_action}
        AND {approval_action}
        AND EXISTS (
            SELECT 1
            FROM public.r27_attestation request_identity
            JOIN public.r27_attestation approval_identity
              ON approval_identity.request_id=request_identity.request_id
             AND approval_identity.action='APPROVE'
            WHERE request_identity.request_id={request_sql}.id
              AND request_identity.action='REQUEST'
              AND request_identity.app_user_id={request_sql}.requester_user_id
              AND approval_identity.app_user_id={request_sql}.approver_user_id
              AND request_identity.subject<>approval_identity.subject
        )
    """


def _r27_current_authority_sql(
    *,
    request_sql: str,
    execution_sql: str,
    manifest_sql: str,
    now_sql: str = "clock_timestamp()",
) -> str:
    """Return accepted human plus consumed recovery authority for execution."""
    human_authority = _r27_human_authority_sql(
        request_sql=request_sql,
        manifest_sql=manifest_sql,
        now_sql=now_sql,
    )
    return f"""
        {request_sql}.state='FINALIZING'
        AND {human_authority}
        AND EXISTS (
            SELECT 1
            FROM public.recovery_generation_witness current_witness
            JOIN public.recovery_generation_verifier_key current_recovery_key
              ON current_recovery_key.id=current_witness.key_id
             AND current_recovery_key.revoked_at IS NULL
             AND current_recovery_key.not_before<=current_witness.issued_at
             AND (
                 current_recovery_key.retired_at IS NULL
                 OR current_witness.issued_at<=current_recovery_key.retired_at
             )
            WHERE current_witness.request_id={request_sql}.id
              AND current_witness.schema_version=1
              AND current_witness.invalidated_at IS NULL
              AND current_witness.result='VERIFIED'
              AND current_witness.manifest_sha256={manifest_sql}.sha256
              AND current_witness.excluded_set_sha256={manifest_sql}.excluded_set_sha256
              AND current_witness.issued_at>={request_sql}.approved_at
              AND current_witness.verified_at>=current_witness.issued_at
              AND current_witness.verified_at<={now_sql}
              AND current_witness.consumed_execution_id={execution_sql}.id
        )
    """


def _r27_exact_source_sql(*, request_sql: str, execution_sql: str, disposition_sql: str) -> str:
    """Return the immutable R27 source-disposition binding."""
    return f"""
        {execution_sql}.request_id={request_sql}.id
        AND {execution_sql}.state IN ('SOURCE_COMMITTED','PURGING')
        AND {execution_sql}.source_committed_at IS NOT NULL
        AND EXISTS (
            SELECT 1
            FROM public.record source_record
            WHERE source_record.id={request_sql}.record_id
              AND source_record.org_id={request_sql}.org_id
              AND source_record.disposition_state='DISPOSED'
        )
        AND {disposition_sql}.org_id={request_sql}.org_id
        AND {disposition_sql}.record_id={request_sql}.record_id
        AND {disposition_sql}.r27_request_id={request_sql}.id
        AND {disposition_sql}.r27_execution_id={execution_sql}.id
        AND {disposition_sql}.action='DESTROY'
        AND {disposition_sql}.tombstone
        AND {disposition_sql}.is_worm_destroy
        AND {disposition_sql}.policy_id IS NULL
        AND {disposition_sql}.derived_from_disposition_event_id IS NULL
        AND {disposition_sql}.requested_by={request_sql}.requester_user_id
        AND {disposition_sql}.approved_by={request_sql}.approver_user_id
        AND {disposition_sql}.legal_basis={request_sql}.normalized_legal_basis
    """


def _ordinary_exact_source_sql(
    *, marker_sql: str, disposition_sql: str, blob_sql: str, record_sql: str
) -> str:
    """Return the immutable ordinary-destroy source and coordinate binding."""
    return f"""
        {marker_sql}.org_id={record_sql}.org_id
        AND {marker_sql}.record_id={record_sql}.id
        AND {record_sql}.disposition_state='DISPOSED'
        AND {marker_sql}.disposition_event_id={disposition_sql}.id
        AND {disposition_sql}.org_id={record_sql}.org_id
        AND {disposition_sql}.record_id={record_sql}.id
        AND {disposition_sql}.action='DESTROY'
        AND {disposition_sql}.tombstone
        AND NOT {disposition_sql}.is_worm_destroy
        AND {disposition_sql}.r27_request_id IS NULL
        AND {disposition_sql}.r27_execution_id IS NULL
        AND {disposition_sql}.policy_id={record_sql}.retention_policy_id
        AND {disposition_sql}.derived_from_disposition_event_id IS NULL
        AND {marker_sql}.sha256={blob_sql}.sha256
        AND {marker_sql}.org_id={blob_sql}.org_id
        AND {marker_sql}.bucket={blob_sql}.bucket
        AND {marker_sql}.object_key={blob_sql}.object_key
        AND {marker_sql}.object_version_id={blob_sql}.object_version_id
    """


def _create_authority_transition_functions() -> None:
    functions = (
        (
            "easysynq_enqueue_ordinary_exact_purge(p_record_id uuid,p_event_id uuid,p_blob_sha text) RETURNS uuid",
            "easysynq_enqueue_ordinary_exact_purge(uuid,uuid,text)",
            "easysynq_retention",
            "uuid",
        ),
        (
            "easysynq_claim_ordinary_exact_purges(p_limit integer,p_at timestamptz) RETURNS TABLE(marker_id uuid,blob_sha256 text,bucket text,object_key text,object_version_id text)",
            "easysynq_claim_ordinary_exact_purges(integer,timestamptz)",
            "easysynq_retention",
            "table",
        ),
        (
            "easysynq_fail_ordinary_exact_purge(p_id uuid,p_code text,p_detail text,p_at timestamptz) RETURNS void",
            "easysynq_fail_ordinary_exact_purge(uuid,text,text,timestamptz)",
            "easysynq_retention",
            "void",
        ),
        (
            "easysynq_record_ordinary_exact_purge(p_id uuid,p_at timestamptz) RETURNS void",
            "easysynq_record_ordinary_exact_purge(uuid,timestamptz)",
            "easysynq_retention",
            "void",
        ),
        (
            "easysynq_authorize_hold_release(p_id uuid,p_digest text,p_identity text,p_at timestamptz) RETURNS bigint",
            "easysynq_authorize_hold_release(uuid,text,text,timestamptz)",
            "easysynq_hold_authorizer",
            "bigint",
        ),
        (
            "easysynq_claim_hold_releases(p_limit integer,p_at timestamptz) RETURNS TABLE(operation_id uuid,record_id uuid,blob_sha256 text,object_version_id text)",
            "easysynq_claim_hold_releases(integer,timestamptz)",
            "easysynq_hold_maintenance",
            "table",
        ),
        (
            "easysynq_fail_hold_release(p_id uuid,p_code text,p_detail text,p_at timestamptz) RETURNS void",
            "easysynq_fail_hold_release(uuid,text,text,timestamptz)",
            "easysynq_hold_maintenance",
            "void",
        ),
        (
            "easysynq_record_ordinary_hold_release(p_sha text,p_version text,p_id uuid,p_at timestamptz) RETURNS void",
            "easysynq_record_ordinary_hold_release(text,text,uuid,timestamptz)",
            "easysynq_hold_maintenance",
            "void",
        ),
        (
            "easysynq_accept_r27_request(p_id uuid,p_at timestamptz) RETURNS bigint",
            "easysynq_accept_r27_request(uuid,timestamptz)",
            "easysynq_r27_authorizer",
            "bigint",
        ),
        (
            "easysynq_accept_r27_approval(p_id uuid,p_at timestamptz) RETURNS bigint",
            "easysynq_accept_r27_approval(uuid,timestamptz)",
            "easysynq_r27_authorizer",
            "bigint",
        ),
        (
            "easysynq_cancel_r27_request(p_id uuid,p_at timestamptz) RETURNS bigint",
            "easysynq_cancel_r27_request(uuid,timestamptz)",
            "easysynq_r27_authorizer",
            "bigint",
        ),
        (
            "easysynq_mark_r27_stale(p_id uuid,p_code text,p_detail text,p_at timestamptz) RETURNS void",
            "easysynq_mark_r27_stale(uuid,text,text,timestamptz)",
            "easysynq_r27_authorizer",
            "void",
        ),
        (
            "easysynq_claim_r27_finalizations(p_limit integer,p_at timestamptz) RETURNS TABLE(request_id uuid,execution_id uuid)",
            "easysynq_claim_r27_finalizations(integer,timestamptz)",
            "easysynq_r27_maintenance",
            "table",
        ),
        (
            "easysynq_fail_r27_execution(p_id uuid,p_code text,p_detail text,p_at timestamptz) RETURNS void",
            "easysynq_fail_r27_execution(uuid,text,text,timestamptz)",
            "easysynq_r27_maintenance",
            "void",
        ),
        (
            "easysynq_record_r27_hold_release(p_sha text,p_version text,p_id uuid,p_at timestamptz) RETURNS void",
            "easysynq_record_r27_hold_release(text,text,uuid,timestamptz)",
            "easysynq_r27_maintenance",
            "void",
        ),
        (
            "easysynq_claim_r27_exact_purges(p_id uuid,p_limit integer,p_at timestamptz) RETURNS TABLE(marker_id uuid,blob_sha256 text,bucket text,object_key text,object_version_id text)",
            "easysynq_claim_r27_exact_purges(uuid,integer,timestamptz)",
            "easysynq_r27_maintenance",
            "table",
        ),
        (
            "easysynq_fail_r27_exact_purge(p_execution uuid,p_marker uuid,p_code text,p_detail text,p_at timestamptz) RETURNS void",
            "easysynq_fail_r27_exact_purge(uuid,uuid,text,text,timestamptz)",
            "easysynq_r27_maintenance",
            "void",
        ),
        (
            "easysynq_record_r27_purge(p_sha text,p_version text,p_id uuid,p_at timestamptz) RETURNS void",
            "easysynq_record_r27_purge(text,text,uuid,timestamptz)",
            "easysynq_r27_maintenance",
            "void",
        ),
        (
            "easysynq_record_r27_surviving_owner(p_sha text,p_version text,p_id uuid,p_at timestamptz) RETURNS void",
            "easysynq_record_r27_surviving_owner(text,text,uuid,timestamptz)",
            "easysynq_r27_maintenance",
            "void",
        ),
        (
            "easysynq_begin_r27_role_membership(p_operation uuid,p_user_id uuid,p_action text,p_identity text,p_at timestamptz) RETURNS void",
            "easysynq_begin_r27_role_membership(uuid,uuid,text,text,timestamptz)",
            "easysynq_r27_role_manager",
            "void",
        ),
        (
            "easysynq_complete_r27_role_membership(p_operation uuid,p_at timestamptz) RETURNS bigint",
            "easysynq_complete_r27_role_membership(uuid,timestamptz)",
            "easysynq_r27_role_manager",
            "bigint",
        ),
        (
            "easysynq_fail_r27_role_membership(p_operation uuid,p_code text,p_detail text,p_at timestamptz) RETURNS void",
            "easysynq_fail_r27_role_membership(uuid,text,text,timestamptz)",
            "easysynq_r27_role_manager",
            "void",
        ),
    )
    ordinary_source = _ordinary_exact_source_sql(
        marker_sql="p",
        disposition_sql="source_disposition",
        blob_sql="source_blob",
        record_sql="source_record",
    )
    ordinary_live_owner = _live_owner_exists_sql(
        org_sql="p.org_id",
        blob_sha_sql="p.sha256",
        target_record_sql="p.record_id",
    )
    ordinary_hold_authority = _ordinary_hold_authority_sql(
        operation_sql="o",
        record_sql="source_record",
        blob_sql="source_blob",
        user_sql="initiator",
        edge_sql="source_edge",
    )
    ordinary_hold_obligation = _hold_obligation_exists_sql(
        org_sql="o.org_id",
        blob_sha_sql="o.blob_sha256",
    )
    locked_hold_obligation = _hold_obligation_exists_sql(
        org_sql="v_org",
        blob_sha_sql="v_sha",
    )
    read_committed_guard = _read_committed_guard_sql()
    pending_request_action = _r27_pending_action_sql(action="REQUEST", now_sql="v_now")
    pending_approval_action = _r27_pending_action_sql(action="APPROVE", now_sql="v_now")
    pending_cancel_action = _r27_pending_action_sql(action="CANCEL", now_sql="v_now")
    prior_request_action = _r27_accepted_action_sql(
        action="REQUEST",
        request_sql="r",
        manifest_sql="m",
        user_sql="r.requester_user_id",
        audit_sql="r.requester_audit_event_id",
        accepted_at_sql="r.requested_at",
        reason="r27-requester-authorized",
    )
    finalization_human_authority = _r27_human_authority_sql(
        request_sql="r",
        manifest_sql="m",
        now_sql="v_now",
    )
    finalization_candidate_human_authority = _r27_human_authority_sql(
        request_sql="r",
        manifest_sql="m",
        now_sql="clock_timestamp()",
    )
    r27_source = _r27_exact_source_sql(
        request_sql="request",
        execution_sql="e",
        disposition_sql="source_disposition",
    )
    r27_current_authority = _r27_current_authority_sql(
        request_sql="request",
        execution_sql="e",
        manifest_sql="m",
    )
    r27_live_owner = _live_owner_exists_sql(
        org_sql="request.org_id",
        blob_sha_sql="target.blob_sha256",
        target_record_sql="request.record_id",
    )
    r27_hold_obligation = _hold_obligation_exists_sql(
        org_sql="request.org_id",
        blob_sha_sql="target.blob_sha256",
    )
    bodies = {
        "easysynq_enqueue_ordinary_exact_purge(uuid,uuid,text)": """
            DECLARE v_id uuid:=gen_random_uuid();
            BEGIN
                IF p_record_id IS NULL OR p_event_id IS NULL OR p_blob_sha IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                INSERT INTO public.pending_blob_purge
                    (id,org_id,record_id,disposition_event_id,sha256,bucket,object_key,
                     object_version_id,bypass_governance,r27_request_id,r27_execution_id)
                SELECT v_id,b.org_id,p_record_id,p_event_id,b.sha256,b.bucket,b.object_key,
                       b.object_version_id,false,NULL,NULL
                FROM public.evidence_blob eb JOIN public.blob b ON b.sha256=eb.blob_sha256
                JOIN public.disposition_event de ON de.id=p_event_id AND de.record_id=p_record_id
                    AND de.org_id=b.org_id AND de.action='DESTROY'
                WHERE eb.record_id=p_record_id AND eb.org_id=b.org_id
                  AND b.sha256=p_blob_sha AND NOT de.is_worm_destroy;
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_exact_purge_refused'; END IF;
                RETURN v_id;
            END""",
        "easysynq_claim_ordinary_exact_purges(integer,timestamptz)": f"""
            DECLARE v_ids uuid[];
            BEGIN
                {read_committed_guard}
                IF p_limit IS NULL OR p_at IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'ordinary_purge_claim_refused'; END IF;
                SELECT array_agg(locked.id ORDER BY locked.created_at,locked.id)
                INTO v_ids
                FROM (
                    SELECT p.id,p.created_at
                    FROM public.pending_blob_purge p
                    JOIN public.disposition_event source_disposition
                      ON source_disposition.id=p.disposition_event_id
                    JOIN public.record source_record ON source_record.id=p.record_id
                    JOIN public.blob source_blob ON source_blob.sha256=p.sha256
                    WHERE NOT p.bypass_governance
                      AND p.r27_request_id IS NULL AND p.r27_execution_id IS NULL
                      AND p.state IN ('PENDING','FAILED')
                      AND {ordinary_source}
                      AND NOT {ordinary_live_owner}
                    ORDER BY p.created_at,p.id
                    FOR UPDATE OF p SKIP LOCKED
                    LIMIT p_limit
                ) AS locked;
                IF COALESCE(cardinality(v_ids),0)=0 THEN RETURN; END IF;
                PERFORM 1
                FROM public.pending_blob_purge p
                JOIN public.blob source_blob ON source_blob.sha256=p.sha256
                WHERE p.id=ANY(v_ids)
                  AND p.org_id=source_blob.org_id
                  AND p.bucket=source_blob.bucket
                  AND p.object_key=source_blob.object_key
                  AND p.object_version_id=source_blob.object_version_id
                ORDER BY source_blob.sha256,p.id
                FOR UPDATE OF source_blob;
                RETURN QUERY WITH valid AS (
                    SELECT p.id
                    FROM public.pending_blob_purge p
                    JOIN public.disposition_event source_disposition
                      ON source_disposition.id=p.disposition_event_id
                    JOIN public.record source_record ON source_record.id=p.record_id
                    JOIN public.blob source_blob ON source_blob.sha256=p.sha256
                    WHERE p.id=ANY(v_ids)
                      AND p.state IN ('PENDING','FAILED')
                      AND NOT p.bypass_governance
                      AND p.r27_request_id IS NULL AND p.r27_execution_id IS NULL
                      AND {ordinary_source}
                      AND NOT {ordinary_live_owner}
                ), claimed AS (
                    UPDATE public.pending_blob_purge p SET state='RUNNING',attempt_count=p.attempt_count+1,
                    claimed_at=clock_timestamp(),error_code=NULL,error_detail=NULL,updated_at=clock_timestamp()
                    FROM valid WHERE p.id=valid.id RETURNING p.*)
                SELECT claimed.id,claimed.sha256::text,claimed.bucket,claimed.object_key,
                       claimed.object_version_id FROM claimed;
            END""",
        "easysynq_fail_ordinary_exact_purge(uuid,text,text,timestamptz)": """
            BEGIN
                IF p_id IS NULL OR p_code IS NULL OR p_at IS NULL OR btrim(p_code)='' OR length(p_code)>64 OR length(COALESCE(p_detail,''))>512 THEN RAISE EXCEPTION 'ordinary_purge_failure_refused'; END IF;
                UPDATE public.pending_blob_purge SET state='FAILED',error_code=p_code,error_detail=p_detail,
                    updated_at=clock_timestamp() WHERE id=p_id AND state='RUNNING'
                      AND NOT bypass_governance AND r27_request_id IS NULL
                      AND r27_execution_id IS NULL;
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_purge_failure_refused'; END IF;
            END""",
        "easysynq_record_ordinary_exact_purge(uuid,timestamptz)": f"""
            DECLARE v_marker uuid;
            BEGIN
                {read_committed_guard}
                IF p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT p.id INTO v_marker
                FROM public.pending_blob_purge p
                JOIN public.blob source_blob ON source_blob.sha256=p.sha256
                WHERE p.id=p_id AND p.state='RUNNING' AND NOT p.bypass_governance
                  AND p.r27_request_id IS NULL AND p.r27_execution_id IS NULL
                  AND p.org_id=source_blob.org_id
                  AND p.bucket=source_blob.bucket
                  AND p.object_key=source_blob.object_key
                  AND p.object_version_id=source_blob.object_version_id
                FOR UPDATE OF p,source_blob;
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_purge_result_refused'; END IF;
                UPDATE public.blob source_blob
                SET purged_at=clock_timestamp(),purge_execution_id=NULL
                FROM public.pending_blob_purge p,
                     public.disposition_event source_disposition,
                     public.record source_record
                WHERE p.id=v_marker AND source_blob.sha256=p.sha256
                  AND p.state='RUNNING' AND NOT p.bypass_governance
                  AND p.r27_request_id IS NULL AND p.r27_execution_id IS NULL
                  AND {ordinary_source}
                  AND NOT {ordinary_live_owner};
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_purge_result_refused'; END IF;
                UPDATE public.pending_blob_purge
                SET state='VERIFIED',completed_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE id=v_marker AND state='RUNNING';
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_purge_result_refused'; END IF;
            END""",
        "easysynq_authorize_hold_release(uuid,text,text,timestamptz)": f"""
            DECLARE v_org uuid; v_record uuid; v_sha text; v_audit bigint;
                    v_now timestamptz;
            BEGIN
                IF p_id IS NULL OR p_digest IS NULL OR p_identity IS NULL OR p_at IS NULL OR btrim(p_identity)='' THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT o.org_id,o.record_id,o.blob_sha256 INTO v_org,v_record,v_sha
                FROM public.worm_hold_release_operation o
                JOIN public.record source_record ON source_record.id=o.record_id
                JOIN public.app_user initiator ON initiator.id=o.initiated_by_user_id
                JOIN public.blob source_blob ON source_blob.sha256=o.blob_sha256
                JOIN public.evidence_blob source_edge
                  ON source_edge.record_id=o.record_id
                 AND source_edge.blob_sha256=o.blob_sha256
                WHERE o.id=p_id AND o.state='PENDING_AUTHORIZATION'
                  AND o.canonical_sha256=p_digest
                  AND {ordinary_hold_authority}
                FOR UPDATE OF o
                FOR SHARE OF source_record,initiator,source_blob;
                IF NOT FOUND OR {locked_hold_obligation} THEN
                    RAISE EXCEPTION 'hold_release_authorization_refused';
                END IF;
                v_now:=clock_timestamp();
                INSERT INTO public.audit_event(org_id,occurred_at,actor_type,event_type,object_type,object_id,scope_ref,reason,after)
                VALUES(v_org,v_now,'system','RECORD_LEGAL_HOLD_RELEASE_AUTHORIZED','record',v_record,'record','ordinary-hold-release-authorized',jsonb_build_object('operator_identity',p_identity,'operation_id',p_id)) RETURNING id INTO v_audit;
                INSERT INTO public.worm_hold_release_authorization(operation_id,canonical_sha256,host_operator_identity,authorizing_audit_event_id,authorized_at,authorizer_role)
                VALUES(p_id,p_digest,p_identity,v_audit,v_now,'easysynq_hold_authorizer');
                RETURN v_audit;
            END""",
        "easysynq_claim_hold_releases(integer,timestamptz)": f"""
            DECLARE v_ids uuid[];
            BEGIN
                {read_committed_guard}
                IF p_limit IS NULL OR p_at IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'hold_release_claim_refused'; END IF;
                SELECT array_agg(locked.id ORDER BY locked.created_at,locked.id)
                INTO v_ids
                FROM (
                    SELECT o.id,o.created_at
                    FROM public.worm_hold_release_operation o
                    JOIN public.worm_hold_release_authorization a ON a.operation_id=o.id
                    JOIN public.record source_record ON source_record.id=o.record_id
                    JOIN public.app_user initiator ON initiator.id=o.initiated_by_user_id
                    JOIN public.blob source_blob ON source_blob.sha256=o.blob_sha256
                    JOIN public.evidence_blob source_edge
                      ON source_edge.record_id=o.record_id
                     AND source_edge.blob_sha256=o.blob_sha256
                    WHERE o.state IN ('AUTHORIZED','FAILED')
                      AND source_blob.worm_legal_hold
                      AND {ordinary_hold_authority}
                      AND NOT {ordinary_hold_obligation}
                    ORDER BY o.created_at,o.id
                    FOR UPDATE OF o SKIP LOCKED
                    LIMIT p_limit
                ) AS locked;
                IF COALESCE(cardinality(v_ids),0)=0 THEN RETURN; END IF;
                PERFORM 1
                FROM public.worm_hold_release_operation o
                JOIN public.record source_record ON source_record.id=o.record_id
                JOIN public.app_user initiator ON initiator.id=o.initiated_by_user_id
                JOIN public.blob source_blob ON source_blob.sha256=o.blob_sha256
                JOIN public.evidence_blob source_edge
                  ON source_edge.record_id=o.record_id
                 AND source_edge.blob_sha256=o.blob_sha256
                WHERE o.id=ANY(v_ids)
                  AND source_blob.worm_legal_hold
                  AND {ordinary_hold_authority}
                ORDER BY source_blob.sha256,o.id
                FOR UPDATE OF source_blob
                FOR SHARE OF source_record,initiator;
                RETURN QUERY WITH valid AS (
                    SELECT o.id
                    FROM public.worm_hold_release_operation o
                    JOIN public.worm_hold_release_authorization a ON a.operation_id=o.id
                    JOIN public.record source_record ON source_record.id=o.record_id
                    JOIN public.app_user initiator ON initiator.id=o.initiated_by_user_id
                    JOIN public.blob source_blob ON source_blob.sha256=o.blob_sha256
                    JOIN public.evidence_blob source_edge
                      ON source_edge.record_id=o.record_id
                     AND source_edge.blob_sha256=o.blob_sha256
                    WHERE o.id=ANY(v_ids)
                      AND o.state IN ('AUTHORIZED','FAILED')
                      AND source_blob.worm_legal_hold
                      AND {ordinary_hold_authority}
                      AND NOT {ordinary_hold_obligation}
                ), claimed AS (
                    UPDATE public.worm_hold_release_operation o SET state='RUNNING',attempt_count=o.attempt_count+1,
                    started_at=COALESCE(o.started_at,clock_timestamp()),error_code=NULL,error_detail=NULL,updated_at=clock_timestamp()
                    FROM valid WHERE o.id=valid.id RETURNING o.*)
                SELECT claimed.id,claimed.record_id,claimed.blob_sha256::text,
                       claimed.object_version_id FROM claimed;
            END""",
        "easysynq_fail_hold_release(uuid,text,text,timestamptz)": """
            BEGIN
                IF p_id IS NULL OR p_code IS NULL OR p_at IS NULL OR btrim(p_code)='' OR length(p_code)>64 OR length(COALESCE(p_detail,''))>512 THEN RAISE EXCEPTION 'hold_release_failure_refused'; END IF;
                UPDATE public.worm_hold_release_operation SET state='FAILED',error_code=p_code,error_detail=p_detail,updated_at=clock_timestamp() WHERE id=p_id AND state='RUNNING';
                IF NOT FOUND THEN RAISE EXCEPTION 'hold_release_failure_refused'; END IF;
            END""",
        "easysynq_record_ordinary_hold_release(text,text,uuid,timestamptz)": f"""
            DECLARE v_operation uuid; v_org uuid; v_record uuid; v_sha text;
                    v_now timestamptz:=clock_timestamp();
            BEGIN
                {read_committed_guard}
                IF p_sha IS NULL OR p_version IS NULL OR p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT o.id,o.org_id,o.record_id,o.blob_sha256
                INTO v_operation,v_org,v_record,v_sha
                FROM public.worm_hold_release_operation o
                JOIN public.worm_hold_release_authorization a ON a.operation_id=o.id
                JOIN public.record source_record ON source_record.id=o.record_id
                JOIN public.app_user initiator ON initiator.id=o.initiated_by_user_id
                JOIN public.blob source_blob ON source_blob.sha256=o.blob_sha256
                JOIN public.evidence_blob source_edge
                  ON source_edge.record_id=o.record_id
                 AND source_edge.blob_sha256=o.blob_sha256
                WHERE o.id=p_id AND o.state='RUNNING'
                  AND o.blob_sha256=p_sha AND o.object_version_id=p_version
                  AND source_blob.worm_legal_hold
                  AND {ordinary_hold_authority}
                FOR UPDATE OF o,source_blob
                FOR SHARE OF source_record,initiator;
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_hold_release_refused'; END IF;
                UPDATE public.blob source_blob
                SET worm_legal_hold=false,worm_legal_hold_verified_at=v_now
                FROM public.worm_hold_release_operation o
                JOIN public.worm_hold_release_authorization a ON a.operation_id=o.id
                JOIN public.record source_record ON source_record.id=o.record_id
                JOIN public.app_user initiator ON initiator.id=o.initiated_by_user_id
                JOIN public.evidence_blob source_edge
                  ON source_edge.record_id=o.record_id
                 AND source_edge.blob_sha256=o.blob_sha256
                WHERE o.id=v_operation AND o.state='RUNNING'
                  AND source_blob.sha256=o.blob_sha256
                  AND source_blob.worm_legal_hold
                  AND {ordinary_hold_authority}
                  AND NOT {ordinary_hold_obligation};
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_hold_release_refused'; END IF;
                UPDATE public.worm_hold_release_operation
                SET state='VERIFIED',completed_at=v_now,updated_at=v_now
                WHERE id=v_operation AND state='RUNNING';
                IF NOT FOUND THEN RAISE EXCEPTION 'ordinary_hold_release_refused'; END IF;
                INSERT INTO public.audit_event
                    (org_id,occurred_at,actor_type,event_type,object_type,object_id,
                     scope_ref,reason,after)
                SELECT o.org_id,v_now,'system','RECORD_LEGAL_HOLD_RELEASED',
                       'record',o.record_id,'record','ordinary-hold-release-verified',
                       jsonb_build_object('operation_id',o.id)
                FROM public.worm_hold_release_operation o WHERE o.id=v_operation;
            END""",
        "easysynq_mark_r27_stale(uuid,text,text,timestamptz)": """
            BEGIN
                IF p_id IS NULL OR p_code IS NULL OR p_at IS NULL OR btrim(p_code)='' OR length(p_code)>64 OR length(COALESCE(p_detail,''))>512 THEN RAISE EXCEPTION 'r27_stale_refused'; END IF;
                UPDATE public.r27_request SET state='STALE',error_code=p_code,error_detail=p_detail,stale_at=clock_timestamp(),updated_at=clock_timestamp()
                WHERE id=p_id AND (state IS NULL OR state IN ('WAITING_FOR_SECOND_APPROVER','WAITING_FOR_RECOVERY_GENERATION','READY_FOR_FINALIZATION'));
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_stale_refused'; END IF;
            END""",
        "easysynq_accept_r27_request(uuid,timestamptz)": f"""
            DECLARE v_request uuid; v_user uuid; v_org uuid; v_record uuid;
                    v_challenge uuid; v_key uuid; v_audit bigint;
                    v_now timestamptz;
            BEGIN
                IF p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT k.id INTO v_key
                FROM public.r27_attestation a
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                WHERE a.id=p_id AND a.action='REQUEST'
                  AND k.revoked_at IS NULL
                  AND k.active_at<=a.issued_at
                  AND (k.retired_at IS NULL OR a.issued_at<=k.retired_at)
                FOR SHARE OF k;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_request_authority_refused'; END IF;
                PERFORM 1
                FROM public.r27_attestation a
                JOIN public.r27_action_challenge c ON c.id=a.challenge_id
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                JOIN public.r27_request r ON r.id=a.request_id
                JOIN public.r27_manifest m ON m.request_id=a.request_id
                JOIN public.app_user action_user ON action_user.id=a.app_user_id
                JOIN public.record source_record ON source_record.id=r.record_id
                WHERE a.id=p_id AND a.action='REQUEST'
                FOR UPDATE OF c,r
                FOR SHARE OF a,k,m,action_user,source_record;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_request_authority_refused'; END IF;
                v_now:=clock_timestamp();
                SELECT a.request_id,a.app_user_id,r.org_id,r.record_id,c.id
                INTO v_request,v_user,v_org,v_record,v_challenge
                FROM public.r27_attestation a
                JOIN public.r27_action_challenge c ON c.id=a.challenge_id
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                JOIN public.r27_request r ON r.id=a.request_id
                JOIN public.r27_manifest m ON m.request_id=a.request_id
                JOIN public.app_user action_user ON action_user.id=a.app_user_id
                JOIN public.record source_record ON source_record.id=r.record_id
                WHERE a.id=p_id AND r.state IS NULL
                  AND {pending_request_action};
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_request_authority_refused'; END IF;
                INSERT INTO public.audit_event(org_id,occurred_at,actor_type,actor_id,event_type,object_type,object_id,scope_ref,reason,after)
                VALUES(v_org,v_now,'user',v_user,'RECORD_WORM_DESTROY_REQUESTED','record',v_record,'record','r27-requester-authorized',jsonb_build_object('request_id',v_request,'attestation_id',p_id)) RETURNING id INTO v_audit;
                UPDATE public.r27_action_challenge SET consumed_at=v_now
                WHERE id=v_challenge AND consumed_at IS NULL;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_request_authority_refused'; END IF;
                UPDATE public.r27_request SET state='WAITING_FOR_SECOND_APPROVER',requester_user_id=v_user,requester_audit_event_id=v_audit,requested_at=v_now,updated_at=v_now
                WHERE id=v_request AND state IS NULL;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_request_authority_refused'; END IF;
                RETURN v_audit;
            END""",
        "easysynq_accept_r27_approval(uuid,timestamptz)": f"""
            DECLARE v_request uuid; v_user uuid; v_org uuid; v_record uuid;
                    v_challenge uuid; v_audit bigint;
                    v_now timestamptz;
            BEGIN
                IF p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                PERFORM locked_key.id
                FROM public.r27_authorizer_key locked_key
                WHERE locked_key.id IN (
                    SELECT a.authorizer_key_id FROM public.r27_attestation a WHERE a.id=p_id
                    UNION
                    SELECT request_attestation.authorizer_key_id
                    FROM public.r27_attestation approval_attestation
                    JOIN public.r27_attestation request_attestation
                      ON request_attestation.request_id=approval_attestation.request_id
                     AND request_attestation.action='REQUEST'
                    WHERE approval_attestation.id=p_id
                )
                ORDER BY locked_key.id
                FOR SHARE OF locked_key;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_approval_authority_refused'; END IF;
                PERFORM 1
                FROM public.r27_attestation a
                JOIN public.r27_action_challenge c ON c.id=a.challenge_id
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                JOIN public.r27_request r ON r.id=a.request_id
                JOIN public.r27_manifest m ON m.request_id=a.request_id
                JOIN public.app_user action_user ON action_user.id=a.app_user_id
                JOIN public.app_user requester_user ON requester_user.id=r.requester_user_id
                JOIN public.record source_record ON source_record.id=r.record_id
                JOIN public.r27_attestation request_attestation
                  ON request_attestation.request_id=r.id
                 AND request_attestation.action='REQUEST'
                JOIN public.r27_action_challenge request_challenge
                  ON request_challenge.id=request_attestation.challenge_id
                JOIN public.r27_authorizer_key request_key
                  ON request_key.id=request_attestation.authorizer_key_id
                WHERE a.id=p_id AND a.action='APPROVE'
                FOR UPDATE OF c,r
                FOR SHARE OF a,k,m,action_user,requester_user,source_record,
                             request_attestation,request_challenge,request_key;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_approval_authority_refused'; END IF;
                v_now:=clock_timestamp();
                SELECT a.request_id,a.app_user_id,r.org_id,r.record_id,c.id
                INTO v_request,v_user,v_org,v_record,v_challenge
                FROM public.r27_attestation a
                JOIN public.r27_action_challenge c ON c.id=a.challenge_id
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                JOIN public.r27_request r ON r.id=a.request_id
                JOIN public.r27_manifest m ON m.request_id=a.request_id
                JOIN public.app_user action_user ON action_user.id=a.app_user_id
                JOIN public.app_user requester_user ON requester_user.id=r.requester_user_id
                JOIN public.record source_record ON source_record.id=r.record_id
                WHERE a.id=p_id
                  AND r.state='WAITING_FOR_SECOND_APPROVER'
                  AND a.app_user_id<>r.requester_user_id
                  AND action_user.keycloak_subject<>requester_user.keycloak_subject
                  AND {pending_approval_action}
                  AND {prior_request_action};
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_approval_authority_refused'; END IF;
                INSERT INTO public.audit_event(org_id,occurred_at,actor_type,actor_id,event_type,object_type,object_id,scope_ref,reason,after)
                VALUES(v_org,v_now,'user',v_user,'RECORD_WORM_DESTROY_REQUESTED','record',v_record,'record','r27-second-approval-authorized',jsonb_build_object('request_id',v_request,'attestation_id',p_id)) RETURNING id INTO v_audit;
                UPDATE public.r27_action_challenge SET consumed_at=v_now
                WHERE id=v_challenge AND consumed_at IS NULL;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_approval_authority_refused'; END IF;
                UPDATE public.r27_request SET state='WAITING_FOR_RECOVERY_GENERATION',approver_user_id=v_user,approver_audit_event_id=v_audit,approved_at=v_now,updated_at=v_now
                WHERE id=v_request AND state='WAITING_FOR_SECOND_APPROVER';
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_approval_authority_refused'; END IF;
                RETURN v_audit;
            END""",
        "easysynq_cancel_r27_request(uuid,timestamptz)": f"""
            DECLARE v_request uuid; v_user uuid; v_org uuid; v_record uuid;
                    v_challenge uuid; v_key uuid; v_audit bigint;
                    v_now timestamptz:=clock_timestamp();
            BEGIN
                IF p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT k.id INTO v_key
                FROM public.r27_attestation a
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                WHERE a.id=p_id AND a.action='CANCEL'
                  AND k.revoked_at IS NULL
                  AND k.active_at<=a.issued_at
                  AND (k.retired_at IS NULL OR a.issued_at<=k.retired_at)
                FOR SHARE OF k;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_cancel_refused'; END IF;
                PERFORM 1
                FROM public.r27_attestation a
                JOIN public.r27_action_challenge c ON c.id=a.challenge_id
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                JOIN public.r27_request r ON r.id=a.request_id
                JOIN public.r27_manifest m ON m.request_id=a.request_id
                JOIN public.app_user action_user ON action_user.id=a.app_user_id
                JOIN public.record source_record ON source_record.id=r.record_id
                WHERE a.id=p_id AND a.action='CANCEL'
                FOR UPDATE OF c,r
                FOR SHARE OF a,k,m,action_user,source_record;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_cancel_refused'; END IF;
                v_now:=clock_timestamp();
                SELECT a.request_id,a.app_user_id,r.org_id,r.record_id,c.id
                INTO v_request,v_user,v_org,v_record,v_challenge
                FROM public.r27_attestation a
                JOIN public.r27_action_challenge c ON c.id=a.challenge_id
                JOIN public.r27_authorizer_key k ON k.id=a.authorizer_key_id
                JOIN public.r27_request r ON r.id=a.request_id
                JOIN public.r27_manifest m ON m.request_id=a.request_id
                JOIN public.app_user action_user ON action_user.id=a.app_user_id
                JOIN public.record source_record ON source_record.id=r.record_id
                WHERE a.id=p_id
                  AND r.state IN ('WAITING_FOR_SECOND_APPROVER','WAITING_FOR_RECOVERY_GENERATION','READY_FOR_FINALIZATION')
                  AND {pending_cancel_action};
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_cancel_refused'; END IF;
                INSERT INTO public.audit_event(org_id,occurred_at,actor_type,actor_id,event_type,object_type,object_id,scope_ref,reason,after)
                VALUES(v_org,v_now,'user',v_user,'RECORD_WORM_DESTROY_CANCELLED','record',v_record,'record','r27-cancelled',jsonb_build_object('request_id',v_request,'attestation_id',p_id)) RETURNING id INTO v_audit;
                UPDATE public.r27_action_challenge SET consumed_at=v_now
                WHERE id=v_challenge AND consumed_at IS NULL;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_cancel_refused'; END IF;
                UPDATE public.r27_request SET state='CANCELLED',cancelled_by_user_id=v_user,cancellation_audit_event_id=v_audit,cancelled_at=v_now,updated_at=v_now
                WHERE id=v_request
                  AND state IN ('WAITING_FOR_SECOND_APPROVER','WAITING_FOR_RECOVERY_GENERATION','READY_FOR_FINALIZATION');
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_cancel_refused'; END IF;
                RETURN v_audit;
            END""",
        "easysynq_claim_r27_finalizations(integer,timestamptz)": f"""
            DECLARE
                v_now timestamptz;
                v_request uuid;
                v_internal uuid;
                v_public uuid;
                v_witness uuid;
                v_recovery_key uuid;
                v_branch text;
                v_skipped uuid[]:=ARRAY[]::uuid[];
                v_claimed integer:=0;
                v_inspected integer:=0;
                v_inspection_budget integer;
            BEGIN
                {read_committed_guard}
                IF p_limit IS NULL OR p_at IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
                    RAISE EXCEPTION 'r27_finalization_claim_refused';
                END IF;
                v_inspection_budget:=LEAST(p_limit * 10, 1000);
                WHILE v_claimed<p_limit AND v_inspected<v_inspection_budget LOOP
                    v_request:=NULL;
                    v_internal:=NULL;
                    v_public:=NULL;
                    v_witness:=NULL;
                    v_recovery_key:=NULL;
                    v_branch:=NULL;
                    SELECT candidate.id,candidate.internal_id,candidate.branch
                    INTO v_request,v_internal,v_branch
                    FROM (
                        SELECT r.id,existing.id AS internal_id,
                               CASE
                                 WHEN existing.id IS NULL THEN 'INITIAL'
                                 WHEN r.state='READY_FOR_FINALIZATION' THEN 'RECOVERY'
                                 ELSE 'RETRY'
                               END AS branch,
                               r.created_at
                        FROM public.r27_request r
                        JOIN public.r27_manifest m ON m.request_id=r.id
                        LEFT JOIN public.r27_execution existing
                          ON existing.request_id=r.id
                        WHERE NOT (r.id=ANY(v_skipped))
                          AND (
                            (r.state='READY_FOR_FINALIZATION' AND
                              (existing.id IS NULL OR
                               (existing.state='FAILED' AND
                                existing.error_code='RECOVERY_KEY_REVOKED')))
                            OR
                            (r.state='FINALIZING' AND existing.state='FAILED'
                             AND existing.error_code IS DISTINCT FROM
                                 'RECOVERY_KEY_REVOKED'
                             AND existing.next_attempt_at<=clock_timestamp())
                          )
                          AND {finalization_candidate_human_authority}
                          AND EXISTS (
                              SELECT 1
                              FROM public.recovery_generation_witness candidate_witness
                              JOIN public.recovery_generation_verifier_key candidate_key
                                ON candidate_key.id=candidate_witness.key_id
                              WHERE candidate_witness.request_id=r.id
                                AND candidate_witness.schema_version=1
                                AND candidate_witness.invalidated_at IS NULL
                                AND candidate_witness.result='VERIFIED'
                                AND candidate_witness.manifest_sha256=m.sha256
                                AND candidate_witness.excluded_set_sha256=
                                    m.excluded_set_sha256
                                AND candidate_witness.issued_at>=r.approved_at
                                AND candidate_witness.verified_at>=
                                    candidate_witness.issued_at
                                AND candidate_witness.verified_at<=clock_timestamp()
                                AND candidate_key.revoked_at IS NULL
                                AND candidate_key.not_before<=
                                    candidate_witness.issued_at
                                AND (
                                    candidate_key.retired_at IS NULL
                                    OR candidate_witness.issued_at<=
                                       candidate_key.retired_at
                                )
                                AND (
                                    (r.state='READY_FOR_FINALIZATION'
                                     AND existing.id IS NULL
                                     AND candidate_witness.consumed_execution_id IS NULL)
                                    OR
                                    (r.state='READY_FOR_FINALIZATION'
                                     AND existing.state='FAILED'
                                     AND existing.error_code='RECOVERY_KEY_REVOKED'
                                     AND candidate_witness.consumed_execution_id IS NULL)
                                    OR
                                    (r.state='FINALIZING'
                                     AND existing.state='FAILED'
                                     AND existing.error_code IS DISTINCT FROM
                                         'RECOVERY_KEY_REVOKED'
                                     AND existing.next_attempt_at<=clock_timestamp()
                                     AND candidate_witness.consumed_execution_id=existing.id)
                                )
                          )
                        ORDER BY r.created_at,r.id
                        LIMIT 1
                    ) AS candidate;
                    EXIT WHEN v_request IS NULL;
                    v_skipped:=array_append(v_skipped,v_request);
                    v_inspected:=v_inspected+1;

                    SELECT w.id,w.key_id
                    INTO v_witness,v_recovery_key
                    FROM public.recovery_generation_witness w
                    WHERE w.request_id=v_request
                      AND w.invalidated_at IS NULL
                      AND (
                        (v_branch IN ('INITIAL','RECOVERY') AND
                         w.consumed_execution_id IS NULL)
                        OR
                        (v_branch='RETRY' AND
                         w.consumed_execution_id=v_internal)
                      )
                    ORDER BY w.id
                    LIMIT 1;
                    CONTINUE WHEN v_witness IS NULL;

                    -- Key lifecycle writers take their key lock before touching
                    -- requests or witnesses.  Matching that order prevents a
                    -- revoke/claim deadlock and closes the lifecycle race.
                    PERFORM locked_key.id
                    FROM public.r27_authorizer_key locked_key
                    JOIN (
                        SELECT DISTINCT authority.authorizer_key_id AS id
                        FROM public.r27_attestation authority
                        WHERE authority.request_id=v_request
                          AND authority.action IN ('REQUEST','APPROVE')
                    ) AS required_key ON required_key.id=locked_key.id
                    ORDER BY locked_key.id
                    FOR SHARE OF locked_key;
                    PERFORM recovery_key.id
                    FROM public.recovery_generation_verifier_key recovery_key
                    WHERE recovery_key.id=v_recovery_key
                    FOR SHARE OF recovery_key;
                    CONTINUE WHEN NOT FOUND;

                    PERFORM 1 FROM public.r27_request r
                    WHERE r.id=v_request FOR UPDATE OF r;
                    CONTINUE WHEN NOT FOUND;
                    IF v_internal IS NOT NULL THEN
                        PERFORM 1 FROM public.r27_execution existing
                        WHERE existing.id=v_internal FOR UPDATE OF existing;
                        CONTINUE WHEN NOT FOUND;
                    END IF;
                    PERFORM 1 FROM public.recovery_generation_witness w
                    WHERE w.id=v_witness FOR UPDATE OF w;
                    CONTINUE WHEN NOT FOUND;

                    PERFORM 1
                    FROM public.r27_request locked_request
                    JOIN public.r27_manifest locked_manifest
                      ON locked_manifest.request_id=locked_request.id
                    JOIN public.record locked_record
                      ON locked_record.id=locked_request.record_id
                    WHERE locked_request.id=v_request
                    FOR SHARE OF locked_manifest,locked_record;
                    CONTINUE WHEN NOT FOUND;
                    PERFORM 1
                    FROM public.r27_attestation locked_attestation
                    JOIN public.r27_action_challenge locked_challenge
                      ON locked_challenge.id=locked_attestation.challenge_id
                    JOIN public.app_user locked_user
                      ON locked_user.id=locked_attestation.app_user_id
                    WHERE locked_attestation.request_id=v_request
                      AND locked_attestation.action IN ('REQUEST','APPROVE')
                    ORDER BY locked_attestation.action
                    FOR SHARE OF locked_attestation,locked_challenge,locked_user;
                    CONTINUE WHEN NOT FOUND;
                    v_now:=clock_timestamp();

                    -- READ COMMITTED gives this statement a fresh snapshot after
                    -- all lifecycle and authority locks above have been acquired;
                    -- the one mutation timestamp is captured only after those locks.
                    PERFORM 1
                    FROM public.r27_request r
                    JOIN public.r27_manifest m ON m.request_id=r.id
                    JOIN public.recovery_generation_witness w
                      ON w.id=v_witness AND w.request_id=r.id
                    JOIN public.recovery_generation_verifier_key recovery_key
                      ON recovery_key.id=w.key_id
                    LEFT JOIN public.r27_execution existing
                      ON existing.request_id=r.id
                    WHERE r.id=v_request
                      AND {finalization_human_authority}
                      AND w.schema_version=1
                      AND w.invalidated_at IS NULL
                      AND w.result='VERIFIED'
                      AND w.manifest_sha256=m.sha256
                      AND w.excluded_set_sha256=m.excluded_set_sha256
                      AND w.issued_at>=r.approved_at
                      AND w.verified_at>=w.issued_at
                      AND w.verified_at<=v_now
                      AND recovery_key.revoked_at IS NULL
                      AND recovery_key.not_before<=w.issued_at
                      AND (recovery_key.retired_at IS NULL OR
                           w.issued_at<=recovery_key.retired_at)
                      AND (
                        (v_branch='INITIAL' AND
                         r.state='READY_FOR_FINALIZATION' AND
                         existing.id IS NULL AND
                         w.consumed_execution_id IS NULL)
                        OR
                        (v_branch='RECOVERY' AND
                         r.state='READY_FOR_FINALIZATION' AND
                         existing.id=v_internal AND
                         existing.state='FAILED' AND
                         existing.error_code='RECOVERY_KEY_REVOKED' AND
                         w.consumed_execution_id IS NULL)
                        OR
                        (v_branch='RETRY' AND
                         r.state='FINALIZING' AND
                         existing.id=v_internal AND
                         existing.state='FAILED' AND
                         existing.error_code IS DISTINCT FROM
                             'RECOVERY_KEY_REVOKED' AND
                         existing.next_attempt_at<=v_now AND
                         w.consumed_execution_id=existing.id)
                      );
                    CONTINUE WHEN NOT FOUND;

                    IF v_branch='INITIAL' THEN
                        v_internal:=gen_random_uuid();
                        v_public:=gen_random_uuid();
                        INSERT INTO public.r27_execution
                            (id,request_id,execution_id,state,claimed_at,
                             attempt_count,updated_at)
                        VALUES
                            (v_internal,v_request,v_public,'CLAIMED',v_now,1,v_now);
                    ELSE
                        UPDATE public.r27_execution existing
                        SET state=CASE
                              WHEN existing.purge_started_at IS NOT NULL
                                THEN 'PURGING'::r27_execution_state
                              WHEN existing.source_committed_at IS NOT NULL
                                THEN 'SOURCE_COMMITTED'::r27_execution_state
                              ELSE 'CLAIMED'::r27_execution_state
                            END,
                            attempt_count=existing.attempt_count+1,
                            error_code=NULL,error_detail=NULL,
                            next_attempt_at=NULL,updated_at=v_now
                        WHERE existing.id=v_internal
                        RETURNING existing.execution_id INTO v_public;
                    END IF;
                    IF v_branch IN ('INITIAL','RECOVERY') THEN
                        UPDATE public.recovery_generation_witness w
                        SET consumed_execution_id=v_internal
                        WHERE w.id=v_witness
                          AND w.invalidated_at IS NULL
                          AND w.consumed_execution_id IS NULL;
                        IF NOT FOUND THEN
                            RAISE EXCEPTION 'r27_finalization_claim_refused';
                        END IF;
                    END IF;
                    UPDATE public.r27_request r
                    SET state='FINALIZING',updated_at=v_now
                    WHERE r.id=v_request;
                    request_id:=v_request;
                    execution_id:=v_public;
                    v_claimed:=v_claimed+1;
                    RETURN NEXT;
                END LOOP;
            END""",
        "easysynq_fail_r27_execution(uuid,text,text,timestamptz)": """
            BEGIN
                IF p_id IS NULL OR p_code IS NULL OR p_at IS NULL
                   OR p_code='RECOVERY_KEY_REVOKED'
                   OR btrim(p_code)='' OR length(p_code)>64
                   OR length(COALESCE(p_detail,''))>512 THEN
                    RAISE EXCEPTION 'r27_execution_failure_refused';
                END IF;
                UPDATE public.r27_execution SET state='FAILED',error_code=p_code,error_detail=p_detail,
                    next_attempt_at=clock_timestamp()+interval '1 minute',updated_at=clock_timestamp()
                WHERE execution_id=p_id AND state IN ('CLAIMED','SOURCE_COMMITTED','PURGING');
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_execution_failure_refused'; END IF;
            END""",
        "easysynq_claim_r27_exact_purges(uuid,integer,timestamptz)": f"""
            DECLARE v_internal uuid; v_marker_ids uuid[];
            BEGIN
                {read_committed_guard}
                IF p_id IS NULL OR p_limit IS NULL OR p_at IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'r27_purge_claim_refused'; END IF;
                SELECT e.id INTO v_internal
                FROM public.r27_execution e
                JOIN public.r27_request request ON request.id=e.request_id
                WHERE e.execution_id=p_id AND e.state IN ('SOURCE_COMMITTED','PURGING')
                FOR UPDATE OF e,request;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_purge_claim_refused'; END IF;
                SELECT array_agg(locked.id ORDER BY locked.created_at,locked.id)
                INTO v_marker_ids
                FROM (
                    SELECT p.id,p.created_at
                    FROM public.pending_blob_purge p
                    JOIN public.r27_execution e ON e.id=p.r27_execution_id
                    JOIN public.r27_request request ON request.id=e.request_id
                    JOIN public.r27_manifest m ON m.request_id=e.request_id
                    JOIN public.r27_manifest_target target
                      ON target.manifest_id=m.id AND target.blob_sha256=p.sha256
                     AND target.bucket=p.bucket AND target.object_key=p.object_key
                     AND target.object_version_id=p.object_version_id
                    JOIN public.blob blob
                      ON blob.sha256=p.sha256 AND blob.org_id=p.org_id
                     AND blob.bucket=p.bucket AND blob.object_key=p.object_key
                     AND blob.object_version_id=p.object_version_id
                    JOIN public.disposition_event source_disposition
                      ON source_disposition.id=p.disposition_event_id
                    WHERE p.r27_execution_id=v_internal
                      AND e.execution_id=p_id
                      AND p.bypass_governance
                      AND p.r27_request_id=e.request_id
                      AND p.org_id=request.org_id AND p.record_id=request.record_id
                      AND p.state IN ('PENDING','FAILED')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM public.r27_execution_target_result existing_result
                          WHERE existing_result.execution_id=e.id
                            AND existing_result.manifest_target_id=target.id
                      )
                      AND {r27_source}
                      AND {r27_current_authority}
                      AND NOT {r27_live_owner}
                    ORDER BY p.created_at,p.id
                    FOR UPDATE OF p SKIP LOCKED
                    LIMIT p_limit
                ) AS locked;
                IF COALESCE(cardinality(v_marker_ids),0)=0 THEN RETURN; END IF;
                PERFORM 1
                FROM public.pending_blob_purge p
                JOIN public.blob blob
                  ON blob.sha256=p.sha256
                 AND blob.org_id=p.org_id
                 AND blob.bucket=p.bucket
                 AND blob.object_key=p.object_key
                 AND blob.object_version_id=p.object_version_id
                WHERE p.id=ANY(v_marker_ids)
                ORDER BY blob.sha256,p.id
                FOR UPDATE OF blob;
                RETURN QUERY WITH valid AS (
                    SELECT p.id FROM public.pending_blob_purge p
                    JOIN public.r27_execution e ON e.id=p.r27_execution_id
                    JOIN public.r27_request request ON request.id=e.request_id
                    JOIN public.r27_manifest m ON m.request_id=e.request_id
                    JOIN public.r27_manifest_target target
                      ON target.manifest_id=m.id AND target.blob_sha256=p.sha256
                     AND target.bucket=p.bucket AND target.object_key=p.object_key
                     AND target.object_version_id=p.object_version_id
                    JOIN public.blob blob
                      ON blob.sha256=p.sha256 AND blob.org_id=p.org_id
                     AND blob.bucket=p.bucket AND blob.object_key=p.object_key
                     AND blob.object_version_id=p.object_version_id
                    JOIN public.disposition_event source_disposition
                      ON source_disposition.id=p.disposition_event_id
                    WHERE p.id=ANY(v_marker_ids)
                      AND e.id=v_internal AND e.execution_id=p_id AND p.bypass_governance
                      AND p.r27_request_id=e.request_id
                      AND p.org_id=request.org_id AND p.record_id=request.record_id
                      AND p.state IN ('PENDING','FAILED')
                      AND NOT EXISTS (
                          SELECT 1
                          FROM public.r27_execution_target_result existing_result
                          WHERE existing_result.execution_id=e.id
                            AND existing_result.manifest_target_id=target.id
                      )
                      AND {r27_source}
                      AND {r27_current_authority}
                      AND NOT {r27_live_owner}
                ),
                claimed AS (UPDATE public.pending_blob_purge p SET state='RUNNING',attempt_count=p.attempt_count+1,claimed_at=clock_timestamp(),error_code=NULL,error_detail=NULL,updated_at=clock_timestamp() FROM valid WHERE p.id=valid.id RETURNING p.*),
                transitioned AS (
                    UPDATE public.r27_execution execution
                    SET state='PURGING',
                        purge_started_at=COALESCE(execution.purge_started_at,clock_timestamp()),
                        updated_at=clock_timestamp()
                    WHERE execution.execution_id=p_id AND EXISTS (SELECT 1 FROM claimed)
                    RETURNING execution.id)
                SELECT claimed.id,claimed.sha256::text,claimed.bucket,claimed.object_key,
                       claimed.object_version_id FROM claimed
                CROSS JOIN transitioned;
            END""",
        "easysynq_fail_r27_exact_purge(uuid,uuid,text,text,timestamptz)": """
            BEGIN
                IF p_execution IS NULL OR p_marker IS NULL OR p_code IS NULL OR p_at IS NULL OR btrim(p_code)='' OR length(p_code)>64 OR length(COALESCE(p_detail,''))>512 THEN RAISE EXCEPTION 'r27_purge_failure_refused'; END IF;
                UPDATE public.pending_blob_purge p SET state='FAILED',error_code=p_code,error_detail=p_detail,updated_at=clock_timestamp()
                FROM public.r27_execution e WHERE e.execution_id=p_execution AND p.id=p_marker AND p.r27_execution_id=e.id AND p.bypass_governance AND p.state='RUNNING';
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_purge_failure_refused'; END IF;
            END""",
        "easysynq_record_r27_hold_release(text,text,uuid,timestamptz)": f"""
            DECLARE v_internal uuid; v_marker uuid; v_target uuid; v_org uuid;
                    v_now timestamptz:=clock_timestamp();
            BEGIN
                {read_committed_guard}
                IF p_sha IS NULL OR p_version IS NULL OR p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT e.id,marker.id,target.id,request.org_id
                INTO v_internal,v_marker,v_target,v_org
                FROM public.r27_execution e
                JOIN public.r27_request request ON request.id=e.request_id
                JOIN public.r27_manifest m ON m.request_id=e.request_id
                JOIN public.r27_manifest_target target ON target.manifest_id=m.id
                JOIN public.pending_blob_purge marker
                  ON marker.r27_execution_id=e.id
                 AND marker.r27_request_id=e.request_id
                 AND marker.org_id=request.org_id
                 AND marker.record_id=request.record_id
                 AND marker.sha256=target.blob_sha256
                 AND marker.bucket=target.bucket
                 AND marker.object_key=target.object_key
                 AND marker.object_version_id=target.object_version_id
                JOIN public.disposition_event source_disposition
                  ON source_disposition.id=marker.disposition_event_id
                JOIN public.blob blob
                  ON blob.sha256=target.blob_sha256
                 AND blob.org_id=request.org_id
                 AND blob.bucket=target.bucket
                 AND blob.object_key=target.object_key
                 AND blob.object_version_id=target.object_version_id
                WHERE e.execution_id=p_id
                  AND target.blob_sha256=p_sha
                  AND target.object_version_id=p_version
                  AND marker.bypass_governance
                  AND marker.state='RUNNING'
                  AND e.state IN ('SOURCE_COMMITTED','PURGING')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.r27_execution_target_result existing_result
                      WHERE existing_result.execution_id=e.id
                        AND existing_result.manifest_target_id=target.id
                  )
                FOR UPDATE OF e,request,marker,blob;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_hold_release_refused'; END IF;
                UPDATE public.blob blob
                SET worm_legal_hold=false,worm_legal_hold_verified_at=v_now
                FROM public.r27_execution e
                JOIN public.r27_request request ON request.id=e.request_id
                JOIN public.r27_manifest m ON m.request_id=e.request_id
                JOIN public.r27_manifest_target target ON target.manifest_id=m.id
                JOIN public.pending_blob_purge marker
                  ON marker.r27_execution_id=e.id
                 AND marker.r27_request_id=e.request_id
                 AND marker.org_id=request.org_id
                 AND marker.record_id=request.record_id
                 AND marker.sha256=target.blob_sha256
                 AND marker.bucket=target.bucket
                 AND marker.object_key=target.object_key
                 AND marker.object_version_id=target.object_version_id
                JOIN public.disposition_event source_disposition
                  ON source_disposition.id=marker.disposition_event_id
                WHERE e.id=v_internal AND marker.id=v_marker AND target.id=v_target
                  AND e.execution_id=p_id
                  AND target.blob_sha256=p_sha
                  AND target.object_version_id=p_version
                  AND marker.state='RUNNING' AND marker.bypass_governance
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.r27_execution_target_result existing_result
                      WHERE existing_result.execution_id=e.id
                        AND existing_result.manifest_target_id=target.id
                  )
                  AND blob.sha256=target.blob_sha256
                  AND blob.org_id=request.org_id
                  AND blob.bucket=target.bucket
                  AND blob.object_key=target.object_key
                  AND blob.object_version_id=target.object_version_id
                  AND blob.worm_legal_hold
                  AND {r27_source}
                  AND {r27_current_authority}
                  AND NOT {r27_hold_obligation};
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_hold_release_refused'; END IF;
            END""",
        "easysynq_record_r27_purge(text,text,uuid,timestamptz)": f"""
            DECLARE
                v_internal uuid;
                v_target uuid;
                v_marker uuid;
                v_org uuid;
                v_bucket text;
                v_object_key text;
                v_now timestamptz:=clock_timestamp();
            BEGIN
                {read_committed_guard}
                IF p_sha IS NULL OR p_version IS NULL OR p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT e.id,target.id,p.id,request.org_id,target.bucket,target.object_key
                INTO v_internal,v_target,v_marker,v_org,v_bucket,v_object_key
                FROM public.r27_execution e
                JOIN public.r27_request request ON request.id=e.request_id
                JOIN public.r27_manifest m ON m.request_id=e.request_id
                JOIN public.r27_manifest_target target ON target.manifest_id=m.id
                JOIN public.pending_blob_purge p ON p.r27_execution_id=e.id
                  AND p.r27_request_id=e.request_id
                  AND p.org_id=request.org_id
                  AND p.record_id=request.record_id
                  AND p.sha256=target.blob_sha256
                  AND p.bucket=target.bucket
                  AND p.object_key=target.object_key
                  AND p.object_version_id=target.object_version_id
                JOIN public.disposition_event source_disposition
                  ON source_disposition.id=p.disposition_event_id
                JOIN public.blob b
                  ON b.sha256=target.blob_sha256
                 AND b.org_id=request.org_id
                 AND b.bucket=target.bucket
                 AND b.object_key=target.object_key
                 AND b.object_version_id=target.object_version_id
                WHERE e.execution_id=p_id
                  AND target.blob_sha256=p_sha
                  AND target.object_version_id=p_version
                  AND p.state='RUNNING'
                  AND p.bypass_governance
                  AND e.state IN ('SOURCE_COMMITTED','PURGING')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.r27_execution_target_result existing_result
                      WHERE existing_result.execution_id=e.id
                        AND existing_result.manifest_target_id=target.id
                  )
                FOR UPDATE OF e,request,p,b,source_disposition;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_purge_result_refused'; END IF;
                UPDATE public.blob b
                SET purged_at=v_now,purge_execution_id=v_internal
                FROM public.r27_execution e
                JOIN public.r27_request request ON request.id=e.request_id
                JOIN public.r27_manifest m ON m.request_id=e.request_id
                JOIN public.r27_manifest_target target ON target.manifest_id=m.id
                JOIN public.pending_blob_purge p
                  ON p.r27_execution_id=e.id
                 AND p.r27_request_id=e.request_id
                 AND p.org_id=request.org_id
                 AND p.record_id=request.record_id
                 AND p.sha256=target.blob_sha256
                 AND p.bucket=target.bucket
                 AND p.object_key=target.object_key
                 AND p.object_version_id=target.object_version_id
                JOIN public.disposition_event source_disposition
                  ON source_disposition.id=p.disposition_event_id
                WHERE e.id=v_internal AND target.id=v_target AND p.id=v_marker
                  AND e.execution_id=p_id
                  AND target.blob_sha256=p_sha
                  AND target.object_version_id=p_version
                  AND p.state='RUNNING' AND p.bypass_governance
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.r27_execution_target_result existing_result
                      WHERE existing_result.execution_id=e.id
                        AND existing_result.manifest_target_id=target.id
                  )
                  AND b.sha256=target.blob_sha256
                  AND b.org_id=request.org_id
                  AND b.bucket=target.bucket
                  AND b.object_key=target.object_key
                  AND b.object_version_id=target.object_version_id
                  AND NOT b.worm_legal_hold
                  AND {r27_source}
                  AND {r27_current_authority}
                  AND NOT {r27_live_owner};
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_purge_result_refused'; END IF;
                UPDATE public.pending_blob_purge SET state='VERIFIED',completed_at=v_now,updated_at=v_now WHERE id=v_marker AND state='RUNNING';
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_purge_result_refused'; END IF;
                INSERT INTO public.r27_execution_target_result(execution_id,manifest_target_id,result_code,verified_at,purge_marker_id)
                VALUES(v_internal,v_target,'PHYSICAL_ERASED',v_now,v_marker);
                IF NOT EXISTS (SELECT 1 FROM public.r27_manifest_target t JOIN public.r27_manifest m ON m.id=t.manifest_id
                               JOIN public.r27_execution e ON e.request_id=m.request_id WHERE e.id=v_internal
                               AND NOT EXISTS (SELECT 1 FROM public.r27_execution_target_result rr WHERE rr.execution_id=e.id AND rr.manifest_target_id=t.id)) THEN
                    UPDATE public.r27_execution e SET state='EXECUTED',result_code=(SELECT CASE WHEN count(*) FILTER (WHERE result_code='PHYSICAL_ERASED')=count(*) THEN 'PHYSICAL_ERASED'::r27_result_code WHEN count(*) FILTER (WHERE result_code='LOGICAL_ONLY_SURVIVING_OWNER')=count(*) THEN 'LOGICAL_ONLY_SURVIVING_OWNER'::r27_result_code ELSE 'MIXED_OUTCOME'::r27_result_code END FROM public.r27_execution_target_result WHERE execution_id=v_internal),completed_at=v_now,updated_at=v_now WHERE e.id=v_internal;
                    UPDATE public.r27_request SET state='EXECUTED',updated_at=v_now WHERE id=(SELECT request_id FROM public.r27_execution WHERE id=v_internal);
                    INSERT INTO public.audit_event(org_id,occurred_at,actor_type,event_type,object_type,object_id,scope_ref,reason,after)
                    SELECT r.org_id,v_now,'system','RECORD_WORM_DESTROYED','record',r.record_id,'record','r27-exact-purge-complete',jsonb_build_object('execution_id',e.execution_id) FROM public.r27_execution e JOIN public.r27_request r ON r.id=e.request_id WHERE e.id=v_internal;
                END IF;
            END""",
        "easysynq_record_r27_surviving_owner(text,text,uuid,timestamptz)": f"""
            DECLARE v_internal uuid; v_target uuid; v_owner uuid; v_kind text; v_org uuid;
                    v_now timestamptz:=clock_timestamp();
            BEGIN
                {read_committed_guard}
                IF p_sha IS NULL OR p_version IS NULL OR p_id IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT e.id,target.id,request.org_id INTO v_internal,v_target,v_org
                FROM public.r27_execution e
                JOIN public.r27_request request ON request.id=e.request_id
                JOIN public.r27_manifest m ON m.request_id=e.request_id
                JOIN public.r27_manifest_target target ON target.manifest_id=m.id
                JOIN public.blob b
                  ON b.sha256=target.blob_sha256
                 AND b.org_id=request.org_id
                 AND b.bucket=target.bucket
                 AND b.object_key=target.object_key
                 AND b.object_version_id=target.object_version_id
                JOIN public.disposition_event source_disposition
                  ON source_disposition.r27_execution_id=e.id
                 AND source_disposition.r27_request_id=e.request_id
                WHERE e.execution_id=p_id
                  AND target.blob_sha256=p_sha
                  AND target.object_version_id=p_version
                  AND e.state IN ('SOURCE_COMMITTED','PURGING')
                FOR UPDATE OF e,request,b,source_disposition;
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_surviving_owner_refused'; END IF;
                PERFORM 1
                FROM public.r27_execution e
                JOIN public.r27_request request ON request.id=e.request_id
                JOIN public.r27_manifest m ON m.request_id=e.request_id
                JOIN public.r27_manifest_target target ON target.manifest_id=m.id
                JOIN public.disposition_event source_disposition
                  ON source_disposition.r27_execution_id=e.id
                 AND source_disposition.r27_request_id=e.request_id
                WHERE e.id=v_internal AND target.id=v_target
                  AND e.execution_id=p_id
                  AND target.blob_sha256=p_sha
                  AND target.object_version_id=p_version
                  AND {r27_source}
                  AND {r27_current_authority};
                IF NOT FOUND THEN RAISE EXCEPTION 'r27_surviving_owner_refused'; END IF;
                SELECT v.id,'DOCUMENT_VERSION' INTO v_owner,v_kind
                FROM public.document_version v
                JOIN public.documented_information owner_document
                  ON owner_document.id=v.document_id
                 AND owner_document.org_id=v.org_id
                WHERE v.source_blob_sha256=p_sha AND v.org_id=v_org
                ORDER BY v.id FOR SHARE OF v LIMIT 1;
                IF NOT FOUND THEN SELECT eb.id,'EVIDENCE_BLOB' INTO v_owner,v_kind
                    FROM public.evidence_blob eb
                    JOIN public.record owner_record
                      ON owner_record.id=eb.record_id
                     AND owner_record.org_id=eb.org_id
                    JOIN public.r27_request target_request
                      ON target_request.id=(
                          SELECT execution.request_id
                          FROM public.r27_execution execution
                          WHERE execution.id=v_internal
                      )
                    WHERE eb.blob_sha256=p_sha
                      AND eb.org_id=v_org
                      AND eb.record_id<>target_request.record_id
                      AND NOT {_valid_destructive_event_exists_sql(record_sql="owner_record")}
                    ORDER BY eb.id FOR SHARE OF eb,owner_record LIMIT 1;
                END IF;
                IF NOT FOUND THEN SELECT pack.id,'SEALED_PACK' INTO v_owner,v_kind
                    FROM public.evidence_pack pack
                    WHERE pack.org_id=v_org
                      AND pack.zip_blob_sha256=p_sha
                      AND pack.status='SEALED'
                      AND pack.invalidated_at IS NULL
                    ORDER BY pack.id FOR SHARE OF pack LIMIT 1;
                END IF;
                IF v_owner IS NULL THEN RAISE EXCEPTION 'r27_surviving_owner_refused'; END IF;
                INSERT INTO public.r27_execution_target_result(execution_id,manifest_target_id,result_code,verified_at,surviving_owner_kind,surviving_owner_id)
                VALUES(v_internal,v_target,'LOGICAL_ONLY_SURVIVING_OWNER',v_now,v_kind,v_owner);
                IF NOT EXISTS (SELECT 1 FROM public.r27_manifest_target t JOIN public.r27_manifest m ON m.id=t.manifest_id
                               JOIN public.r27_execution e ON e.request_id=m.request_id WHERE e.id=v_internal
                               AND NOT EXISTS (SELECT 1 FROM public.r27_execution_target_result rr WHERE rr.execution_id=e.id AND rr.manifest_target_id=t.id)) THEN
                    UPDATE public.r27_execution e SET state='EXECUTED',result_code=(SELECT CASE WHEN count(*) FILTER (WHERE result_code='PHYSICAL_ERASED')=count(*) THEN 'PHYSICAL_ERASED'::r27_result_code WHEN count(*) FILTER (WHERE result_code='LOGICAL_ONLY_SURVIVING_OWNER')=count(*) THEN 'LOGICAL_ONLY_SURVIVING_OWNER'::r27_result_code ELSE 'MIXED_OUTCOME'::r27_result_code END FROM public.r27_execution_target_result WHERE execution_id=v_internal),completed_at=v_now,updated_at=v_now WHERE e.id=v_internal;
                    UPDATE public.r27_request SET state='EXECUTED',updated_at=v_now WHERE id=(SELECT request_id FROM public.r27_execution WHERE id=v_internal);
                    INSERT INTO public.audit_event(org_id,occurred_at,actor_type,event_type,object_type,object_id,scope_ref,reason,after)
                    SELECT r.org_id,v_now,'system','RECORD_WORM_DESTROYED','record',r.record_id,'record','r27-exact-purge-complete',jsonb_build_object('execution_id',e.execution_id) FROM public.r27_execution e JOIN public.r27_request r ON r.id=e.request_id WHERE e.id=v_internal;
                END IF;
            END""",
        "easysynq_begin_r27_role_membership(uuid,uuid,text,text,timestamptz)": """
            DECLARE v_org uuid;
            BEGIN
                IF p_operation IS NULL OR p_user_id IS NULL OR p_action IS NULL OR p_identity IS NULL OR p_at IS NULL OR p_action NOT IN ('ASSIGN','REVOKE') OR btrim(p_identity)='' THEN RAISE EXCEPTION 'role_membership_begin_refused'; END IF;
                SELECT org_id INTO v_org FROM public.app_user WHERE id=p_user_id;
                IF NOT FOUND THEN RAISE EXCEPTION 'role_membership_begin_refused'; END IF;
                INSERT INTO public.r27_role_membership_operation(id,user_id,org_id,action,operator_identity,state,requested_at)
                VALUES(p_operation,p_user_id,v_org,p_action,p_identity,'REQUESTED',clock_timestamp())
                ON CONFLICT(id) DO NOTHING;
                IF NOT EXISTS (SELECT 1 FROM public.r27_role_membership_operation WHERE id=p_operation AND user_id=p_user_id AND action=p_action AND operator_identity=p_identity) THEN RAISE EXCEPTION 'role_membership_idempotency_conflict'; END IF;
            END""",
        "easysynq_complete_r27_role_membership(uuid,timestamptz)": """
            DECLARE v_op public.r27_role_membership_operation%ROWTYPE; v_audit bigint;
            BEGIN
                IF p_operation IS NULL OR p_at IS NULL THEN RAISE EXCEPTION 'required_argument_is_null'; END IF;
                SELECT * INTO v_op FROM public.r27_role_membership_operation WHERE id=p_operation FOR UPDATE;
                IF NOT FOUND THEN RAISE EXCEPTION 'role_membership_complete_refused'; END IF;
                IF v_op.state='AUDITED' THEN RETURN v_op.audit_event_id; END IF;
                IF v_op.state NOT IN ('REQUESTED','FAILED') THEN RAISE EXCEPTION 'role_membership_complete_refused'; END IF;
                INSERT INTO public.audit_event(org_id,occurred_at,actor_type,event_type,object_type,object_id,scope_ref,reason,after)
                VALUES(v_op.org_id,clock_timestamp(),'system',CASE v_op.action WHEN 'ASSIGN' THEN 'ROLE_ASSIGN'::event_type ELSE 'ROLE_REVOKE'::event_type END,'user',v_op.user_id,'r27-approver',CASE v_op.action WHEN 'ASSIGN' THEN 'host-r27-role-assignment' ELSE 'host-r27-role-revocation' END,jsonb_build_object('operator_identity',v_op.operator_identity,'action',v_op.action,'operation_id',v_op.id)) RETURNING id INTO v_audit;
                UPDATE public.r27_role_membership_operation SET state='AUDITED',audit_event_id=v_audit,completed_at=clock_timestamp(),error_code=NULL,error_detail=NULL WHERE id=p_operation;
                RETURN v_audit;
            END""",
        "easysynq_fail_r27_role_membership(uuid,text,text,timestamptz)": """
            BEGIN
                IF p_operation IS NULL OR p_code IS NULL OR p_at IS NULL OR btrim(p_code)='' OR length(p_code)>64 OR length(COALESCE(p_detail,''))>512 THEN RAISE EXCEPTION 'role_membership_failure_refused'; END IF;
                UPDATE public.r27_role_membership_operation SET state='FAILED',error_code=p_code,
                    error_detail=p_detail,completed_at=clock_timestamp()
                WHERE id=p_operation AND state IN ('REQUESTED','FAILED');
                IF NOT FOUND THEN RAISE EXCEPTION 'role_membership_failure_refused'; END IF;
            END""",
    }
    for declaration, identity, role, return_kind in functions:
        del return_kind
        body = bodies[identity]
        _create_definer_function(declaration, identity, role, body)


def _grant_database_authority() -> None:
    bind = op.get_bind()
    op.execute(f"REVOKE ALL ON public.r27_request FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE ON public.record,public.retention_policy FROM {APP_ROLE}")
    op.execute(
        f"GRANT UPDATE ({','.join(_APP_RECORD_UPDATE_COLUMNS)}) ON public.record TO {APP_ROLE}"
    )
    op.execute(
        f"GRANT UPDATE ({','.join(_APP_RETENTION_POLICY_UPDATE_COLUMNS)}) "
        f"ON public.retention_policy TO {APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, DELETE ON public.blob TO {APP_ROLE}")
    op.execute(f"GRANT INSERT ({','.join(_APP_BLOB_INSERT_COLUMNS)}) ON public.blob TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE (verified_at,verify_failed_at) ON public.blob TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.document_version TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON public.evidence_blob TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON public.pending_blob_purge TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON public.audit_event TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON public.signature_event TO {APP_ROLE}")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO easysynq_backup")
    op.execute("GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO easysynq_backup")
    op.execute(
        "GRANT SELECT ON public.retention_operation,public.retention_operation_target,public.blob TO easysynq_retention"
    )
    op.execute(
        "GRANT SELECT ON public.worm_hold_release_operation,public.blob,public.record TO easysynq_hold_authorizer,easysynq_hold_maintenance"
    )
    op.execute(
        "GRANT SELECT ON public.r27_request,public.r27_manifest,public.r27_manifest_target,public.r27_attestation,public.r27_action_challenge,public.r27_authorizer_key TO easysynq_r27_authorizer"
    )
    op.execute(
        "GRANT INSERT ON public.r27_manifest,public.r27_manifest_target,"
        "public.r27_manifest_derivative,public.r27_attestation TO easysynq_r27_authorizer"
    )
    op.execute(
        "GRANT INSERT (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256) "
        "ON public.r27_request TO easysynq_r27_authorizer"
    )
    op.execute(
        "GRANT INSERT (id,action,request_id,record_id,issuer,token_jti,action_nonce,"
        "accepted_claims,manifest_sha256,expires_at) ON public.r27_action_challenge "
        "TO easysynq_r27_authorizer"
    )
    op.execute(
        "GRANT SELECT ON public.r27_request,public.r27_manifest,public.r27_manifest_target,public.r27_attestation,public.r27_execution,public.r27_execution_target_result,public.recovery_generation_witness,public.pending_blob_purge,public.blob TO easysynq_r27_maintenance"
    )
    op.execute("GRANT SELECT ON public.r27_authorizer_key TO easysynq_r27_authorizer_key_manager")
    op.execute(
        "GRANT SELECT ON public.recovery_generation_verifier_key TO easysynq_recovery_key_manager"
    )
    op.execute(
        "GRANT SELECT ON public.app_user,public.r27_role_membership_operation TO easysynq_r27_role_manager"
    )
    op.execute(
        "GRANT SELECT ON public.audit_event,public.organization,public.system_config,public.audit_chain_cursor,public.audit_checkpoint,public.audit_checkpoint_sink,public.audit_maintenance_schedule TO easysynq_audit_signer"
    )
    op.execute(
        "GRANT INSERT ON public.audit_event,public.audit_checkpoint TO easysynq_audit_signer"
    )
    op.execute("GRANT USAGE,SELECT ON SEQUENCE public.audit_event_id_seq TO easysynq_audit_signer")
    op.execute(
        "GRANT UPDATE (prev_hash,row_hash,chained_at) ON public.audit_event TO easysynq_audit_signer"
    )
    op.execute("GRANT SELECT,INSERT,UPDATE ON public.audit_chain_cursor TO easysynq_audit_signer")
    audit_children = bind.execute(
        sa.text(
            """
            SELECT child.relname
            FROM pg_inherits inheritance
            JOIN pg_class child ON child.oid=inheritance.inhrelid
            JOIN pg_namespace namespace ON namespace.oid=child.relnamespace
            JOIN pg_class parent ON parent.oid=inheritance.inhparent
            JOIN pg_namespace parent_namespace ON parent_namespace.oid=parent.relnamespace
            WHERE namespace.nspname='public' AND parent_namespace.nspname='public'
              AND parent.relname='audit_event'
            """
        )
    ).scalars()
    for child in audit_children:
        _execute_composed(
            bind,
            psycopg_sql.SQL("REVOKE ALL ON public.{} FROM PUBLIC,{}").format(
                psycopg_sql.Identifier(child), psycopg_sql.Identifier(LINKER_ROLE)
            ),
        )
        _execute_composed(
            bind,
            psycopg_sql.SQL("GRANT SELECT,INSERT ON public.{} TO {},{}").format(
                psycopg_sql.Identifier(child),
                psycopg_sql.Identifier(APP_ROLE),
                psycopg_sql.Identifier("easysynq_audit_signer"),
            ),
        )
        _execute_composed(
            bind,
            psycopg_sql.SQL(
                "GRANT UPDATE (prev_hash,row_hash,chained_at) ON public.{} TO {}"
            ).format(
                psycopg_sql.Identifier(child),
                psycopg_sql.Identifier("easysynq_audit_signer"),
            ),
        )
        _execute_composed(
            bind,
            psycopg_sql.SQL("GRANT SELECT ON public.{} TO {}").format(
                psycopg_sql.Identifier(child), psycopg_sql.Identifier("easysynq_backup")
            ),
        )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION public.easysynq_create_audit_partition(date) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.easysynq_create_audit_partition(date) TO easysynq_audit_signer"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.easysynq_create_audit_partition(p_start date)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = public, pg_temp AS $function$
        DECLARE
            v_end date := (p_start + INTERVAL '1 month')::date;
            v_name text := 'audit_event_' || to_char(p_start,'YYYY_MM');
            v_from text := to_char(p_start,'YYYY-MM-DD') || ' 00:00:00+00';
            v_to text := to_char(v_end,'YYYY-MM-DD') || ' 00:00:00+00';
        BEGIN
            IF p_start IS NULL OR p_start<>date_trunc('month',p_start)::date THEN
                RAISE EXCEPTION 'audit_partition_start_refused';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                           WHERE n.nspname='public' AND c.relname=v_name AND c.relkind='r') THEN
                EXECUTE format('CREATE TABLE public.%I PARTITION OF public.audit_event FOR VALUES FROM (%L) TO (%L)',v_name,v_from,v_to);
                EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,easysynq_linker',v_name);
                EXECUTE format('GRANT SELECT,INSERT ON public.%I TO easysynq_app,easysynq_audit_signer',v_name);
                EXECUTE format('GRANT UPDATE (prev_hash,row_hash,chained_at) ON public.%I TO easysynq_audit_signer',v_name);
                EXECUTE format('GRANT SELECT ON public.%I TO easysynq_backup',v_name);
            END IF;
        END
        $function$
        """
    )


def _create_database_authority() -> None:
    _create_worm_guard_triggers()
    _create_task4_worm_functions()
    op.execute(
        """
        CREATE FUNCTION public.easysynq_guard_r27_result_history() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp AS $function$
        BEGIN RAISE EXCEPTION 'r27_result_history_is_immutable'; END $function$;
        REVOKE ALL ON FUNCTION public.easysynq_guard_r27_result_history() FROM PUBLIC;
        CREATE TRIGGER trg_r27_execution_target_result_history BEFORE UPDATE OR DELETE
        ON public.r27_execution_target_result FOR EACH ROW
        EXECUTE FUNCTION public.easysynq_guard_r27_result_history();

        CREATE FUNCTION public.easysynq_guard_recovery_witness_history() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp AS $function$
        BEGIN
            IF TG_OP='DELETE' OR NEW.id IS DISTINCT FROM OLD.id OR NEW.key_id IS DISTINCT FROM OLD.key_id
               OR NEW.witness_nonce IS DISTINCT FROM OLD.witness_nonce OR NEW.request_id IS DISTINCT FROM OLD.request_id
               OR NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256 OR NEW.generation_id IS DISTINCT FROM OLD.generation_id
               OR NEW.generation_identity IS DISTINCT FROM OLD.generation_identity OR NEW.excluded_set_sha256 IS DISTINCT FROM OLD.excluded_set_sha256
               OR NEW.result IS DISTINCT FROM OLD.result OR NEW.canonical_bytes IS DISTINCT FROM OLD.canonical_bytes
               OR NEW.signature IS DISTINCT FROM OLD.signature OR NEW.issued_at IS DISTINCT FROM OLD.issued_at
               OR NEW.verified_at IS DISTINCT FROM OLD.verified_at
               OR (OLD.consumed_execution_id IS NOT NULL AND NEW.consumed_execution_id IS DISTINCT FROM OLD.consumed_execution_id)
               OR (OLD.invalidated_at IS NOT NULL AND (NEW.invalidated_at IS DISTINCT FROM OLD.invalidated_at
                   OR NEW.invalidation_audit_event_id IS DISTINCT FROM OLD.invalidation_audit_event_id
                   OR NEW.invalidation_reason IS DISTINCT FROM OLD.invalidation_reason)) THEN
                RAISE EXCEPTION 'recovery_witness_history_is_immutable';
            END IF;
            RETURN NEW;
        END $function$;
        REVOKE ALL ON FUNCTION public.easysynq_guard_recovery_witness_history() FROM PUBLIC;
        CREATE TRIGGER trg_recovery_witness_history BEFORE UPDATE OR DELETE
        ON public.recovery_generation_witness FOR EACH ROW
        EXECUTE FUNCTION public.easysynq_guard_recovery_witness_history();

        CREATE FUNCTION public.easysynq_guard_role_membership_history() RETURNS trigger
        LANGUAGE plpgsql SET search_path = public, pg_temp AS $function$
        BEGIN
            IF TG_OP='DELETE' OR NEW.id IS DISTINCT FROM OLD.id OR NEW.user_id IS DISTINCT FROM OLD.user_id
               OR NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.action IS DISTINCT FROM OLD.action
               OR NEW.operator_identity IS DISTINCT FROM OLD.operator_identity OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
               OR OLD.state='AUDITED' OR (OLD.audit_event_id IS NOT NULL AND NEW.audit_event_id IS DISTINCT FROM OLD.audit_event_id) THEN
                RAISE EXCEPTION 'role_membership_history_is_immutable';
            END IF;
            RETURN NEW;
        END $function$;
        REVOKE ALL ON FUNCTION public.easysynq_guard_role_membership_history() FROM PUBLIC;
        CREATE TRIGGER trg_role_membership_history BEFORE UPDATE OR DELETE
        ON public.r27_role_membership_operation FOR EACH ROW
        EXECUTE FUNCTION public.easysynq_guard_role_membership_history();
        """
    )
    _create_retention_functions()
    _create_key_functions()
    _create_authority_transition_functions()
    _grant_database_authority()


def _refuse_populated_0089_downgrade(bind: sa.Connection) -> None:
    populated = bind.execute(
        sa.text(
            """
            SELECT
                EXISTS (
                    SELECT 1 FROM blob
                    WHERE worm_locked
                       OR object_version_id IS NOT NULL
                       OR worm_enforced_mode IS NOT NULL
                       OR worm_asserted_retain_until IS NOT NULL
                       OR worm_asserted_at IS NOT NULL
                       OR worm_retention_verified_at IS NOT NULL
                       OR worm_legal_hold IS NOT NULL
                       OR worm_legal_hold_verified_at IS NOT NULL
                       OR purged_at IS NOT NULL
                       OR purge_execution_id IS NOT NULL
                    LIMIT 1
                )
                OR EXISTS (SELECT 1 FROM document_worm_config LIMIT 1)
                OR EXISTS (SELECT 1 FROM document_version LIMIT 1)
                OR EXISTS (
                    SELECT 1 FROM record
                    WHERE retention_basis_provisional
                    LIMIT 1
                )
                OR EXISTS (
                    SELECT 1 FROM retention_policy
                    WHERE active_revision_no<>1
                    LIMIT 1
                )
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
                OR EXISTS (SELECT 1 FROM r27_execution_target_result LIMIT 1)
                OR EXISTS (SELECT 1 FROM r27_role_membership_operation LIMIT 1)
                OR EXISTS (SELECT 1 FROM pending_blob_purge LIMIT 1)
                OR EXISTS (SELECT 1 FROM audit_maintenance_schedule LIMIT 1)
                OR EXISTS (SELECT 1 FROM backup_maintenance_operation LIMIT 1)
                OR EXISTS (
                    SELECT 1 FROM audit_event
                    WHERE event_type='RECORD_LEGAL_HOLD_RELEASE_AUTHORIZED'
                    LIMIT 1
                )
            """
        )
    ).scalar_one()
    if populated:
        raise RuntimeError("populated_0089_downgrade_refused")


def _drop_database_authority() -> None:
    for table, trigger in (
        ("disposition_event", "trg_app_disposition_insert_guard"),
        ("blob", "trg_blob_worm_identity"),
        ("document_version", "trg_document_version_worm_owner"),
        ("record", "trg_record_retention_policy_pin"),
        ("retention_policy", "trg_retention_policy_worm_owner"),
        ("evidence_blob", "trg_evidence_blob_worm_owner"),
        ("r27_authorizer_key", "trg_r27_authorizer_key_history"),
        ("recovery_generation_verifier_key", "trg_recovery_verifier_key_history"),
        ("r27_execution_target_result", "trg_r27_execution_target_result_history"),
        ("recovery_generation_witness", "trg_recovery_witness_history"),
        ("r27_role_membership_operation", "trg_role_membership_history"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON public.{table}")
    for name in _AUTHORITY_FUNCTION_NAMES:
        op.execute(f"DROP FUNCTION IF EXISTS public.{name}")
    for name in (
        "easysynq_guard_blob_worm_identity",
        "easysynq_guard_app_disposition_insert",
        "easysynq_guard_worm_owner_pointer",
        "easysynq_guard_key_registry_history",
        "easysynq_guard_r27_result_history",
        "easysynq_guard_recovery_witness_history",
        "easysynq_guard_role_membership_history",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{name}()")


def _restore_0088_database_authority(bind: sa.Connection) -> None:
    settings = get_settings()
    for role in _NEW_AUTHORITY_ROLES:
        if bind.execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"),
            {"role": role},
        ).scalar_one():
            _execute_composed(
                bind,
                psycopg_sql.SQL("DROP OWNED BY {}").format(psycopg_sql.Identifier(role)),
            )
            _execute_composed(
                bind,
                psycopg_sql.SQL("DROP ROLE {}").format(psycopg_sql.Identifier(role)),
            )
    for role, password in (
        (APP_ROLE, settings.app_db_password),
        (LINKER_ROLE, settings.linker_db_password),
    ):
        _execute_composed(
            bind,
            psycopg_sql.SQL("ALTER ROLE {} WITH LOGIN INHERIT PASSWORD {}").format(
                psycopg_sql.Identifier(role), psycopg_sql.Literal(password)
            ),
        )
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    op.execute(
        f"GRANT SELECT,INSERT,UPDATE,DELETE ON blob,document_version,evidence_blob TO {APP_ROLE}"
    )
    op.execute(f"GRANT UPDATE ON record,retention_policy TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE (verified_at,verify_failed_at) ON blob FROM {APP_ROLE}")
    op.execute(f"GRANT SELECT,DELETE ON pending_blob_purge TO {APP_ROLE}")
    op.execute(
        f"GRANT INSERT (id,org_id,sha256,bucket,object_key,bypass_governance,record_id,"
        f"disposition_event_id,worm_destroy_request_id) ON pending_blob_purge TO {APP_ROLE}"
    )
    op.execute(f"GRANT UPDATE (id) ON pending_blob_purge TO {APP_ROLE}")
    op.execute(f"GRANT SELECT,INSERT ON audit_event,signature_event TO {APP_ROLE}")
    op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON worm_destroy_request TO {APP_ROLE}")
    op.execute(f"GRANT SELECT,INSERT ON audit_checkpoint TO {APP_ROLE}")
    op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON audit_checkpoint_sink TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON audit_event,organization,system_config TO {LINKER_ROLE}")
    op.execute(f"GRANT UPDATE (prev_hash,row_hash,chained_at) ON audit_event TO {LINKER_ROLE}")
    op.execute(f"GRANT SELECT,INSERT,UPDATE ON audit_chain_cursor TO {LINKER_ROLE}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION easysynq_create_audit_partition(date) TO PUBLIC,{APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO {APP_ROLE}"
    )
    op.execute("ALTER DEFAULT PRIVILEGES GRANT EXECUTE ON FUNCTIONS TO PUBLIC")


def _restore_0088_audit_partition_factory(bind: sa.Connection) -> None:
    op.execute(
        f"""
CREATE OR REPLACE FUNCTION easysynq_create_audit_partition(p_start date)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
    v_end  date := (p_start + INTERVAL '1 month')::date;
    v_name text := 'audit_event_' || to_char(p_start, 'YYYY_MM');
    v_from text := to_char(p_start, 'YYYY-MM-DD') || ' 00:00:00+00';
    v_to   text := to_char(v_end,  'YYYY-MM-DD') || ' 00:00:00+00';
BEGIN
    IF NOT EXISTS (SELECT FROM pg_class WHERE relname = v_name AND relkind = 'r') THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF audit_event FOR VALUES FROM (%L) TO (%L)',
            v_name, v_from, v_to
        );
        -- Mirror the parent's least-privilege grants on the child (belt-and-suspenders: parent-
        -- routed DML checks the parent, but never grant the app UPDATE/DELETE on a child either).
        EXECUTE format('REVOKE ALL ON %I FROM {APP_ROLE}', v_name);
        EXECUTE format('GRANT SELECT, INSERT ON %I TO {APP_ROLE}', v_name);
        EXECUTE format('GRANT SELECT ON %I TO {LINKER_ROLE}', v_name);
        EXECUTE format(
            'GRANT UPDATE (prev_hash, row_hash, chained_at) ON %I TO {LINKER_ROLE}', v_name
        );
    END IF;
END
$fn$;
        """
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.easysynq_create_audit_partition(date) "
        f"TO PUBLIC,{APP_ROLE}"
    )
    child_names = bind.execute(
        sa.text(
            """
            SELECT child.relname
            FROM pg_inherits inheritance
            JOIN pg_class parent ON parent.oid=inheritance.inhparent
            JOIN pg_namespace parent_schema ON parent_schema.oid=parent.relnamespace
            JOIN pg_class child ON child.oid=inheritance.inhrelid
            JOIN pg_namespace child_schema ON child_schema.oid=child.relnamespace
            WHERE parent_schema.nspname='public' AND parent.relname='audit_event'
              AND child_schema.nspname='public'
            """
        )
    ).scalars()
    for child_name in child_names:
        child = psycopg_sql.Identifier("public", child_name)
        _execute_composed(
            bind,
            psycopg_sql.SQL("GRANT SELECT ON {} TO {}").format(
                child, psycopg_sql.Identifier(LINKER_ROLE)
            ),
        )
        _execute_composed(
            bind,
            psycopg_sql.SQL("GRANT UPDATE ({}) ON {} TO {}").format(
                psycopg_sql.SQL(",").join(
                    map(psycopg_sql.Identifier, ("prev_hash", "row_hash", "chained_at"))
                ),
                child,
                psycopg_sql.Identifier(LINKER_ROLE),
            ),
        )


def _restore_alembic_version_width_after_stamp() -> None:
    """Restore 0088's typmod after Alembic replaces the long 0089 revision value."""
    context = op.get_context()
    existing_callbacks = context.on_version_apply_callbacks

    def restore_width(*, ctx: object, step: object, heads: set[str], run_args: object) -> None:
        del step, run_args
        if heads != {down_revision}:
            return
        connection = ctx.connection
        connection.execute(
            sa.text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32)")
        )

    context.on_version_apply_callbacks = (*existing_callbacks, restore_width)


def _restore_0088_event_type(bind: sa.Connection) -> None:
    """Remove only Task 2's event label while preserving historical enum order."""
    target = "RECORD_LEGAL_HOLD_RELEASE_AUTHORIZED"
    op.execute("LOCK TABLE public.audit_event IN ACCESS EXCLUSIVE MODE")
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM public.audit_event WHERE event_type=:target)"),
        {"target": target},
    ).scalar_one():
        raise RuntimeError("populated_0089_downgrade_refused")
    labels = tuple(
        bind.execute(
            sa.text(
                """
                SELECT enum.enumlabel
                FROM pg_enum AS enum
                JOIN pg_type AS type ON type.oid=enum.enumtypid
                JOIN pg_namespace AS namespace ON namespace.oid=type.typnamespace
                WHERE namespace.nspname='public' AND type.typname='event_type'
                ORDER BY enum.enumsortorder
                """
            )
        ).scalars()
    )
    if labels.count(target) != 1:
        raise RuntimeError("database_authority_event_type_shape_refused")
    restored_labels = tuple(label for label in labels if label != target)
    _execute_composed(
        bind,
        psycopg_sql.SQL("CREATE TYPE public.event_type_0088_restored AS ENUM ({})").format(
            psycopg_sql.SQL(",").join(map(psycopg_sql.Literal, restored_labels))
        ),
    )
    op.execute(
        "ALTER TABLE public.audit_event ALTER COLUMN event_type "
        "TYPE public.event_type_0088_restored "
        "USING event_type::text::public.event_type_0088_restored"
    )
    op.execute("DROP TYPE public.event_type RESTRICT")
    op.execute("ALTER TYPE public.event_type_0088_restored RENAME TO event_type")


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_populated_0089_downgrade(bind)
    _drop_database_authority()

    op.drop_table("backup_maintenance_operation")
    op.drop_table("audit_maintenance_schedule")
    op.drop_table("r27_role_membership_operation")
    _downgrade_pending_blob_purge()
    op.drop_table("recovery_generation_witness")
    op.drop_table("recovery_generation_verifier_key")
    op.drop_constraint("fk_blob_purge_execution_id_r27_execution", "blob", type_="foreignkey")
    op.drop_constraint(
        "ck_disposition_event_r27_authority_shape", "disposition_event", type_="check"
    )
    op.drop_constraint(
        "fk_disposition_event_r27_execution_id_r27_execution",
        "disposition_event",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_disposition_event_r27_request_id_r27_request",
        "disposition_event",
        type_="foreignkey",
    )
    op.drop_column("disposition_event", "r27_execution_id")
    op.drop_column("disposition_event", "r27_request_id")
    op.drop_table("r27_execution")
    op.drop_table("r27_attestation")
    op.drop_table("r27_authorizer_key")
    op.drop_table("r27_action_challenge")
    op.drop_table("r27_manifest_derivative")
    op.drop_table("r27_manifest_target")
    op.drop_table("r27_manifest")
    op.execute(
        "REVOKE INSERT (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256) "
        "ON public.r27_request FROM easysynq_r27_authorizer"
    )
    _restore_worm_destroy_request()
    _drop_hold_release()
    op.drop_table("retention_operation_target")
    op.drop_table("retention_operation")
    op.execute(
        f"REVOKE UPDATE ({','.join(_TASK4_RECORD_UPDATE_COLUMNS)}) ON public.record FROM {APP_ROLE}"
    )
    op.execute(
        f"REVOKE UPDATE ({','.join(_TASK4_RETENTION_POLICY_UPDATE_COLUMNS)}) "
        f"ON public.retention_policy FROM {APP_ROLE}"
    )
    _drop_document_retention()
    op.execute(
        f"REVOKE INSERT ({','.join(_APP_BLOB_INSERT_COLUMNS)}) ON public.blob FROM {APP_ROLE}"
    )
    _downgrade_blob()
    _restore_0088_event_type(bind)
    _restore_0088_database_authority(bind)
    _restore_0088_audit_partition_factory(bind)

    for name in reversed(tuple(_ENUM_VALUES)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
    _restore_alembic_version_width_after_stamp()


def _downgrade_pending_blob_purge() -> None:
    op.drop_table("r27_execution_target_result")
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
        "ON public.worm_hold_release_authorization"
    )
    op.execute(
        "DROP TRIGGER trg_worm_hold_release_authorize ON public.worm_hold_release_authorization"
    )
    op.execute("DROP FUNCTION public.easysynq_guard_hold_release_authorization_history()")
    op.execute("DROP FUNCTION public.easysynq_guard_hold_release_authorization_insert()")
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
    op.drop_constraint(op.f("ck_blob_purge_provenance_shape"), "blob", type_="check")
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
