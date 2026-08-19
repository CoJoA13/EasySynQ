"""Isolated PostgreSQL proofs for the clean-only 0089 schema boundary."""

from __future__ import annotations

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


def _seed_legacy_pack_pointer(connection: sa.Connection) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO evidence_pack
                (id, org_id, framework_id, title, scope_kind, scope_selector,
                 zip_blob_sha256, created_by)
            VALUES
                (:id, :org_id, :framework_id, 'legacy', 'CLAUSE', '{}'::jsonb,
                 :sha256, :created_by)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
            "framework_id": uuid.uuid4(),
            "sha256": "5" * 64,
            "created_by": uuid.uuid4(),
        },
    )


_LEGACY_SEEDS: tuple[tuple[str, Callable[[sa.Connection], None]], ...] = (
    ("blob", _seed_legacy_blob),
    ("document_version", _seed_legacy_document_version),
    ("evidence_blob", _seed_legacy_evidence_blob),
    ("pending_blob_purge", _seed_legacy_pending_purge),
    ("worm_destroy_request", _seed_legacy_worm_request),
    ("pack_physical_pointer", _seed_legacy_pack_pointer),
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


def test_populated_0089_refuses_downgrade_without_schema_change(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        _point_at(monkeypatch, scratch_url)
        config = _config()
        command.upgrade(config, _REVISION)
        engine = sa.create_engine(scratch_url)
        try:
            with engine.begin() as connection:
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
