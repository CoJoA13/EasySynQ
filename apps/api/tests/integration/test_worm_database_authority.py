"""Real PostgreSQL authority proofs for exact WORM identity."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from time import monotonic, sleep

import pytest
import sqlalchemy as sa


def _engine(database_authority_dsns: dict[str, str], role: str) -> sa.Engine:
    return sa.create_engine(database_authority_dsns[role])


def _wait_for_named_lock(engine: sa.Engine, application_name: str) -> bool:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(
                sa.text(
                    "SELECT wait_event_type='Lock' FROM pg_stat_activity "
                    "WHERE application_name=:name"
                ),
                {"name": application_name},
            ).scalar_one_or_none()
        if waiting:
            return True
        sleep(0.02)
    return False


def _insert_blob(
    connection: sa.Connection,
    *,
    sha256: str,
    worm_locked: bool,
) -> uuid.UUID:
    org_id = connection.execute(
        sa.text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
    ).scalar_one()
    if worm_locked:
        now = datetime.now(UTC)
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
                    (:sha256, :org_id, 1, 'application/octet-stream', 'test-worm',
                     :object_key, :object_version_id, true, 'GOVERNANCE', :retain_until,
                     :now, :retain_until, :now, false, :now, false)
                """
            ),
            {
                "sha256": sha256,
                "org_id": org_id,
                "object_key": sha256,
                "object_version_id": f"version-{sha256[:12]}",
                "retain_until": now + timedelta(days=30),
                "now": now,
            },
        )
    else:
        connection.execute(
            sa.text(
                """
                INSERT INTO blob
                    (sha256, org_id, size_bytes, mime_type, bucket, object_key,
                     worm_locked, sse)
                VALUES
                    (:sha256, :org_id, 1, 'application/octet-stream',
                     'test-non-worm', :object_key, false, false)
                """
            ),
            {"sha256": sha256, "org_id": org_id, "object_key": sha256},
        )
    return org_id


def test_app_cannot_update_protected_blob_identity(
    database_authority_dsns: dict[str, str],
) -> None:
    """Removing the 0010 broad Blob UPDATE grant must make this statement fail."""
    sha256 = uuid.uuid4().hex * 2
    app_engine = sa.create_engine(database_authority_dsns["easysynq_app"])
    try:
        with app_engine.begin() as connection:
            org_id = connection.execute(
                sa.text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
            ).scalar_one()
            connection.execute(
                sa.text(
                    """
                    INSERT INTO blob
                        (sha256, org_id, size_bytes, mime_type, bucket, object_key,
                         worm_locked, sse)
                    VALUES
                        (:sha256, :org_id, 1, 'application/octet-stream',
                         'test-worm', :object_key, false, false)
                    """
                ),
                {"sha256": sha256, "org_id": org_id, "object_key": sha256},
            )

        with pytest.raises(sa.exc.ProgrammingError):
            with app_engine.begin() as connection:
                connection.execute(
                    sa.text("UPDATE blob SET bucket = bucket WHERE sha256 = :sha256"),
                    {"sha256": sha256},
                )
    finally:
        app_engine.dispose()


def test_app_blob_grant_allows_complete_insert_and_only_integrity_stamp_updates(
    database_authority_dsns: dict[str, str],
) -> None:
    sha256 = uuid.uuid4().hex * 2
    app_engine = _engine(database_authority_dsns, "easysynq_app")
    try:
        with app_engine.begin() as connection:
            _insert_blob(connection, sha256=sha256, worm_locked=True)
            connection.execute(
                sa.text(
                    "UPDATE blob SET verified_at=now(), verify_failed_at=now() WHERE sha256=:sha256"
                ),
                {"sha256": sha256},
            )

        for statement in (
            "UPDATE blob SET object_version_id='substitute' WHERE sha256=:sha256",
            "UPDATE blob SET worm_locked=false WHERE sha256=:sha256",
            "DELETE FROM blob WHERE sha256=:sha256",
        ):
            with pytest.raises(sa.exc.DBAPIError):
                with app_engine.begin() as connection:
                    connection.execute(sa.text(statement), {"sha256": sha256})
    finally:
        app_engine.dispose()


@pytest.mark.parametrize(
    ("forbidden_column", "forbidden_value"),
    (
        ("purged_at", "clock_timestamp()"),
        ("purge_execution_id", ":purge_execution_id"),
        ("created_at", "clock_timestamp()"),
        ("verified_at", "clock_timestamp()"),
        ("verify_failed_at", "clock_timestamp()"),
    ),
)
def test_app_blob_insert_cannot_supply_transition_or_result_columns(
    database_authority_dsns: dict[str, str],
    forbidden_column: str,
    forbidden_value: str,
) -> None:
    sha256 = uuid.uuid4().hex * 2
    purge_execution_id: uuid.UUID | None = None
    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    try:
        if forbidden_column == "purge_execution_id":
            from tests.integration.test_ordinary_authority_transitions import (
                _seed_ordinary_owner,
            )

            request_id = uuid.uuid4()
            purge_execution_id = uuid.uuid4()
            with owner.begin() as connection:
                seed = _seed_ordinary_owner(connection)
                connection.execute(
                    sa.text(
                        "INSERT INTO r27_request "
                        "(id,org_id,record_id,normalized_legal_basis,legal_basis_sha256) "
                        "VALUES (:id,:org,:record,'forbidden Blob insert',:digest)"
                    ),
                    {
                        "id": request_id,
                        "org": seed.org_id,
                        "record": seed.record_id,
                        "digest": uuid.uuid4().hex * 2,
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO r27_execution "
                        "(id,request_id,execution_id,state,claimed_at) "
                        "VALUES (:id,:request,:public_id,'CLAIMED',clock_timestamp())"
                    ),
                    {
                        "id": purge_execution_id,
                        "request": request_id,
                        "public_id": uuid.uuid4(),
                    },
                )

        statement = sa.text(
            "INSERT INTO blob "  # noqa: S608 -- closed test parameters above
            "(sha256,org_id,size_bytes,mime_type,bucket,object_key,worm_locked,sse,"
            f"{forbidden_column}) "
            "SELECT :sha,id,1,'application/octet-stream','forbidden-insert',:sha,false,false,"
            f"{forbidden_value} FROM organization ORDER BY created_at LIMIT 1"
        )
        with pytest.raises(sa.exc.ProgrammingError) as denied:
            with app.begin() as connection:
                connection.execute(
                    statement,
                    {"sha": sha256, "purge_execution_id": purge_execution_id},
                )
        assert denied.value.orig.sqlstate == "42501"

        with owner.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": sha256},
            ).scalar_one()
    finally:
        owner.dispose()
        app.dispose()


def test_blob_rejects_purge_execution_without_physical_absence_timestamp(
    database_authority_dsns: dict[str, str],
) -> None:
    from tests.integration.test_r27_authority_transitions import _seed_source_execution

    owner = _engine(database_authority_dsns, "owner")
    sha256 = uuid.uuid4().hex * 2
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        with pytest.raises(sa.exc.IntegrityError, match="purge_provenance_shape"):
            with owner.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO blob
                            (sha256,org_id,size_bytes,mime_type,bucket,object_key,
                             worm_locked,purge_execution_id,sse)
                        VALUES (:sha,:org,1,'application/octet-stream','invalid-purge-shape',
                                :sha,false,:execution,false)
                        """
                    ),
                    {
                        "sha": sha256,
                        "org": source.actors.org_id,
                        "execution": source.internal_execution_id,
                    },
                )
        with owner.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM blob WHERE sha256=:sha)"),
                {"sha": sha256},
            ).scalar_one()
    finally:
        owner.dispose()


def test_blob_model_declares_purge_provenance_shape() -> None:
    from easysynq_api.db.models.blob import Blob

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Blob.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert constraints["ck_blob_purge_provenance_shape"] == (
        "purge_execution_id IS NULL OR purged_at IS NOT NULL"
    )


def test_non_worm_blob_without_owner_can_be_deleted_but_cannot_be_converted_by_update(
    database_authority_dsns: dict[str, str],
) -> None:
    delete_sha = uuid.uuid4().hex * 2
    convert_sha = uuid.uuid4().hex * 2
    app_engine = _engine(database_authority_dsns, "easysynq_app")
    owner_engine = _engine(database_authority_dsns, "owner")
    try:
        with app_engine.begin() as connection:
            _insert_blob(connection, sha256=delete_sha, worm_locked=False)
            _insert_blob(connection, sha256=convert_sha, worm_locked=False)
            connection.execute(
                sa.text("DELETE FROM blob WHERE sha256=:sha256"), {"sha256": delete_sha}
            )

        now = datetime.now(UTC)
        with pytest.raises(sa.exc.DBAPIError, match="worm_blob_conversion_requires_insert"):
            with owner_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        UPDATE blob
                        SET worm_locked=true, object_version_id='converted',
                            worm_enforced_mode='GOVERNANCE',
                            worm_asserted_retain_until=:retain_until, worm_asserted_at=:now,
                            worm_retain_until=:retain_until, worm_retention_verified_at=:now,
                            worm_legal_hold=false, worm_legal_hold_verified_at=:now
                        WHERE sha256=:sha256
                        """
                    ),
                    {
                        "sha256": convert_sha,
                        "now": now,
                        "retain_until": now + timedelta(days=30),
                    },
                )
    finally:
        app_engine.dispose()
        owner_engine.dispose()


def test_app_owner_insert_requires_complete_same_org_worm_assertion(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    evidence_sha = uuid.uuid4().hex * 2
    document_sha = uuid.uuid4().hex * 2
    incomplete_sha = uuid.uuid4().hex * 2
    cross_org_sha = uuid.uuid4().hex * 2
    user_id = uuid.uuid4()
    framework_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    record_id = uuid.uuid4()
    document_id = uuid.uuid4()
    inverse_record_id = uuid.uuid4()
    inverse_document_id = uuid.uuid4()
    other_org = uuid.uuid4()
    inverse_evidence_sha = uuid.uuid4().hex * 2
    inverse_document_sha = uuid.uuid4().hex * 2
    try:
        with owner.begin() as connection:
            org_id = connection.execute(
                sa.text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
            ).scalar_one()
            connection.execute(
                sa.text(
                    "INSERT INTO app_user (id,org_id,keycloak_subject,display_name) "
                    "VALUES (:id,:org,:subject,'WORM owner actor')"
                ),
                {"id": user_id, "org": org_id, "subject": f"worm-owner-{user_id}"},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO framework (id,org_id,code,name,is_active,is_authorable) "
                    "VALUES (:id,:org,:code,'WORM owner framework',true,false)"
                ),
                {"id": framework_id, "org": org_id, "code": f"worm:{framework_id}"},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy "
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'P1Y','P1Y','DESTROY')"
                ),
                {"id": policy_id, "org": org_id, "name": f"worm-policy-{policy_id}"},
            )
            for item_id, kind, identifier in (
                (record_id, "RECORD", f"WORM-REC-{record_id}"),
                (document_id, "DOCUMENT", f"WORM-DOC-{document_id}"),
            ):
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO documented_information
                            (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                             current_state,is_singleton,classification,
                             acknowledgement_required,created_by)
                        VALUES (:id,:org,:framework,:kind,:identifier,'WORM owner',:user,
                                'Draft',false,'Internal',false,:user)
                        """
                    ),
                    {
                        "id": item_id,
                        "org": org_id,
                        "framework": framework_id,
                        "kind": kind,
                        "identifier": identifier,
                        "user": user_id,
                    },
                )
            connection.execute(
                sa.text(
                    "INSERT INTO record "
                    "(id,org_id,record_type,captured_by,content_hash_version,"
                    "retention_policy_id,disposition_state,legal_hold) "
                    "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',false)"
                ),
                {
                    "id": record_id,
                    "org": org_id,
                    "user": user_id,
                    "policy": policy_id,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO organization (id,legal_name,short_code) VALUES (:id,:name,:code)"
                ),
                {"id": other_org, "name": f"Other {other_org}", "code": f"WO-{other_org.hex[:12]}"},
            )
            for item_id, kind, identifier in (
                (inverse_record_id, "RECORD", f"WORM-INV-REC-{inverse_record_id}"),
                (inverse_document_id, "DOCUMENT", f"WORM-INV-DOC-{inverse_document_id}"),
            ):
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO documented_information
                            (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                             current_state,is_singleton,classification,
                             acknowledgement_required,created_by)
                        VALUES (:id,:org,:framework,:kind,:identifier,'Inverse parent',:user,
                                'Draft',false,'Internal',false,:user)
                        """
                    ),
                    {
                        "id": item_id,
                        "org": other_org,
                        "framework": framework_id,
                        "kind": kind,
                        "identifier": identifier,
                        "user": user_id,
                    },
                )
            connection.execute(
                sa.text(
                    "INSERT INTO record "
                    "(id,org_id,record_type,captured_by,content_hash_version,"
                    "retention_policy_id,disposition_state,legal_hold) "
                    "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',false)"
                ),
                {
                    "id": inverse_record_id,
                    "org": other_org,
                    "user": user_id,
                    "policy": policy_id,
                },
            )

        with app.begin() as connection:
            _insert_blob(connection, sha256=evidence_sha, worm_locked=True)
            _insert_blob(connection, sha256=document_sha, worm_locked=True)
            connection.execute(
                sa.text(
                    "INSERT INTO evidence_blob "
                    "(org_id,record_id,blob_sha256,is_original,created_by) "
                    "VALUES (:org,:record,:sha,true,:user)"
                ),
                {"org": org_id, "record": record_id, "sha": evidence_sha, "user": user_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO document_version
                        (org_id,document_id,version_seq,revision_label,change_significance,
                         change_reason,version_state,retention_authority_kind,
                         retention_policy_id,retention_basis_date,source_blob_sha256,
                         metadata_snapshot,imported,author_user_id,created_by)
                    VALUES (:org,:document,1,'A','MINOR','initial','Draft','POLICY',
                            :policy,current_date,:sha,'{}'::jsonb,false,:user,:user)
                    """
                ),
                {
                    "org": org_id,
                    "document": document_id,
                    "policy": policy_id,
                    "sha": document_sha,
                    "user": user_id,
                },
            )

        with app.begin() as connection:
            _insert_blob(connection, sha256=incomplete_sha, worm_locked=False)
            _insert_blob(connection, sha256=cross_org_sha, worm_locked=True)
            _insert_blob(connection, sha256=inverse_evidence_sha, worm_locked=True)
            _insert_blob(connection, sha256=inverse_document_sha, worm_locked=True)

        for sha256, attempted_org in (
            (incomplete_sha, org_id),
            (cross_org_sha, other_org),
        ):
            with pytest.raises(sa.exc.DBAPIError, match="worm_owner_requires_complete_assertion"):
                with app.begin() as connection:
                    connection.execute(
                        sa.text(
                            "INSERT INTO evidence_blob "
                            "(org_id,record_id,blob_sha256,is_original,created_by) "
                            "VALUES (:org,:record,:sha,true,:user)"
                        ),
                        {
                            "org": attempted_org,
                            "record": record_id,
                            "sha": sha256,
                            "user": user_id,
                        },
                    )

        inverse_denials: list[str] = []
        try:
            with app.begin() as connection:
                connection.execute(
                    sa.text(
                        "INSERT INTO evidence_blob "
                        "(org_id,record_id,blob_sha256,is_original,created_by) "
                        "VALUES (:org,:record,:sha,true,:user)"
                    ),
                    {
                        "org": org_id,
                        "record": inverse_record_id,
                        "sha": inverse_evidence_sha,
                        "user": user_id,
                    },
                )
        except sa.exc.DBAPIError as error:
            assert "worm_owner_requires_complete_assertion" in str(error)
            inverse_denials.append("EVIDENCE")

        try:
            with app.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO document_version
                            (org_id,document_id,version_seq,revision_label,change_significance,
                             change_reason,version_state,retention_authority_kind,
                             retention_policy_id,retention_basis_date,source_blob_sha256,
                             metadata_snapshot,imported,author_user_id,created_by)
                        VALUES (:org,:document,1,'X','MINOR','inverse','Draft','POLICY',
                                :policy,current_date,:sha,'{}'::jsonb,false,:user,:user)
                        """
                    ),
                    {
                        "org": org_id,
                        "document": inverse_document_id,
                        "policy": policy_id,
                        "sha": inverse_document_sha,
                        "user": user_id,
                    },
                )
        except sa.exc.DBAPIError as error:
            assert "worm_owner_requires_complete_assertion" in str(error)
            inverse_denials.append("DOCUMENT")

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM evidence_blob "
                        "WHERE blob_sha256 IN (:incomplete,:cross_org)"
                    ),
                    {"incomplete": incomplete_sha, "cross_org": cross_org_sha},
                ).scalar_one()
                == 0
            )
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE blob_sha256=:sha)"),
                {"sha": inverse_evidence_sha},
            ).scalar_one()
            assert not connection.execute(
                sa.text(
                    "SELECT EXISTS (SELECT 1 FROM document_version WHERE source_blob_sha256=:sha)"
                ),
                {"sha": inverse_document_sha},
            ).scalar_one()
        assert inverse_denials == ["EVIDENCE", "DOCUMENT"]
    finally:
        owner.dispose()
        app.dispose()


@pytest.mark.parametrize("owner_family", ("EVIDENCE", "DOCUMENT"))
def test_owner_insert_rechecks_parent_org_after_waiting_for_parent_update(
    database_authority_dsns: dict[str, str], owner_family: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    parent_id = uuid.uuid4()
    owner_edge_id = uuid.uuid4()
    blob_sha256 = uuid.uuid4().hex * 2
    try:
        with owner.begin() as connection:
            org_id = connection.execute(
                sa.text("SELECT id FROM organization WHERE short_code='DEFAULT'")
            ).scalar_one()
            other_org_id = uuid.uuid4()
            user_id = uuid.uuid4()
            framework_id = uuid.uuid4()
            policy_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO organization(id,legal_name,short_code) VALUES (:id,:name,:code)"
                ),
                {
                    "id": other_org_id,
                    "name": f"Parent lock other {other_org_id}",
                    "code": f"PLO-{other_org_id.hex[:10]}",
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO app_user(id,org_id,keycloak_subject,display_name) "
                    "VALUES (:id,:org,:subject,'Parent-lock actor')"
                ),
                {"id": user_id, "org": org_id, "subject": f"parent-lock-{user_id}"},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO framework(id,org_id,code,name,is_active,is_authorable) "
                    "VALUES (:id,:org,:code,'Parent-lock framework',true,false)"
                ),
                {"id": framework_id, "org": org_id, "code": f"parent-lock:{framework_id}"},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy"
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'P1Y','P1Y','DESTROY')"
                ),
                {"id": policy_id, "org": org_id, "name": f"parent-lock-{policy_id}"},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO documented_information
                        (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                         current_state,is_singleton,classification,
                         acknowledgement_required,created_by)
                    VALUES (:id,:org,:framework,CAST(:kind AS document_kind),:identifier,
                            'Parent-lock target',:user,'Draft',false,'Internal',false,:user)
                    """
                ),
                {
                    "id": parent_id,
                    "org": org_id,
                    "framework": framework_id,
                    "kind": "RECORD" if owner_family == "EVIDENCE" else "DOCUMENT",
                    "identifier": f"PARENT-LOCK-{parent_id}",
                    "user": user_id,
                },
            )
            if owner_family == "EVIDENCE":
                connection.execute(
                    sa.text(
                        "INSERT INTO record"
                        "(id,org_id,record_type,captured_by,content_hash_version,"
                        "retention_policy_id,disposition_state,legal_hold) "
                        "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',false)"
                    ),
                    {
                        "id": parent_id,
                        "org": org_id,
                        "user": user_id,
                        "policy": policy_id,
                    },
                )
            now = datetime.now(UTC)
            connection.execute(
                sa.text(
                    """
                    INSERT INTO blob
                        (sha256,org_id,size_bytes,mime_type,bucket,object_key,
                         object_version_id,worm_locked,worm_enforced_mode,
                         worm_asserted_retain_until,worm_asserted_at,worm_retain_until,
                         worm_retention_verified_at,worm_legal_hold,
                         worm_legal_hold_verified_at,sse)
                    VALUES (:sha,:org,1,'application/octet-stream','parent-lock',:key,
                            :version,true,'GOVERNANCE',:retain,:now,:retain,:now,false,:now,false)
                    """
                ),
                {
                    "sha": blob_sha256,
                    "org": org_id,
                    "key": f"parent-lock/{blob_sha256}",
                    "version": f"version-{uuid.uuid4()}",
                    "retain": now + timedelta(days=30),
                    "now": now,
                },
            )

        def insert_owner() -> tuple[str, str | None]:
            try:
                with app.begin() as connection:
                    connection.execute(
                        sa.text("SELECT set_config('application_name',:name,true)"),
                        {"name": application_name},
                    )
                    connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
                    if owner_family == "EVIDENCE":
                        connection.execute(
                            sa.text(
                                "INSERT INTO evidence_blob"
                                "(id,org_id,record_id,blob_sha256,is_original,created_by) "
                                "VALUES (:id,:org,:parent,:sha,true,:user)"
                            ),
                            {
                                "id": owner_edge_id,
                                "org": org_id,
                                "parent": parent_id,
                                "sha": blob_sha256,
                                "user": user_id,
                            },
                        )
                    else:
                        connection.execute(
                            sa.text(
                                """
                                INSERT INTO document_version
                                    (id,org_id,document_id,version_seq,revision_label,
                                     change_significance,change_reason,version_state,
                                     retention_authority_kind,retention_policy_id,
                                     retention_basis_date,source_blob_sha256,metadata_snapshot,
                                     imported,author_user_id,created_by)
                                VALUES (:id,:org,:parent,1,'A','MINOR','parent-lock','Draft',
                                        'POLICY',:policy,current_date,:sha,'{}'::jsonb,false,:user,:user)
                                """
                            ),
                            {
                                "id": owner_edge_id,
                                "org": org_id,
                                "parent": parent_id,
                                "policy": policy_id,
                                "sha": blob_sha256,
                                "user": user_id,
                            },
                        )
                return ("ok", None)
            except sa.exc.DBAPIError as error:
                return ("error", str(error))

        application_name = f"t2-parent-{owner_family.lower()}-{uuid.uuid4().hex[:8]}"
        lock_connection = owner.connect()
        lock_transaction = lock_connection.begin()
        try:
            update_parent = (
                "UPDATE record SET org_id=:org WHERE id=:id"
                if owner_family == "EVIDENCE"
                else "UPDATE documented_information SET org_id=:org WHERE id=:id"
            )
            lock_connection.execute(
                sa.text(update_parent),
                {"org": other_org_id, "id": parent_id},
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(insert_owner)
                blocked = _wait_for_named_lock(owner, application_name)
                lock_transaction.commit()
                outcome, error = future.result(timeout=6)
            assert blocked, (outcome, error)
            assert outcome == "error"
            assert error is not None and "worm_owner_requires_complete_assertion" in error
        finally:
            if lock_transaction.is_active:
                lock_transaction.rollback()
            lock_connection.close()

        with owner.connect() as connection:
            owner_exists = (
                "SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"
                if owner_family == "EVIDENCE"
                else "SELECT EXISTS (SELECT 1 FROM document_version WHERE id=:id)"
            )
            assert not connection.execute(
                sa.text(owner_exists),
                {"id": owner_edge_id},
            ).scalar_one()
    finally:
        owner.dispose()
        app.dispose()


@pytest.mark.parametrize("purge_path", ("ORDINARY", "R27"))
@pytest.mark.parametrize("owner_family", ("EVIDENCE", "DOCUMENT"))
def test_real_app_cannot_attach_owner_after_exact_physical_purge(
    database_authority_dsns: dict[str, str], purge_path: str, owner_family: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner
    from tests.integration.test_r27_authority_transitions import _seed_source_execution

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    r27_maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    attempted_owner_id = uuid.uuid4()
    document_id = uuid.uuid4()
    evidence_record_id = uuid.uuid4()
    try:
        if purge_path == "ORDINARY":
            with owner.begin() as connection:
                seed = _seed_ordinary_owner(connection)
            org_id = seed.org_id
            user_id = seed.user_id
            record_id = seed.record_id
            policy_id = seed.policy_id
            framework_id = seed.framework_id
            blob_sha256 = seed.blob_sha256
            with retention.begin() as connection:
                marker_id = connection.execute(
                    sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                    {
                        "record": seed.record_id,
                        "event": seed.disposition_event_id,
                        "sha": seed.blob_sha256,
                    },
                ).scalar_one()
                claimed_ids = {
                    row.marker_id
                    for row in connection.execute(
                        sa.text(
                            "SELECT * FROM easysynq_claim_ordinary_exact_purges"
                            "(100,clock_timestamp())"
                        )
                    )
                }
                assert marker_id in claimed_ids
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_ordinary_exact_purge(:marker,clock_timestamp())"
                    ),
                    {"marker": marker_id},
                )
        else:
            source = _seed_source_execution(database_authority_dsns, owner)
            physical = source.request.targets[0]
            org_id = source.actors.org_id
            user_id = source.actors.requester_id
            record_id = source.actors.record_id
            blob_sha256 = physical.sha256
            with owner.connect() as connection:
                framework_id, policy_id = connection.execute(
                    sa.text(
                        "SELECT information.framework_id,record.retention_policy_id "
                        "FROM documented_information information "
                        "JOIN record ON record.id=information.id WHERE record.id=:record"
                    ),
                    {"record": record_id},
                ).one()
            with r27_maintenance.begin() as connection:
                claimed_ids = {
                    row.marker_id
                    for row in connection.execute(
                        sa.text(
                            "SELECT * FROM easysynq_claim_r27_exact_purges"
                            "(:execution,10,clock_timestamp())"
                        ),
                        {"execution": source.public_execution_id},
                    )
                }
                assert source.physical_marker_id in claimed_ids
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_hold_release"
                        "(:sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": physical.sha256,
                        "version": physical.object_version_id,
                        "execution": source.public_execution_id,
                    },
                )
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_purge"
                        "(:sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": physical.sha256,
                        "version": physical.object_version_id,
                        "execution": source.public_execution_id,
                    },
                )

        with owner.begin() as connection:
            for parent_id, kind in (
                (document_id, "DOCUMENT"),
                (evidence_record_id, "RECORD"),
            ):
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO documented_information
                            (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                             current_state,is_singleton,classification,
                             acknowledgement_required,created_by)
                        VALUES (:id,:org,:framework,CAST(:kind AS document_kind),:identifier,
                                'Post-purge parent',:user,'Draft',false,'Internal',false,:user)
                        """
                    ),
                    {
                        "id": parent_id,
                        "org": org_id,
                        "framework": framework_id,
                        "kind": kind,
                        "identifier": f"POST-PURGE-{parent_id}",
                        "user": user_id,
                    },
                )
            connection.execute(
                sa.text(
                    "INSERT INTO record "
                    "(id,org_id,record_type,captured_by,content_hash_version,"
                    "retention_policy_id,disposition_state,legal_hold) "
                    "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',false)"
                ),
                {
                    "id": evidence_record_id,
                    "org": org_id,
                    "user": user_id,
                    "policy": policy_id,
                },
            )

        with pytest.raises(sa.exc.DBAPIError, match="worm_owner_requires_complete_assertion"):
            with app.begin() as connection:
                if owner_family == "EVIDENCE":
                    connection.execute(
                        sa.text(
                            "INSERT INTO evidence_blob "
                            "(id,org_id,record_id,blob_sha256,is_original,created_by) "
                            "VALUES (:id,:org,:parent,:sha,true,:user)"
                        ),
                        {
                            "id": attempted_owner_id,
                            "org": org_id,
                            "parent": evidence_record_id,
                            "sha": blob_sha256,
                            "user": user_id,
                        },
                    )
                else:
                    connection.execute(
                        sa.text(
                            """
                            INSERT INTO document_version
                                (id,org_id,document_id,version_seq,revision_label,
                                 change_significance,change_reason,version_state,
                                 retention_authority_kind,retention_policy_id,
                                 retention_basis_date,source_blob_sha256,metadata_snapshot,
                                 imported,author_user_id,created_by)
                            VALUES (:id,:org,:parent,1,'A','MINOR','post-purge','Draft',
                                    'POLICY',:policy,current_date,:sha,'{}'::jsonb,false,:user,:user)
                            """
                        ),
                        {
                            "id": attempted_owner_id,
                            "org": org_id,
                            "parent": document_id,
                            "policy": policy_id,
                            "sha": blob_sha256,
                            "user": user_id,
                        },
                    )
        with owner.connect() as connection:
            table = "evidence_blob" if owner_family == "EVIDENCE" else "document_version"
            assert not connection.execute(
                sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE id=:id)"),  # noqa: S608
                {"id": attempted_owner_id},
            ).scalar_one()
    finally:
        owner.dispose()
        app.dispose()
        retention.dispose()
        r27_maintenance.dispose()


@pytest.mark.parametrize("purge_path", ("ORDINARY", "R27"))
def test_owner_insert_waiting_behind_physical_purge_rechecks_absence_after_commit(
    database_authority_dsns: dict[str, str], purge_path: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner
    from tests.integration.test_r27_authority_transitions import _seed_source_execution

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    r27_maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    application_name = f"post-purge-owner-race-{uuid.uuid4()}"
    evidence_id = uuid.uuid4()

    def insert_distinct_record_parent(
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        framework_id: uuid.UUID,
        policy_id: uuid.UUID,
    ) -> uuid.UUID:
        record_id = uuid.uuid4()
        with owner.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO documented_information
                        (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                         current_state,is_singleton,classification,
                         acknowledgement_required,created_by)
                    VALUES (:id,:org,:framework,'RECORD',:identifier,'Race owner parent',:user,
                            'Draft',false,'Internal',false,:user)
                    """
                ),
                {
                    "id": record_id,
                    "org": org_id,
                    "framework": framework_id,
                    "identifier": f"RACE-OWNER-{record_id}",
                    "user": user_id,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO record "
                    "(id,org_id,record_type,captured_by,content_hash_version,"
                    "retention_policy_id,disposition_state,legal_hold) "
                    "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',false)"
                ),
                {
                    "id": record_id,
                    "org": org_id,
                    "user": user_id,
                    "policy": policy_id,
                },
            )
        return record_id

    try:
        if purge_path == "ORDINARY":
            with owner.begin() as connection:
                seed = _seed_ordinary_owner(connection)
            org_id = seed.org_id
            user_id = seed.user_id
            blob_sha256 = seed.blob_sha256
            with retention.begin() as connection:
                marker_id = connection.execute(
                    sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                    {
                        "record": seed.record_id,
                        "event": seed.disposition_event_id,
                        "sha": seed.blob_sha256,
                    },
                ).scalar_one()
                assert marker_id in {
                    row.marker_id
                    for row in connection.execute(
                        sa.text(
                            "SELECT * FROM easysynq_claim_ordinary_exact_purges"
                            "(100,clock_timestamp())"
                        )
                    )
                }
            record_id = insert_distinct_record_parent(
                org_id,
                user_id,
                seed.framework_id,
                seed.policy_id,
            )
            purge_connection = retention.connect()
            purge_transaction = purge_connection.begin()
            purge_connection.execute(
                sa.text("SELECT easysynq_record_ordinary_exact_purge(:marker,clock_timestamp())"),
                {"marker": marker_id},
            )
        else:
            source = _seed_source_execution(database_authority_dsns, owner)
            physical = source.request.targets[0]
            org_id = source.actors.org_id
            user_id = source.actors.requester_id
            blob_sha256 = physical.sha256
            with owner.connect() as connection:
                framework_id, policy_id = connection.execute(
                    sa.text(
                        "SELECT information.framework_id,record.retention_policy_id "
                        "FROM documented_information information "
                        "JOIN record ON record.id=information.id WHERE record.id=:record"
                    ),
                    {"record": source.actors.record_id},
                ).one()
            with r27_maintenance.begin() as connection:
                assert source.physical_marker_id in {
                    row.marker_id
                    for row in connection.execute(
                        sa.text(
                            "SELECT * FROM easysynq_claim_r27_exact_purges"
                            "(:execution,10,clock_timestamp())"
                        ),
                        {"execution": source.public_execution_id},
                    )
                }
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_hold_release"
                        "(:sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": physical.sha256,
                        "version": physical.object_version_id,
                        "execution": source.public_execution_id,
                    },
                )
            record_id = insert_distinct_record_parent(
                org_id,
                user_id,
                framework_id,
                policy_id,
            )
            purge_connection = r27_maintenance.connect()
            purge_transaction = purge_connection.begin()
            purge_connection.execute(
                sa.text(
                    "SELECT easysynq_record_r27_purge(:sha,:version,:execution,clock_timestamp())"
                ),
                {
                    "sha": physical.sha256,
                    "version": physical.object_version_id,
                    "execution": source.public_execution_id,
                },
            )

        def insert_owner() -> tuple[str, str | None]:
            try:
                with app.begin() as connection:
                    connection.execute(
                        sa.text("SELECT set_config('application_name',:name,false)"),
                        {"name": application_name},
                    )
                    connection.execute(sa.text("SET LOCAL statement_timeout='10s'"))
                    connection.execute(
                        sa.text(
                            "INSERT INTO evidence_blob "
                            "(id,org_id,record_id,blob_sha256,is_original,created_by) "
                            "VALUES (:id,:org,:record,:sha,true,:user)"
                        ),
                        {
                            "id": evidence_id,
                            "org": org_id,
                            "record": record_id,
                            "sha": blob_sha256,
                            "user": user_id,
                        },
                    )
                return "ok", None
            except sa.exc.DBAPIError as error:
                return "error", str(error)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(insert_owner)
                blocked = _wait_for_named_lock(owner, application_name)
                purge_transaction.commit()
                outcome, detail = future.result(timeout=15)
        finally:
            if purge_transaction.is_active:
                purge_transaction.rollback()
            purge_connection.close()

        assert blocked
        assert outcome == "error"
        assert detail is not None and "worm_owner_requires_complete_assertion" in detail
        with owner.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM evidence_blob WHERE id=:id)"),
                {"id": evidence_id},
            ).scalar_one()
    finally:
        owner.dispose()
        app.dispose()
        retention.dispose()
        r27_maintenance.dispose()


def test_real_app_cannot_repoint_or_delete_worm_owner_edges(
    database_authority_dsns: dict[str, str],
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner
    from tests.integration.test_r27_database_authority import _add_permanent_document_owner

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    replacement_sha = uuid.uuid4().hex * 2
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            _add_permanent_document_owner(connection, seed, "POLICY")
            evidence_id = connection.execute(
                sa.text("SELECT id FROM evidence_blob WHERE blob_sha256=:sha"),
                {"sha": seed.blob_sha256},
            ).scalar_one()
            document_id = connection.execute(
                sa.text("SELECT id FROM document_version WHERE source_blob_sha256=:sha"),
                {"sha": seed.blob_sha256},
            ).scalar_one()
        with app.begin() as connection:
            _insert_blob(connection, sha256=replacement_sha, worm_locked=True)

        for statement, parameters in (
            (
                "UPDATE evidence_blob SET blob_sha256=:replacement WHERE id=:id",
                {"replacement": replacement_sha, "id": evidence_id},
            ),
            (
                "UPDATE document_version SET source_blob_sha256=:replacement WHERE id=:id",
                {"replacement": replacement_sha, "id": document_id},
            ),
            ("DELETE FROM evidence_blob WHERE id=:id", {"id": evidence_id}),
            ("DELETE FROM document_version WHERE id=:id", {"id": document_id}),
        ):
            with pytest.raises(sa.exc.DBAPIError):
                with app.begin() as connection:
                    connection.execute(sa.text(statement), parameters)

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT blob_sha256 FROM evidence_blob WHERE id=:id"),
                    {"id": evidence_id},
                ).scalar_one()
                == seed.blob_sha256
            )
            assert (
                connection.execute(
                    sa.text("SELECT source_blob_sha256 FROM document_version WHERE id=:id"),
                    {"id": document_id},
                ).scalar_one()
                == seed.blob_sha256
            )
    finally:
        owner.dispose()
        app.dispose()


@pytest.mark.parametrize(
    "operation",
    ("EVIDENCE_REPOINT", "EVIDENCE_DELETE", "DOCUMENT_REPOINT", "DOCUMENT_DELETE"),
)
def test_owner_trigger_rejects_parent_repoint_and_delete(
    database_authority_dsns: dict[str, str], operation: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner
    from tests.integration.test_r27_database_authority import _add_permanent_document_owner

    owner = _engine(database_authority_dsns, "owner")
    replacement_record_id = uuid.uuid4()
    replacement_document_id = uuid.uuid4()
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            _add_permanent_document_owner(connection, seed, "POLICY")
            evidence_id = connection.execute(
                sa.text("SELECT id FROM evidence_blob WHERE blob_sha256=:sha"),
                {"sha": seed.blob_sha256},
            ).scalar_one()
            document_version_id = connection.execute(
                sa.text("SELECT id FROM document_version WHERE source_blob_sha256=:sha"),
                {"sha": seed.blob_sha256},
            ).scalar_one()
            for parent_id, kind in (
                (replacement_record_id, "RECORD"),
                (replacement_document_id, "DOCUMENT"),
            ):
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO documented_information
                            (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                             current_state,is_singleton,classification,
                             acknowledgement_required,created_by)
                        VALUES (:id,:org,:framework,CAST(:kind AS document_kind),:identifier,
                                'Replacement parent',:user,'Draft',false,'Internal',false,:user)
                        """
                    ),
                    {
                        "id": parent_id,
                        "org": seed.org_id,
                        "framework": seed.framework_id,
                        "kind": kind,
                        "identifier": f"WORM-REPOINT-{parent_id}",
                        "user": seed.user_id,
                    },
                )
            connection.execute(
                sa.text(
                    "INSERT INTO record"
                    "(id,org_id,record_type,captured_by,content_hash_version,"
                    "retention_policy_id,disposition_state) "
                    "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE')"
                ),
                {
                    "id": replacement_record_id,
                    "org": seed.org_id,
                    "user": seed.user_id,
                    "policy": seed.policy_id,
                },
            )

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(edge) FROM evidence_blob edge WHERE id=:evidence),
              (SELECT to_jsonb(version) FROM document_version version WHERE id=:version)
            """
        )
        parameters = {"evidence": evidence_id, "version": document_version_id}
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()

        statement, statement_parameters = {
            "EVIDENCE_REPOINT": (
                "UPDATE evidence_blob SET record_id=:parent WHERE id=:id",
                {"parent": replacement_record_id, "id": evidence_id},
            ),
            "EVIDENCE_DELETE": (
                "DELETE FROM evidence_blob WHERE id=:id",
                {"id": evidence_id},
            ),
            "DOCUMENT_REPOINT": (
                "UPDATE document_version SET document_id=:parent WHERE id=:id",
                {"parent": replacement_document_id, "id": document_version_id},
            ),
            "DOCUMENT_DELETE": (
                "DELETE FROM document_version WHERE id=:id",
                {"id": document_version_id},
            ),
        }[operation]
        with pytest.raises(sa.exc.DBAPIError, match="worm_owner_pointer_is_immutable"):
            with owner.begin() as connection:
                connection.execute(sa.text(statement), statement_parameters)

        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner.dispose()


def test_retention_claim_ratchets_forward_and_refuses_shortening(
    database_authority_dsns: dict[str, str],
) -> None:
    sha256 = uuid.uuid4().hex * 2
    operation_id = uuid.uuid4()
    target_id = uuid.uuid4()
    owner_engine = _engine(database_authority_dsns, "owner")
    retention_engine = _engine(database_authority_dsns, "easysynq_retention")
    now = datetime.now(UTC)
    later = now + timedelta(days=90)
    try:
        with owner_engine.begin() as connection:
            org_id = _insert_blob(connection, sha256=sha256, worm_locked=True)
            revision_id = connection.execute(
                sa.text(
                    "SELECT rr.id FROM retention_revision rr "
                    "JOIN retention_policy rp ON rp.id=rr.retention_policy_id "
                    "WHERE rp.org_id=:org_id ORDER BY rr.created_at LIMIT 1"
                ),
                {"org_id": org_id},
            ).scalar_one()
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_operation
                        (id,org_id,revision_id,target_count)
                    VALUES (:id,:org_id,:revision_id,1)
                    """
                ),
                {"id": operation_id, "org_id": org_id, "revision_id": revision_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_operation_target
                        (id,operation_id,blob_sha256,bucket,object_key,object_version_id,
                         required_retain_until,required_legal_hold)
                    SELECT :id,:operation_id,sha256,bucket,object_key,object_version_id,
                           :required_retain_until,true
                    FROM blob WHERE sha256=:sha256
                    """
                ),
                {
                    "id": target_id,
                    "operation_id": operation_id,
                    "sha256": sha256,
                    "required_retain_until": later,
                },
            )

        with retention_engine.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_retention_targets(1,:claimed_at)"),
                {"claimed_at": now},
            ).one()
            assert claimed.target_id == target_id

        with owner_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE retention_operation_target SET object_key='wrong-coordinate' "
                    "WHERE id=:id"
                ),
                {"id": target_id},
            )
        with pytest.raises(sa.exc.DBAPIError, match="worm_retention_ratchet_refused"):
            with retention_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_ratchet_worm_assertion"
                        "(:sha256,:version_id,:retain_until,true,:verified_at,:operation_id)"
                    ),
                    {
                        "sha256": sha256,
                        "version_id": f"version-{sha256[:12]}",
                        "retain_until": later,
                        "verified_at": now,
                        "operation_id": operation_id,
                    },
                )
        with owner_engine.begin() as connection:
            unchanged = connection.execute(
                sa.text(
                    "SELECT b.worm_retain_until,b.worm_legal_hold,t.state::text "
                    "FROM blob b CROSS JOIN retention_operation_target t "
                    "WHERE b.sha256=:sha256 AND t.id=:target_id"
                ),
                {"sha256": sha256, "target_id": target_id},
            ).one()
            assert unchanged[1:] == (False, "RUNNING")
            connection.execute(
                sa.text(
                    "UPDATE retention_operation_target SET object_key=:object_key WHERE id=:id"
                ),
                {"object_key": sha256, "id": target_id},
            )

        def snapshot() -> tuple[object, ...]:
            with owner_engine.connect() as connection:
                return tuple(
                    connection.execute(
                        sa.text(
                            """
                            SELECT target.state::text,target.bucket,target.object_key,
                                   target.object_version_id,operation.org_id,operation.state::text,
                                   blob.worm_retain_until,blob.worm_legal_hold,
                                   blob.worm_retention_verified_at,
                                   blob.worm_legal_hold_verified_at
                            FROM retention_operation_target target
                            JOIN retention_operation operation
                              ON operation.id=target.operation_id
                            JOIN blob ON blob.sha256=target.blob_sha256
                            WHERE target.id=:target_id
                            """
                        ),
                        {"target_id": target_id},
                    ).one()
                )

        def assert_ratchet_refused(
            *,
            caller_sha: str = sha256,
            caller_version: str = f"version-{sha256[:12]}",
            caller_operation: uuid.UUID = operation_id,
        ) -> None:
            before = snapshot()
            with pytest.raises(sa.exc.DBAPIError, match="worm_retention_ratchet_refused"):
                with retention_engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            "SELECT easysynq_ratchet_worm_assertion"
                            "(:sha,:version,:retain_until,true,:observed,:operation)"
                        ),
                        {
                            "sha": caller_sha,
                            "version": caller_version,
                            "retain_until": later,
                            "observed": now,
                            "operation": caller_operation,
                        },
                    )
            assert snapshot() == before

        for update_statement, wrong_value, original_value in (
            (
                "UPDATE retention_operation_target SET bucket=:value WHERE id=:id",
                "wrong-bucket",
                "test-worm",
            ),
            (
                "UPDATE retention_operation_target SET object_key=:value WHERE id=:id",
                "wrong-key",
                sha256,
            ),
            (
                "UPDATE retention_operation_target SET object_version_id=:value WHERE id=:id",
                "wrong-version",
                f"version-{sha256[:12]}",
            ),
        ):
            with owner_engine.begin() as connection:
                connection.execute(
                    sa.text(update_statement),
                    {"value": wrong_value, "id": target_id},
                )
            assert_ratchet_refused()
            with owner_engine.begin() as connection:
                connection.execute(
                    sa.text(update_statement),
                    {"value": original_value, "id": target_id},
                )

        wrong_org = uuid.uuid4()
        with owner_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO organization (id,legal_name,short_code) VALUES (:id,:name,:code)"
                ),
                {
                    "id": wrong_org,
                    "name": f"Wrong operation org {wrong_org}",
                    "code": f"WR-{wrong_org.hex[:12]}",
                },
            )
            connection.execute(
                sa.text("UPDATE retention_operation SET org_id=:org WHERE id=:id"),
                {"org": wrong_org, "id": operation_id},
            )
        assert_ratchet_refused()
        with owner_engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE retention_operation SET org_id=:org WHERE id=:id"),
                {"org": org_id, "id": operation_id},
            )

        assert_ratchet_refused(caller_sha="e" * 64)
        assert_ratchet_refused(caller_version="wrong-caller-version")
        assert_ratchet_refused(caller_operation=uuid.uuid4())

        for attempted_retain_until, attempted_hold in (
            (now, True),
            (later, False),
        ):
            with pytest.raises(sa.exc.DBAPIError, match="worm_retention_ratchet_refused"):
                with retention_engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            "SELECT easysynq_ratchet_worm_assertion"
                            "(:sha256,:version_id,:retain_until,:legal_hold,"
                            ":verified_at,:operation_id)"
                        ),
                        {
                            "sha256": sha256,
                            "version_id": f"version-{sha256[:12]}",
                            "retain_until": attempted_retain_until,
                            "legal_hold": attempted_hold,
                            "verified_at": now,
                            "operation_id": operation_id,
                        },
                    )
            with owner_engine.connect() as connection:
                assert (
                    connection.execute(
                        sa.text("SELECT state::text FROM retention_operation_target WHERE id=:id"),
                        {"id": target_id},
                    ).scalar_one()
                    == "RUNNING"
                )

        with retention_engine.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_ratchet_worm_assertion"
                    "(:sha256,:version_id,:retain_until,true,:verified_at,:operation_id)"
                ),
                {
                    "sha256": sha256,
                    "version_id": f"version-{sha256[:12]}",
                    "retain_until": later,
                    "verified_at": now,
                    "operation_id": operation_id,
                },
            )

        with owner_engine.connect() as connection:
            state = connection.execute(
                sa.text("SELECT worm_retain_until,worm_legal_hold FROM blob WHERE sha256=:sha256"),
                {"sha256": sha256},
            ).one()
            assert state == (later, True)

        with pytest.raises(sa.exc.DBAPIError, match="worm_retention_ratchet_refused"):
            with retention_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_ratchet_worm_assertion"
                        "(:sha256,:version_id,:retain_until,false,:verified_at,:operation_id)"
                    ),
                    {
                        "sha256": sha256,
                        "version_id": f"version-{sha256[:12]}",
                        "retain_until": now,
                        "verified_at": now,
                        "operation_id": operation_id,
                    },
                )
    finally:
        owner_engine.dispose()
        retention_engine.dispose()


def test_retention_parent_requires_exact_target_count_before_verified(
    database_authority_dsns: dict[str, str],
) -> None:
    first_sha = uuid.uuid4().hex * 2
    second_sha = uuid.uuid4().hex * 2
    policy_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    observed = datetime.now(UTC)
    retain_until = observed + timedelta(days=90)
    try:
        with owner.begin() as connection:
            org_id = _insert_blob(connection, sha256=first_sha, worm_locked=True)
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy "
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'P1Y','P1Y','DESTROY')"
                ),
                {"id": policy_id, "org": org_id, "name": f"count-{policy_id}"},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_revision
                        (id,authority_kind,retention_policy_id,revision_no,active_values,
                         state,activated_at)
                    VALUES (:id,'POLICY',:policy,1,'{}'::jsonb,'ACTIVE',clock_timestamp())
                    """
                ),
                {"id": revision_id, "policy": policy_id},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO retention_operation (id,org_id,revision_id,target_count) "
                    "VALUES (:id,:org_id,:revision_id,2)"
                ),
                {"id": operation_id, "org_id": org_id, "revision_id": revision_id},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_operation_target
                        (operation_id,blob_sha256,bucket,object_key,object_version_id,
                         required_retain_until,required_legal_hold)
                    SELECT :operation_id,sha256,bucket,object_key,object_version_id,
                           :retain_until,false FROM blob WHERE sha256=:sha
                    """
                ),
                {
                    "operation_id": operation_id,
                    "retain_until": retain_until,
                    "sha": first_sha,
                },
            )

        with retention.begin() as connection:
            connection.execute(
                sa.text("SELECT * FROM easysynq_claim_retention_targets(1,:observed)"),
                {"observed": observed},
            ).one()
            connection.execute(
                sa.text(
                    "SELECT easysynq_ratchet_worm_assertion"
                    "(:sha,:version,:retain_until,false,:observed,:operation)"
                ),
                {
                    "sha": first_sha,
                    "version": f"version-{first_sha[:12]}",
                    "retain_until": retain_until,
                    "observed": observed,
                    "operation": operation_id,
                },
            )

        with owner.begin() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT state::text,verified_count,target_count,completed_at "
                    "FROM retention_operation WHERE id=:id"
                ),
                {"id": operation_id},
            ).one() == ("RUNNING", 1, 2, None)
            _insert_blob(connection, sha256=second_sha, worm_locked=True)
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_operation_target
                        (operation_id,blob_sha256,bucket,object_key,object_version_id,
                         required_retain_until,required_legal_hold)
                    SELECT :operation_id,sha256,bucket,object_key,object_version_id,
                           :retain_until,false FROM blob WHERE sha256=:sha
                    """
                ),
                {
                    "operation_id": operation_id,
                    "retain_until": retain_until,
                    "sha": second_sha,
                },
            )

        with retention.begin() as connection:
            connection.execute(
                sa.text("SELECT * FROM easysynq_claim_retention_targets(1,:observed)"),
                {"observed": observed},
            ).one()
            connection.execute(
                sa.text(
                    "SELECT easysynq_ratchet_worm_assertion"
                    "(:sha,:version,:retain_until,false,:observed,:operation)"
                ),
                {
                    "sha": second_sha,
                    "version": f"version-{second_sha[:12]}",
                    "retain_until": retain_until,
                    "observed": observed,
                    "operation": operation_id,
                },
            )

        with owner.connect() as connection:
            completed = connection.execute(
                sa.text(
                    "SELECT state::text,verified_count,target_count,completed_at IS NOT NULL "
                    "FROM retention_operation WHERE id=:id"
                ),
                {"id": operation_id},
            ).one()
            assert completed == ("VERIFIED", 2, 2, True)
    finally:
        owner.dispose()
        retention.dispose()


@pytest.mark.parametrize("parent_state", ("VERIFIED", "CANCELLED_PRE_START"))
def test_retention_claim_never_reopens_terminal_parent(
    database_authority_dsns: dict[str, str], parent_state: str
) -> None:
    sha256 = uuid.uuid4().hex * 2
    policy_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    target_id = uuid.uuid4()
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            org_id = _insert_blob(connection, sha256=sha256, worm_locked=True)
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy "
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'P1Y','P1Y','DESTROY')"
                ),
                {"id": policy_id, "org": org_id, "name": f"terminal-{policy_id}"},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_revision
                        (id,authority_kind,retention_policy_id,revision_no,active_values,
                         state,activated_at)
                    VALUES (:id,'POLICY',:policy,1,'{}'::jsonb,'ACTIVE',clock_timestamp())
                    """
                ),
                {"id": revision_id, "policy": policy_id},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO retention_operation "
                    "(id,org_id,revision_id,state,target_count,completed_at) "
                    "VALUES (:id,:org,:revision,:state,1,clock_timestamp())"
                ),
                {
                    "id": operation_id,
                    "org": org_id,
                    "revision": revision_id,
                    "state": parent_state,
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_operation_target
                        (id,operation_id,blob_sha256,bucket,object_key,object_version_id,
                         required_legal_hold,state)
                    SELECT :id,:operation,sha256,bucket,object_key,object_version_id,false,'PENDING'
                    FROM blob WHERE sha256=:sha
                    """
                ),
                {"id": target_id, "operation": operation_id, "sha": sha256},
            )

        with retention.begin() as connection:
            claimed_ids = {
                row.target_id
                for row in connection.execute(
                    sa.text("SELECT * FROM easysynq_claim_retention_targets(100,clock_timestamp())")
                )
            }
            assert target_id not in claimed_ids

        with owner.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT operation.state::text,target.state::text "
                    "FROM retention_operation operation "
                    "JOIN retention_operation_target target "
                    "ON target.operation_id=operation.id WHERE operation.id=:id"
                ),
                {"id": operation_id},
            ).one() == (parent_state, "PENDING")
    finally:
        owner.dispose()
        retention.dispose()


def test_concurrent_retention_ratchets_serialize_parent_completion(
    database_authority_dsns: dict[str, str],
) -> None:
    shas = (uuid.uuid4().hex * 2, uuid.uuid4().hex * 2)
    policy_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    observed = datetime.now(UTC)
    retain_until = observed + timedelta(days=90)
    owner = _engine(database_authority_dsns, "owner")
    retention_one = _engine(database_authority_dsns, "easysynq_retention")
    retention_two = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            org_id = _insert_blob(connection, sha256=shas[0], worm_locked=True)
            _insert_blob(connection, sha256=shas[1], worm_locked=True)
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy "
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'P1Y','P1Y','DESTROY')"
                ),
                {"id": policy_id, "org": org_id, "name": f"concurrent-{policy_id}"},
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO retention_revision
                        (id,authority_kind,retention_policy_id,revision_no,active_values,
                         state,activated_at)
                    VALUES (:id,'POLICY',:policy,1,'{}'::jsonb,'ACTIVE',clock_timestamp())
                    """
                ),
                {"id": revision_id, "policy": policy_id},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO retention_operation (id,org_id,revision_id,target_count) "
                    "VALUES (:id,:org,:revision,2)"
                ),
                {"id": operation_id, "org": org_id, "revision": revision_id},
            )
            for sha256 in shas:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO retention_operation_target
                            (operation_id,blob_sha256,bucket,object_key,object_version_id,
                             required_retain_until,required_legal_hold)
                        SELECT :operation,sha256,bucket,object_key,object_version_id,
                               :retain_until,false FROM blob WHERE sha256=:sha
                        """
                    ),
                    {
                        "operation": operation_id,
                        "retain_until": retain_until,
                        "sha": sha256,
                    },
                )
        with retention_one.begin() as connection:
            assert (
                len(
                    connection.execute(
                        sa.text("SELECT * FROM easysynq_claim_retention_targets(2,:observed)"),
                        {"observed": observed},
                    ).all()
                )
                == 2
            )

        barrier = Barrier(3)
        application_names = (
            f"retention-ratchet-{uuid.uuid4().hex}",
            f"retention-ratchet-{uuid.uuid4().hex}",
        )

        def ratchet(engine: sa.Engine, sha256: str, application_name: str) -> None:
            with engine.begin() as connection:
                connection.execute(
                    sa.text("SELECT set_config('application_name',:name,false)"),
                    {"name": application_name},
                )
                connection.execute(sa.text("SET LOCAL statement_timeout='15s'"))
                barrier.wait(timeout=5)
                connection.execute(
                    sa.text(
                        "SELECT easysynq_ratchet_worm_assertion"
                        "(:sha,:version,:retain_until,false,:observed,:operation)"
                    ),
                    {
                        "sha": sha256,
                        "version": f"version-{sha256[:12]}",
                        "retain_until": retain_until,
                        "observed": observed,
                        "operation": operation_id,
                    },
                )

        executor = ThreadPoolExecutor(max_workers=2)
        try:
            with owner.connect() as locker:
                lock_transaction = locker.begin()
                locker.execute(
                    sa.text("SELECT id FROM retention_operation WHERE id=:id FOR UPDATE"),
                    {"id": operation_id},
                ).one()
                futures = (
                    executor.submit(ratchet, retention_one, shas[0], application_names[0]),
                    executor.submit(ratchet, retention_two, shas[1], application_names[1]),
                )
                try:
                    barrier.wait(timeout=5)
                    deadline = monotonic() + 5
                    blocked = 0
                    while monotonic() < deadline:
                        locker.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = locker.execute(
                            sa.text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE application_name=ANY(:names) AND state='active' "
                                "AND wait_event_type='Lock'"
                            ),
                            {"names": list(application_names)},
                        ).scalar_one()
                        if blocked == 2:
                            break
                        sleep(0.05)
                finally:
                    if lock_transaction.is_active:
                        lock_transaction.commit()
            assert blocked == 2
            for future in futures:
                future.result(timeout=10)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        with owner.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT verified_count,target_count,state::text,completed_at IS NOT NULL "
                    "FROM retention_operation WHERE id=:id"
                ),
                {"id": operation_id},
            ).one() == (2, 2, "VERIFIED", True)
    finally:
        owner.dispose()
        retention_one.dispose()
        retention_two.dispose()
