"""Real-role behavior proofs for ordinary purge and hold-release authority."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa


@dataclass(frozen=True)
class OrdinarySeed:
    org_id: uuid.UUID
    user_id: uuid.UUID
    framework_id: uuid.UUID
    policy_id: uuid.UUID
    record_id: uuid.UUID
    blob_sha256: str
    bucket: str
    object_key: str
    object_version_id: str
    disposition_event_id: uuid.UUID


def _engine(database_authority_dsns: dict[str, str], role: str) -> sa.Engine:
    return sa.create_engine(database_authority_dsns[role])


def _insert_org(connection: sa.Connection) -> uuid.UUID:
    org_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO organization (id,legal_name,short_code) VALUES (:id,:name,:short_code)"
        ),
        {
            "id": org_id,
            "name": f"Ordinary authority {org_id}",
            "short_code": f"OA-{org_id.hex[:12]}",
        },
    )
    return org_id


def _seed_ordinary_owner(
    connection: sa.Connection,
    *,
    logical_hold: bool = False,
    policy_duration: str = "P1Y",
    physical_hold: bool = True,
) -> OrdinarySeed:
    org_id = _insert_org(connection)
    user_id = uuid.uuid4()
    framework_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    record_id = uuid.uuid4()
    disposition_event_id = uuid.uuid4()
    blob_sha256 = uuid.uuid4().hex * 2
    object_version_id = f"version-{uuid.uuid4()}"
    bucket = f"ordinary-{org_id.hex[:12]}"
    object_key = f"records/{blob_sha256}"
    now = datetime.now(UTC)

    connection.execute(
        sa.text(
            """
            INSERT INTO app_user (id,org_id,keycloak_subject,display_name)
            VALUES (:id,:org_id,:subject,'Ordinary authority actor')
            """
        ),
        {"id": user_id, "org_id": org_id, "subject": f"ordinary-{user_id}"},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO framework (id,org_id,code,name,is_active,is_authorable)
            VALUES (:id,:org_id,:code,'Ordinary authority framework',true,false)
            """
        ),
        {"id": framework_id, "org_id": org_id, "code": f"oa:{framework_id}"},
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO retention_policy
                (id,org_id,name,duration,worm_lock_period,disposition_action)
            VALUES
                (:id,:org_id,:name,:duration,:worm_lock_period,'DESTROY')
            """
        ),
        {
            "id": policy_id,
            "org_id": org_id,
            "name": f"ordinary-policy-{policy_id}",
            "duration": policy_duration,
            "worm_lock_period": policy_duration,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                 current_state,is_singleton,classification,acknowledgement_required,created_by)
            VALUES
                (:id,:org_id,:framework_id,'RECORD',:identifier,'Ordinary authority record',
                 :user_id,'Draft',false,'Internal',false,:user_id)
            """
        ),
        {
            "id": record_id,
            "org_id": org_id,
            "framework_id": framework_id,
            "identifier": f"OA-REC-{record_id}",
            "user_id": user_id,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO record
                (id,org_id,record_type,captured_by,content_hash_version,
                 retention_policy_id,disposition_state,legal_hold)
            VALUES
                (:id,:org_id,'EVIDENCE',:user_id,2,:policy_id,'DISPOSED',:logical_hold)
            """
        ),
        {
            "id": record_id,
            "org_id": org_id,
            "user_id": user_id,
            "policy_id": policy_id,
            "logical_hold": logical_hold,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO blob
                (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                 worm_locked,worm_enforced_mode,worm_asserted_retain_until,worm_asserted_at,
                 worm_retain_until,worm_retention_verified_at,worm_legal_hold,
                 worm_legal_hold_verified_at,sse)
            VALUES
                (:sha256,:org_id,1,'application/octet-stream',:bucket,:object_key,
                 :object_version_id,true,'GOVERNANCE',:retain_until,:now,:retain_until,:now,
                 :physical_hold,:now,false)
            """
        ),
        {
            "sha256": blob_sha256,
            "org_id": org_id,
            "bucket": bucket,
            "object_key": object_key,
            "object_version_id": object_version_id,
            "retain_until": now + timedelta(days=365),
            "physical_hold": physical_hold,
            "now": now,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO evidence_blob
                (id,org_id,record_id,blob_sha256,is_original,created_by)
            VALUES
                (:id,:org_id,:record_id,:blob_sha256,true,:user_id)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org_id": org_id,
            "record_id": record_id,
            "blob_sha256": blob_sha256,
            "user_id": user_id,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO disposition_event
                (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                 is_worm_destroy,legal_basis)
            VALUES
                (:id,:org_id,:record_id,'DESTROY',true,:policy_id,:user_id,
                 false,'ordinary retention expiry')
            """
        ),
        {
            "id": disposition_event_id,
            "org_id": org_id,
            "record_id": record_id,
            "policy_id": policy_id,
            "user_id": user_id,
        },
    )
    return OrdinarySeed(
        org_id=org_id,
        user_id=user_id,
        framework_id=framework_id,
        policy_id=policy_id,
        record_id=record_id,
        blob_sha256=blob_sha256,
        bucket=bucket,
        object_key=object_key,
        object_version_id=object_version_id,
        disposition_event_id=disposition_event_id,
    )


def _add_owner(
    connection: sa.Connection,
    seed: OrdinarySeed,
    *,
    logical_hold: bool,
    policy_duration: str,
) -> uuid.UUID:
    policy_id = uuid.uuid4()
    record_id = uuid.uuid4()
    connection.execute(
        sa.text(
            """
            INSERT INTO retention_policy
                (id,org_id,name,duration,worm_lock_period,disposition_action)
            VALUES
                (:id,:org_id,:name,:duration,:duration,'RETAIN_PERMANENT')
            """
        ),
        {
            "id": policy_id,
            "org_id": seed.org_id,
            "name": f"additional-owner-{policy_id}",
            "duration": policy_duration,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                 current_state,is_singleton,classification,acknowledgement_required,created_by)
            VALUES
                (:id,:org_id,:framework_id,'RECORD',:identifier,'Additional WORM owner',
                 :user_id,'Draft',false,'Internal',false,:user_id)
            """
        ),
        {
            "id": record_id,
            "org_id": seed.org_id,
            "framework_id": seed.framework_id,
            "identifier": f"OA-OWNER-{record_id}",
            "user_id": seed.user_id,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO record
                (id,org_id,record_type,captured_by,content_hash_version,
                 retention_policy_id,disposition_state,legal_hold)
            VALUES
                (:id,:org_id,'EVIDENCE',:user_id,2,:policy_id,'ACTIVE',:logical_hold)
            """
        ),
        {
            "id": record_id,
            "org_id": seed.org_id,
            "user_id": seed.user_id,
            "policy_id": policy_id,
            "logical_hold": logical_hold,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO evidence_blob
                (id,org_id,record_id,blob_sha256,is_original,created_by)
            VALUES
                (:id,:org_id,:record_id,:blob_sha256,true,:user_id)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org_id": seed.org_id,
            "record_id": record_id,
            "blob_sha256": seed.blob_sha256,
            "user_id": seed.user_id,
        },
    )
    return record_id


def _insert_hold_operation(
    connection: sa.Connection,
    seed: OrdinarySeed,
) -> tuple[uuid.UUID, str]:
    operation_id = uuid.uuid4()
    canonical_bytes = f"ordinary-hold-release:{operation_id}".encode()
    canonical_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    connection.execute(
        sa.text(
            """
            INSERT INTO worm_hold_release_operation
                (id,org_id,record_id,blob_sha256,object_version_id,initiated_by_user_id,
                 idempotency_key,normalized_release_basis,canonical_bytes,canonical_sha256,
                 owner_snapshot_sha256,state)
            VALUES
                (:id,:org_id,:record_id,:blob_sha256,:object_version_id,:user_id,
                 :idempotency_key,'approved ordinary domain release',:canonical_bytes,
                 :canonical_sha256,:owner_snapshot_sha256,'PENDING_AUTHORIZATION')
            """
        ),
        {
            "id": operation_id,
            "org_id": seed.org_id,
            "record_id": seed.record_id,
            "blob_sha256": seed.blob_sha256,
            "object_version_id": seed.object_version_id,
            "user_id": seed.user_id,
            "idempotency_key": str(operation_id),
            "canonical_bytes": canonical_bytes,
            "canonical_sha256": canonical_sha256,
            "owner_snapshot_sha256": hashlib.sha256(seed.blob_sha256.encode()).hexdigest(),
        },
    )
    return operation_id, canonical_sha256


def _authorize(
    connection: sa.Connection,
    operation_id: uuid.UUID,
    digest: str,
    *,
    identity: str = "host-ordinary-authorizer",
) -> int:
    return int(
        connection.execute(
            sa.text(
                "SELECT easysynq_authorize_hold_release"
                "(:operation_id,:digest,:identity,clock_timestamp())"
            ),
            {"operation_id": operation_id, "digest": digest, "identity": identity},
        ).scalar_one()
    )


def _claim_hold_operations(
    connection: sa.Connection,
) -> list[sa.RowMapping]:
    return list(
        connection.execute(
            sa.text("SELECT * FROM easysynq_claim_hold_releases(100,clock_timestamp())")
        )
        .mappings()
        .all()
    )


def test_ordinary_exact_purge_resolves_coordinates_and_retries_without_r27_bypass(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)

        with retention.begin() as connection:
            marker_id = connection.execute(
                sa.text(
                    "SELECT easysynq_enqueue_ordinary_exact_purge"
                    "(:record_id,:event_id,:blob_sha256)"
                ),
                {
                    "record_id": seed.record_id,
                    "event_id": seed.disposition_event_id,
                    "blob_sha256": seed.blob_sha256,
                },
            ).scalar_one()

        with owner.connect() as connection:
            marker = connection.execute(
                sa.text(
                    """
                    SELECT org_id,record_id,disposition_event_id,bucket,object_key,
                           object_version_id,bypass_governance,r27_request_id,r27_execution_id,
                           state::text,attempt_count
                    FROM pending_blob_purge WHERE id=:marker_id
                    """
                ),
                {"marker_id": marker_id},
            ).one()
            assert marker == (
                seed.org_id,
                seed.record_id,
                seed.disposition_event_id,
                seed.bucket,
                seed.object_key,
                seed.object_version_id,
                False,
                None,
                None,
                "PENDING",
                0,
            )

        with retention.begin() as connection:
            claimed = (
                connection.execute(
                    sa.text(
                        "SELECT * FROM easysynq_claim_ordinary_exact_purges(100,clock_timestamp())"
                    )
                )
                .mappings()
                .all()
            )
            target = next(row for row in claimed if row["marker_id"] == marker_id)
            assert target["blob_sha256"] == seed.blob_sha256
            assert target["bucket"] == seed.bucket
            assert target["object_key"] == seed.object_key
            assert target["object_version_id"] == seed.object_version_id
            connection.execute(
                sa.text(
                    "SELECT easysynq_fail_ordinary_exact_purge"
                    "(:marker_id,'OBJECT_STORE_RETRY','temporary failure',clock_timestamp())"
                ),
                {"marker_id": marker_id},
            )

        with retention.begin() as connection:
            retried = (
                connection.execute(
                    sa.text(
                        "SELECT * FROM easysynq_claim_ordinary_exact_purges(100,clock_timestamp())"
                    )
                )
                .mappings()
                .all()
            )
            assert marker_id in {row["marker_id"] for row in retried}
            connection.execute(
                sa.text(
                    "SELECT easysynq_record_ordinary_exact_purge(:marker_id,clock_timestamp())"
                ),
                {"marker_id": marker_id},
            )

        with owner.connect() as connection:
            state = connection.execute(
                sa.text(
                    """
                    SELECT state::text,attempt_count,error_code,error_detail,
                           completed_at IS NOT NULL
                    FROM pending_blob_purge WHERE id=:marker_id
                    """
                ),
                {"marker_id": marker_id},
            ).one()
            assert state == ("VERIFIED", 2, None, None, True)
            assert connection.execute(
                sa.text("SELECT purged_at,purge_execution_id FROM blob WHERE sha256=:blob_sha256"),
                {"blob_sha256": seed.blob_sha256},
            ).one() == (None, None)
    finally:
        owner.dispose()
        retention.dispose()


def test_ordinary_enqueue_rejects_wrong_event_blob_org_and_r27_authority(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            other = _seed_ordinary_owner(connection)
            wrong_org_id = _insert_org(connection)
            wrong_org_event_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    """
                    INSERT INTO disposition_event
                        (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                         is_worm_destroy,legal_basis)
                    VALUES
                        (:id,:org_id,:record_id,'DESTROY',true,:policy_id,:user_id,
                         false,'cross-organization event')
                    """
                ),
                {
                    "id": wrong_org_event_id,
                    "org_id": wrong_org_id,
                    "record_id": seed.record_id,
                    "policy_id": seed.policy_id,
                    "user_id": seed.user_id,
                },
            )

            r27_request_id = uuid.uuid4()
            r27_execution_id = uuid.uuid4()
            r27_event_id = uuid.uuid4()
            requester_audit_event_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO audit_event
                        (org_id,occurred_at,actor_id,actor_type,event_type,object_type,
                         object_id,scope_ref,reason)
                    VALUES
                        (:org_id,now(),:user_id,'user','RECORD_WORM_DESTROY_REQUESTED',
                         'record',:record_id,'record','ordinary-authority-fixture')
                    RETURNING id
                    """
                ),
                {
                    "org_id": seed.org_id,
                    "user_id": seed.user_id,
                    "record_id": seed.record_id,
                },
            ).scalar_one()
            connection.execute(
                sa.text(
                    """
                    INSERT INTO r27_request
                        (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256,
                         state,requester_user_id,requester_audit_event_id,requested_at)
                    VALUES
                        (:id,:org_id,:record_id,'court order',:digest,'EXECUTED',:user_id,
                         :requester_audit_event_id,now())
                    """
                ),
                {
                    "id": r27_request_id,
                    "org_id": seed.org_id,
                    "record_id": seed.record_id,
                    "digest": "d" * 64,
                    "user_id": seed.user_id,
                    "requester_audit_event_id": requester_audit_event_id,
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO r27_execution
                        (id,request_id,execution_id,state,claimed_at)
                    VALUES (:id,:request_id,:execution_id,'EXECUTED',now())
                    """
                ),
                {
                    "id": r27_execution_id,
                    "request_id": r27_request_id,
                    "execution_id": uuid.uuid4(),
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO disposition_event
                        (id,org_id,record_id,action,tombstone,approved_by,requested_by,
                         is_worm_destroy,legal_basis,r27_request_id,r27_execution_id)
                    VALUES
                        (:id,:org_id,:record_id,'DESTROY',true,:user_id,:user_id,
                         true,'court order',:request_id,:execution_id)
                    """
                ),
                {
                    "id": r27_event_id,
                    "org_id": seed.org_id,
                    "record_id": seed.record_id,
                    "user_id": seed.user_id,
                    "request_id": r27_request_id,
                    "execution_id": r27_execution_id,
                },
            )

        refused_inputs = (
            (seed.record_id, other.disposition_event_id, seed.blob_sha256),
            (seed.record_id, seed.disposition_event_id, uuid.uuid4().hex * 2),
            (seed.record_id, wrong_org_event_id, seed.blob_sha256),
            (seed.record_id, r27_event_id, seed.blob_sha256),
        )
        for record_id, event_id, blob_sha256 in refused_inputs:
            with pytest.raises(sa.exc.DBAPIError, match="ordinary_exact_purge_refused"):
                with retention.begin() as connection:
                    connection.execute(
                        sa.text(
                            "SELECT easysynq_enqueue_ordinary_exact_purge"
                            "(:record_id,:event_id,:blob_sha256)"
                        ),
                        {
                            "record_id": record_id,
                            "event_id": event_id,
                            "blob_sha256": blob_sha256,
                        },
                    )

        with owner.connect() as connection:
            count = connection.execute(
                sa.text(
                    "SELECT count(*) FROM pending_blob_purge "
                    "WHERE disposition_event_id=ANY(:event_ids)"
                ),
                {
                    "event_ids": [
                        other.disposition_event_id,
                        wrong_org_event_id,
                        r27_event_id,
                    ]
                },
            ).scalar_one()
            assert count == 0
    finally:
        owner.dispose()
        retention.dispose()


def test_ordinary_roles_cannot_forge_state_or_execute_each_others_functions(
    database_authority_dsns: dict[str, str],
) -> None:
    app = _engine(database_authority_dsns, "easysynq_app")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    try:
        denied = (
            (retention, "UPDATE pending_blob_purge SET state='VERIFIED' WHERE false"),
            (authorizer, "UPDATE worm_hold_release_operation SET state='AUTHORIZED' WHERE false"),
            (maintenance, "UPDATE worm_hold_release_operation SET state='VERIFIED' WHERE false"),
            (
                app,
                "INSERT INTO worm_hold_release_authorization "
                "(operation_id,canonical_sha256,host_operator_identity,"
                "authorizing_audit_event_id,authorized_at,authorizer_role) "
                "VALUES (gen_random_uuid(),"
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
                "'forged',1,now(),'forged')",
            ),
            (
                maintenance,
                "SELECT easysynq_authorize_hold_release"
                "(gen_random_uuid(),'x','forged',clock_timestamp())",
            ),
            (authorizer, "SELECT * FROM easysynq_claim_hold_releases(1,clock_timestamp())"),
            (
                maintenance,
                "SELECT easysynq_enqueue_ordinary_exact_purge"
                "(gen_random_uuid(),gen_random_uuid(),'x')",
            ),
            (
                retention,
                "SELECT easysynq_record_ordinary_hold_release"
                "('x','version',gen_random_uuid(),clock_timestamp())",
            ),
        )
        for engine, statement in denied:
            with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
                with engine.begin() as connection:
                    connection.execute(sa.text(statement))
    finally:
        app.dispose()
        retention.dispose()
        authorizer.dispose()
        maintenance.dispose()


def test_hold_authorization_atomically_binds_exact_digest_and_audit(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)

        with pytest.raises(sa.exc.DBAPIError, match="hold_release_authorization_refused"):
            with authorizer.begin() as connection:
                _authorize(connection, operation_id, "f" * 64)

        with authorizer.begin() as connection:
            audit_id = _authorize(connection, operation_id, digest)

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT state::text FROM worm_hold_release_operation WHERE id=:operation_id"
                    ),
                    {"operation_id": operation_id},
                ).scalar_one()
                == "AUTHORIZED"
            )
            authorization = connection.execute(
                sa.text(
                    """
                    SELECT canonical_sha256,host_operator_identity,authorizing_audit_event_id,
                           authorizer_role
                    FROM worm_hold_release_authorization WHERE operation_id=:operation_id
                    """
                ),
                {"operation_id": operation_id},
            ).one()
            assert authorization == (
                digest,
                "host-ordinary-authorizer",
                audit_id,
                "easysynq_hold_authorizer",
            )
            audit = connection.execute(
                sa.text(
                    """
                    SELECT actor_type::text,event_type::text,object_type::text,object_id,
                           scope_ref,reason,after->>'operator_identity',after->>'operation_id'
                    FROM audit_event WHERE id=:audit_id
                    """
                ),
                {"audit_id": audit_id},
            ).one()
            assert audit == (
                "system",
                "RECORD_LEGAL_HOLD_RELEASE_AUTHORIZED",
                "record",
                seed.record_id,
                "record",
                "ordinary-hold-release-authorized",
                "host-ordinary-authorizer",
                str(operation_id),
            )
    finally:
        owner.dispose()
        authorizer.dispose()


def test_hold_authorization_rolls_back_audit_when_fresh_owner_check_fails(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection, logical_hold=True)
            operation_id, digest = _insert_hold_operation(connection, seed)

        with pytest.raises(sa.exc.DBAPIError, match="hold_release_authorization_refused"):
            with authorizer.begin() as connection:
                _authorize(connection, operation_id, digest)

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT state::text FROM worm_hold_release_operation WHERE id=:operation_id"
                    ),
                    {"operation_id": operation_id},
                ).scalar_one()
                == "PENDING_AUTHORIZATION"
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM worm_hold_release_authorization "
                        "WHERE operation_id=:operation_id"
                    ),
                    {"operation_id": operation_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event "
                        "WHERE reason='ordinary-hold-release-authorized' "
                        "AND after->>'operation_id'=:operation_id"
                    ),
                    {"operation_id": str(operation_id)},
                ).scalar_one()
                == 0
            )
    finally:
        owner.dispose()
        authorizer.dispose()


def test_hold_claim_retries_failed_operation_with_same_authorization(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
        with authorizer.begin() as connection:
            audit_id = _authorize(connection, operation_id, digest)

        with maintenance.begin() as connection:
            first_claim = _claim_hold_operations(connection)
            target = next(row for row in first_claim if row["operation_id"] == operation_id)
            assert target["record_id"] == seed.record_id
            assert target["blob_sha256"] == seed.blob_sha256
            assert target["object_version_id"] == seed.object_version_id
            connection.execute(
                sa.text(
                    "SELECT easysynq_fail_hold_release"
                    "(:operation_id,'OBJECT_STORE_RETRY','temporary failure',clock_timestamp())"
                ),
                {"operation_id": operation_id},
            )

        with maintenance.begin() as connection:
            second_claim = _claim_hold_operations(connection)
            assert operation_id in {row["operation_id"] for row in second_claim}

        with owner.connect() as connection:
            operation = connection.execute(
                sa.text(
                    """
                    SELECT state::text,attempt_count,error_code,error_detail
                    FROM worm_hold_release_operation WHERE id=:operation_id
                    """
                ),
                {"operation_id": operation_id},
            ).one()
            assert operation == ("RUNNING", 2, None, None)
            assert (
                connection.execute(
                    sa.text(
                        "SELECT authorizing_audit_event_id FROM worm_hold_release_authorization "
                        "WHERE operation_id=:operation_id"
                    ),
                    {"operation_id": operation_id},
                ).scalar_one()
                == audit_id
            )
    finally:
        owner.dispose()
        authorizer.dispose()
        maintenance.dispose()


def test_hold_claim_rechecks_all_current_owners_before_running(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
        with authorizer.begin() as connection:
            _authorize(connection, operation_id, digest)
        with owner.begin() as connection:
            _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="PERMANENT",
            )

        with maintenance.begin() as connection:
            claimed = _claim_hold_operations(connection)
            assert operation_id not in {row["operation_id"] for row in claimed}

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT state::text FROM worm_hold_release_operation WHERE id=:operation_id"
                    ),
                    {"operation_id": operation_id},
                ).scalar_one()
                == "AUTHORIZED"
            )
            assert (
                connection.execute(
                    sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:blob_sha256"),
                    {"blob_sha256": seed.blob_sha256},
                ).scalar_one()
                is True
            )
    finally:
        owner.dispose()
        authorizer.dispose()
        maintenance.dispose()


def test_hold_result_rechecks_owner_releases_exact_version_and_appends_audit(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
        with authorizer.begin() as connection:
            _authorize(connection, operation_id, digest)
        with maintenance.begin() as connection:
            claimed = _claim_hold_operations(connection)
            assert operation_id in {row["operation_id"] for row in claimed}

        with pytest.raises(sa.exc.DBAPIError, match="ordinary_hold_release_refused"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_ordinary_hold_release"
                        "(:blob_sha256,'wrong-version',:operation_id,clock_timestamp())"
                    ),
                    {
                        "blob_sha256": seed.blob_sha256,
                        "operation_id": operation_id,
                    },
                )

        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_record_ordinary_hold_release"
                    "(:blob_sha256,:object_version_id,:operation_id,clock_timestamp())"
                ),
                {
                    "blob_sha256": seed.blob_sha256,
                    "object_version_id": seed.object_version_id,
                    "operation_id": operation_id,
                },
            )

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:blob_sha256"),
                    {"blob_sha256": seed.blob_sha256},
                ).scalar_one()
                is False
            )
            assert connection.execute(
                sa.text(
                    "SELECT state::text,completed_at IS NOT NULL "
                    "FROM worm_hold_release_operation WHERE id=:operation_id"
                ),
                {"operation_id": operation_id},
            ).one() == ("VERIFIED", True)
            audit = connection.execute(
                sa.text(
                    """
                    SELECT actor_type::text,event_type::text,object_type::text,object_id,
                           scope_ref,reason,after->>'operation_id'
                    FROM audit_event
                    WHERE reason='ordinary-hold-release-verified'
                      AND after->>'operation_id'=:operation_id
                    """
                ),
                {"operation_id": str(operation_id)},
            ).one()
            assert audit == (
                "system",
                "RECORD_LEGAL_HOLD_RELEASED",
                "record",
                seed.record_id,
                "record",
                "ordinary-hold-release-verified",
                str(operation_id),
            )
    finally:
        owner.dispose()
        authorizer.dispose()
        maintenance.dispose()


def test_hold_result_refuses_new_logical_hold_after_claim(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
        with authorizer.begin() as connection:
            _authorize(connection, operation_id, digest)
        with maintenance.begin() as connection:
            claimed = _claim_hold_operations(connection)
            assert operation_id in {row["operation_id"] for row in claimed}
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE record SET legal_hold=true WHERE id=:record_id"),
                {"record_id": seed.record_id},
            )

        with pytest.raises(sa.exc.DBAPIError, match="ordinary_hold_release_refused"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_ordinary_hold_release"
                        "(:blob_sha256,:object_version_id,:operation_id,clock_timestamp())"
                    ),
                    {
                        "blob_sha256": seed.blob_sha256,
                        "object_version_id": seed.object_version_id,
                        "operation_id": operation_id,
                    },
                )

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:blob_sha256"),
                    {"blob_sha256": seed.blob_sha256},
                ).scalar_one()
                is True
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT state::text FROM worm_hold_release_operation WHERE id=:operation_id"
                    ),
                    {"operation_id": operation_id},
                ).scalar_one()
                == "RUNNING"
            )
    finally:
        owner.dispose()
        authorizer.dispose()
        maintenance.dispose()
