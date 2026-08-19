"""Real-role behavior proofs for ordinary purge and hold-release authority."""

from __future__ import annotations

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

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


def _wait_for_named_lock(
    engine: sa.Engine, application_name: str, *, timeout_seconds: float = 5
) -> bool:
    deadline = monotonic() + timeout_seconds
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


def _apply_hold_authority_drift(
    connection: sa.Connection,
    seed: OrdinarySeed,
    operation_id: uuid.UUID,
    drift: str,
) -> None:
    other_org_id = _insert_org(connection)
    connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
    if drift == "OPERATION_ORG":
        connection.execute(
            sa.text("UPDATE worm_hold_release_operation SET org_id=:org WHERE id=:id"),
            {"org": other_org_id, "id": operation_id},
        )
    elif drift == "RECORD_ORG":
        connection.execute(
            sa.text("UPDATE record SET org_id=:org WHERE id=:id"),
            {"org": other_org_id, "id": seed.record_id},
        )
    elif drift == "BLOB_ORG":
        connection.execute(
            sa.text("UPDATE blob SET org_id=:org WHERE sha256=:sha"),
            {"org": other_org_id, "sha": seed.blob_sha256},
        )
    elif drift == "BLOB_NON_WORM":
        connection.execute(
            sa.text(
                "UPDATE blob SET worm_locked=false,object_version_id=NULL,"
                "worm_enforced_mode=NULL,worm_asserted_retain_until=NULL,worm_asserted_at=NULL,"
                "worm_retain_until=NULL,worm_retention_verified_at=NULL,worm_legal_hold=NULL,"
                "worm_legal_hold_verified_at=NULL WHERE sha256=:sha"
            ),
            {"sha": seed.blob_sha256},
        )
    elif drift == "ACTOR_ORG":
        connection.execute(
            sa.text("UPDATE app_user SET org_id=:org WHERE id=:id"),
            {"org": other_org_id, "id": seed.user_id},
        )
    elif drift == "EDGE_ORG":
        connection.execute(
            sa.text("UPDATE evidence_blob SET org_id=:org WHERE blob_sha256=:sha"),
            {"org": other_org_id, "sha": seed.blob_sha256},
        )
    elif drift == "EDGE_RECORD":
        alternate_record_id = uuid.uuid4()
        connection.execute(
            sa.text(
                """
                INSERT INTO documented_information
                    (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                     current_state,is_singleton,classification,
                     acknowledgement_required,created_by)
                VALUES (:id,:org,:framework,'RECORD',:identifier,'Alternate edge record',:user,
                        'Draft',false,'Internal',false,:user)
                """
            ),
            {
                "id": alternate_record_id,
                "org": seed.org_id,
                "framework": seed.framework_id,
                "identifier": f"ALT-EDGE-{alternate_record_id}",
                "user": seed.user_id,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO record"
                "(id,org_id,record_type,captured_by,content_hash_version,"
                "retention_policy_id,disposition_state,legal_hold) "
                "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'DISPOSED',false)"
            ),
            {
                "id": alternate_record_id,
                "org": seed.org_id,
                "user": seed.user_id,
                "policy": seed.policy_id,
            },
        )
        connection.execute(
            sa.text("UPDATE evidence_blob SET record_id=:record WHERE blob_sha256=:sha"),
            {"record": alternate_record_id, "sha": seed.blob_sha256},
        )
    elif drift == "EDGE_BLOB":
        alternate_sha256 = uuid.uuid4().hex * 2
        connection.execute(
            sa.text(
                """
                INSERT INTO blob
                    (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                     worm_locked,worm_enforced_mode,worm_asserted_retain_until,worm_asserted_at,
                     worm_retain_until,worm_retention_verified_at,worm_legal_hold,
                     worm_legal_hold_verified_at,sse)
                VALUES
                    (:sha,:org,1,'application/octet-stream',:bucket,:key,:version,true,
                     'GOVERNANCE',clock_timestamp()+interval '1 year',clock_timestamp(),
                     clock_timestamp()+interval '2 years',clock_timestamp(),true,
                     clock_timestamp(),false)
                """
            ),
            {
                "sha": alternate_sha256,
                "org": seed.org_id,
                "bucket": f"alternate-{uuid.uuid4()}",
                "key": f"alternate/{uuid.uuid4()}",
                "version": f"version-{uuid.uuid4()}",
            },
        )
        connection.execute(
            sa.text("UPDATE evidence_blob SET blob_sha256=:alternate WHERE blob_sha256=:sha"),
            {"alternate": alternate_sha256, "sha": seed.blob_sha256},
        )
    elif drift == "EDGE_MISSING":
        connection.execute(
            sa.text("DELETE FROM evidence_blob WHERE blob_sha256=:sha"),
            {"sha": seed.blob_sha256},
        )
    elif drift == "VERSION":
        connection.execute(
            sa.text(
                "UPDATE worm_hold_release_operation SET object_version_id='wrong-version' "
                "WHERE id=:id"
            ),
            {"id": operation_id},
        )
    else:  # pragma: no cover - the parameter tables below are closed
        raise AssertionError(drift)


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


def _run_named_owner_update(
    owner: sa.Engine,
    *,
    application_name: str,
    statement: str,
    parameters: dict[str, object],
) -> str:
    with owner.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            sa.text("SELECT set_config('application_name',:name,true)"),
            {"name": application_name},
        )
        connection.execute(sa.text("SET LOCAL statement_timeout='5s'"))
        connection.execute(sa.text(statement), parameters)
        transaction.rollback()
    return "ok"


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
            purged_at, purge_execution_id = connection.execute(
                sa.text("SELECT purged_at,purge_execution_id FROM blob WHERE sha256=:blob_sha256"),
                {"blob_sha256": seed.blob_sha256},
            ).one()
            assert purged_at is not None
            assert purge_execution_id is None
    finally:
        owner.dispose()
        retention.dispose()


@pytest.mark.parametrize(
    ("owner_kind", "owner_state", "must_block"),
    (
        ("EVIDENCE", "ACTIVE", True),
        ("EVIDENCE", "DUE_FOR_REVIEW", True),
        ("EVIDENCE", "ON_HOLD", True),
        ("EVIDENCE", "DISPOSED", False),
        ("DOCUMENT", None, True),
    ),
)
def test_ordinary_purge_claim_uses_closed_live_owner_predicate(
    database_authority_dsns: dict[str, str],
    owner_kind: str,
    owner_state: str | None,
    must_block: bool,
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
        with retention.begin() as connection:
            marker_id = connection.execute(
                sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                {
                    "record": seed.record_id,
                    "event": seed.disposition_event_id,
                    "sha": seed.blob_sha256,
                },
            ).scalar_one()

        with owner.begin() as connection:
            if owner_kind == "DOCUMENT":
                from tests.integration.test_r27_database_authority import (
                    _add_permanent_document_owner,
                )

                _add_permanent_document_owner(connection, seed, "POLICY")
            else:
                owner_record_id = _add_owner(
                    connection,
                    seed,
                    logical_hold=owner_state == "ON_HOLD",
                    policy_duration="P1Y",
                )
                connection.execute(
                    sa.text("UPDATE record SET disposition_state=:state WHERE id=:id"),
                    {"state": owner_state, "id": owner_record_id},
                )

        with owner.connect() as connection:
            before = _ordinary_result_snapshot(connection, marker_id, seed)
        with retention.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_ordinary_exact_purges(100,clock_timestamp())")
            ).mappings()
            claimed_ids = {row["marker_id"] for row in claimed}
        assert (marker_id not in claimed_ids) is must_block

        with owner.connect() as connection:
            expected_state = "PENDING" if must_block else "RUNNING"
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM pending_blob_purge WHERE id=:id"),
                    {"id": marker_id},
                ).scalar_one()
                == expected_state
            )
            if must_block:
                assert _ordinary_result_snapshot(connection, marker_id, seed) == before
    finally:
        owner.dispose()
        retention.dispose()


def test_ordinary_purge_claim_holds_blob_lock_until_transaction_end(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
        with retention.begin() as connection:
            marker_id = connection.execute(
                sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                {
                    "record": seed.record_id,
                    "event": seed.disposition_event_id,
                    "sha": seed.blob_sha256,
                },
            ).scalar_one()

        with retention.connect() as claim_connection:
            claim_transaction = claim_connection.begin()
            try:
                claimed_ids = {
                    row.marker_id
                    for row in claim_connection.execute(
                        sa.text(
                            "SELECT * FROM easysynq_claim_ordinary_exact_purges"
                            "(100,clock_timestamp())"
                        )
                    )
                }
                assert marker_id in claimed_ids

                with owner.connect() as owner_connection:
                    owner_transaction = owner_connection.begin()
                    try:
                        owner_connection.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                        with pytest.raises(sa.exc.DBAPIError, match="lock timeout"):
                            _add_owner(
                                owner_connection,
                                seed,
                                logical_hold=False,
                                policy_duration="P1Y",
                            )
                    finally:
                        owner_transaction.rollback()
            finally:
                claim_transaction.rollback()
    finally:
        owner.dispose()
        retention.dispose()


@pytest.mark.parametrize(
    "boundary",
    ("PURGE_CLAIM", "PURGE_RESULT", "HOLD_CLAIM", "HOLD_RESULT"),
)
def test_ordinary_authority_rechecks_after_waiting_for_owner_key_share(
    database_authority_dsns: dict[str, str], boundary: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    hold_authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    hold_maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    application_name = f"ordinary-owner-race-{uuid.uuid4()}"
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
        with retention.begin() as connection:
            marker_id = connection.execute(
                sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                {
                    "record": seed.record_id,
                    "event": seed.disposition_event_id,
                    "sha": seed.blob_sha256,
                },
            ).scalar_one()
            if boundary == "PURGE_RESULT":
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
        with hold_authorizer.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_authorize_hold_release"
                    "(:id,:digest,'ordinary-race-authorizer',clock_timestamp())"
                ),
                {"id": operation_id, "digest": digest},
            )
        if boundary == "HOLD_RESULT":
            with hold_maintenance.begin() as connection:
                claimed_ids = {
                    row.operation_id
                    for row in connection.execute(
                        sa.text("SELECT * FROM easysynq_claim_hold_releases(100,clock_timestamp())")
                    )
                }
                assert operation_id in claimed_ids

        def invoke_boundary() -> tuple[str, object]:
            engine = retention if boundary.startswith("PURGE") else hold_maintenance
            try:
                with engine.begin() as connection:
                    connection.execute(
                        sa.text("SELECT set_config('application_name',:name,false)"),
                        {"name": application_name},
                    )
                    connection.execute(sa.text("SET LOCAL statement_timeout='10s'"))
                    if boundary == "PURGE_CLAIM":
                        rows = connection.execute(
                            sa.text(
                                "SELECT * FROM easysynq_claim_ordinary_exact_purges"
                                "(100,clock_timestamp())"
                            )
                        ).all()
                        return "rows", {row.marker_id for row in rows}
                    if boundary == "PURGE_RESULT":
                        connection.execute(
                            sa.text(
                                "SELECT easysynq_record_ordinary_exact_purge(:id,clock_timestamp())"
                            ),
                            {"id": marker_id},
                        )
                        return "ok", None
                    if boundary == "HOLD_CLAIM":
                        rows = connection.execute(
                            sa.text(
                                "SELECT * FROM easysynq_claim_hold_releases(100,clock_timestamp())"
                            )
                        ).all()
                        return "rows", {row.operation_id for row in rows}
                    connection.execute(
                        sa.text(
                            "SELECT easysynq_record_ordinary_hold_release"
                            "(:sha,:version,:id,clock_timestamp())"
                        ),
                        {
                            "sha": seed.blob_sha256,
                            "version": seed.object_version_id,
                            "id": operation_id,
                        },
                    )
                    return "ok", None
            except sa.exc.DBAPIError as error:
                return "error", str(error)

        owner_connection = owner.connect()
        owner_transaction = owner_connection.begin()
        try:
            _add_owner(
                owner_connection,
                seed,
                logical_hold=boundary.startswith("HOLD"),
                policy_duration="P1Y",
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(invoke_boundary)
                blocked = _wait_for_named_lock(owner, application_name)
                owner_transaction.commit()
                outcome, detail = future.result(timeout=15)
        finally:
            if owner_transaction.is_active:
                owner_transaction.rollback()
            owner_connection.close()

        assert blocked
        if boundary == "PURGE_CLAIM":
            assert outcome == "rows" and marker_id not in detail
        elif boundary == "HOLD_CLAIM":
            assert outcome == "rows" and operation_id not in detail
        else:
            expected = (
                "ordinary_purge_result_refused"
                if boundary == "PURGE_RESULT"
                else "ordinary_hold_release_refused"
            )
            assert outcome == "error" and expected in str(detail)

        with owner.connect() as connection:
            marker_state = connection.execute(
                sa.text("SELECT state::text FROM pending_blob_purge WHERE id=:id"),
                {"id": marker_id},
            ).scalar_one()
            operation_state = connection.execute(
                sa.text("SELECT state::text FROM worm_hold_release_operation WHERE id=:id"),
                {"id": operation_id},
            ).scalar_one()
            assert marker_state == ("RUNNING" if boundary == "PURGE_RESULT" else "PENDING")
            assert operation_state == ("RUNNING" if boundary == "HOLD_RESULT" else "AUTHORIZED")
            assert connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": seed.blob_sha256},
            ).scalar_one()
    finally:
        owner.dispose()
        retention.dispose()
        hold_authorizer.dispose()
        hold_maintenance.dispose()


def test_ordinary_authority_refuses_non_read_committed_isolation(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
        with retention.begin() as connection:
            marker_id = connection.execute(
                sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                {
                    "record": seed.record_id,
                    "event": seed.disposition_event_id,
                    "sha": seed.blob_sha256,
                },
            ).scalar_one()
        with pytest.raises(sa.exc.DBAPIError, match="authority_requires_read_committed"):
            with retention.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                with connection.begin():
                    connection.execute(
                        sa.text(
                            "SELECT * FROM easysynq_claim_ordinary_exact_purges"
                            "(100,clock_timestamp())"
                        )
                    ).all()
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM pending_blob_purge WHERE id=:id"),
                    {"id": marker_id},
                ).scalar_one()
                == "PENDING"
            )
    finally:
        owner.dispose()
        retention.dispose()


def _ordinary_result_snapshot(
    connection: sa.Connection,
    marker_id: uuid.UUID,
    seed: OrdinarySeed,
) -> sa.Row:
    return connection.execute(
        sa.text(
            """
            SELECT
              (SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:marker),
              (SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha),
              (SELECT to_jsonb(event) FROM disposition_event event WHERE id=:event),
              (SELECT count(*) FROM audit_event)
            """
        ),
        {
            "marker": marker_id,
            "sha": seed.blob_sha256,
            "event": seed.disposition_event_id,
        },
    ).one()


def test_ordinary_purge_claim_revalidates_exact_source_disposition(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            source_drift = _seed_ordinary_owner(connection)
        with retention.begin() as connection:
            marker_id = connection.execute(
                sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                {
                    "record": source_drift.record_id,
                    "event": source_drift.disposition_event_id,
                    "sha": source_drift.blob_sha256,
                },
            ).scalar_one()
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE disposition_event SET action='ARCHIVE_COLD' WHERE id=:id"),
                {"id": source_drift.disposition_event_id},
            )

        with owner.connect() as connection:
            before = _ordinary_result_snapshot(connection, marker_id, source_drift)
        with retention.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_ordinary_exact_purges(100,clock_timestamp())")
            ).mappings()
            claimed_ids = {row["marker_id"] for row in claimed}
        assert marker_id not in claimed_ids

        with owner.connect() as connection:
            marker = connection.execute(
                sa.text(
                    "SELECT state::text,attempt_count,error_code,error_detail,completed_at "
                    "FROM pending_blob_purge WHERE id=:id"
                ),
                {"id": marker_id},
            ).one()
            assert marker == ("PENDING", 0, None, None, None)
            assert _ordinary_result_snapshot(connection, marker_id, source_drift) == before
    finally:
        owner.dispose()
        retention.dispose()


@pytest.mark.parametrize(
    ("post_claim_change", "accepted"),
    (
        ("ACTIVE_OWNER", False),
        ("DISPOSED_OWNER", True),
        ("SOURCE_ACTION", False),
    ),
)
def test_ordinary_purge_result_revalidates_source_and_live_owners_atomically(
    database_authority_dsns: dict[str, str],
    post_claim_change: str,
    accepted: bool,
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
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
                        "SELECT * FROM easysynq_claim_ordinary_exact_purges(100,clock_timestamp())"
                    )
                )
            }
            assert marker_id in claimed_ids

        with owner.begin() as connection:
            if post_claim_change in {"ACTIVE_OWNER", "DISPOSED_OWNER"}:
                owner_record_id = _add_owner(
                    connection,
                    seed,
                    logical_hold=False,
                    policy_duration="P1Y",
                )
                connection.execute(
                    sa.text("UPDATE record SET disposition_state=:state WHERE id=:id"),
                    {
                        "state": "ACTIVE" if post_claim_change == "ACTIVE_OWNER" else "DISPOSED",
                        "id": owner_record_id,
                    },
                )
            else:
                connection.execute(
                    sa.text("UPDATE disposition_event SET action='ARCHIVE_COLD' WHERE id=:id"),
                    {"id": seed.disposition_event_id},
                )

        with owner.connect() as connection:
            before = _ordinary_result_snapshot(connection, marker_id, seed)

        statement = sa.text(
            "SELECT easysynq_record_ordinary_exact_purge(:marker,clock_timestamp())"
        )
        if accepted:
            with retention.begin() as connection:
                connection.execute(statement, {"marker": marker_id})
        else:
            with pytest.raises(sa.exc.DBAPIError, match="ordinary_purge_result_refused"):
                with retention.begin() as connection:
                    connection.execute(statement, {"marker": marker_id})

        with owner.connect() as connection:
            after = _ordinary_result_snapshot(connection, marker_id, seed)
            if accepted:
                assert after[0]["state"] == "VERIFIED"
                assert after[0]["completed_at"] is not None
                assert before[1]["purged_at"] is None
                assert after[1]["purged_at"] is not None
                assert after[1]["purge_execution_id"] is None
                expected_blob = dict(before[1])
                expected_blob["purged_at"] = after[1]["purged_at"]
                assert after[1] == expected_blob
                assert after[2] == before[2]
                assert after[3] == before[3]
            else:
                assert after == before
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


@pytest.mark.parametrize(
    "drift",
    (
        "OPERATION_ORG",
        "RECORD_ORG",
        "BLOB_ORG",
        "BLOB_NON_WORM",
        "ACTOR_ORG",
        "EDGE_ORG",
        "EDGE_RECORD",
        "EDGE_BLOB",
        "EDGE_MISSING",
        "VERSION",
    ),
)
def test_hold_authorization_requires_exact_same_org_record_edge_and_blob(
    database_authority_dsns: dict[str, str], drift: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
            _apply_hold_authority_drift(connection, seed, operation_id, drift)
        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(operation) FROM worm_hold_release_operation operation
                 WHERE id=:operation),
              (SELECT count(*) FROM worm_hold_release_authorization WHERE operation_id=:operation),
              (SELECT count(*) FROM audit_event
                 WHERE reason='ordinary-hold-release-authorized'
                   AND after->>'operation_id'=:operation_text),
              (SELECT to_jsonb(blob) FROM blob blob WHERE sha256=:sha)
            """
        )
        parameters = {
            "operation": operation_id,
            "operation_text": str(operation_id),
            "sha": seed.blob_sha256,
        }
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()
        with pytest.raises(sa.exc.DBAPIError, match="hold_release_authorization_refused"):
            with authorizer.begin() as connection:
                _authorize(connection, operation_id, digest)
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner.dispose()
        authorizer.dispose()


@pytest.mark.parametrize("boundary", ("CLAIM", "RESULT"))
@pytest.mark.parametrize(
    "drift",
    (
        "OPERATION_ORG",
        "RECORD_ORG",
        "BLOB_ORG",
        "BLOB_NON_WORM",
        "ACTOR_ORG",
        "EDGE_ORG",
        "EDGE_RECORD",
        "EDGE_BLOB",
        "EDGE_MISSING",
        "VERSION",
    ),
)
def test_hold_claim_and_result_revalidate_exact_same_org_authority(
    database_authority_dsns: dict[str, str], boundary: str, drift: str
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
        if boundary == "RESULT":
            with maintenance.begin() as connection:
                assert operation_id in {
                    row["operation_id"] for row in _claim_hold_operations(connection)
                }
        with owner.begin() as connection:
            _apply_hold_authority_drift(connection, seed, operation_id, drift)

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(operation) FROM worm_hold_release_operation operation
                 WHERE id=:operation),
              (SELECT to_jsonb(blob) FROM blob blob WHERE sha256=:sha),
              (SELECT to_jsonb(hold_authorization)
                 FROM worm_hold_release_authorization hold_authorization
                 WHERE operation_id=:operation),
              (SELECT count(*) FROM audit_event)
            """
        )
        parameters = {"operation": operation_id, "sha": seed.blob_sha256}
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()
        if boundary == "CLAIM":
            with maintenance.begin() as connection:
                assert operation_id not in {
                    row["operation_id"] for row in _claim_hold_operations(connection)
                }
        else:
            with pytest.raises(sa.exc.DBAPIError, match="ordinary_hold_release_refused"):
                with maintenance.begin() as connection:
                    connection.execute(
                        sa.text(
                            "SELECT easysynq_record_ordinary_hold_release"
                            "(:sha,:version,:operation,clock_timestamp())"
                        ),
                        {
                            "sha": seed.blob_sha256,
                            "version": seed.object_version_id,
                            "operation": operation_id,
                        },
                    )
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner.dispose()
        authorizer.dispose()
        maintenance.dispose()


@pytest.mark.parametrize(
    ("boundary", "locked_relation", "statement"),
    (
        (
            "AUTHORIZATION",
            "RECORD",
            "UPDATE record SET org_id=:alternate_org WHERE id=:record",
        ),
        (
            "AUTHORIZATION",
            "INITIATING_APP_USER",
            "UPDATE app_user SET org_id=:alternate_org WHERE id=:user",
        ),
        (
            "AUTHORIZATION",
            "BLOB",
            "UPDATE blob SET verified_at=clock_timestamp() WHERE sha256=:sha",
        ),
        ("CLAIM", "RECORD", "UPDATE record SET org_id=:alternate_org WHERE id=:record"),
        (
            "CLAIM",
            "INITIATING_APP_USER",
            "UPDATE app_user SET org_id=:alternate_org WHERE id=:user",
        ),
        ("RESULT", "RECORD", "UPDATE record SET org_id=:alternate_org WHERE id=:record"),
        (
            "RESULT",
            "INITIATING_APP_USER",
            "UPDATE app_user SET org_id=:alternate_org WHERE id=:user",
        ),
    ),
)
def test_hold_authority_locks_exact_tuple_through_caller_transaction(
    database_authority_dsns: dict[str, str],
    boundary: str,
    locked_relation: str,
    statement: str,
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    call_connection: sa.Connection | None = None
    call_transaction: sa.Transaction | None = None
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
            alternate_org_id = _insert_org(connection)
        if boundary != "AUTHORIZATION":
            with authorizer.begin() as connection:
                _authorize(connection, operation_id, digest)
        if boundary == "RESULT":
            with maintenance.begin() as connection:
                assert operation_id in {
                    row["operation_id"] for row in _claim_hold_operations(connection)
                }

        caller = authorizer if boundary == "AUTHORIZATION" else maintenance
        call_connection = caller.connect()
        call_transaction = call_connection.begin()
        if boundary == "AUTHORIZATION":
            _authorize(call_connection, operation_id, digest)
        elif boundary == "CLAIM":
            assert operation_id in {
                row["operation_id"] for row in _claim_hold_operations(call_connection)
            }
        else:
            call_connection.execute(
                sa.text(
                    "SELECT easysynq_record_ordinary_hold_release"
                    "(:sha,:version,:operation,clock_timestamp())"
                ),
                {
                    "sha": seed.blob_sha256,
                    "version": seed.object_version_id,
                    "operation": operation_id,
                },
            )

        application_name = (
            f"t2-hold-{boundary[:5].lower()}-{locked_relation[:8].lower()}-{uuid.uuid4().hex[:8]}"
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _run_named_owner_update,
                owner,
                application_name=application_name,
                statement=statement,
                parameters={
                    "record": seed.record_id,
                    "user": seed.user_id,
                    "sha": seed.blob_sha256,
                    "alternate_org": alternate_org_id,
                },
            )
            blocked = _wait_for_named_lock(owner, application_name, timeout_seconds=2)
            call_transaction.rollback()
            call_transaction = None
            assert future.result(timeout=6) == "ok"
        assert blocked, f"{boundary} did not retain a lock on {locked_relation}"
    finally:
        if call_transaction is not None:
            call_transaction.rollback()
        if call_connection is not None:
            call_connection.close()
        owner.dispose()
        authorizer.dispose()
        maintenance.dispose()


def test_hold_authorization_rolls_back_audit_when_fresh_owner_check_fails(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            operation_id, digest = _insert_hold_operation(connection, seed)
            _add_owner(
                connection,
                seed,
                logical_hold=True,
                policy_duration="P1Y",
            )

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


@pytest.mark.parametrize("boundary", ("CLAIM", "RESULT"))
@pytest.mark.parametrize(
    "owner_shape",
    (
        "DISPOSED_PERMANENT",
        "CROSS_ORG_EVIDENCE",
        "TENANT_ORPHAN_EVIDENCE",
        "TENANT_ORPHAN_EVIDENCE_PERMANENT",
        "CROSS_ORG_DOCUMENT",
        "TENANT_ORPHAN_DOCUMENT",
    ),
)
def test_hold_release_ignores_noncurrent_or_cross_tenant_historical_evidence(
    database_authority_dsns: dict[str, str], boundary: str, owner_shape: str
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
        if boundary == "RESULT":
            with maintenance.begin() as connection:
                assert operation_id in {
                    row["operation_id"] for row in _claim_hold_operations(connection)
                }

        with owner.begin() as connection:
            if owner_shape == "DISPOSED_PERMANENT":
                owner_record_id = _add_owner(
                    connection,
                    seed,
                    logical_hold=False,
                    policy_duration="PERMANENT",
                )
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                    {"id": owner_record_id},
                )
            else:
                is_document = owner_shape.endswith("DOCUMENT")
                other_org_id = _insert_org(connection)
                other_user_id = uuid.uuid4()
                other_framework_id = uuid.uuid4()
                other_policy_id = uuid.uuid4()
                target_permanent_policy_id = uuid.uuid4()
                other_record_id = uuid.uuid4()
                connection.execute(
                    sa.text(
                        "INSERT INTO app_user(id,org_id,keycloak_subject,display_name) "
                        "VALUES (:id,:org,:subject,'Cross-tenant owner')"
                    ),
                    {
                        "id": other_user_id,
                        "org": other_org_id,
                        "subject": f"cross-owner-{other_user_id}",
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO framework(id,org_id,code,name,is_active,is_authorable) "
                        "VALUES (:id,:org,:code,'Cross owner framework',true,false)"
                    ),
                    {
                        "id": other_framework_id,
                        "org": other_org_id,
                        "code": f"cross:{other_framework_id}",
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO retention_policy"
                        "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                        "VALUES (:id,:org,:name,'PERMANENT','PERMANENT','RETAIN_PERMANENT')"
                    ),
                    {
                        "id": other_policy_id,
                        "org": other_org_id,
                        "name": f"cross-policy-{other_policy_id}",
                    },
                )
                connection.execute(
                    sa.text(
                        "INSERT INTO retention_policy"
                        "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                        "VALUES (:id,:org,:name,'PERMANENT','PERMANENT','RETAIN_PERMANENT')"
                    ),
                    {
                        "id": target_permanent_policy_id,
                        "org": seed.org_id,
                        "name": f"target-permanent-{target_permanent_policy_id}",
                    },
                )
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO documented_information
                            (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                             current_state,is_singleton,classification,
                             acknowledgement_required,created_by)
                        VALUES (:id,:org,:framework,CAST(:kind AS document_kind),:identifier,
                                'Cross owner',:user,
                                'Draft',false,'Internal',false,:user)
                        """
                    ),
                    {
                        "id": other_record_id,
                        "org": other_org_id,
                        "framework": other_framework_id,
                        "kind": "DOCUMENT" if is_document else "RECORD",
                        "identifier": f"CROSS-OWNER-{other_record_id}",
                        "user": other_user_id,
                    },
                )
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                edge_org_id = other_org_id if owner_shape.startswith("CROSS_ORG") else seed.org_id
                edge_policy_id = (
                    target_permanent_policy_id
                    if owner_shape == "TENANT_ORPHAN_DOCUMENT"
                    or owner_shape == "TENANT_ORPHAN_EVIDENCE_PERMANENT"
                    else seed.policy_id
                    if owner_shape.startswith("TENANT_ORPHAN_EVIDENCE")
                    else other_policy_id
                )
                edge_user_id = (
                    seed.user_id if owner_shape.startswith("TENANT_ORPHAN") else other_user_id
                )
                if is_document:
                    connection.execute(
                        sa.text(
                            """
                            INSERT INTO document_version
                                (id,org_id,document_id,version_seq,revision_label,
                                 change_significance,change_reason,version_state,
                                 retention_authority_kind,retention_policy_id,
                                 retention_basis_date,source_blob_sha256,metadata_snapshot,
                                 imported,author_user_id,created_by)
                            VALUES (:id,:org,:document,1,'A','MINOR','cross owner','Draft',
                                    'POLICY',:policy,current_date,:sha,'{}'::jsonb,false,:user,:user)
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "org": edge_org_id,
                            "document": other_record_id,
                            "policy": edge_policy_id,
                            "sha": seed.blob_sha256,
                            "user": edge_user_id,
                        },
                    )
                else:
                    connection.execute(
                        sa.text(
                            "INSERT INTO record"
                            "(id,org_id,record_type,captured_by,content_hash_version,"
                            "retention_policy_id,disposition_state,legal_hold) "
                            "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',:legal_hold)"
                        ),
                        {
                            "id": other_record_id,
                            "org": other_org_id,
                            "user": edge_user_id,
                            "policy": edge_policy_id,
                            "legal_hold": owner_shape != "TENANT_ORPHAN_EVIDENCE_PERMANENT",
                        },
                    )
                    connection.execute(
                        sa.text(
                            "INSERT INTO evidence_blob"
                            "(id,org_id,record_id,blob_sha256,is_original,created_by) "
                            "VALUES (:id,:edge_org,:record,:sha,true,:user)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "edge_org": edge_org_id,
                            "record": other_record_id,
                            "sha": seed.blob_sha256,
                            "user": edge_user_id,
                        },
                    )

        with maintenance.begin() as connection:
            if boundary == "CLAIM":
                assert operation_id in {
                    row["operation_id"] for row in _claim_hold_operations(connection)
                }
            else:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_ordinary_hold_release"
                        "(:sha,:version,:operation,clock_timestamp())"
                    ),
                    {
                        "sha": seed.blob_sha256,
                        "version": seed.object_version_id,
                        "operation": operation_id,
                    },
                )
        with owner.connect() as connection:
            expected_state = "RUNNING" if boundary == "CLAIM" else "VERIFIED"
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM worm_hold_release_operation WHERE id=:id"),
                    {"id": operation_id},
                ).scalar_one()
                == expected_state
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
            _add_owner(
                connection,
                seed,
                logical_hold=True,
                policy_duration="P1Y",
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


def test_ordinary_purge_limit_prefilters_ineligible_oldest_marker(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            invalid_seed = _seed_ordinary_owner(connection)
            valid_seed = _seed_ordinary_owner(connection)
        with retention.begin() as connection:
            invalid_marker = connection.execute(
                sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                {
                    "record": invalid_seed.record_id,
                    "event": invalid_seed.disposition_event_id,
                    "sha": invalid_seed.blob_sha256,
                },
            ).scalar_one()
            valid_marker = connection.execute(
                sa.text("SELECT easysynq_enqueue_ordinary_exact_purge(:record,:event,:sha)"),
                {
                    "record": valid_seed.record_id,
                    "event": valid_seed.disposition_event_id,
                    "sha": valid_seed.blob_sha256,
                },
            ).scalar_one()
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE disposition_event SET action='ARCHIVE_COLD' WHERE id=:event"),
                {"event": invalid_seed.disposition_event_id},
            )
            connection.execute(
                sa.text(
                    "UPDATE pending_blob_purge SET created_at=CASE id "
                    "WHEN :invalid THEN clock_timestamp()-interval '2 hours' "
                    "ELSE clock_timestamp()-interval '1 hour' END "
                    "WHERE id IN (:invalid,:valid)"
                ),
                {"invalid": invalid_marker, "valid": valid_marker},
            )
        snapshot_sql = sa.text(
            "SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:id"
        )
        with owner.connect() as connection:
            invalid_before = connection.execute(snapshot_sql, {"id": invalid_marker}).scalar_one()

        with retention.begin() as connection:
            claimed = (
                connection.execute(
                    sa.text(
                        "SELECT marker_id FROM "
                        "easysynq_claim_ordinary_exact_purges(1,clock_timestamp())"
                    )
                )
                .scalars()
                .all()
            )

        assert claimed == [valid_marker]
        with owner.connect() as connection:
            assert (
                connection.execute(snapshot_sql, {"id": invalid_marker}).scalar_one()
                == invalid_before
            )
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM pending_blob_purge WHERE id=:id"),
                    {"id": valid_marker},
                ).scalar_one()
                == "RUNNING"
            )
    finally:
        owner.dispose()
        retention.dispose()


def test_hold_release_limit_prefilters_ineligible_oldest_operation(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_hold_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_hold_maintenance")
    try:
        with owner.begin() as connection:
            invalid_seed = _seed_ordinary_owner(connection)
            valid_seed = _seed_ordinary_owner(connection)
            invalid_operation, invalid_digest = _insert_hold_operation(connection, invalid_seed)
            valid_operation, valid_digest = _insert_hold_operation(connection, valid_seed)
        with authorizer.begin() as connection:
            _authorize(connection, invalid_operation, invalid_digest)
            _authorize(connection, valid_operation, valid_digest)
        with owner.begin() as connection:
            _add_owner(
                connection,
                invalid_seed,
                logical_hold=True,
                policy_duration="P1Y",
            )
            connection.execute(
                sa.text(
                    "UPDATE worm_hold_release_operation SET created_at=CASE id "
                    "WHEN :invalid THEN clock_timestamp()-interval '2 hours' "
                    "ELSE clock_timestamp()-interval '1 hour' END "
                    "WHERE id IN (:invalid,:valid)"
                ),
                {"invalid": invalid_operation, "valid": valid_operation},
            )
        snapshot_sql = sa.text(
            "SELECT to_jsonb(operation) FROM worm_hold_release_operation operation WHERE id=:id"
        )
        with owner.connect() as connection:
            invalid_before = connection.execute(
                snapshot_sql, {"id": invalid_operation}
            ).scalar_one()

        with maintenance.begin() as connection:
            claimed = (
                connection.execute(
                    sa.text(
                        "SELECT operation_id FROM easysynq_claim_hold_releases(1,clock_timestamp())"
                    )
                )
                .scalars()
                .all()
            )

        assert claimed == [valid_operation]
        with owner.connect() as connection:
            assert (
                connection.execute(snapshot_sql, {"id": invalid_operation}).scalar_one()
                == invalid_before
            )
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM worm_hold_release_operation WHERE id=:id"),
                    {"id": valid_operation},
                ).scalar_one()
                == "RUNNING"
            )
    finally:
        owner.dispose()
        authorizer.dispose()
        maintenance.dispose()
