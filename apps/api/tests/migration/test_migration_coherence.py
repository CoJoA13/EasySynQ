"""Populated-database regressions for migration/ORM coherence.

The ordinary migration gate starts from an empty database, so seed downgrades and operational
upgrade repairs can false-pass. This module creates a separate scratch database on the explicitly
provided disposable PostgreSQL server (or its own testcontainer locally) and drives the historical
boundaries with live dependent rows.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from testcontainers.postgres import PostgresContainer

from easysynq_api.config import get_settings
from easysynq_api.services.backup.dsn import conn_kwargs

_API_ROOT = Path(__file__).resolve().parents[2]
_M9_REVISION = "0079_migration_orm_coherence"
_M10_REVISION = "0080_schema_index_design"
_CANONICAL_CHECK = "ck_process_edge_no_self_loop"
_LEGACY_CHECK = "ck_process_edge_ck_process_edge_no_self_loop"
_RECORD_SOURCE_DOCUMENT_INDEX = "ix_record_source_document_id"
_ROLE_ASSIGNMENT_USER_INDEX = "ix_role_assignment_user_id"
_EXPECTED_RETENTION_GRANTS = {
    ("Internal Auditor", "retention.read"),
    ("QMS Owner", "retention.manage"),
    ("QMS Owner", "retention.read"),
}


@pytest.fixture(scope="module")
def postgres_admin_url() -> Iterator[str]:
    """Use only an explicitly disposable server; never infer a developer/production DSN."""
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
    database = f"easysynq_migrations_{uuid.uuid4().hex[:12]}"
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
    return Config(str(_API_ROOT / "alembic.ini"))


def _retention_grants(connection: sa.Connection) -> set[tuple[str, str]]:
    return {
        (str(role_name), str(permission_key))
        for role_name, permission_key in connection.execute(
            sa.text(
                "SELECT r.name, p.key "
                "FROM role_grant rg "
                "JOIN role r ON r.id = rg.role_id "
                "JOIN permission p ON p.id = rg.permission_id "
                "WHERE (r.name = 'QMS Owner' "
                "AND p.key IN ('retention.read', 'retention.manage')) "
                "OR (r.name = 'Internal Auditor' AND p.key = 'retention.read')"
            )
        )
    }


def _process_edge_checks(connection: sa.Connection) -> set[str]:
    return {
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'process_edge'::regclass AND contype = 'c'"
            )
        ).scalars()
    }


def _column_nullable(
    connection: sa.Connection,
    table_name: str,
    column_name: str,
) -> str | None:
    return connection.execute(
        sa.text(
            "SELECT is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table_name "
            "AND column_name = :column_name"
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar_one_or_none()


def _table_indexes(connection: sa.Connection, table_name: str) -> set[str]:
    return {
        str(name)
        for name in connection.execute(
            sa.text(
                "SELECT indexname "
                "FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = :table_name"
            ),
            {"table_name": table_name},
        ).scalars()
    }


def test_populated_historical_transitions_and_head_repairs(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _scratch_database(postgres_admin_url) as scratch_url:
        monkeypatch.setenv("DATABASE_URL", scratch_url)
        monkeypatch.setenv("DATABASE_URL_SYNC", scratch_url)
        get_settings.cache_clear()
        config = _config()
        engine = sa.create_engine(scratch_url)
        try:
            # 0004: role assignments and permission overrides must preserve their full FK closure.
            command.upgrade(config, "0004_seed_authz")
            with engine.begin() as connection:
                org_id = connection.execute(sa.text("SELECT id FROM organization")).scalar_one()
                user_id = connection.execute(
                    sa.text(
                        "INSERT INTO app_user (org_id, keycloak_subject, display_name) "
                        "VALUES (:org, 'm9-user', 'M9 User') RETURNING id"
                    ),
                    {"org": org_id},
                ).scalar_one()
                role_id = connection.execute(
                    sa.text("SELECT id FROM role WHERE org_id = :org AND name = 'QMS Owner'"),
                    {"org": org_id},
                ).scalar_one()
                permission_id = connection.execute(
                    sa.text("SELECT id FROM permission WHERE key = 'document.read'")
                ).scalar_one()
                scope_id = connection.execute(
                    sa.text(
                        "INSERT INTO scope (org_id, level) VALUES (:org, 'SYSTEM') RETURNING id"
                    ),
                    {"org": org_id},
                ).scalar_one()
                connection.execute(
                    sa.text(
                        "INSERT INTO role_assignment (org_id, user_id, role_id) "
                        "VALUES (:org, :user, :role)"
                    ),
                    {"org": org_id, "user": user_id, "role": role_id},
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO permission_override "
                        "(org_id, user_id, permission_id, effect, scope_id) "
                        "VALUES (:org, :user, :permission, 'ALLOW', :scope)"
                    ),
                    {
                        "org": org_id,
                        "user": user_id,
                        "permission": permission_id,
                        "scope": scope_id,
                    },
                )

            command.downgrade(config, "0003_authz")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM role WHERE id = :role"), {"role": role_id}
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM role_assignment WHERE role_id = :role"),
                        {"role": role_id},
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM role_grant WHERE role_id = :role"),
                        {"role": role_id},
                    ).scalar_one()
                    > 0
                )
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM permission WHERE id = :permission"),
                        {"permission": permission_id},
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        sa.text(
                            "SELECT count(*) FROM permission_override "
                            "WHERE permission_id = :permission"
                        ),
                        {"permission": permission_id},
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM role WHERE name = 'Approver'")
                    ).scalar_one()
                    == 0
                )

            # 0018: a mapped ISO clause survives its seed downgrade while unreferenced rows go.
            command.upgrade(config, "0018_seed_clauses")
            with engine.begin() as connection:
                framework_id = connection.execute(
                    sa.text("SELECT id FROM framework WHERE code = 'iso9001:2015'")
                ).scalar_one()
                clause_id = connection.execute(
                    sa.text(
                        "SELECT id FROM clause WHERE framework_id = :framework AND number = '4.1'"
                    ),
                    {"framework": framework_id},
                ).scalar_one()
                document_id = connection.execute(
                    sa.text(
                        "INSERT INTO documented_information "
                        "(org_id, framework_id, kind, identifier, title, "
                        "owner_user_id, created_by) "
                        "VALUES (:org, :framework, 'DOCUMENT', 'M9-CLAUSE-DOC', "
                        "'M9 clause mapping', :user, :user) RETURNING id"
                    ),
                    {"org": org_id, "framework": framework_id, "user": user_id},
                ).scalar_one()
                connection.execute(
                    sa.text(
                        "INSERT INTO clause_mapping "
                        "(org_id, framework_id, clause_id, documented_information_id, created_by) "
                        "VALUES (:org, :framework, :clause, :document, :user)"
                    ),
                    {
                        "org": org_id,
                        "framework": framework_id,
                        "clause": clause_id,
                        "document": document_id,
                        "user": user_id,
                    },
                )

            command.downgrade(config, "0017_clause_ia")
            with engine.connect() as connection:
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM clause WHERE id = :clause"),
                        {"clause": clause_id},
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM clause WHERE framework_id = :framework"),
                        {"framework": framework_id},
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM clause_mapping WHERE clause_id = :clause"),
                        {"clause": clause_id},
                    ).scalar_one()
                    == 1
                )

            # 0028: grants resolve through org-scoped roles after setup renames the short code.
            command.upgrade(config, "0027_structured_forms")
            with engine.begin() as connection:
                connection.execute(
                    sa.text("UPDATE organization SET short_code = 'RENAMED' WHERE id = :org"),
                    {"org": org_id},
                )
            command.upgrade(config, "0028_retention_policy_crud")
            with engine.connect() as connection:
                assert _retention_grants(connection) == _EXPECTED_RETENTION_GRANTS

            # 0079: repair an installation that already skipped those grants and stored the doubled
            # CHECK name before the corrected historical migrations were shipped.
            command.upgrade(config, "0078_record_content_hash_version")
            with engine.begin() as connection:
                assert _CANONICAL_CHECK in _process_edge_checks(connection)
                connection.execute(
                    sa.text(
                        "DELETE FROM role_grant rg USING role r, permission p "
                        "WHERE rg.role_id = r.id AND rg.permission_id = p.id "
                        "AND ((r.name = 'QMS Owner' "
                        "AND p.key IN ('retention.read', 'retention.manage')) "
                        "OR (r.name = 'Internal Auditor' AND p.key = 'retention.read'))"
                    )
                )
                connection.execute(
                    sa.text(
                        "ALTER TABLE process_edge "
                        f"RENAME CONSTRAINT {_CANONICAL_CHECK} TO {_LEGACY_CHECK}"
                    )
                )
                assert _retention_grants(connection) == set()

            command.upgrade(config, _M9_REVISION)
            with engine.connect() as connection:
                assert _retention_grants(connection) == _EXPECTED_RETENTION_GRANTS
                check_names = _process_edge_checks(connection)
                assert _CANONICAL_CHECK in check_names
                assert _LEGACY_CHECK not in check_names

            # 0080: unsupported manual data must fail closed before the dead column is removed.
            source_blob_sha256 = "8" * 64
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO blob "
                        "(sha256, org_id, size_bytes, mime_type, bucket, object_key, "
                        "worm_locked, sse) "
                        "VALUES (:sha256, :org, 10, 'application/pdf', "
                        "'m10-vault', 'm10/source.pdf', true, true)"
                    ),
                    {"sha256": source_blob_sha256, "org": org_id},
                )
                version_id = connection.execute(
                    sa.text(
                        "INSERT INTO document_version "
                        "(org_id, document_id, version_seq, revision_label, "
                        "change_significance, change_reason, change_summary, version_state, "
                        "source_blob_sha256, metadata_snapshot, imported, author_user_id, "
                        "created_by) "
                        "VALUES (:org, :document, 1, 'A', 'MINOR', 'M10 migration proof', "
                        "'unsupported manual summary', 'Draft', :sha256, "
                        "CAST('{}' AS jsonb), false, :user, :user) "
                        "RETURNING id"
                    ),
                    {
                        "org": org_id,
                        "document": document_id,
                        "sha256": source_blob_sha256,
                        "user": user_id,
                    },
                ).scalar_one()

            with pytest.raises(sa.exc.DBAPIError, match="cannot upgrade 0080"):
                command.upgrade(config, _M10_REVISION)
            with engine.connect() as connection:
                assert _column_nullable(connection, "document_version", "change_summary") == "YES"
                assert _RECORD_SOURCE_DOCUMENT_INDEX not in _table_indexes(connection, "record")
                assert _ROLE_ASSIGNMENT_USER_INDEX not in _table_indexes(
                    connection, "role_assignment"
                )

            # Once deliberately cleared, the populated upgrade removes only the unsupported field.
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE document_version SET change_summary = NULL WHERE id = :version"
                    ),
                    {"version": version_id},
                )
            command.upgrade(config, _M10_REVISION)
            with engine.connect() as connection:
                assert _column_nullable(connection, "document_version", "change_summary") is None
                assert _RECORD_SOURCE_DOCUMENT_INDEX in _table_indexes(connection, "record")
                assert _ROLE_ASSIGNMENT_USER_INDEX in _table_indexes(connection, "role_assignment")
                assert (
                    connection.execute(
                        sa.text("SELECT count(*) FROM document_version WHERE id = :version"),
                        {"version": version_id},
                    ).scalar_one()
                    == 1
                )

            # The downgrade restores a nullable compatibility column and removes only M10's
            # indexes; a clean re-upgrade remains safe with the live version row in place.
            command.downgrade(config, _M9_REVISION)
            with engine.connect() as connection:
                assert _column_nullable(connection, "document_version", "change_summary") == "YES"
                assert _RECORD_SOURCE_DOCUMENT_INDEX not in _table_indexes(connection, "record")
                assert _ROLE_ASSIGNMENT_USER_INDEX not in _table_indexes(
                    connection, "role_assignment"
                )
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _column_nullable(connection, "document_version", "change_summary") is None
                assert _RECORD_SOURCE_DOCUMENT_INDEX in _table_indexes(connection, "record")
                assert _ROLE_ASSIGNMENT_USER_INDEX in _table_indexes(connection, "role_assignment")

            # The repair downgrade is intentionally non-destructive; re-upgrade is idempotent.
            command.downgrade(config, "0078_record_content_hash_version")
            with engine.connect() as connection:
                assert _retention_grants(connection) == _EXPECTED_RETENTION_GRANTS
                assert _CANONICAL_CHECK in _process_edge_checks(connection)
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _process_edge_checks(connection) == {_CANONICAL_CHECK}

            # Defensive branches: collapse a duplicate legacy copy and restore an absent CHECK.
            command.downgrade(config, "0078_record_content_hash_version")
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "ALTER TABLE process_edge "
                        f"ADD CONSTRAINT {_LEGACY_CHECK} "
                        "CHECK (from_process_id <> to_process_id)"
                    )
                )
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _process_edge_checks(connection) == {_CANONICAL_CHECK}

            command.downgrade(config, "0078_record_content_hash_version")
            with engine.begin() as connection:
                connection.execute(
                    sa.text(f"ALTER TABLE process_edge DROP CONSTRAINT {_CANONICAL_CHECK}")
                )
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _process_edge_checks(connection) == {_CANONICAL_CHECK}
            command.check(config)
        finally:
            engine.dispose()
            get_settings.cache_clear()
