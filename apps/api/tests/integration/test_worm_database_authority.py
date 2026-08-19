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
    other_org = uuid.uuid4()
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
    finally:
        owner.dispose()
        app.dispose()


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
