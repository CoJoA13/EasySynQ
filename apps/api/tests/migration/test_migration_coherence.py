"""Populated-database regressions for migration/ORM coherence.

The ordinary migration gate starts from an empty database, so seed downgrades and operational
upgrade repairs can false-pass. This module creates a separate scratch database on the explicitly
provided disposable PostgreSQL server (or its own testcontainer locally) and drives the historical
boundaries with live dependent rows.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
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

_M9_REVISION = "0079_migration_orm_coherence"
_M10_REVISION = "0080_schema_index_design"
_PURGE_AUTHORITY_REVISION = "0081_pending_purge_authority"
_PACK_ERASURE_REVISION = "0082_pack_legal_erasure"
_PACK_PRINCIPAL_REVISION = "0083_pack_build_principal"
# Clause 7 has exactly 17 seeded rows: the 13 flipped by 0084 + the 4-row 7.5 subtree.
_CLAUSE7_ROW_COUNT = 17
_CANONICAL_CHECK = "ck_process_edge_no_self_loop"
_LEGACY_CHECK = "ck_process_edge_ck_process_edge_no_self_loop"
_RECORD_SOURCE_DOCUMENT_INDEX = "ix_record_source_document_id"
_ROLE_ASSIGNMENT_USER_INDEX = "ix_role_assignment_user_id"
_RECORD_PAGE_INDEX = "ix_record_org_id_captured_at_id_desc"
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
    # Alembic's INI logging setup mutates process-global logger state. These migrations run inside
    # pytest, so use the same explicit script-only configuration as the integration fixture.
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


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


def _index_definition(connection: sa.Connection, index_name: str) -> str | None:
    return connection.execute(
        sa.text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :index_name"
        ),
        {"index_name": index_name},
    ).scalar_one_or_none()


def _clause_phases(connection: sa.Connection, framework_id: object) -> dict[str, str]:
    return {
        str(number): str(phase)
        for number, phase in connection.execute(
            sa.text(
                "SELECT number, pdca_phase::text FROM clause "
                "WHERE framework_id = :framework "
                "AND (number = '7' OR number LIKE '7.%' OR number IN ('6.2', '8'))"
            ),
            {"framework": framework_id},
        )
    }


def _clause7_intent(connection: sa.Connection, framework_id: object) -> str:
    return str(
        connection.execute(
            sa.text(
                "SELECT intent_text FROM clause WHERE framework_id = :framework AND number = '7'"
            ),
            {"framework": framework_id},
        ).scalar_one()
    )


def test_populated_historical_transitions_and_head_repairs(
    postgres_admin_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_logger = logging.getLogger("easysynq.migration_isolation_probe")
    application_logger.disabled = False
    with _scratch_database(postgres_admin_url) as scratch_url:
        monkeypatch.setenv("DATABASE_URL", scratch_url)
        monkeypatch.setenv("DATABASE_URL_SYNC", scratch_url)
        get_settings.cache_clear()
        config = _config()
        engine = sa.create_engine(scratch_url)
        try:
            # 0004: role assignments and permission overrides must preserve their full FK closure.
            command.upgrade(config, "0004_seed_authz")
            assert application_logger.disabled is False
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

            # 0081: pre-existing purge work cannot be reconstructed after its evidence links were
            # deleted. Preserve it as explicitly unbound legacy work; never promote its old bypass
            # bit into authority.
            legacy_purge_id = uuid.uuid4()
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO pending_blob_purge "
                        "(id, org_id, sha256, bucket, object_key, bypass_governance) "
                        "VALUES (:id, :org, :sha256, 'records', :object_key, true)"
                    ),
                    {
                        "id": legacy_purge_id,
                        "org": org_id,
                        "sha256": "legacy-" + uuid.uuid4().hex,
                        "object_key": uuid.uuid4().hex,
                    },
                )
            command.upgrade(config, _PURGE_AUTHORITY_REVISION)
            with engine.connect() as connection:
                legacy = connection.execute(
                    sa.text(
                        "SELECT authority_bound, record_id, disposition_event_id, "
                        "worm_destroy_request_id, bypass_governance "
                        "FROM pending_blob_purge WHERE id = :id"
                    ),
                    {"id": legacy_purge_id},
                ).one()
                assert legacy == (False, None, None, None, True)

            # 0082: a legally erased pack must survive downgrade as an undeliverable terminal
            # SEALED tombstone. FAILED is retryable in the pre-0082 application and would strand
            # the row in a BUILDING/reaper loop. The downgrade must also refuse while derived
            # purge authority or an immutable PACK_INVALIDATED event would be lost.
            command.upgrade(config, _PACK_ERASURE_REVISION)
            pack_record_id = uuid.uuid4()
            source_event_id = uuid.uuid4()
            invalidated_pack_id = uuid.uuid4()
            draft_pack_id = uuid.uuid4()
            failed_pack_id = uuid.uuid4()
            with engine.begin() as connection:
                requester_id = connection.execute(
                    sa.text(
                        "INSERT INTO app_user (org_id, keycloak_subject, display_name) "
                        "VALUES (:org, 'm361-requester', 'M361 Requester') RETURNING id"
                    ),
                    {"org": org_id},
                ).scalar_one()
                retention_policy_id = connection.execute(
                    sa.text(
                        "SELECT id FROM retention_policy WHERE org_id = :org ORDER BY created_at "
                        "LIMIT 1"
                    ),
                    {"org": org_id},
                ).scalar_one()
                connection.execute(
                    sa.text(
                        "INSERT INTO documented_information "
                        "(id, org_id, framework_id, kind, identifier, title, owner_user_id, "
                        "current_state, is_singleton, classification, acknowledgement_required, "
                        "created_by) "
                        "VALUES (:id, :org, :framework, 'RECORD', 'M361-PACK-RECORD', "
                        "'M361 invalidated pack record', :user, 'Draft', false, 'Internal', "
                        "false, :user)"
                    ),
                    {
                        "id": pack_record_id,
                        "org": org_id,
                        "framework": framework_id,
                        "user": user_id,
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO record "
                        "(id, org_id, record_type, captured_by, content_hash_version, "
                        "retention_policy_id, disposition_state, legal_hold) "
                        "VALUES (:id, :org, 'EVIDENCE', :user, 2, :policy, 'DISPOSED', false)"
                    ),
                    {
                        "id": pack_record_id,
                        "org": org_id,
                        "user": user_id,
                        "policy": retention_policy_id,
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO disposition_event "
                        "(id, org_id, record_id, action, tombstone, approved_by, requested_by, "
                        "is_worm_destroy, legal_basis) "
                        "VALUES (:id, :org, :record, 'DESTROY', true, :approver, :requester, "
                        "true, 'M361 migration proof')"
                    ),
                    {
                        "id": source_event_id,
                        "org": org_id,
                        "record": pack_record_id,
                        "approver": user_id,
                        "requester": requester_id,
                    },
                )

            with pytest.raises(sa.exc.IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            "INSERT INTO evidence_pack "
                            "(id, org_id, framework_id, title, scope_kind, scope_selector, "
                            "status, created_by, invalidated_at) "
                            "VALUES (:id, :org, :framework, 'M361 partial provenance', 'PROCESS', "
                            "CAST('{\"process_ids\": []}' AS jsonb), 'SEALED', :user, now())"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "org": org_id,
                            "framework": framework_id,
                            "user": user_id,
                        },
                    )

            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO evidence_pack "
                        "(id, org_id, framework_id, title, scope_kind, scope_selector, status, "
                        "pack_record_id, created_by, invalidated_at, "
                        "invalidated_by_disposition_event_id) "
                        "VALUES (:id, :org, :framework, 'M361 invalidated pack', 'PROCESS', "
                        "CAST('{\"process_ids\": []}' AS jsonb), 'UNAVAILABLE', :record, :user, "
                        "now(), :event)"
                    ),
                    {
                        "id": invalidated_pack_id,
                        "org": org_id,
                        "framework": framework_id,
                        "record": pack_record_id,
                        "user": user_id,
                        "event": source_event_id,
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO evidence_pack "
                        "(id, org_id, framework_id, title, scope_kind, scope_selector, status, "
                        "created_by) "
                        "VALUES (:draft_id, :org, :framework, 'M363 draft pack', 'PROCESS', "
                        "CAST('{\"process_ids\": []}' AS jsonb), 'DRAFT', :user), "
                        "(:failed_id, :org, :framework, 'M363 failed pack', 'PROCESS', "
                        "CAST('{\"process_ids\": []}' AS jsonb), 'FAILED', :user)"
                    ),
                    {
                        "draft_id": draft_pack_id,
                        "failed_id": failed_pack_id,
                        "org": org_id,
                        "framework": framework_id,
                        "user": user_id,
                    },
                )

            derived_event_id = uuid.uuid4()
            derived_purge_id = uuid.uuid4()
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO disposition_event "
                        "(id, org_id, record_id, action, tombstone, approved_by, requested_by, "
                        "is_worm_destroy, legal_basis, derived_from_disposition_event_id) "
                        "VALUES (:id, :org, :record, 'DESTROY', true, :approver, :requester, "
                        "true, 'M361 derived migration proof', :source_event)"
                    ),
                    {
                        "id": derived_event_id,
                        "org": org_id,
                        "record": pack_record_id,
                        "approver": user_id,
                        "requester": requester_id,
                        "source_event": source_event_id,
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO pending_blob_purge "
                        "(id, org_id, sha256, bucket, object_key, bypass_governance, record_id, "
                        "disposition_event_id) "
                        "VALUES (:id, :org, :sha, 'records', :key, false, :record, :event)"
                    ),
                    {
                        "id": derived_purge_id,
                        "org": org_id,
                        "sha": "derived-" + uuid.uuid4().hex,
                        "key": uuid.uuid4().hex,
                        "record": pack_record_id,
                        "event": derived_event_id,
                    },
                )
            with pytest.raises(sa.exc.DBAPIError, match="derived pending blob purges"):
                command.downgrade(config, _PURGE_AUTHORITY_REVISION)
            with engine.begin() as connection:
                connection.execute(
                    sa.text("DELETE FROM pending_blob_purge WHERE id = :id"),
                    {"id": derived_purge_id},
                )
                connection.execute(
                    sa.text("DELETE FROM disposition_event WHERE id = :id"),
                    {"id": derived_event_id},
                )

            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO audit_event "
                        "(org_id, occurred_at, actor_id, actor_type, event_type, object_type, "
                        "object_id) "
                        "VALUES (:org, now(), :actor, 'user', 'PACK_INVALIDATED', "
                        "'evidence_pack', :object_id)"
                    ),
                    {
                        "org": org_id,
                        "actor": user_id,
                        "object_id": invalidated_pack_id,
                    },
                )
            with pytest.raises(sa.exc.DBAPIError, match="PACK_INVALIDATED audit events"):
                command.downgrade(config, _PURGE_AUTHORITY_REVISION)
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "DELETE FROM audit_event "
                        "WHERE event_type::text = 'PACK_INVALIDATED' AND object_id = :object_id"
                    ),
                    {"object_id": invalidated_pack_id},
                )

            command.downgrade(config, _PURGE_AUTHORITY_REVISION)
            with engine.connect() as connection:
                downgraded = connection.execute(
                    sa.text("SELECT status::text, error FROM evidence_pack WHERE id = :id"),
                    {"id": invalidated_pack_id},
                ).one()
                assert downgraded[0] == "SEALED"
                assert "legal erasure" in downgraded[1]
            command.upgrade(config, "head")
            with engine.connect() as connection:
                reupgraded = connection.execute(
                    sa.text(
                        "SELECT status::text, invalidated_at, "
                        "invalidated_by_disposition_event_id "
                        "FROM evidence_pack WHERE id = :id"
                    ),
                    {"id": invalidated_pack_id},
                ).one()
                assert reupgraded == ("SEALED", None, None)
                # 0083 backfills already-started/terminal attempts with their only historical
                # principal, but leaves never-generated drafts empty. Future attempts overwrite
                # this compatibility value with the actual generate caller.
                principals = {
                    row.id: row.build_requested_by
                    for row in connection.execute(
                        sa.text(
                            "SELECT id, build_requested_by FROM evidence_pack "
                            "WHERE id IN (:draft_id, :failed_id)"
                        ),
                        {"draft_id": draft_pack_id, "failed_id": failed_pack_id},
                    )
                }
                assert principals == {
                    draft_pack_id: None,
                    failed_pack_id: user_id,
                }
                assert (
                    connection.execute(
                        sa.text(
                            "SELECT udt_name FROM information_schema.columns "
                            "WHERE table_schema = current_schema() "
                            "AND table_name = 'evidence_pack' "
                            "AND column_name = 'build_source_ip'"
                        )
                    ).scalar_one()
                    == "text"
                )
                expanded_ipv6 = "0:0:0:0:0:0:0:1"
                connection.execute(
                    sa.text("UPDATE evidence_pack SET build_source_ip = :source_ip WHERE id = :id"),
                    {"source_ip": expanded_ipv6, "id": failed_pack_id},
                )
                assert (
                    connection.execute(
                        sa.text("SELECT build_source_ip FROM evidence_pack WHERE id = :id"),
                        {"id": failed_pack_id},
                    ).scalar_one()
                    == expanded_ipv6
                )
                assert (
                    connection.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_constraint "
                            "WHERE conrelid = 'evidence_pack'::regclass "
                            "AND conname = "
                            "'fk_evidence_pack_build_requested_by_app_user'"
                        )
                    ).scalar_one()
                    == 1
                )

            # The new attempt context is intentionally ephemeral migration state: downgrade drops
            # both columns cleanly, and re-upgrade deterministically reconstructs historical
            # non-DRAFT principals without inventing source IPs.
            command.downgrade(config, _PACK_ERASURE_REVISION)
            with engine.connect() as connection:
                assert _column_nullable(connection, "evidence_pack", "build_requested_by") is None
                assert _column_nullable(connection, "evidence_pack", "build_source_ip") is None
            command.upgrade(config, _PACK_PRINCIPAL_REVISION)
            with engine.connect() as connection:
                rebuilt = connection.execute(
                    sa.text(
                        "SELECT build_requested_by, build_source_ip "
                        "FROM evidence_pack WHERE id = :id"
                    ),
                    {"id": failed_pack_id},
                ).one()
                assert rebuilt == (user_id, None)

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
                assert (
                    connection.execute(
                        sa.text("SELECT authority_bound FROM pending_blob_purge WHERE id = :id"),
                        {"id": legacy_purge_id},
                    ).scalar_one()
                    is False
                )

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

            # 0084: clause 7 moves wholly to DO (R62). A fresh chain seeds the NEW catalog via
            # 0018, so only the downgrade->upgrade cycle exercises the populated flip — run it
            # with a live clause_mapping on 7.2 in place to prove no dependent-row interference.
            with engine.begin() as connection:
                clause_72_id = connection.execute(
                    sa.text(
                        "SELECT id FROM clause WHERE framework_id = :framework AND number = '7.2'"
                    ),
                    {"framework": framework_id},
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
                        "clause": clause_72_id,
                        "document": document_id,
                        "user": user_id,
                    },
                )
            with engine.connect() as connection:
                phases = _clause_phases(connection, framework_id)
                assert phases["7"] == "DO"  # fresh seed already carries the R62 catalog
                assert phases["7.2"] == "DO"
            command.downgrade(config, _PACK_PRINCIPAL_REVISION)
            with engine.connect() as connection:
                phases = _clause_phases(connection, framework_id)
                # Structural, not a spot set: an _FLIPPED omission in 0084 is otherwise
                # CI-blind (fresh chains seed DO via 0018, so head state still looks right).
                sevens = {n: p for n, p in phases.items() if n == "7" or n.startswith("7.")}
                assert len(sevens) == _CLAUSE7_ROW_COUNT
                for number, phase in sevens.items():
                    expected = "DO" if number == "7.5" or number.startswith("7.5.") else "PLAN"
                    assert phase == expected, number  # historical split restored exactly
                assert phases["8"] == "DO"
                assert "split across PLAN" in _clause7_intent(connection, framework_id)
            command.upgrade(config, "head")
            with engine.connect() as connection:
                phases = _clause_phases(connection, framework_id)
                sevens = {n: p for n, p in phases.items() if n == "7" or n.startswith("7.")}
                assert len(sevens) == _CLAUSE7_ROW_COUNT
                for number, phase in sevens.items():
                    assert phase == "DO", number  # the whole clause-7 tree is DO (R62)
                assert phases["6.2"] == "PLAN"  # neighbors untouched
                assert "split across PLAN" not in _clause7_intent(connection, framework_id)

            # 0086 adds only the candidate-page index. Its downgrade is data-safe on this
            # populated record table, and re-upgrade restores the exact descending keyset shape.
            with engine.connect() as connection:
                head_indexes = _table_indexes(connection, "record")
                assert _RECORD_PAGE_INDEX in head_indexes
                index_definition = _index_definition(connection, _RECORD_PAGE_INDEX)
                assert index_definition is not None
                org_position = index_definition.index("org_id")
                captured_position = index_definition.index("captured_at DESC")
                id_position = index_definition.index("id DESC", captured_position)
                assert org_position < captured_position < id_position
            command.downgrade(config, "0085_user_credential_issued")
            with engine.connect() as connection:
                downgraded_indexes = _table_indexes(connection, "record")
                assert head_indexes - downgraded_indexes == {_RECORD_PAGE_INDEX}
                assert downgraded_indexes == head_indexes - {_RECORD_PAGE_INDEX}
                assert _index_definition(connection, _RECORD_PAGE_INDEX) is None
            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert _table_indexes(connection, "record") == head_indexes
                assert _index_definition(connection, _RECORD_PAGE_INDEX) == index_definition
            command.check(config)
        finally:
            engine.dispose()
            get_settings.cache_clear()
