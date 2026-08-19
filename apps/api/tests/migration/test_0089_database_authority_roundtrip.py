"""Atomic PostgreSQL authority refusal and 0089 round-trip proofs."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer

from easysynq_api.config import get_settings
from easysynq_api.readiness import MIGRATIONS_DIR
from easysynq_api.services.backup.dsn import conn_kwargs

_BASE_REVISION = "0088_bootstrap_credential"
_REVISION = "0089_worm_retention_container_identity"
_MEMBERSHIP_REFUSAL = "database_authority_role_membership_refused"
_SCHEMA_OWNER_REFUSAL = "database_authority_wrong_public_schema_owner"
_DEFAULT_ACL_REFUSAL = "database_authority_wrong_default_acl_grantor"
_DOWNGRADE_REFUSAL = "populated_0089_downgrade_refused"

_APP_ROLE = "easysynq_app"
_LINKER_ROLE = "easysynq_linker"
_NEW_ROLES = (
    "easysynq_retention",
    "easysynq_hold_authorizer",
    "easysynq_hold_maintenance",
    "easysynq_r27_authorizer",
    "easysynq_r27_maintenance",
    "easysynq_r27_authorizer_key_manager",
    "easysynq_recovery_key_manager",
    "easysynq_r27_role_manager",
    "easysynq_audit_signer",
    "easysynq_backup",
)
_TARGET_ROLES = (_APP_ROLE, _LINKER_ROLE, *_NEW_ROLES)

_AUTHORITY_FUNCTIONS = (
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
_AUTHORITY_TRIGGERS = (
    "trg_blob_worm_identity",
    "trg_document_version_worm_owner",
    "trg_evidence_blob_worm_owner",
    "trg_r27_authorizer_key_history",
    "trg_recovery_verifier_key_history",
    "trg_r27_execution_target_result_history",
    "trg_recovery_witness_history",
    "trg_role_membership_history",
)


@dataclass(frozen=True)
class _DatabaseSnapshot:
    revision: str
    schema: tuple[tuple[object, ...], ...]
    owners: tuple[tuple[object, ...], ...]
    roles: tuple[tuple[object, ...], ...]
    memberships: tuple[tuple[object, ...], ...]
    current_acls: tuple[tuple[object, ...], ...]
    default_acls: tuple[tuple[object, ...], ...]
    functions: tuple[tuple[object, ...], ...]
    triggers: tuple[tuple[object, ...], ...]


@pytest.fixture(scope="module")
def postgres_admin_url() -> Iterator[str]:
    configured = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    if configured:
        yield configured
        return
    with PostgresContainer(
        "postgres:16",
        username="test",
        password="test",
        dbname="test",
        driver="psycopg",
    ) as postgres:
        yield postgres.get_connection_url()


def _role_names(admin_url: str, candidates: Sequence[str]) -> set[str]:
    with psycopg.connect(
        **conn_kwargs(admin_url, dbname="postgres"),
        autocommit=True,
    ) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                (list(candidates),),
            )
        }


def _drop_test_roles(admin_url: str, roles: set[str]) -> None:
    if not roles:
        return
    with psycopg.connect(
        **conn_kwargs(admin_url, dbname="postgres"),
        autocommit=True,
    ) as connection:
        memberships = connection.execute(
            """
            SELECT granted.rolname, member.rolname
            FROM pg_auth_members AS membership
            JOIN pg_roles AS granted ON granted.oid = membership.roleid
            JOIN pg_roles AS member ON member.oid = membership.member
            WHERE granted.rolname = ANY(%s) OR member.rolname = ANY(%s)
            """,
            (list(roles), list(roles)),
        ).fetchall()
        for granted, member in memberships:
            connection.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(str(granted)),
                    sql.Identifier(str(member)),
                )
            )
        for role in sorted(roles, reverse=True):
            connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@contextmanager
def _scratch_database(
    admin_url: str,
    *,
    extra_roles: Sequence[str] = (),
) -> Iterator[str]:
    tracked_roles = (*_TARGET_ROLES, *extra_roles)
    preexisting_roles = _role_names(admin_url, tracked_roles)
    database = f"easysynq_0089_authority_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(
        **conn_kwargs(admin_url, dbname="postgres"),
        autocommit=True,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    scratch_url = make_url(admin_url).set(database=database).render_as_string(hide_password=False)
    try:
        yield scratch_url
    finally:
        get_settings.cache_clear()
        with psycopg.connect(
            **conn_kwargs(admin_url, dbname="postgres"),
            autocommit=True,
        ) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
        created_roles = _role_names(admin_url, tracked_roles) - preexisting_roles
        _drop_test_roles(admin_url, created_roles)


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def _point_at(monkeypatch: pytest.MonkeyPatch, scratch_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    monkeypatch.setenv("DATABASE_URL_SYNC", scratch_url)
    get_settings.cache_clear()


def _rows(
    connection: sa.Connection,
    statement: str,
    parameters: dict[str, object] | None = None,
) -> tuple[tuple[object, ...], ...]:
    result = connection.execute(sa.text(statement), parameters or {})
    return tuple(tuple(row) for row in result)


def _revision(connection: sa.Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _schema_signature(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT 'column', table_name, column_name, data_type, udt_name,
               is_nullable, COALESCE(character_maximum_length::text, ''),
               COALESCE(column_default, '')
        FROM information_schema.columns
        WHERE table_schema = 'public'
        UNION ALL
        SELECT 'constraint', relation.relname, con.conname,
               con.contype::text, pg_get_constraintdef(con.oid), '', '', ''
        FROM pg_constraint AS con
        JOIN pg_class AS relation ON relation.oid = con.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'index', tablename, indexname, indexdef, '', '', '', ''
        FROM pg_indexes
        WHERE schemaname = 'public'
        UNION ALL
        SELECT 'enum', type.typname, enum.enumlabel, enum.enumsortorder::text, '', '', '', ''
        FROM pg_type AS type
        JOIN pg_enum AS enum ON enum.enumtypid = type.oid
        JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace
        WHERE namespace.nspname = 'public'
        UNION ALL
        SELECT 'relation', relation.relname, relation.relkind::text,
               pg_get_userbyid(relation.relowner), relation.relpersistence::text,
               relation.relispartition::text, '', ''
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'S', 'v', 'm')
        ORDER BY 1, 2, 3, 4, 5, 6, 7, 8
        """,
    )


def _owner_signature(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT 'database', datname, pg_get_userbyid(datdba)
        FROM pg_database
        WHERE datname = current_database()
        UNION ALL
        SELECT 'schema', nspname, pg_get_userbyid(nspowner)
        FROM pg_namespace
        WHERE nspname = 'public'
        ORDER BY 1, 2, 3
        """,
    )


def _role_signature(
    connection: sa.Connection,
    roles: Sequence[str],
) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb,
               rolcreaterole, rolreplication, rolbypassrls, rolconnlimit,
               COALESCE(rolvaliduntil::text, ''), COALESCE(rolpassword, '')
        FROM pg_authid
        WHERE rolname = ANY(:roles)
        ORDER BY rolname
        """,
        {"roles": list(roles)},
    )


def _membership_signature(
    connection: sa.Connection,
    roles: Sequence[str],
) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT granted.rolname, member.rolname, pg_get_userbyid(membership.grantor),
               membership.admin_option, membership.inherit_option, membership.set_option
        FROM pg_auth_members AS membership
        JOIN pg_roles AS granted ON granted.oid = membership.roleid
        JOIN pg_roles AS member ON member.oid = membership.member
        WHERE granted.rolname = ANY(:roles) OR member.rolname = ANY(:roles)
        ORDER BY 1, 2, 3, 4, 5, 6
        """,
        {"roles": list(roles)},
    )


def _current_acl_signature(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        WITH authority_grantees AS (
            SELECT unnest(ARRAY['PUBLIC', 'easysynq_app', 'easysynq_linker']) AS name
        ), grants AS (
            SELECT 'schema' AS object_kind, namespace.nspname AS object_name,
                   '' AS subobject_name, COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
                   pg_get_userbyid(acl.grantor) AS grantor, acl.privilege_type,
                   acl.is_grantable
            FROM pg_namespace AS namespace
            CROSS JOIN LATERAL aclexplode(namespace.nspacl) AS acl
            LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = 'public'
            UNION ALL
            SELECT 'relation', relation.relname, relation.relkind::text,
                   COALESCE(grantee.rolname, 'PUBLIC'), pg_get_userbyid(acl.grantor),
                   acl.privilege_type, acl.is_grantable
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL aclexplode(relation.relacl) AS acl
            LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = 'public'
            UNION ALL
            SELECT 'column', relation.relname, attribute.attname,
                   COALESCE(grantee.rolname, 'PUBLIC'), pg_get_userbyid(acl.grantor),
                   acl.privilege_type, acl.is_grantable
            FROM pg_attribute AS attribute
            JOIN pg_class AS relation ON relation.oid = attribute.attrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            CROSS JOIN LATERAL aclexplode(attribute.attacl) AS acl
            LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = 'public' AND attribute.attnum > 0
            UNION ALL
            SELECT 'function', function.oid::regprocedure::text, function.prokind::text,
                   COALESCE(grantee.rolname, 'PUBLIC'), pg_get_userbyid(acl.grantor),
                   acl.privilege_type, acl.is_grantable
            FROM pg_proc AS function
            JOIN pg_namespace AS namespace ON namespace.oid = function.pronamespace
            CROSS JOIN LATERAL aclexplode(
                COALESCE(function.proacl, acldefault('f', function.proowner))
            ) AS acl
            LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE namespace.nspname = 'public'
        )
        SELECT object_kind, object_name, subobject_name, grants.grantee,
               grantor, privilege_type, is_grantable
        FROM grants
        JOIN authority_grantees ON authority_grantees.name = grants.grantee
        ORDER BY 1, 2, 3, 4, 5, 6, 7
        """,
    )


def _default_acl_signature(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT pg_get_userbyid(defaults.defaclrole),
               COALESCE(namespace.nspname, ''), defaults.defaclobjtype::text,
               COALESCE(grantee.rolname, 'PUBLIC'), pg_get_userbyid(acl.grantor),
               acl.privilege_type, acl.is_grantable
        FROM pg_default_acl AS defaults
        LEFT JOIN pg_namespace AS namespace ON namespace.oid = defaults.defaclnamespace
        CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
        WHERE defaults.defaclnamespace = 0 OR namespace.nspname = 'public'
        ORDER BY 1, 2, 3, 4, 5, 6, 7
        """,
    )


def _function_signature(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT function.oid::regprocedure::text, pg_get_userbyid(function.proowner),
               function.prosecdef, COALESCE(function.proconfig::text, ''),
               COALESCE(function.proacl::text, ''), pg_get_functiondef(function.oid)
        FROM pg_proc AS function
        JOIN pg_namespace AS namespace ON namespace.oid = function.pronamespace
        WHERE namespace.nspname = 'public'
          AND function.prokind IN ('f', 'p')
          AND function.proname LIKE 'easysynq_%'
        ORDER BY 1
        """,
    )


def _trigger_signature(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT relation.relname, trigger.tgname, trigger.tgenabled::text,
               pg_get_triggerdef(trigger.oid)
        FROM pg_trigger AS trigger
        JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public' AND NOT trigger.tgisinternal
        ORDER BY 1, 2
        """,
    )


def _snapshot(
    connection: sa.Connection,
    *,
    roles: Sequence[str] = _TARGET_ROLES,
) -> _DatabaseSnapshot:
    return _DatabaseSnapshot(
        revision=_revision(connection),
        schema=_schema_signature(connection),
        owners=_owner_signature(connection),
        roles=_role_signature(connection, roles),
        memberships=_membership_signature(connection, roles),
        current_acls=_current_acl_signature(connection),
        default_acls=_default_acl_signature(connection),
        functions=_function_signature(connection),
        triggers=_trigger_signature(connection),
    )


def _execute_composed(connection: sa.Connection, statement: sql.Composed) -> None:
    with connection.connection.driver_connection.cursor() as cursor:
        cursor.execute(statement)


def _create_role(
    connection: sa.Connection,
    role: str,
    *,
    password: str | None = None,
    privileged_attributes: bool = False,
) -> None:
    attributes = sql.SQL("LOGIN INHERIT CREATEDB CREATEROLE REPLICATION BYPASSRLS")
    if not privileged_attributes:
        attributes = sql.SQL("NOLOGIN NOINHERIT NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
    password_clause = sql.SQL("")
    if password is not None:
        password_clause = sql.SQL(" PASSWORD {}").format(sql.Literal(password))
    _execute_composed(
        connection,
        sql.SQL("CREATE ROLE {} {}{}").format(
            sql.Identifier(role),
            attributes,
            password_clause,
        ),
    )


@pytest.mark.parametrize("direction", ("target_is_member", "target_is_granted"))
def test_any_target_role_membership_refuses_upgrade_atomically(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
    direction: str,
) -> None:
    peer_role = f"esq_authority_peer_{uuid.uuid4().hex[:12]}"
    with _scratch_database(postgres_admin_url, extra_roles=(peer_role,)) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _BASE_REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                for index, role in enumerate(_NEW_ROLES, start=1):
                    _create_role(
                        connection,
                        role,
                        password=f"preflight-role-{index}-quote'\\snowman-☃",
                        privileged_attributes=True,
                    )
                _create_role(connection, peer_role)

            for target_role in _TARGET_ROLES:
                if direction == "target_is_member":
                    granted_role, member_role = peer_role, target_role
                else:
                    granted_role, member_role = target_role, peer_role
                with engine.begin() as connection:
                    _execute_composed(
                        connection,
                        sql.SQL("GRANT {} TO {}").format(
                            sql.Identifier(granted_role),
                            sql.Identifier(member_role),
                        ),
                    )
                try:
                    with engine.connect() as connection:
                        before = _snapshot(connection, roles=(*_TARGET_ROLES, peer_role))

                    with pytest.raises(Exception, match=_MEMBERSHIP_REFUSAL):
                        command.upgrade(config, _REVISION)

                    with engine.connect() as connection:
                        assert _snapshot(connection, roles=(*_TARGET_ROLES, peer_role)) == before, (
                            f"membership refusal mutated state for {target_role}"
                        )
                finally:
                    with engine.begin() as connection:
                        _execute_composed(
                            connection,
                            sql.SQL("REVOKE {} FROM {}").format(
                                sql.Identifier(granted_role),
                                sql.Identifier(member_role),
                            ),
                        )
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    ("unexpected_authority", "expected_error"),
    (
        ("public_schema_owner", _SCHEMA_OWNER_REFUSAL),
        ("default_acl_grantor", _DEFAULT_ACL_REFUSAL),
    ),
)
def test_unexpected_authority_owner_refuses_upgrade_atomically(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
    unexpected_authority: str,
    expected_error: str,
) -> None:
    unexpected_role = f"esq_unexpected_owner_{uuid.uuid4().hex[:12]}"
    with _scratch_database(postgres_admin_url, extra_roles=(unexpected_role,)) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _BASE_REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                _create_role(connection, unexpected_role)
                if unexpected_authority == "public_schema_owner":
                    _execute_composed(
                        connection,
                        sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                            sql.Identifier(unexpected_role)
                        ),
                    )
                else:
                    _execute_composed(
                        connection,
                        sql.SQL(
                            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                            "GRANT SELECT ON TABLES TO {}"
                        ).format(
                            sql.Identifier(unexpected_role),
                            sql.Identifier(_APP_ROLE),
                        ),
                    )
            with engine.connect() as connection:
                before = _snapshot(connection, roles=(*_TARGET_ROLES, unexpected_role))

            with pytest.raises(Exception, match=expected_error):
                command.upgrade(config, _REVISION)

            with engine.connect() as connection:
                assert _snapshot(connection, roles=(*_TARGET_ROLES, unexpected_role)) == before
        finally:
            engine.dispose()


def _seed_target_result(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO r27_execution_target_result
                (id, execution_id, manifest_target_id, result_code, verified_at,
                 surviving_owner_kind, surviving_owner_id)
            VALUES
                (:id, :execution_id, :manifest_target_id,
                 'LOGICAL_ONLY_SURVIVING_OWNER', now(),
                 'DOCUMENT_VERSION', :surviving_owner_id)
            """
        ),
        {
            "id": uuid.uuid4(),
            "execution_id": uuid.uuid4(),
            "manifest_target_id": uuid.uuid4(),
            "surviving_owner_id": uuid.uuid4(),
        },
    )


def _seed_role_membership_operation(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO r27_role_membership_operation
                (id, user_id, org_id, action, operator_identity, state, requested_at)
            VALUES
                (:id, :user_id, :org_id, 'ASSIGN', 'roundtrip-host-operator',
                 'REQUESTED', now())
            """
        ),
        {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
        },
    )


def _seed_invalidated_witness(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO recovery_generation_witness
                (id, schema_version, key_id, witness_nonce, request_id,
                 manifest_sha256, generation_id, generation_identity,
                 excluded_set_sha256, result, canonical_bytes, signature,
                 issued_at, verified_at, invalidated_at,
                 invalidation_audit_event_id, invalidation_reason)
            VALUES
                (:id, 1, :key_id, :witness_nonce, :request_id,
                 :manifest_sha256, :generation_id, :generation_identity,
                 :excluded_set_sha256, 'VERIFIED', :canonical_bytes, :signature,
                 now(), now(), now(), 987654321, 'KEY_REVOKED')
            """
        ),
        {
            "id": uuid.uuid4(),
            "key_id": uuid.uuid4(),
            "witness_nonce": uuid.uuid4().hex + "abcdefghijk",
            "request_id": uuid.uuid4(),
            "manifest_sha256": "a" * 64,
            "generation_id": "sealed-generation-1",
            "generation_identity": "external-generation-identity-1",
            "excluded_set_sha256": "b" * 64,
            "canonical_bytes": b"canonical-witness",
            "signature": b"witness-signature",
        },
    )


_POPULATED_STATES: tuple[tuple[str, Callable[[sa.Connection], None]], ...] = (
    ("r27_execution_target_result", _seed_target_result),
    ("r27_role_membership_operation", _seed_role_membership_operation),
    ("invalidated_recovery_generation_witness", _seed_invalidated_witness),
)


def _new_state_data(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT 'r27_execution_target_result', id::text, to_jsonb(row_data)::text
        FROM r27_execution_target_result AS row_data
        UNION ALL
        SELECT 'r27_role_membership_operation', id::text, to_jsonb(row_data)::text
        FROM r27_role_membership_operation AS row_data
        UNION ALL
        SELECT 'recovery_generation_witness', id::text, to_jsonb(row_data)::text
        FROM recovery_generation_witness AS row_data
        ORDER BY 1, 2, 3
        """,
    )


@pytest.mark.parametrize(("state_kind", "seed"), _POPULATED_STATES, ids=lambda value: str(value))
def test_each_new_state_refuses_downgrade_without_any_authority_mutation(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
    state_kind: str,
    seed: Callable[[sa.Connection], None],
) -> None:
    membership_role = f"esq_downgrade_member_{uuid.uuid4().hex[:12]}"
    with _scratch_database(postgres_admin_url, extra_roles=(membership_role,)) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                seed(connection)
                _create_role(connection, membership_role)
                _execute_composed(
                    connection,
                    sql.SQL("GRANT {} TO {}").format(
                        sql.Identifier("easysynq_retention"),
                        sql.Identifier(membership_role),
                    ),
                )
            with engine.connect() as connection:
                before = _snapshot(connection, roles=(*_TARGET_ROLES, membership_role))
                before_data = _new_state_data(connection)

            with pytest.raises(Exception, match=_DOWNGRADE_REFUSAL):
                command.downgrade(config, _BASE_REVISION)

            with engine.connect() as connection:
                assert _snapshot(connection, roles=(*_TARGET_ROLES, membership_role)) == before, (
                    f"{state_kind} refusal mutated database authority"
                )
                assert _new_state_data(connection) == before_data
        finally:
            engine.dispose()


def _existing_names(
    connection: sa.Connection,
    *,
    catalog: str,
    names: Sequence[str],
) -> set[str]:
    if catalog == "roles":
        statement = "SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"
    elif catalog == "functions":
        statement = (
            "SELECT DISTINCT function.proname FROM pg_proc AS function "
            "JOIN pg_namespace AS namespace ON namespace.oid = function.pronamespace "
            "WHERE namespace.nspname = 'public' AND function.proname = ANY(:names)"
        )
    else:
        statement = (
            "SELECT trigger.tgname FROM pg_trigger AS trigger "
            "JOIN pg_class AS relation ON relation.oid = trigger.tgrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = 'public' AND NOT trigger.tgisinternal "
            "AND trigger.tgname = ANY(:names)"
        )
    return set(connection.execute(sa.text(statement), {"names": list(names)}).scalars().all())


def _role_attributes_without_verifier(
    connection: sa.Connection,
    roles: Sequence[str],
) -> tuple[tuple[object, ...], ...]:
    return tuple(row[:-1] for row in _role_signature(connection, roles))


def _factory_authority(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        SELECT function.oid::regprocedure::text, pg_get_userbyid(function.proowner),
               function.prosecdef, COALESCE(function.proconfig::text, ''),
               COALESCE(grantee.rolname, 'PUBLIC'), pg_get_userbyid(acl.grantor),
               acl.privilege_type, acl.is_grantable
        FROM pg_proc AS function
        JOIN pg_namespace AS namespace ON namespace.oid = function.pronamespace
        CROSS JOIN LATERAL aclexplode(
            COALESCE(function.proacl, acldefault('f', function.proowner))
        ) AS acl
        LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
        WHERE namespace.nspname = 'public'
          AND function.proname = 'easysynq_create_audit_partition'
        ORDER BY 1, 5, 6, 7, 8
        """,
    )


def _child_authority(
    connection: sa.Connection,
    table_name: str,
) -> tuple[tuple[object, ...], ...]:
    return _rows(
        connection,
        """
        WITH grants AS (
            SELECT 'relation' AS kind, '' AS column_name,
                   COALESCE(grantee.rolname, 'PUBLIC') AS grantee,
                   acl.privilege_type, acl.is_grantable
            FROM pg_class AS relation
            CROSS JOIN LATERAL aclexplode(relation.relacl) AS acl
            LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE relation.oid = CAST(:table_name AS regclass)
            UNION ALL
            SELECT 'column', attribute.attname,
                   COALESCE(grantee.rolname, 'PUBLIC'),
                   acl.privilege_type, acl.is_grantable
            FROM pg_attribute AS attribute
            CROSS JOIN LATERAL aclexplode(attribute.attacl) AS acl
            LEFT JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
            WHERE attribute.attrelid = CAST(:table_name AS regclass)
              AND attribute.attnum > 0
        )
        SELECT kind, column_name, grantee, privilege_type, is_grantable
        FROM grants
        WHERE grantee IN ('PUBLIC', 'easysynq_app', 'easysynq_linker')
        ORDER BY 1, 2, 3, 4, 5
        """,
        {"table_name": table_name},
    )


def _difference(
    label: str,
    before: tuple[tuple[object, ...], ...],
    after: tuple[tuple[object, ...], ...],
) -> str:
    missing = sorted(set(before) - set(after), key=repr)[:6]
    extra = sorted(set(after) - set(before), key=repr)[:6]
    return f"{label}: missing={missing!r}, extra={extra!r}"


def _authenticate(scratch_url: str, role: str, password: str) -> str:
    role_url = (
        make_url(scratch_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    with psycopg.connect(**conn_kwargs(role_url)) as connection:
        return str(connection.execute("SELECT session_user").fetchone()[0])


def test_empty_authority_roundtrip_is_exact_and_special_passwords_reauthenticate(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_password = "App'quote\\slash:@/snowman-☃"
    linker_password = "Linker'quote\\slash:@/lambda-λ"
    monkeypatch.setenv("APP_DB_PASSWORD", app_password)
    monkeypatch.setenv("LINKER_DB_PASSWORD", linker_password)

    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _BASE_REVISION)
        engine = sa.create_engine(scratch_url)
        failures: list[str] = []
        try:
            with engine.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_create_audit_partition(CAST(:start AS date))"),
                    {"start": "2036-01-01"},
                )
            with engine.connect() as connection:
                baseline_schema = _schema_signature(connection)
                baseline_roles = _role_attributes_without_verifier(
                    connection, (_APP_ROLE, _LINKER_ROLE)
                )
                baseline_current_acls = _current_acl_signature(connection)
                baseline_default_acls = _default_acl_signature(connection)
                baseline_factory = _factory_authority(connection)
                baseline_child = _child_authority(connection, "audit_event_2036_01")

            command.upgrade(config, _REVISION)
            with engine.connect() as connection:
                assert _existing_names(connection, catalog="roles", names=_NEW_ROLES) == set(
                    _NEW_ROLES
                )
                assert _existing_names(
                    connection, catalog="functions", names=_AUTHORITY_FUNCTIONS
                ) == set(_AUTHORITY_FUNCTIONS)
                assert _existing_names(
                    connection, catalog="triggers", names=_AUTHORITY_TRIGGERS
                ) == set(_AUTHORITY_TRIGGERS)

            command.downgrade(config, _BASE_REVISION)
            with engine.connect() as connection:
                assert _revision(connection) == _BASE_REVISION
                remaining_roles = _existing_names(connection, catalog="roles", names=_NEW_ROLES)
                remaining_functions = _existing_names(
                    connection, catalog="functions", names=_AUTHORITY_FUNCTIONS
                )
                remaining_triggers = _existing_names(
                    connection, catalog="triggers", names=_AUTHORITY_TRIGGERS
                )
                if remaining_roles:
                    failures.append(f"new roles survived downgrade: {sorted(remaining_roles)!r}")
                if remaining_functions:
                    failures.append(
                        f"authority functions survived downgrade: {sorted(remaining_functions)!r}"
                    )
                if remaining_triggers:
                    failures.append(
                        f"authority triggers survived downgrade: {sorted(remaining_triggers)!r}"
                    )

                roundtrip_schema = _schema_signature(connection)
                if roundtrip_schema != baseline_schema:
                    failures.append(
                        _difference("0088 schema and enums", baseline_schema, roundtrip_schema)
                    )
                version_width = connection.execute(
                    sa.text(
                        """
                        SELECT character_maximum_length
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'alembic_version'
                          AND column_name = 'version_num'
                        """
                    )
                ).scalar_one()
                if version_width != 32:
                    failures.append(
                        f"alembic_version.version_num width is {version_width!r}, expected 32"
                    )

                roundtrip_roles = _role_attributes_without_verifier(
                    connection, (_APP_ROLE, _LINKER_ROLE)
                )
                if roundtrip_roles != baseline_roles:
                    failures.append(f"role attributes: {baseline_roles!r} != {roundtrip_roles!r}")

                roundtrip_current_acls = _current_acl_signature(connection)
                if roundtrip_current_acls != baseline_current_acls:
                    failures.append(
                        _difference("current ACLs", baseline_current_acls, roundtrip_current_acls)
                    )
                roundtrip_default_acls = _default_acl_signature(connection)
                if roundtrip_default_acls != baseline_default_acls:
                    failures.append(
                        _difference("default ACLs", baseline_default_acls, roundtrip_default_acls)
                    )
                roundtrip_factory = _factory_authority(connection)
                if roundtrip_factory != baseline_factory:
                    failures.append(
                        _difference("partition factory", baseline_factory, roundtrip_factory)
                    )

            assert _authenticate(scratch_url, _APP_ROLE, app_password) == _APP_ROLE
            assert _authenticate(scratch_url, _LINKER_ROLE, linker_password) == _LINKER_ROLE

            try:
                with engine.begin() as connection:
                    connection.execute(
                        sa.text("SELECT easysynq_create_audit_partition(CAST(:start AS date))"),
                        {"start": "2037-01-01"},
                    )
                with engine.connect() as connection:
                    roundtrip_child = _child_authority(connection, "audit_event_2037_01")
                if roundtrip_child != baseline_child:
                    failures.append(
                        _difference("partition child ACLs", baseline_child, roundtrip_child)
                    )
            except sa.exc.DBAPIError as error:
                failures.append(f"restored partition factory failed: {error.orig}")

            command.upgrade(config, _REVISION)
            with engine.connect() as connection:
                assert _revision(connection) == _REVISION
            assert _authenticate(scratch_url, _APP_ROLE, app_password) == _APP_ROLE
            assert _authenticate(scratch_url, _LINKER_ROLE, linker_password) == _LINKER_ROLE

            assert failures == [], "\n".join(failures)
        finally:
            engine.dispose()
