"""Isolated PostgreSQL proofs for the clean-only 0089 schema boundary."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager

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
_LEGACY_REFUSAL = "unsupported_legacy_physical_owner_state"
_DOWNGRADE_REFUSAL = "populated_0089_downgrade_refused"


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


@contextmanager
def _scratch_database(admin_url: str) -> Iterator[str]:
    database = f"easysynq_0089_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(
        **conn_kwargs(admin_url, dbname="postgres"),
        autocommit=True,
    ) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    scratch_url = make_url(admin_url).set(database=database).render_as_string(hide_password=False)
    try:
        yield scratch_url
    finally:
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


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def _point_at(monkeypatch: pytest.MonkeyPatch, scratch_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", scratch_url)
    monkeypatch.setenv("DATABASE_URL_SYNC", scratch_url)
    get_settings.cache_clear()


def _revision(connection: sa.Connection) -> str:
    return str(connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one())


def _schema_signature(connection: sa.Connection) -> tuple[tuple[object, ...], ...]:
    """Capture reflected schema state, excluding data and the Alembic version row."""
    rows = connection.execute(
        sa.text(
            """
            SELECT 'column', table_name, column_name, data_type, udt_name,
                   is_nullable, COALESCE(column_default, '')
            FROM information_schema.columns
            WHERE table_schema = current_schema()
            UNION ALL
            SELECT 'constraint', c.relname, con.conname, con.contype::text,
                   pg_get_constraintdef(con.oid), '', ''
            FROM pg_constraint AS con
            JOIN pg_class AS c ON c.oid = con.conrelid
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
            UNION ALL
            SELECT 'index', tablename, indexname, indexdef, '', '', ''
            FROM pg_indexes
            WHERE schemaname = current_schema()
            UNION ALL
            SELECT 'enum', t.typname, e.enumlabel, e.enumsortorder::text, '', '', ''
            FROM pg_type AS t
            JOIN pg_enum AS e ON e.enumtypid = t.oid
            JOIN pg_namespace AS n ON n.oid = t.typnamespace
            WHERE n.nspname = current_schema()
            ORDER BY 1, 2, 3, 4, 5, 6, 7
            """
        )
    )
    return tuple(tuple(row) for row in rows)


def _create_hold_release_operation(
    connection: sa.Connection,
    *,
    legal_hold: bool = False,
    duration: str = "P1Y",
    worm_lock_period: str | None = "P1Y",
    state: str = "PENDING_AUTHORIZATION",
) -> tuple[uuid.UUID, str]:
    org_id = connection.execute(
        sa.text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
    ).scalar_one()
    policy_id = uuid.uuid4()
    record_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    digest = uuid.uuid4().hex * 2
    connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
    connection.execute(
        sa.text(
            """
            INSERT INTO retention_policy
                (id, org_id, name, duration, worm_lock_period)
            VALUES
                (:id, :org_id, :name, :duration, :worm_lock_period)
            """
        ),
        {
            "id": policy_id,
            "org_id": org_id,
            "name": f"trigger-test-{policy_id}",
            "duration": duration,
            "worm_lock_period": worm_lock_period,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO record
                (id, org_id, record_type, captured_by, retention_policy_id, legal_hold)
            VALUES
                (:id, :org_id, 'EVIDENCE', :captured_by, :policy_id, :legal_hold)
            """
        ),
        {
            "id": record_id,
            "org_id": org_id,
            "captured_by": uuid.uuid4(),
            "policy_id": policy_id,
            "legal_hold": legal_hold,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO worm_hold_release_operation
                (id, org_id, record_id, blob_sha256, object_version_id,
                 initiated_by_user_id, idempotency_key, normalized_release_basis,
                 canonical_bytes, canonical_sha256, owner_snapshot_sha256, state)
            VALUES
                (:id, :org_id, :record_id, :blob_sha256, 'opaque-version',
                 :initiated_by_user_id, :idempotency_key, 'approved basis',
                 :canonical_bytes, :canonical_sha256, :owner_snapshot_sha256,
                 CAST(:state AS hold_release_state))
            """
        ),
        {
            "id": operation_id,
            "org_id": org_id,
            "record_id": record_id,
            "blob_sha256": "a" * 64,
            "initiated_by_user_id": uuid.uuid4(),
            "idempotency_key": str(operation_id),
            "canonical_bytes": b"test",
            "canonical_sha256": digest,
            "owner_snapshot_sha256": "b" * 64,
            "state": state,
        },
    )
    return operation_id, digest


def _authorize_hold_release(
    connection: sa.Connection,
    operation_id: uuid.UUID,
    digest: str,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO worm_hold_release_authorization
                (operation_id, canonical_sha256, host_operator_identity,
                 authorizing_audit_event_id, authorized_at, authorizer_role)
            VALUES
                (:operation_id, :canonical_sha256, 'test-operator', 1, now(),
                 'worm-hold-authorizer')
            """
        ),
        {"operation_id": operation_id, "canonical_sha256": digest},
    )


def _seed_legacy_blob(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO blob
                (sha256, org_id, size_bytes, mime_type, bucket, object_key, worm_locked, sse)
            VALUES
                (:sha256, :org_id, 1, 'application/octet-stream', 'legacy', 'legacy', false, false)
            """
        ),
        {"sha256": "1" * 64, "org_id": uuid.uuid4()},
    )


def _seed_legacy_document_version(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO document_version
                (id, org_id, document_id, version_seq, revision_label, change_significance,
                 change_reason, source_blob_sha256, metadata_snapshot, author_user_id, created_by)
            VALUES
                (:id, :org_id, :document_id, 1, '1', 'MINOR', 'legacy', :sha256,
                 '{}'::jsonb, :author_user_id, :created_by)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
            "document_id": uuid.uuid4(),
            "sha256": "2" * 64,
            "author_user_id": uuid.uuid4(),
            "created_by": uuid.uuid4(),
        },
    )


def _seed_legacy_evidence_blob(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO evidence_blob
                (id, org_id, record_id, blob_sha256, created_by)
            VALUES
                (:id, :org_id, :record_id, :sha256, :created_by)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
            "record_id": uuid.uuid4(),
            "sha256": "3" * 64,
            "created_by": uuid.uuid4(),
        },
    )


def _seed_legacy_pending_purge(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO pending_blob_purge
                (id, org_id, sha256, bucket, object_key, bypass_governance, authority_bound)
            VALUES
                (:id, :org_id, :sha256, 'legacy', 'legacy', false, false)
            """
        ),
        {"id": uuid.uuid4(), "org_id": uuid.uuid4(), "sha256": "4" * 64},
    )


def _seed_legacy_worm_request(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO worm_destroy_request
                (id, org_id, record_id, legal_basis, requested_by)
            VALUES
                (:id, :org_id, :record_id, 'legacy', :requested_by)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
            "record_id": uuid.uuid4(),
            "requested_by": uuid.uuid4(),
        },
    )


_PACK_POINTER_INSERTS = {
    "pack_record_id": sa.text(
        """
        INSERT INTO evidence_pack
            (id, org_id, framework_id, title, scope_kind, scope_selector,
             pack_record_id, created_by)
        VALUES
            (:id, :org_id, :framework_id, 'legacy', 'CLAUSE', '{}'::jsonb,
             :pointer_value, :created_by)
        """
    ),
    "zip_blob_sha256": sa.text(
        """
        INSERT INTO evidence_pack
            (id, org_id, framework_id, title, scope_kind, scope_selector,
             zip_blob_sha256, created_by)
        VALUES
            (:id, :org_id, :framework_id, 'legacy', 'CLAUSE', '{}'::jsonb,
             :pointer_value, :created_by)
        """
    ),
    "portfolio_blob_sha256": sa.text(
        """
        INSERT INTO evidence_pack
            (id, org_id, framework_id, title, scope_kind, scope_selector,
             portfolio_blob_sha256, created_by)
        VALUES
            (:id, :org_id, :framework_id, 'legacy', 'CLAUSE', '{}'::jsonb,
             :pointer_value, :created_by)
        """
    ),
}


def _seed_legacy_pack_pointer(connection: sa.Connection, pointer_column: str) -> None:
    pointer_values: dict[str, object] = {
        "pack_record_id": uuid.uuid4(),
        "zip_blob_sha256": "5" * 64,
        "portfolio_blob_sha256": "6" * 64,
    }
    connection.execute(
        _PACK_POINTER_INSERTS[pointer_column],
        {
            "id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
            "framework_id": uuid.uuid4(),
            "pointer_value": pointer_values[pointer_column],
            "created_by": uuid.uuid4(),
        },
    )


def _seed_legacy_pack_record_pointer(connection: sa.Connection) -> None:
    _seed_legacy_pack_pointer(connection, "pack_record_id")


def _seed_legacy_pack_zip_pointer(connection: sa.Connection) -> None:
    _seed_legacy_pack_pointer(connection, "zip_blob_sha256")


def _seed_legacy_pack_portfolio_pointer(connection: sa.Connection) -> None:
    _seed_legacy_pack_pointer(connection, "portfolio_blob_sha256")


_LEGACY_SEEDS: tuple[tuple[str, Callable[[sa.Connection], None]], ...] = (
    ("blob", _seed_legacy_blob),
    ("document_version", _seed_legacy_document_version),
    ("evidence_blob", _seed_legacy_evidence_blob),
    ("pending_blob_purge", _seed_legacy_pending_purge),
    ("worm_destroy_request", _seed_legacy_worm_request),
    ("pack_record_pointer", _seed_legacy_pack_record_pointer),
    ("pack_zip_pointer", _seed_legacy_pack_zip_pointer),
    ("pack_portfolio_pointer", _seed_legacy_pack_portfolio_pointer),
)


def _seed_populated_worm_blob(connection: sa.Connection) -> None:
    org_id = connection.execute(
        sa.text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
    ).scalar_one()
    now = connection.execute(sa.text("SELECT now()")).scalar_one()
    connection.execute(
        sa.text(
            """
            INSERT INTO blob
                (sha256, org_id, size_bytes, mime_type, bucket, object_key,
                 object_version_id, worm_locked, worm_enforced_mode,
                 worm_asserted_retain_until, worm_asserted_at, worm_retain_until,
                 worm_retention_verified_at, worm_legal_hold,
                 worm_legal_hold_verified_at, sse)
            VALUES
                (:sha256, :org_id, 1, 'application/octet-stream', 'worm', 'object',
                 'opaque-version', true, 'GOVERNANCE', :now, :now, :now,
                 :now, false, :now, false)
            """
        ),
        {"sha256": "a" * 64, "org_id": org_id, "now": now},
    )


def _seed_populated_retention_operation(connection: sa.Connection) -> None:
    org_id, revision_id = connection.execute(
        sa.text(
            """
            SELECT rp.org_id, rr.id
            FROM retention_revision AS rr
            JOIN retention_policy AS rp ON rp.id = rr.retention_policy_id
            ORDER BY rr.created_at
            LIMIT 1
            """
        )
    ).one()
    connection.execute(
        sa.text(
            "INSERT INTO retention_operation (id, org_id, revision_id) "
            "VALUES (:id, :org_id, :revision_id)"
        ),
        {"id": uuid.uuid4(), "org_id": org_id, "revision_id": revision_id},
    )


def _seed_populated_r27_authorizer_key(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO r27_authorizer_key
                (id, key_id, public_key, fingerprint, active_at)
            VALUES
                (:id, :key_id, :public_key, :fingerprint, now())
            """
        ),
        {
            "id": uuid.uuid4(),
            "key_id": "test-key",
            "public_key": b"test-public-key",
            "fingerprint": "b" * 64,
        },
    )


def _seed_populated_audit_schedule(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO audit_maintenance_schedule
                (id, job_kind, interval_seconds, next_due_at)
            VALUES
                (:id, 'CHAIN_LINK', 60, now())
            """
        ),
        {"id": uuid.uuid4()},
    )


_POPULATED_0089_SEEDS: tuple[tuple[str, Callable[[sa.Connection], None]], ...] = (
    ("worm_blob", _seed_populated_worm_blob),
    ("retention_operation", _seed_populated_retention_operation),
    ("r27_authorizer_key", _seed_populated_r27_authorizer_key),
    ("audit_maintenance_schedule", _seed_populated_audit_schedule),
)


def test_empty_0088_upgrades_to_0089(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _BASE_REVISION)
        command.upgrade(config, _REVISION)

        engine = sa.create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                assert _revision(connection) == _REVISION
                table_names = set(sa.inspect(connection).get_table_names())
                assert {
                    "document_worm_config",
                    "retention_revision",
                    "retention_operation",
                    "retention_operation_target",
                    "worm_hold_release_operation",
                    "worm_hold_release_authorization",
                    "r27_request",
                    "r27_manifest",
                    "r27_manifest_target",
                    "r27_manifest_derivative",
                    "r27_action_challenge",
                    "r27_authorizer_key",
                    "r27_attestation",
                    "r27_execution",
                    "recovery_generation_verifier_key",
                    "recovery_generation_witness",
                    "audit_maintenance_schedule",
                    "backup_maintenance_operation",
                } <= table_names
                assert "worm_destroy_request" not in table_names
                policy_count = connection.execute(
                    sa.text("SELECT count(*) FROM retention_policy")
                ).scalar_one()
                revision_count = connection.execute(
                    sa.text(
                        "SELECT count(*) FROM retention_revision "
                        "WHERE state::text = 'ACTIVE' AND revision_no = 1"
                    )
                ).scalar_one()
                assert revision_count == policy_count
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    ("table_name", "required_values", "statement"),
    (
        (
            "retention_operation_target",
            {
                "operation_id": uuid.UUID(int=1),
                "bucket": "test",
                "object_key": "test",
                "object_version_id": "opaque-version",
                "required_legal_hold": False,
            },
            sa.text(
                """
                INSERT INTO retention_operation_target
                    (operation_id, bucket, object_key, object_version_id,
                     required_legal_hold, blob_sha256)
                VALUES
                    (:operation_id, :bucket, :object_key, :object_version_id,
                     :required_legal_hold, :blob_sha256)
                """
            ),
        ),
        (
            "worm_hold_release_operation",
            {
                "org_id": uuid.UUID(int=2),
                "record_id": uuid.UUID(int=3),
                "object_version_id": "opaque-version",
                "initiated_by_user_id": uuid.UUID(int=4),
                "idempotency_key": "test",
                "normalized_release_basis": "test",
                "canonical_bytes": b"test",
                "canonical_sha256": "a" * 64,
                "owner_snapshot_sha256": "b" * 64,
            },
            sa.text(
                """
                INSERT INTO worm_hold_release_operation
                    (org_id, record_id, object_version_id, initiated_by_user_id,
                     idempotency_key, normalized_release_basis, canonical_bytes,
                     canonical_sha256, owner_snapshot_sha256, blob_sha256)
                VALUES
                    (:org_id, :record_id, :object_version_id, :initiated_by_user_id,
                     :idempotency_key, :normalized_release_basis, :canonical_bytes,
                     :canonical_sha256, :owner_snapshot_sha256, :blob_sha256)
                """
            ),
        ),
    ),
)
def test_authority_blob_sha256_columns_are_exact_lowercase_sha256(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
    table_name: str,
    required_values: dict[str, object],
    statement: sa.TextClause,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        command.upgrade(_config(), _REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                column = connection.execute(
                    sa.text(
                        """
                        SELECT data_type, character_maximum_length
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = :table_name
                          AND column_name = 'blob_sha256'
                        """
                    ),
                    {"table_name": table_name},
                ).one()
                assert column == ("character", 64)
                blob_fk = next(
                    foreign_key
                    for foreign_key in sa.inspect(connection).get_foreign_keys(table_name)
                    if foreign_key["constrained_columns"] == ["blob_sha256"]
                )
                assert blob_fk["referred_table"] == "blob"
                assert blob_fk["referred_columns"] == ["sha256"]

            with engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                connection.execute(
                    statement,
                    {**required_values, "blob_sha256": "c" * 64},
                )

            for invalid_sha256 in ("d" * 63, "D" * 64, "g" * 64):
                with pytest.raises(sa.exc.IntegrityError):
                    with engine.begin() as connection:
                        connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                        connection.execute(
                            statement,
                            {**required_values, "blob_sha256": invalid_sha256},
                        )
        finally:
            engine.dispose()


def test_recovery_witness_requires_distinct_nonblank_generation_identity(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        command.upgrade(_config(), _REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                column = connection.execute(
                    sa.text(
                        """
                        SELECT data_type, character_maximum_length, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'recovery_generation_witness'
                          AND column_name = 'generation_identity'
                        """
                    )
                ).one()
                assert column == ("character varying", 255, "NO")

            values = {
                "id": uuid.uuid4(),
                "schema_version": 1,
                "key_id": uuid.uuid4(),
                "witness_nonce": "a" * 43,
                "request_id": uuid.uuid4(),
                "manifest_sha256": "b" * 64,
                "generation_id": "internal-generation-id",
                "generation_identity": "opaque-external-generation-identity",
                "excluded_set_sha256": "c" * 64,
                "result": "VERIFIED",
                "canonical_bytes": b"test",
                "signature": b"test",
            }
            statement = sa.text(
                """
                INSERT INTO recovery_generation_witness
                    (id, schema_version, key_id, witness_nonce, request_id,
                     manifest_sha256, generation_id, generation_identity,
                     excluded_set_sha256, result, canonical_bytes, signature,
                     issued_at, verified_at)
                VALUES
                    (:id, :schema_version, :key_id, :witness_nonce, :request_id,
                     :manifest_sha256, :generation_id, :generation_identity,
                     :excluded_set_sha256, :result, :canonical_bytes, :signature,
                     now(), now())
                """
            )
            with engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                connection.execute(statement, values)

            unexpectedly_accepted: list[str] = []
            for case_number, blank_identity in enumerate(
                ("", "   ", "\t", "\n", "\r", " \t\r\n "), start=1
            ):
                try:
                    with engine.begin() as connection:
                        connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                        connection.execute(
                            statement,
                            {
                                **values,
                                "id": uuid.uuid4(),
                                "witness_nonce": uuid.uuid4().hex + "a" * 11,
                                "request_id": uuid.uuid4(),
                                "manifest_sha256": f"{case_number:064x}",
                                "generation_id": f"internal-generation-{case_number}",
                                "generation_identity": blank_identity,
                            },
                        )
                except sa.exc.IntegrityError as error:
                    assert (
                        error.orig.diag.constraint_name
                        == "ck_recovery_generation_witness_generation_identity_nonblank"
                    )
                    continue
                unexpectedly_accepted.append(repr(blank_identity))
            assert unexpectedly_accepted == []
        finally:
            engine.dispose()


def test_r27_attestation_audience_is_a_nonempty_nonblank_string_array(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        command.upgrade(_config(), _REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                column = connection.execute(
                    sa.text(
                        """
                        SELECT data_type, udt_name, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'r27_attestation'
                          AND column_name = 'audience'
                        """
                    )
                ).one()
                assert column == ("jsonb", "jsonb", "NO")

            statement = sa.text(
                """
                INSERT INTO r27_attestation
                    (id, challenge_id, request_id, action, canonical_bytes,
                     canonical_sha256, authorizer_key_id, signature, app_user_id,
                     issuer, subject, session_id, token_jti, audience,
                     authorized_party, acr, auth_time, amr, permission_granted,
                     issued_at, expires_at)
                VALUES
                    (:id, :challenge_id, :request_id, 'REQUEST', :canonical_bytes,
                     :canonical_sha256, :authorizer_key_id, :signature, :app_user_id,
                     'issuer', 'subject', 'session', 'jti', CAST(:audience AS jsonb),
                     'client', 'acr', now(), '["pwd"]'::jsonb, true,
                     now(), now() + interval '1 minute')
                RETURNING audience
                """
            )
            values = {
                "id": uuid.uuid4(),
                "challenge_id": uuid.uuid4(),
                "request_id": uuid.uuid4(),
                "canonical_bytes": b"test",
                "canonical_sha256": "a" * 64,
                "authorizer_key_id": uuid.uuid4(),
                "signature": b"test",
                "app_user_id": uuid.uuid4(),
            }
            valid_audience = ["api://records", "api://recovery"]
            with engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                stored_audience = connection.execute(
                    statement,
                    {**values, "audience": json.dumps(valid_audience)},
                ).scalar_one()
                assert stored_audience == valid_audience

            invalid_audiences: tuple[object, ...] = (
                "api://records",
                [],
                ["api://records", 7],
                ["api://records", ""],
                ["api://records", "   "],
            )
            for invalid_audience in invalid_audiences:
                with pytest.raises(sa.exc.IntegrityError):
                    with engine.begin() as connection:
                        connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                        connection.execute(
                            statement,
                            {
                                **values,
                                "id": uuid.uuid4(),
                                "challenge_id": uuid.uuid4(),
                                "request_id": uuid.uuid4(),
                                "audience": json.dumps(invalid_audience),
                            },
                        )
        finally:
            engine.dispose()


def test_hold_release_authorization_trigger_enforces_exact_authority(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        command.upgrade(_config(), _REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                valid_operation_id, valid_digest = _create_hold_release_operation(connection)
            with engine.begin() as connection:
                _authorize_hold_release(connection, valid_operation_id, valid_digest)
                assert (
                    connection.execute(
                        sa.text(
                            "SELECT state::text FROM worm_hold_release_operation WHERE id = :id"
                        ),
                        {"id": valid_operation_id},
                    ).scalar_one()
                    == "AUTHORIZED"
                )

            refusal_cases = (
                ("wrong_digest", {}, "c" * 64),
                ("non_pending", {"state": "FAILED"}, None),
                ("logical_hold", {"legal_hold": True}, None),
                ("permanent_duration", {"duration": "PERMANENT"}, None),
                ("permanent_worm_period", {"worm_lock_period": "PERMANENT"}, None),
            )
            for _case_name, operation_values, authorization_digest in refusal_cases:
                with engine.begin() as connection:
                    operation_id, digest = _create_hold_release_operation(
                        connection, **operation_values
                    )
                with pytest.raises(sa.exc.DBAPIError, match="hold_release_authorization_refused"):
                    with engine.begin() as connection:
                        _authorize_hold_release(
                            connection,
                            operation_id,
                            authorization_digest or digest,
                        )

            with pytest.raises(sa.exc.DBAPIError, match="hold_release_authorization_refused"):
                with engine.begin() as connection:
                    _authorize_hold_release(connection, valid_operation_id, valid_digest)

            with pytest.raises(
                sa.exc.DBAPIError,
                match="worm_hold_release_authorization_is_immutable",
            ):
                with engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            """
                            UPDATE worm_hold_release_authorization
                            SET host_operator_identity = 'changed'
                            WHERE operation_id = :operation_id
                            """
                        ),
                        {"operation_id": valid_operation_id},
                    )

            with pytest.raises(
                sa.exc.DBAPIError,
                match="worm_hold_release_authorization_is_immutable",
            ):
                with engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            "DELETE FROM worm_hold_release_authorization "
                            "WHERE operation_id = :operation_id"
                        ),
                        {"operation_id": valid_operation_id},
                    )
        finally:
            engine.dispose()


@pytest.mark.parametrize(("legacy_kind", "seed"), _LEGACY_SEEDS, ids=lambda value: str(value))
def test_legacy_physical_owner_state_refuses_upgrade_without_schema_change(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
    legacy_kind: str,
    seed: Callable[[sa.Connection], None],
) -> None:
    del legacy_kind
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _BASE_REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
                seed(connection)
            with engine.connect() as connection:
                before = _schema_signature(connection)

            with pytest.raises(Exception, match=_LEGACY_REFUSAL):
                command.upgrade(config, _REVISION)

            with engine.connect() as connection:
                assert _revision(connection) == _BASE_REVISION
                assert _schema_signature(connection) == before
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    ("populated_kind", "seed"),
    _POPULATED_0089_SEEDS,
    ids=lambda value: str(value),
)
def test_populated_0089_refuses_downgrade_without_schema_change(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
    populated_kind: str,
    seed: Callable[[sa.Connection], None],
) -> None:
    del populated_kind
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.begin() as connection:
                seed(connection)
            with engine.connect() as connection:
                before = _schema_signature(connection)

            with pytest.raises(Exception, match=_DOWNGRADE_REFUSAL):
                command.downgrade(config, _BASE_REVISION)

            with engine.connect() as connection:
                assert _revision(connection) == _REVISION
                assert _schema_signature(connection) == before
        finally:
            engine.dispose()


def test_empty_0089_downgrades_and_reupgrades(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _REVISION)
        command.downgrade(config, _BASE_REVISION)
        command.upgrade(config, _REVISION)

        engine = sa.create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                assert _revision(connection) == _REVISION
                assert "r27_request" in sa.inspect(connection).get_table_names()
                assert "worm_destroy_request" not in sa.inspect(connection).get_table_names()
        finally:
            engine.dispose()
