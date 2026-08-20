"""Real PostgreSQL authority proofs for exact WORM identity."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from time import monotonic, sleep
from typing import Any

import pytest
import sqlalchemy as sa


def _engine(database_authority_dsns: dict[str, str], role: str) -> sa.Engine:
    return sa.create_engine(database_authority_dsns[role])


def _unsigned32(value: int) -> int:
    return value & 0xFFFFFFFF


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


def _wait_for_exact_blocker(
    engine: sa.Engine,
    *,
    blocked_name: str,
    blocker_name: str,
) -> tuple[int, int, list[int]] | None:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        with engine.connect() as connection:
            observed = connection.execute(
                sa.text(
                    """
                    SELECT blocked.pid,blocker.pid,pg_blocking_pids(blocked.pid)
                    FROM pg_stat_activity AS blocked
                    JOIN pg_stat_activity AS blocker ON blocker.application_name=:blocker
                    WHERE blocked.application_name=:blocked
                    """
                ),
                {"blocked": blocked_name, "blocker": blocker_name},
            ).one_or_none()
        if observed is not None and observed[1] in observed[2]:
            return observed[0], observed[1], list(observed[2])
        sleep(0.02)
    return None


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


@pytest.mark.parametrize("role", ("easysynq_app", "easysynq_retention"))
def test_registry_roles_can_use_only_hardened_blob_and_owner_lock_seams(
    database_authority_dsns: dict[str, str], role: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import (
        _add_owner,
        _seed_ordinary_owner,
    )

    owner = _engine(database_authority_dsns, "owner")
    runtime = _engine(database_authority_dsns, role)
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            record_id = _add_owner(
                connection,
                seed,
                logical_hold=False,
                policy_duration="P3Y",
            )
            connection.execute(
                sa.text("UPDATE record SET retention_basis_date=current_date WHERE id=:id"),
                {"id": record_id},
            )
            evidence_id = connection.execute(
                sa.text("SELECT id FROM evidence_blob WHERE record_id=:id"),
                {"id": record_id},
            ).scalar_one()

        with runtime.begin() as connection:
            blob = (
                connection.execute(
                    sa.text("SELECT * FROM easysynq_lock_worm_blob(:org,:sha)"),
                    {"org": seed.org_id, "sha": seed.blob_sha256},
                )
                .mappings()
                .one()
            )
            owners = (
                connection.execute(
                    sa.text("SELECT * FROM easysynq_lock_worm_owners(:org,:sha)"),
                    {"org": seed.org_id, "sha": seed.blob_sha256},
                )
                .mappings()
                .all()
            )

        assert blob["blob_sha256"] == seed.blob_sha256
        assert blob["org_id"] == seed.org_id
        assert blob["bucket"] == seed.bucket
        assert blob["object_key"] == seed.object_key
        assert blob["object_version_id"] == seed.object_version_id
        assert {(row["owner_kind"], row["owner_id"]) for row in owners} == {
            ("RECORD_EVIDENCE", evidence_id)
        }

        forbidden_updates = (
            sa.text("UPDATE blob SET org_id=org_id WHERE false"),
            sa.text("UPDATE record SET org_id=org_id WHERE false"),
            sa.text("UPDATE evidence_blob SET org_id=org_id WHERE false"),
            sa.text("UPDATE retention_policy SET org_id=org_id WHERE false"),
        )
        for statement in forbidden_updates:
            with pytest.raises(sa.exc.DBAPIError):
                with runtime.begin() as connection:
                    connection.execute(statement)
    finally:
        owner.dispose()
        runtime.dispose()


def test_proposed_owner_liveness_seam_is_exact_hardened_and_app_only(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    try:
        with owner.connect() as connection:
            schema_owner = connection.execute(
                sa.text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='public'")
            ).scalar_one()
            migration_owner = (
                connection.execute(
                    sa.text(
                        "SELECT pg_get_userbyid(datdba) FROM pg_database "
                        "WHERE datname=current_database()"
                    )
                ).scalar_one()
                if schema_owner == "pg_database_owner"
                else schema_owner
            )
            function = connection.execute(
                sa.text(
                    """
                    SELECT proc.oid,proc.prosecdef,proc.proconfig,
                           pg_get_userbyid(proc.proowner) AS owner_name,
                           format_type(proc.prorettype,NULL) AS return_type,
                           pg_get_function_identity_arguments(proc.oid) AS identity_arguments
                    FROM pg_proc proc
                    WHERE proc.oid=to_regprocedure(
                        'public.easysynq_assert_worm_record_live(uuid,uuid)'
                    )
                    """
                )
            ).one()
            executors = (
                connection.execute(
                    sa.text(
                        """
                        SELECT COALESCE(role.rolname,'PUBLIC')
                        FROM pg_proc proc
                        CROSS JOIN LATERAL aclexplode(
                            COALESCE(proc.proacl,acldefault('f',proc.proowner))
                        ) acl
                        LEFT JOIN pg_roles role ON role.oid=acl.grantee
                        WHERE proc.oid=to_regprocedure(
                            'public.easysynq_assert_worm_record_live(uuid,uuid)'
                        )
                          AND acl.privilege_type='EXECUTE'
                          AND acl.grantee<>proc.proowner
                        """
                    )
                )
                .scalars()
                .all()
            )

        assert function.prosecdef is True
        assert function.proconfig == ["search_path=public, pg_temp"]
        assert function.owner_name == migration_owner
        assert function.owner_name not in {"easysynq_app", "easysynq_retention"}
        assert function.return_type == "void"
        assert function.identity_arguments == "p_org_id uuid, p_record_id uuid"
        assert executors == ["easysynq_app"]
    finally:
        owner.dispose()


@pytest.mark.parametrize(
    "live_shape",
    (
        "LIVE",
        "DISPOSED_WITHOUT_EVENT",
        "ARCHIVE_COLD",
        "TRANSFER",
        "ORDINARY_WRONG_POLICY",
        "ORDINARY_NO_TOMBSTONE",
    ),
)
def test_app_liveness_seam_accepts_exact_non_destroy_controls_without_writes(
    database_authority_dsns: dict[str, str], live_shape: str
) -> None:
    from tests.integration.test_worm_owner_registry import (
        _add_disposition,
        _seed_proposed_record,
    )

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    try:
        with owner.begin() as connection:
            seeded = _seed_proposed_record(connection)
            if live_shape == "DISPOSED_WITHOUT_EVENT":
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                    {"id": seeded.record_id},
                )
            elif live_shape in {"ARCHIVE_COLD", "TRANSFER"}:
                _add_disposition(
                    connection,
                    seeded.seed,
                    seeded.record_id,
                    action=live_shape,
                )
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                    {"id": seeded.record_id},
                )
            elif live_shape in {"ORDINARY_WRONG_POLICY", "ORDINARY_NO_TOMBSTONE"}:
                event_id = _add_disposition(
                    connection,
                    seeded.seed,
                    seeded.record_id,
                    action="DESTROY",
                    exact_policy=live_shape != "ORDINARY_WRONG_POLICY",
                )
                if live_shape == "ORDINARY_NO_TOMBSTONE":
                    connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                    connection.execute(
                        sa.text("UPDATE disposition_event SET tombstone=false WHERE id=:id"),
                        {"id": event_id},
                    )
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                    {"id": seeded.record_id},
                )
            snapshot = sa.text(
                """
                SELECT
                    (SELECT to_jsonb(row) FROM record row WHERE id=:id),
                    (SELECT COALESCE(jsonb_agg(to_jsonb(event) ORDER BY event.id),'[]'::jsonb)
                     FROM disposition_event event WHERE event.record_id=:id)
                """
            )
            before = connection.execute(snapshot, {"id": seeded.record_id}).one()

        dml_counters = sa.text(
            "SELECT coalesce(sum(n_tup_ins),0),coalesce(sum(n_tup_upd),0),"
            "coalesce(sum(n_tup_del),0) FROM pg_stat_xact_user_tables"
        )
        with app.begin() as connection:
            counters_before = connection.execute(dml_counters).one()
            connection.execute(
                sa.text("SELECT easysynq_assert_worm_record_live(:org,:record)"),
                {"org": seeded.seed.org_id, "record": seeded.record_id},
            ).one()
            counters_after = connection.execute(dml_counters).one()
            assert counters_before == counters_after == (0, 0, 0)

        with owner.connect() as connection:
            assert connection.execute(snapshot, {"id": seeded.record_id}).one() == before
    finally:
        owner.dispose()
        app.dispose()


@pytest.mark.parametrize(
    "binding_shape",
    (
        "REQUEST_BINDING",
        "EXECUTION_BINDING",
        "REQUESTED_ACTOR",
        "APPROVED_ACTOR",
        "LEGAL_BASIS",
        "SOURCE_COMMITTED_AT",
        "ROOT_LINEAGE",
        "ROOT_RECORD_RELATION",
        "DERIVED_LINEAGE",
        "DERIVED_RECORD_RELATION",
    ),
)
def test_app_liveness_seam_accepts_each_isolated_noncanonical_r27_binding(
    database_authority_dsns: dict[str, str], binding_shape: str
) -> None:
    from tests.integration.test_r27_authority_transitions import (
        _add_r27_evidence_owner,
        _seed_source_execution,
    )

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        record_id = source.actors.record_id
        event_id = source.disposition_event_id
        intermediate_event_id: uuid.UUID | None = None
        with owner.begin() as connection:
            if binding_shape.startswith("DERIVED_"):
                record_id, _edge_id = _add_r27_evidence_owner(
                    connection,
                    source,
                    source.request.targets[0],
                    state="DISPOSED",
                )
                event_id = uuid.uuid4()
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO disposition_event
                            (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                             requested_by,is_worm_destroy,legal_basis,
                             derived_from_disposition_event_id,r27_request_id,r27_execution_id)
                        SELECT
                            :id,event.org_id,:record,'DESTROY',true,NULL,event.approved_by,
                            event.requested_by,true,event.legal_basis,event.id,
                            event.r27_request_id,event.r27_execution_id
                        FROM disposition_event event WHERE event.id=:source_event
                        """
                    ),
                    {
                        "id": event_id,
                        "record": record_id,
                        "source_event": source.disposition_event_id,
                    },
                )
                if binding_shape == "DERIVED_LINEAGE":
                    intermediate_event_id = uuid.uuid4()
                    connection.execute(
                        sa.text(
                            """
                            INSERT INTO disposition_event
                                (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                                 requested_by,is_worm_destroy,legal_basis,
                                 derived_from_disposition_event_id,r27_request_id,r27_execution_id)
                            SELECT
                                :id,event.org_id,event.record_id,event.action,event.tombstone,
                                event.policy_id,event.approved_by,event.requested_by,
                                event.is_worm_destroy,event.legal_basis,event.id,
                                event.r27_request_id,event.r27_execution_id
                            FROM disposition_event event WHERE event.id=:source_event
                            """
                        ),
                        {
                            "id": intermediate_event_id,
                            "source_event": source.disposition_event_id,
                        },
                    )

            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            if binding_shape == "REQUEST_BINDING":
                cloned_request_id = uuid.uuid4()
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO r27_request
                            (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256,state,
                             requester_user_id,requester_audit_event_id,approver_user_id,
                             approver_audit_event_id,requested_at,approved_at,created_at,updated_at)
                        SELECT
                            :id,org_id,record_id,normalized_legal_basis,legal_basis_sha256,
                            'EXECUTED',requester_user_id,requester_audit_event_id,approver_user_id,
                            approver_audit_event_id,requested_at,approved_at,created_at,updated_at
                        FROM r27_request WHERE id=:source
                        """
                    ),
                    {"id": cloned_request_id, "source": source.request.request_id},
                )
                connection.execute(
                    sa.text("UPDATE disposition_event SET r27_request_id=:value WHERE id=:id"),
                    {"value": cloned_request_id, "id": event_id},
                )
            elif binding_shape == "EXECUTION_BINDING":
                cloned_request_id = uuid.uuid4()
                cloned_execution_id = uuid.uuid4()
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO r27_request
                            (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256,state,
                             requester_user_id,requester_audit_event_id,approver_user_id,
                             approver_audit_event_id,requested_at,approved_at,created_at,updated_at)
                        SELECT
                            :id,org_id,record_id,normalized_legal_basis,legal_basis_sha256,
                            'EXECUTED',requester_user_id,requester_audit_event_id,approver_user_id,
                            approver_audit_event_id,requested_at,approved_at,created_at,updated_at
                        FROM r27_request WHERE id=:source
                        """
                    ),
                    {"id": cloned_request_id, "source": source.request.request_id},
                )
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO r27_execution
                            (id,request_id,execution_id,state,claimed_at,attempt_count,
                             source_committed_at,updated_at)
                        SELECT
                            :id,:request,:public_execution,'EXECUTED',claimed_at,attempt_count,
                            source_committed_at,updated_at
                        FROM r27_execution WHERE id=:source
                        """
                    ),
                    {
                        "id": cloned_execution_id,
                        "request": cloned_request_id,
                        "public_execution": uuid.uuid4(),
                        "source": source.internal_execution_id,
                    },
                )
                connection.execute(
                    sa.text("UPDATE disposition_event SET r27_execution_id=:value WHERE id=:id"),
                    {"value": cloned_execution_id, "id": event_id},
                )
            elif binding_shape == "REQUESTED_ACTOR":
                connection.execute(
                    sa.text("UPDATE disposition_event SET requested_by=:value WHERE id=:id"),
                    {"value": source.actors.canceller_id, "id": event_id},
                )
            elif binding_shape == "APPROVED_ACTOR":
                connection.execute(
                    sa.text("UPDATE disposition_event SET approved_by=:value WHERE id=:id"),
                    {"value": source.actors.canceller_id, "id": event_id},
                )
            elif binding_shape == "LEGAL_BASIS":
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET legal_basis='different valid basis' "
                        "WHERE id=:id"
                    ),
                    {"id": event_id},
                )
            elif binding_shape == "SOURCE_COMMITTED_AT":
                connection.execute(
                    sa.text("UPDATE r27_execution SET source_committed_at=NULL WHERE id=:id"),
                    {"id": source.internal_execution_id},
                )
            elif binding_shape == "ROOT_LINEAGE":
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET derived_from_disposition_event_id=:value "
                        "WHERE id=:id"
                    ),
                    {"value": event_id, "id": event_id},
                )
            elif binding_shape == "DERIVED_LINEAGE":
                assert intermediate_event_id is not None
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET derived_from_disposition_event_id=:value "
                        "WHERE id=:id"
                    ),
                    {"value": intermediate_event_id, "id": event_id},
                )
            elif binding_shape == "ROOT_RECORD_RELATION":
                record_id = source.logical_owner_record_id
                connection.execute(
                    sa.text("UPDATE disposition_event SET record_id=:record WHERE id=:id"),
                    {"record": record_id, "id": event_id},
                )
            elif binding_shape == "DERIVED_RECORD_RELATION":
                connection.execute(
                    sa.text("UPDATE r27_request SET record_id=:record WHERE id=:id"),
                    {"record": record_id, "id": source.request.request_id},
                )
            else:  # pragma: no cover - closed parameter set
                raise AssertionError(binding_shape)

            snapshot = sa.text(
                """
                SELECT
                    (SELECT to_jsonb(row) FROM record row WHERE id=:record),
                    (SELECT to_jsonb(event) FROM disposition_event event WHERE id=:event),
                    (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
                    (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
                    (SELECT count(*) FROM audit_event)
                """
            )
            snapshot_parameters = {
                "record": record_id,
                "event": event_id,
                "request": source.request.request_id,
                "execution": source.internal_execution_id,
            }
            before = connection.execute(snapshot, snapshot_parameters).one()

        dml_counters = sa.text(
            "SELECT coalesce(sum(n_tup_ins),0),coalesce(sum(n_tup_upd),0),"
            "coalesce(sum(n_tup_del),0) FROM pg_stat_xact_user_tables"
        )
        with app.begin() as connection:
            counters_before = connection.execute(dml_counters).one()
            connection.execute(
                sa.text("SELECT easysynq_assert_worm_record_live(:org,:record)"),
                {"org": source.actors.org_id, "record": record_id},
            ).one()
            counters_after = connection.execute(dml_counters).one()
            assert counters_before == counters_after == (0, 0, 0)

        with owner.connect() as connection:
            assert connection.execute(snapshot, snapshot_parameters).one() == before
    finally:
        owner.dispose()
        app.dispose()


@pytest.mark.parametrize(
    "destroy_shape",
    ("MISSING", "CROSS_ORG", "ORDINARY", "R27_ROOT", "R27_DERIVED"),
)
def test_app_liveness_seam_uniformly_refuses_invalid_or_destroyed_authority(
    database_authority_dsns: dict[str, str], destroy_shape: str
) -> None:
    from tests.integration.test_r27_authority_transitions import (
        _add_r27_evidence_owner,
        _seed_source_execution,
    )
    from tests.integration.test_worm_owner_registry import (
        _add_disposition,
        _seed_proposed_record,
    )

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    try:
        if destroy_shape in {"R27_ROOT", "R27_DERIVED"}:
            source = _seed_source_execution(database_authority_dsns, owner)
            org_id = source.actors.org_id
            record_id = source.actors.record_id
            if destroy_shape == "R27_DERIVED":
                with owner.begin() as connection:
                    record_id, _edge_id = _add_r27_evidence_owner(
                        connection,
                        source,
                        source.request.targets[0],
                        state="DISPOSED",
                    )
                    connection.execute(
                        sa.text(
                            """
                            INSERT INTO disposition_event
                                (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                                 requested_by,is_worm_destroy,legal_basis,
                                 derived_from_disposition_event_id,r27_request_id,r27_execution_id)
                            SELECT
                                :id,event.org_id,:record,'DESTROY',true,NULL,event.approved_by,
                                event.requested_by,true,event.legal_basis,event.id,
                                event.r27_request_id,event.r27_execution_id
                            FROM disposition_event event WHERE event.id=:source_event
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "record": record_id,
                            "source_event": source.disposition_event_id,
                        },
                    )
        else:
            with owner.begin() as connection:
                local = _seed_proposed_record(connection)
                org_id = local.seed.org_id
                record_id = local.record_id
                if destroy_shape == "MISSING":
                    record_id = uuid.uuid4()
                elif destroy_shape == "CROSS_ORG":
                    foreign = _seed_proposed_record(connection)
                    record_id = foreign.record_id
                else:
                    _add_disposition(
                        connection,
                        local.seed,
                        local.record_id,
                        action="DESTROY",
                    )

        snapshot = sa.text(
            """
            SELECT
                (SELECT to_jsonb(row) FROM record row WHERE id=:record),
                (SELECT COALESCE(jsonb_agg(to_jsonb(event) ORDER BY event.id),'[]'::jsonb)
                 FROM disposition_event event WHERE event.record_id=:record),
                (SELECT count(*) FROM audit_event)
            """
        )
        with owner.connect() as connection:
            before = connection.execute(snapshot, {"record": record_id}).one()

        dml_counters = sa.text(
            "SELECT coalesce(sum(n_tup_ins),0),coalesce(sum(n_tup_upd),0),"
            "coalesce(sum(n_tup_del),0) FROM pg_stat_xact_user_tables"
        )
        with app.connect() as connection:
            transaction = connection.begin()
            counters_before = connection.execute(dml_counters).one()
            savepoint = connection.begin_nested()
            try:
                with pytest.raises(sa.exc.DBAPIError) as error:
                    connection.execute(
                        sa.text("SELECT easysynq_assert_worm_record_live(:org,:record)"),
                        {"org": org_id, "record": record_id},
                    )
            finally:
                if savepoint.is_active:
                    savepoint.rollback()
            counters_after = connection.execute(dml_counters).one()
            assert counters_before == counters_after == (0, 0, 0)
            transaction.rollback()

        assert getattr(error.value.orig, "sqlstate", None) == "P0001"
        diagnostics = getattr(error.value.orig, "diag", None)
        assert (
            getattr(diagnostics, "message_primary", None) == "worm_proposed_owner_liveness_refused"
        )
        with owner.connect() as connection:
            assert connection.execute(snapshot, {"record": record_id}).one() == before
    finally:
        owner.dispose()
        app.dispose()


@pytest.mark.parametrize("null_coordinate", ("ORG", "RECORD"))
def test_app_liveness_seam_rejects_required_null_without_writes(
    database_authority_dsns: dict[str, str], null_coordinate: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    try:
        with owner.connect() as connection:
            before = connection.execute(
                sa.text(
                    "SELECT (SELECT count(*) FROM record),"
                    "(SELECT count(*) FROM disposition_event),"
                    "(SELECT count(*) FROM audit_event)"
                )
            ).one()
        coordinates = {
            "org": None if null_coordinate == "ORG" else uuid.uuid4(),
            "record": None if null_coordinate == "RECORD" else uuid.uuid4(),
        }
        dml_counters = sa.text(
            "SELECT coalesce(sum(n_tup_ins),0),coalesce(sum(n_tup_upd),0),"
            "coalesce(sum(n_tup_del),0) FROM pg_stat_xact_user_tables"
        )
        with app.connect() as connection:
            transaction = connection.begin()
            counters_before = connection.execute(dml_counters).one()
            savepoint = connection.begin_nested()
            try:
                with pytest.raises(sa.exc.DBAPIError) as error:
                    connection.execute(
                        sa.text("SELECT easysynq_assert_worm_record_live(:org,:record)"),
                        coordinates,
                    )
            finally:
                if savepoint.is_active:
                    savepoint.rollback()
            counters_after = connection.execute(dml_counters).one()
            assert counters_before == counters_after == (0, 0, 0)
            transaction.rollback()

        assert getattr(error.value.orig, "sqlstate", None) == "P0001"
        diagnostics = getattr(error.value.orig, "diag", None)
        assert getattr(diagnostics, "message_primary", None) == "required_argument_is_null"
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT (SELECT count(*) FROM record),"
                        "(SELECT count(*) FROM disposition_event),"
                        "(SELECT count(*) FROM audit_event)"
                    )
                ).one()
                == before
            )
    finally:
        owner.dispose()
        app.dispose()


def test_app_liveness_seam_locks_only_the_exact_record_row(
    database_authority_dsns: dict[str, str],
) -> None:
    from tests.integration.test_worm_owner_registry import _seed_proposed_record

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    try:
        with owner.begin() as connection:
            target = _seed_proposed_record(connection)
            other = _seed_proposed_record(connection)
            before = connection.execute(
                sa.text("SELECT to_jsonb(row) FROM record row WHERE id=:id"),
                {"id": target.record_id},
            ).scalar_one()

        dml_counters = sa.text(
            "SELECT coalesce(sum(n_tup_ins),0),coalesce(sum(n_tup_upd),0),"
            "coalesce(sum(n_tup_del),0) FROM pg_stat_xact_user_tables"
        )
        with app.connect() as connection:
            transaction = connection.begin()
            counters_before = connection.execute(dml_counters).one()
            connection.execute(
                sa.text("SELECT easysynq_assert_worm_record_live(:org,:record)"),
                {"org": target.seed.org_id, "record": target.record_id},
            ).one()
            counters_after = connection.execute(dml_counters).one()
            assert counters_before == counters_after == (0, 0, 0)

            with owner.begin() as probe:
                assert (
                    probe.execute(
                        sa.text("SELECT id FROM record WHERE id=:id FOR UPDATE"),
                        {"id": other.record_id},
                    ).scalar_one()
                    == other.record_id
                )
            with pytest.raises(sa.exc.DBAPIError, match="lock timeout"):
                with owner.begin() as probe:
                    probe.execute(sa.text("SET LOCAL lock_timeout='100ms'"))
                    probe.execute(
                        sa.text("SELECT id FROM record WHERE id=:id FOR UPDATE"),
                        {"id": target.record_id},
                    )
            transaction.rollback()

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT to_jsonb(row) FROM record row WHERE id=:id"),
                    {"id": target.record_id},
                ).scalar_one()
                == before
            )
    finally:
        owner.dispose()
        app.dispose()


def test_app_config_lock_seam_returns_and_locks_only_exact_same_org_authority(
    database_authority_dsns: dict[str, str],
) -> None:
    from tests.integration.test_worm_owner_registry import _seed_proposed_document

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    try:
        with owner.begin() as connection:
            seeded = _seed_proposed_document(
                connection,
                authority_kind="INSTALLATION_MINIMUM",
            )
            other = _seed_proposed_document(
                connection,
                authority_kind="INSTALLATION_MINIMUM",
            )
            expected_revision = connection.execute(
                sa.text("SELECT active_revision_no FROM document_worm_config WHERE id=:id"),
                {"id": seeded.authority_id},
            ).scalar_one()
            config_before = connection.execute(
                sa.text("SELECT to_jsonb(config) FROM document_worm_config config WHERE id=:id"),
                {"id": seeded.authority_id},
            ).scalar_one()

        with app.connect() as connection:
            transaction = connection.begin()
            dml_counters = sa.text(
                "SELECT coalesce(sum(n_tup_ins),0),coalesce(sum(n_tup_upd),0),"
                "coalesce(sum(n_tup_del),0) FROM pg_stat_xact_user_tables"
            )
            counters_before = connection.execute(dml_counters).one()
            row = connection.execute(
                sa.text("SELECT * FROM easysynq_lock_document_worm_config(:org,:config)"),
                {"org": seeded.seed.org_id, "config": seeded.authority_id},
            ).one()
            counters_after = connection.execute(dml_counters).one()
            assert counters_before == counters_after == (0, 0, 0)
            assert tuple(row._mapping) == (
                "id",
                "org_id",
                "active_period",
                "active_revision_no",
            )
            assert row == (
                seeded.authority_id,
                seeded.seed.org_id,
                "P10Y",
                expected_revision,
            )
            with owner.connect() as monitor:
                assert (
                    monitor.execute(
                        sa.text(
                            "SELECT to_jsonb(config) FROM document_worm_config config WHERE id=:id"
                        ),
                        {"id": seeded.authority_id},
                    ).scalar_one()
                    == config_before
                )
            with pytest.raises(sa.exc.DBAPIError, match="lock timeout"):
                with owner.begin() as probe:
                    probe.execute(sa.text("SET LOCAL lock_timeout='100ms'"))
                    probe.execute(
                        sa.text("SELECT id FROM document_worm_config WHERE id=:id FOR UPDATE"),
                        {"id": seeded.authority_id},
                    )
            assert connection.in_transaction()
            transaction.rollback()

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT to_jsonb(config) FROM document_worm_config config WHERE id=:id"
                    ),
                    {"id": seeded.authority_id},
                ).scalar_one()
                == config_before
            )

        with pytest.raises(sa.exc.DBAPIError) as direct_lock_refused:
            with app.begin() as connection:
                connection.execute(
                    sa.text("SELECT id FROM document_worm_config WHERE id=:id FOR UPDATE"),
                    {"id": seeded.authority_id},
                )
        assert direct_lock_refused.value.orig.sqlstate == "42501"

        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            with app.begin() as connection:
                connection.execute(
                    sa.text("UPDATE document_worm_config SET active_period='P1D' WHERE id=:id"),
                    {"id": seeded.authority_id},
                )
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT to_jsonb(config) FROM document_worm_config config WHERE id=:id"
                    ),
                    {"id": seeded.authority_id},
                ).scalar_one()
                == config_before
            )

        invalid_coordinates = (
            (seeded.seed.org_id, uuid.uuid4()),
            (other.seed.org_id, seeded.authority_id),
        )
        for org_id, config_id in invalid_coordinates:
            with pytest.raises(
                sa.exc.DBAPIError,
                match="document_worm_config_lock_refused",
            ):
                with app.begin() as connection:
                    connection.execute(
                        sa.text("SELECT * FROM easysynq_lock_document_worm_config(:org,:config)"),
                        {"org": org_id, "config": config_id},
                    )

        with pytest.raises(sa.exc.DBAPIError, match="permission denied"):
            with retention.begin() as connection:
                connection.execute(
                    sa.text("SELECT * FROM easysynq_lock_document_worm_config(:org,:config)"),
                    {"org": seeded.seed.org_id, "config": seeded.authority_id},
                )
    finally:
        owner.dispose()
        app.dispose()
        retention.dispose()


def test_app_has_no_table_or_column_update_privilege_on_document_worm_config(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    try:
        with owner.connect() as connection:
            assert not connection.execute(
                sa.text(
                    "SELECT has_table_privilege"
                    "('easysynq_app','public.document_worm_config','UPDATE')"
                )
            ).scalar_one()
            assert (
                connection.execute(
                    sa.text(
                        """
                    SELECT attribute.attname
                    FROM pg_attribute attribute
                    WHERE attribute.attrelid='public.document_worm_config'::regclass
                      AND attribute.attnum>0
                      AND NOT attribute.attisdropped
                      AND has_column_privilege(
                          'easysynq_app',
                          'public.document_worm_config',
                          attribute.attname,
                          'UPDATE'
                      )
                    ORDER BY attribute.attnum
                    """
                    )
                )
                .scalars()
                .all()
                == []
            )
    finally:
        owner.dispose()


def test_app_monotone_assertion_recorder_extends_exact_state_only(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    retention = _engine(database_authority_dsns, "easysynq_retention")
    sha256 = uuid.uuid4().hex * 2
    try:
        with owner.begin() as connection:
            org_id = _insert_blob(connection, sha256=sha256, worm_locked=True)
            current_retain = connection.execute(
                sa.text("SELECT worm_retain_until FROM blob WHERE sha256=:sha"),
                {"sha": sha256},
            ).scalar_one()
            before = connection.execute(
                sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                {"sha": sha256},
            ).scalar_one()
        first_verified_at = datetime.now(UTC)
        arguments = {
            "org": org_id,
            "sha": sha256,
            "bucket": before["bucket"],
            "key": before["object_key"],
            "version": before["object_version_id"],
            "retain": current_retain,
            "hold": False,
            "verified": first_verified_at,
        }
        statement = sa.text(
            "SELECT easysynq_record_worm_assertion"
            "(:org,:sha,:bucket,:key,:version,:retain,:hold,:verified)"
        )
        # Equal retention and an already-OFF hold is a valid idempotent finite assertion.
        with app.begin() as connection:
            connection.execute(statement, arguments)
        with owner.connect() as connection:
            idempotent = connection.execute(
                sa.text(
                    "SELECT worm_retain_until,worm_retention_verified_at,worm_legal_hold,"
                    "worm_legal_hold_verified_at FROM blob WHERE sha256=:sha"
                ),
                {"sha": sha256},
            ).one()
        assert idempotent == (current_retain, first_verified_at, False, first_verified_at)

        # A finite owner may extend retention while preserving physical hold OFF.
        later = current_retain + timedelta(days=30)
        finite_verified_at = datetime.now(UTC)
        finite_arguments = arguments | {
            "retain": later,
            "verified": finite_verified_at,
        }
        with app.begin() as connection:
            connection.execute(statement, finite_arguments)
        with owner.connect() as connection:
            finite = connection.execute(
                sa.text(
                    "SELECT worm_retain_until,worm_retention_verified_at,worm_legal_hold,"
                    "worm_legal_hold_verified_at FROM blob WHERE sha256=:sha"
                ),
                {"sha": sha256},
            ).one()
        assert finite == (later, finite_verified_at, False, finite_verified_at)

        hold_verified_at = datetime.now(UTC)
        protected_arguments = finite_arguments | {
            "hold": True,
            "verified": hold_verified_at,
        }
        with app.begin() as connection:
            connection.execute(statement, protected_arguments)

        with owner.connect() as connection:
            after = connection.execute(
                sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                {"sha": sha256},
            ).scalar_one()
            recorded = connection.execute(
                sa.text(
                    "SELECT worm_retain_until,worm_retention_verified_at,worm_legal_hold,"
                    "worm_legal_hold_verified_at FROM blob WHERE sha256=:sha"
                ),
                {"sha": sha256},
            ).one()
        assert recorded == (later, hold_verified_at, True, hold_verified_at)
        assert after["worm_legal_hold"] is True
        for immutable in (
            "sha256",
            "org_id",
            "bucket",
            "object_key",
            "object_version_id",
            "worm_locked",
            "worm_enforced_mode",
            "worm_asserted_retain_until",
            "worm_asserted_at",
            "purged_at",
            "purge_execution_id",
            "verified_at",
            "verify_failed_at",
        ):
            assert after[immutable] == before[immutable]

        invalid = (
            {"retain": later - timedelta(days=1)},
            {"hold": False},
            {"org": uuid.uuid4()},
            {"sha": "f" * 64},
            {"bucket": "wrong-bucket"},
            {"key": "wrong-key"},
            {"version": "wrong-version"},
            {"verified": hold_verified_at - timedelta(seconds=1)},
        )
        for override in invalid:
            refused = protected_arguments | override
            with pytest.raises(sa.exc.DBAPIError):
                with app.begin() as connection:
                    connection.execute(statement, refused)
            with owner.connect() as connection:
                assert (
                    connection.execute(
                        sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                        {"sha": sha256},
                    ).scalar_one()
                    == after
                )

        with pytest.raises(sa.exc.DBAPIError):
            with retention.begin() as connection:
                connection.execute(
                    statement,
                    protected_arguments
                    | {"retain": later + timedelta(days=1), "verified": datetime.now(UTC)},
                )
    finally:
        owner.dispose()
        app.dispose()
        retention.dispose()


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


def test_blob_model_makes_persisted_non_governance_worm_state_impossible() -> None:
    """Provider mode drift is observable, but it cannot be a valid ledger state."""
    from easysynq_api.db.models.blob import Blob

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Blob.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assertion_shape = constraints["ck_blob_worm_assertion_shape"]
    assert "worm_locked AND object_version_id IS NOT NULL" in assertion_shape
    assert "worm_enforced_mode = 'GOVERNANCE'" in assertion_shape
    assert "NOT worm_locked AND worm_enforced_mode IS NULL" in assertion_shape


@pytest.mark.parametrize("invalid_shape", ("PARTIAL_BLOB", "DUAL_DOCUMENT_AUTHORITY"))
def test_database_checks_reject_partial_blob_and_dual_document_authority_shapes(
    database_authority_dsns: dict[str, str], invalid_shape: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner
    from tests.integration.test_r27_database_authority import _add_permanent_document_owner

    owner = _engine(database_authority_dsns, "owner")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            _add_permanent_document_owner(connection, seed, "POLICY")
            version_id = connection.execute(
                sa.text(
                    "SELECT id FROM document_version WHERE source_blob_sha256=:sha "
                    "AND retention_authority_kind='POLICY'"
                ),
                {"sha": seed.blob_sha256},
            ).scalar_one()
            config_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO document_worm_config (id,org_id,active_period) "
                    "VALUES (:id,:org,'PERMANENT')"
                ),
                {"id": config_id, "org": seed.org_id},
            )

        with owner.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                if invalid_shape == "PARTIAL_BLOB":
                    with pytest.raises(sa.exc.IntegrityError, match="ck_blob_worm_assertion_shape"):
                        connection.execute(
                            sa.text("UPDATE blob SET object_version_id=NULL WHERE sha256=:sha"),
                            {"sha": seed.blob_sha256},
                        )
                else:
                    assert invalid_shape == "DUAL_DOCUMENT_AUTHORITY"
                    with pytest.raises(
                        sa.exc.IntegrityError,
                        match="ck_document_version_retention_authority_shape",
                    ):
                        connection.execute(
                            sa.text(
                                "UPDATE document_version SET document_worm_config_id=:config "
                                "WHERE id=:version"
                            ),
                            {"config": config_id, "version": version_id},
                        )
            finally:
                transaction.rollback()
    finally:
        owner.dispose()


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


@pytest.mark.parametrize(
    "mutation",
    ("BASIS_DATE", "POLICY_ID", "AUTHORITY_KIND_AND_CONFIG"),
)
def test_document_retention_authority_is_immutable_after_owner_visibility(
    database_authority_dsns: dict[str, str], mutation: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner
    from tests.integration.test_r27_database_authority import _add_permanent_document_owner

    owner = _engine(database_authority_dsns, "owner")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            _add_permanent_document_owner(connection, seed, "POLICY")
            _add_permanent_document_owner(connection, seed, "INSTALLATION_MINIMUM")
            policy_version = (
                connection.execute(
                    sa.text(
                        "SELECT id FROM document_version "
                        "WHERE source_blob_sha256=:sha AND retention_authority_kind='POLICY'"
                    ),
                    {"sha": seed.blob_sha256},
                )
                .mappings()
                .one()
            )
            config_version = (
                connection.execute(
                    sa.text(
                        "SELECT id,document_worm_config_id FROM document_version "
                        "WHERE source_blob_sha256=:sha "
                        "AND retention_authority_kind='INSTALLATION_MINIMUM'"
                    ),
                    {"sha": seed.blob_sha256},
                )
                .mappings()
                .one()
            )
            replacement_policy = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy "
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'PERMANENT','PERMANENT','RETAIN_PERMANENT')"
                ),
                {
                    "id": replacement_policy,
                    "org": seed.org_id,
                    "name": f"replacement-{replacement_policy}",
                },
            )

        version_id = policy_version["id"]
        snapshot = sa.text("SELECT to_jsonb(v) FROM document_version v WHERE id=:id")
        with owner.connect() as connection:
            before = connection.execute(snapshot, {"id": version_id}).scalar_one()

        statement, parameters = {
            "BASIS_DATE": (
                "UPDATE document_version SET retention_basis_date=retention_basis_date+1 "
                "WHERE id=:id",
                {"id": version_id},
            ),
            "POLICY_ID": (
                "UPDATE document_version SET retention_policy_id=:authority WHERE id=:id",
                {"id": version_id, "authority": replacement_policy},
            ),
            "AUTHORITY_KIND_AND_CONFIG": (
                "UPDATE document_version SET retention_authority_kind='INSTALLATION_MINIMUM',"
                "retention_policy_id=NULL,document_worm_config_id=:authority WHERE id=:id",
                {
                    "id": version_id,
                    "authority": config_version["document_worm_config_id"],
                },
            ),
        }[mutation]
        with pytest.raises(sa.exc.DBAPIError, match="worm_owner_pointer_is_immutable"):
            with owner.begin() as connection:
                connection.execute(sa.text(statement), parameters)

        with owner.connect() as connection:
            assert connection.execute(snapshot, {"id": version_id}).scalar_one() == before
        with owner.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE document_version SET change_reason='allowed metadata correction' "
                    "WHERE id=:id"
                ),
                {"id": version_id},
            )
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT change_reason FROM document_version WHERE id=:id"),
                    {"id": version_id},
                ).scalar_one()
                == "allowed metadata correction"
            )
    finally:
        owner.dispose()


def test_document_owner_guard_source_covers_every_schema_possible_pinned_field(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    try:
        with owner.connect() as connection:
            definition = connection.execute(
                sa.text(
                    "SELECT pg_get_functiondef("
                    "to_regprocedure('public.easysynq_guard_worm_owner_pointer()'))"
                )
            ).scalar_one()

        normalized = "".join(definition.lower().split()).replace("::text", "")
        for column in (
            "retention_authority_kind",
            "retention_policy_id",
            "document_worm_config_id",
            "retention_basis_date",
        ):
            new_field = f"to_jsonb(new)->>'{column}'"
            old_field = f"to_jsonb(old)->>'{column}'"
            new_index = normalized.index(new_field)
            old_index = normalized.index(old_field, new_index)
            assert "isdistinctfrom" in normalized[new_index:old_index]
        assert "raiseexception'worm_owner_pointer_is_immutable'" in normalized
    finally:
        owner.dispose()


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    (
        ("record", "retention_basis_date"),
        ("record", "retention_basis_provisional"),
        ("retention_policy", "active_revision_no"),
    ),
)
def test_app_cannot_directly_rewrite_record_basis_or_policy_revision(
    database_authority_dsns: dict[str, str],
    table_name: str,
    column_name: str,
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            row_id = seed.record_id if table_name == "record" else seed.policy_id
        snapshot = sa.text(
            {
                "record": "SELECT to_jsonb(row) FROM record row WHERE id=:id",
                "retention_policy": ("SELECT to_jsonb(row) FROM retention_policy row WHERE id=:id"),
            }[table_name]
        )
        with owner.connect() as connection:
            before = connection.execute(snapshot, {"id": row_id}).scalar_one()

        statement = sa.text(
            {
                ("record", "retention_basis_date"): (
                    "UPDATE record SET retention_basis_date=current_date WHERE id=:id"
                ),
                ("record", "retention_basis_provisional"): (
                    "UPDATE record SET retention_basis_provisional=true WHERE id=:id"
                ),
                ("retention_policy", "active_revision_no"): (
                    "UPDATE retention_policy SET active_revision_no=active_revision_no+1 "
                    "WHERE id=:id"
                ),
            }[(table_name, column_name)]
        )
        with pytest.raises(sa.exc.DBAPIError) as error:
            with app.begin() as connection:
                connection.execute(statement, {"id": row_id})
        assert getattr(error.value.orig, "sqlstate", None) == "42501"

        with owner.connect() as connection:
            assert connection.execute(snapshot, {"id": row_id}).scalar_one() == before
    finally:
        owner.dispose()
        app.dispose()


def test_app_column_acl_revokes_direct_basis_provisional_and_revision_writes(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    try:
        with owner.connect() as connection:
            assert connection.execute(
                sa.text(
                    """
                    SELECT table_name,column_name,
                           has_column_privilege(
                               'easysynq_app',format('public.%I',table_name),column_name,'UPDATE'
                           )
                    FROM (VALUES
                        ('record','retention_basis_date'),
                        ('record','retention_basis_provisional'),
                        ('retention_policy','active_revision_no')
                    ) AS protected(table_name,column_name)
                    ORDER BY table_name,column_name
                    """
                )
            ).all() == [
                ("record", "retention_basis_date", False),
                ("record", "retention_basis_provisional", False),
                ("retention_policy", "active_revision_no", False),
            ]
    finally:
        owner.dispose()


@pytest.mark.parametrize(
    "pin_family",
    ("RECORD_ACTIVE", "RECORD_DISPOSED", "DOCUMENT_VERSION"),
)
@pytest.mark.parametrize(
    ("column_name", "replacement"),
    (("duration", "P4Y"), ("worm_lock_period", "P11Y")),
)
def test_app_cannot_rewrite_pinned_policy_period_but_can_edit_unpinned_policy(
    database_authority_dsns: dict[str, str],
    pin_family: str,
    column_name: str,
    replacement: str,
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner
    from tests.integration.test_r27_database_authority import _add_permanent_document_owner

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    unpinned_policy = uuid.uuid4()
    pinned_policy = uuid.uuid4()
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy "
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'P3Y','P10Y','DESTROY')"
                ),
                {
                    "id": pinned_policy,
                    "org": seed.org_id,
                    "name": f"pinned-{pinned_policy}",
                },
            )
            if pin_family in {"RECORD_ACTIVE", "RECORD_DISPOSED"}:
                connection.execute(
                    sa.text("UPDATE record SET retention_policy_id=:policy WHERE id=:record"),
                    {"policy": pinned_policy, "record": seed.record_id},
                )
                connection.execute(
                    sa.text("UPDATE disposition_event SET policy_id=:policy WHERE id=:event"),
                    {"policy": pinned_policy, "event": seed.disposition_event_id},
                )
                if pin_family == "RECORD_ACTIVE":
                    connection.execute(
                        sa.text("UPDATE record SET disposition_state='ACTIVE' WHERE id=:record"),
                        {"record": seed.record_id},
                    )
            else:
                _add_permanent_document_owner(connection, seed, "POLICY")
                document_version = connection.execute(
                    sa.text(
                        "SELECT id FROM document_version "
                        "WHERE source_blob_sha256=:sha AND retention_authority_kind='POLICY'"
                    ),
                    {"sha": seed.blob_sha256},
                ).scalar_one()
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text(
                        "UPDATE document_version SET retention_policy_id=:policy WHERE id=:version"
                    ),
                    {"policy": pinned_policy, "version": document_version},
                )
            connection.execute(
                sa.text(
                    "INSERT INTO retention_policy "
                    "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                    "VALUES (:id,:org,:name,'P3Y','P10Y','DESTROY')"
                ),
                {
                    "id": unpinned_policy,
                    "org": seed.org_id,
                    "name": f"unpinned-{unpinned_policy}",
                },
            )
            pinned_before = connection.execute(
                sa.text("SELECT to_jsonb(p) FROM retention_policy p WHERE id=:id"),
                {"id": pinned_policy},
            ).scalar_one()

        with pytest.raises(sa.exc.DBAPIError, match="worm_pinned_policy_is_immutable"):
            with app.begin() as connection:
                connection.execute(
                    sa.text(
                        {
                            "duration": (
                                "UPDATE retention_policy SET duration=:value WHERE id=:id"
                            ),
                            "worm_lock_period": (
                                "UPDATE retention_policy SET worm_lock_period=:value WHERE id=:id"
                            ),
                        }[column_name]
                    ),
                    {"id": pinned_policy, "value": replacement},
                )

        with app.begin() as connection:
            connection.execute(
                sa.text(
                    {
                        "duration": "UPDATE retention_policy SET duration=:value WHERE id=:id",
                        "worm_lock_period": (
                            "UPDATE retention_policy SET worm_lock_period=:value WHERE id=:id"
                        ),
                    }[column_name]
                ),
                {"id": unpinned_policy, "value": replacement},
            )

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT to_jsonb(p) FROM retention_policy p WHERE id=:id"),
                    {"id": pinned_policy},
                ).scalar_one()
                == pinned_before
            )
            assert connection.execute(
                sa.text("SELECT duration,worm_lock_period FROM retention_policy WHERE id=:id"),
                {"id": unpinned_policy},
            ).one() == (
                (replacement if column_name == "duration" else "P3Y"),
                (replacement if column_name == "worm_lock_period" else "P10Y"),
            )
    finally:
        owner.dispose()
        app.dispose()


def _seed_policy_serialization_fixture(
    connection: sa.Connection,
    *,
    pin_family: str,
) -> dict[str, Any]:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner

    seed = _seed_ordinary_owner(connection)
    policy_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    pin_id = parent_id if pin_family == "RECORD" else uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO retention_policy "
            "(id,org_id,name,duration,worm_lock_period,disposition_action) "
            "VALUES (:id,:org,:name,'P3Y','P10Y','DESTROY')"
        ),
        {
            "id": policy_id,
            "org": seed.org_id,
            "name": f"serialization-{policy_id}",
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,owner_user_id,current_state,
                 is_singleton,classification,acknowledgement_required,created_by)
            VALUES
                (:id,:org,:framework,:kind,:identifier,'Policy serialization pin',:user,'Draft',
                 false,'Internal',false,:user)
            """
        ),
        {
            "id": parent_id,
            "org": seed.org_id,
            "framework": seed.framework_id,
            "kind": pin_family,
            "identifier": f"POLICY-RACE-{parent_id}",
            "user": seed.user_id,
        },
    )
    before = connection.execute(
        sa.text("SELECT to_jsonb(policy) FROM retention_policy policy WHERE id=:id"),
        {"id": policy_id},
    ).scalar_one()
    return {
        "seed": seed,
        "policy_id": policy_id,
        "parent_id": parent_id,
        "pin_id": pin_id,
        "policy_before": before,
    }


def _cleanup_policy_serialization_fixture(
    connection: sa.Connection,
    fixture: dict[str, Any],
    *,
    pin_family: str,
) -> None:
    seed = fixture["seed"]
    connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
    if pin_family == "DOCUMENT":
        connection.execute(
            sa.text("DELETE FROM document_version WHERE id=:id"),
            {"id": fixture["pin_id"]},
        )
    else:
        connection.execute(
            sa.text("DELETE FROM record WHERE id=:id"),
            {"id": fixture["pin_id"]},
        )
    connection.execute(
        sa.text("DELETE FROM documented_information WHERE id=:id"),
        {"id": fixture["parent_id"]},
    )
    connection.execute(
        sa.text("DELETE FROM disposition_event WHERE id=:id"),
        {"id": seed.disposition_event_id},
    )
    connection.execute(
        sa.text("DELETE FROM evidence_blob WHERE record_id=:id"),
        {"id": seed.record_id},
    )
    connection.execute(
        sa.text("DELETE FROM record WHERE id=:id"),
        {"id": seed.record_id},
    )
    connection.execute(
        sa.text("DELETE FROM documented_information WHERE id=:id"),
        {"id": seed.record_id},
    )
    connection.execute(
        sa.text("DELETE FROM blob WHERE sha256=:sha"),
        {"sha": seed.blob_sha256},
    )
    connection.execute(
        sa.text("DELETE FROM retention_policy WHERE id IN (:policy,:seed_policy)"),
        {"policy": fixture["policy_id"], "seed_policy": seed.policy_id},
    )
    connection.execute(
        sa.text("DELETE FROM framework WHERE id=:id"),
        {"id": seed.framework_id},
    )
    connection.execute(
        sa.text("DELETE FROM app_user WHERE id=:id"),
        {"id": seed.user_id},
    )
    connection.execute(
        sa.text("DELETE FROM organization WHERE id=:id"),
        {"id": seed.org_id},
    )


@pytest.mark.parametrize("pin_family", ("RECORD", "DOCUMENT"))
@pytest.mark.parametrize("first_writer", ("PIN", "POLICY"))
def test_policy_period_and_owner_pin_serialize_in_both_lock_orders(
    database_authority_dsns: dict[str, str],
    pin_family: str,
    first_writer: str,
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    fixture: dict[str, Any] | None = None
    pin_started = Event()
    pin_applied = Event()
    release_pin = Event()
    policy_started = Event()
    policy_applied = Event()
    release_policy = Event()
    names = {
        "pin": f"policy-pin-{pin_family.lower()}-{first_writer.lower()}-{uuid.uuid4().hex}",
        "policy": f"policy-update-{pin_family.lower()}-{first_writer.lower()}-{uuid.uuid4().hex}",
    }
    try:
        with owner.begin() as connection:
            fixture = _seed_policy_serialization_fixture(
                connection,
                pin_family=pin_family,
            )

        pin_statement = sa.text(
            """
                INSERT INTO record
                    (id,org_id,record_type,captured_by,content_hash_version,
                     retention_policy_id,disposition_state,legal_hold)
                VALUES
                    (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',false)
                """
            if pin_family == "RECORD"
            else """
                INSERT INTO document_version
                    (id,org_id,document_id,version_seq,revision_label,change_significance,
                     change_reason,version_state,retention_authority_kind,retention_policy_id,
                     document_worm_config_id,retention_basis_date,source_blob_sha256,
                     metadata_snapshot,imported,author_user_id,created_by)
                VALUES
                    (:id,:org,:parent,1,'A','MINOR','serialization pin','Draft','POLICY',
                     :policy,NULL,current_date,:sha,'{}'::jsonb,false,:user,:user)
                """
        )
        pin_parameters = {
            "id": fixture["pin_id"],
            "org": fixture["seed"].org_id,
            "parent": fixture["parent_id"],
            "user": fixture["seed"].user_id,
            "policy": fixture["policy_id"],
            "sha": fixture["seed"].blob_sha256,
        }

        def insert_pin(*, hold_after_insert: bool) -> tuple[str, int]:
            with app.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        sa.text("SELECT set_config('application_name',:name,false)"),
                        {"name": names["pin"]},
                    )
                    connection.execute(sa.text("SET LOCAL statement_timeout='10s'"))
                    pid = int(connection.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
                    pin_started.set()
                    connection.execute(pin_statement, pin_parameters)
                    pin_applied.set()
                    if hold_after_insert and not release_pin.wait(8):
                        raise AssertionError("pin transaction release timed out")
                    transaction.commit()
                    return "committed", pid
                except BaseException:
                    if transaction.is_active:
                        transaction.rollback()
                    raise

        def update_policy(*, hold_after_update: bool) -> tuple[str, int, str | None, str | None]:
            with app.connect() as connection:
                transaction = connection.begin()
                try:
                    connection.execute(
                        sa.text("SELECT set_config('application_name',:name,false)"),
                        {"name": names["policy"]},
                    )
                    connection.execute(sa.text("SET LOCAL statement_timeout='10s'"))
                    pid = int(connection.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
                    policy_started.set()
                    try:
                        connection.execute(
                            sa.text("UPDATE retention_policy SET duration='P4Y' WHERE id=:policy"),
                            {"policy": fixture["policy_id"]},
                        )
                    except sa.exc.DBAPIError as error:
                        transaction.rollback()
                        return (
                            "refused",
                            pid,
                            getattr(error.orig, "sqlstate", None),
                            getattr(
                                getattr(error.orig, "diag", None),
                                "message_primary",
                                None,
                            ),
                        )
                    policy_applied.set()
                    if hold_after_update and not release_policy.wait(8):
                        raise AssertionError("policy transaction release timed out")
                    transaction.commit()
                    return "committed", pid, None, None
                except BaseException:
                    if transaction.is_active:
                        transaction.rollback()
                    raise

        executor = ThreadPoolExecutor(max_workers=2)
        try:
            if first_writer == "PIN":
                pin_future = executor.submit(insert_pin, hold_after_insert=True)
                assert pin_applied.wait(5), "pin insertion did not reach its hold point"
                policy_future = executor.submit(update_policy, hold_after_update=False)
                assert policy_started.wait(5), "policy update did not start"
                blocking = _wait_for_exact_blocker(
                    owner,
                    blocked_name=names["policy"],
                    blocker_name=names["pin"],
                )
                release_pin.set()
            else:
                policy_future = executor.submit(update_policy, hold_after_update=True)
                assert policy_applied.wait(5), "policy update did not reach its hold point"
                pin_future = executor.submit(insert_pin, hold_after_insert=False)
                assert pin_started.wait(5), "pin insertion did not start"
                blocking = _wait_for_exact_blocker(
                    owner,
                    blocked_name=names["pin"],
                    blocker_name=names["policy"],
                )
                release_policy.set()

            pin_outcome = pin_future.result(timeout=10)
            policy_outcome = policy_future.result(timeout=10)
        finally:
            release_pin.set()
            release_policy.set()
            executor.shutdown(wait=True, cancel_futures=True)

        assert blocking is not None
        blocked_pid, blocker_pid, blocking_pids = blocking
        expected_blocked = policy_outcome[1] if first_writer == "PIN" else pin_outcome[1]
        expected_blocker = pin_outcome[1] if first_writer == "PIN" else policy_outcome[1]
        assert (blocked_pid, blocker_pid, blocking_pids) == (
            expected_blocked,
            expected_blocker,
            [expected_blocker],
        )
        assert pin_outcome[0] == "committed"

        with owner.connect() as connection:
            policy_after = connection.execute(
                sa.text("SELECT to_jsonb(policy) FROM retention_policy policy WHERE id=:id"),
                {"id": fixture["policy_id"]},
            ).scalar_one()
            if pin_family == "RECORD":
                pin_after = connection.execute(
                    sa.text(
                        "SELECT id,org_id,retention_policy_id,disposition_state::text "
                        "FROM record WHERE id=:id"
                    ),
                    {"id": fixture["pin_id"]},
                ).one()
                assert pin_after == (
                    fixture["pin_id"],
                    fixture["seed"].org_id,
                    fixture["policy_id"],
                    "ACTIVE",
                )
            else:
                pin_after = connection.execute(
                    sa.text(
                        "SELECT id,org_id,document_id,retention_authority_kind::text,"
                        "retention_policy_id FROM document_version WHERE id=:id"
                    ),
                    {"id": fixture["pin_id"]},
                ).one()
                assert pin_after == (
                    fixture["pin_id"],
                    fixture["seed"].org_id,
                    fixture["parent_id"],
                    "POLICY",
                    fixture["policy_id"],
                )

        if first_writer == "PIN":
            assert policy_outcome[0:3] == ("refused", policy_outcome[1], "P0001")
            assert policy_outcome[3] == "worm_pinned_policy_is_immutable"
            assert policy_after == fixture["policy_before"]
        else:
            assert policy_outcome[0] == "committed"
            assert policy_after["duration"] == "P4Y"
            assert policy_after["worm_lock_period"] == "P10Y"
    finally:
        app.dispose()
        if fixture is not None:
            with owner.begin() as connection:
                _cleanup_policy_serialization_fixture(
                    connection,
                    fixture,
                    pin_family=pin_family,
                )
        owner.dispose()


@pytest.mark.parametrize("role", ("easysynq_app", "easysynq_retention"))
def test_blob_lock_seam_distinguishes_missing_from_foreign_without_disclosure(
    database_authority_dsns: dict[str, str], role: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner

    owner = _engine(database_authority_dsns, "owner")
    runtime = _engine(database_authority_dsns, role)
    try:
        with owner.begin() as connection:
            local = _seed_ordinary_owner(connection)
            foreign = _seed_ordinary_owner(connection)
            foreign_blob = connection.execute(
                sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                {"sha": foreign.blob_sha256},
            ).scalar_one()

        missing_sha = uuid.uuid4().hex * 2
        with runtime.begin() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT * FROM easysynq_lock_worm_blob(:org,:sha)"),
                    {"org": local.org_id, "sha": missing_sha},
                ).all()
                == []
            )

        with pytest.raises(sa.exc.DBAPIError) as error:
            with runtime.begin() as connection:
                connection.execute(
                    sa.text("SELECT * FROM easysynq_lock_worm_blob(:org,:sha)"),
                    {"org": local.org_id, "sha": foreign.blob_sha256},
                ).all()

        assert getattr(error.value.orig, "sqlstate", None) == "P0001"
        diagnostics = getattr(error.value.orig, "diag", None)
        assert getattr(diagnostics, "message_primary", None) == "worm_blob_lock_refused"
        primary = str(getattr(diagnostics, "message_primary", ""))
        assert foreign.bucket not in primary
        assert foreign.object_key not in primary
        assert foreign.object_version_id not in primary

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha"),
                    {"sha": foreign.blob_sha256},
                ).scalar_one()
                == foreign_blob
            )
    finally:
        owner.dispose()
        runtime.dispose()


@pytest.mark.parametrize("role", ("easysynq_app", "easysynq_retention"))
@pytest.mark.parametrize("seam", ("BLOB", "OWNERS"))
def test_registry_definers_hold_exact_esor_then_essh_transaction_locks(
    database_authority_dsns: dict[str, str], role: str, seam: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner

    owner = _engine(database_authority_dsns, "owner")
    runtime = _engine(database_authority_dsns, role)
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)

        statement = sa.text(
            {
                "BLOB": "SELECT * FROM easysynq_lock_worm_blob(:org,:sha)",
                "OWNERS": "SELECT * FROM easysynq_lock_worm_owners(:org,:sha)",
            }[seam]
        )
        with runtime.begin() as connection:
            connection.execute(
                statement,
                {"org": seed.org_id, "sha": seed.blob_sha256},
            ).all()
            locks = (
                connection.execute(
                    sa.text(
                        """
                    SELECT classid=obj.namespace::oid AND objid=obj.identity_hash::oid
                    FROM pg_locks locks
                    CROSS JOIN (VALUES
                        (1163087698,hashtext(:org)),
                        (1163088712,hashtext(:sha))
                    ) AS obj(namespace,identity_hash)
                    WHERE locks.locktype='advisory'
                      AND locks.pid=pg_backend_pid()
                      AND locks.granted
                      AND classid=obj.namespace::oid
                      AND objid=obj.identity_hash::oid
                    ORDER BY obj.namespace
                    """
                    ),
                    {"org": str(seed.org_id), "sha": seed.blob_sha256},
                )
                .scalars()
                .all()
            )

        assert locks == [True, True]
    finally:
        owner.dispose()
        runtime.dispose()


@pytest.mark.parametrize("seam", ("BLOB", "OWNERS"))
@pytest.mark.parametrize("held_prefix", ("ESOR", "ESSH"))
def test_registry_definer_lock_prefix_precedes_blob_and_owner_rows(
    database_authority_dsns: dict[str, str], seam: str, held_prefix: str
) -> None:
    from tests.integration.test_ordinary_authority_transitions import _seed_ordinary_owner

    owner = _engine(database_authority_dsns, "owner")
    app = _engine(database_authority_dsns, "easysynq_app")
    application_name = f"registry-prefix-{seam.lower()}-{held_prefix.lower()}-{uuid.uuid4().hex}"
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)

        statement = sa.text(
            {
                "BLOB": "SELECT * FROM easysynq_lock_worm_blob(:org,:sha)",
                "OWNERS": "SELECT * FROM easysynq_lock_worm_owners(:org,:sha)",
            }[seam]
        )
        held_namespace = 1163087698 if held_prefix == "ESOR" else 1163088712
        held_value = str(seed.org_id) if held_prefix == "ESOR" else seed.blob_sha256

        def invoke() -> list[sa.Row[Any]]:
            with app.begin() as connection:
                connection.execute(
                    sa.text("SELECT set_config('application_name',:name,false)"),
                    {"name": application_name},
                )
                connection.execute(sa.text("SET LOCAL statement_timeout='15s'"))
                return connection.execute(
                    statement,
                    {"org": seed.org_id, "sha": seed.blob_sha256},
                ).all()

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            with owner.connect() as locker:
                transaction = locker.begin()
                locker.execute(
                    sa.text("SELECT pg_advisory_xact_lock(:namespace,hashtext(:value))"),
                    {"namespace": held_namespace, "value": held_value},
                )
                future = executor.submit(invoke)
                try:
                    assert _wait_for_named_lock(owner, application_name)
                    pid = locker.execute(
                        sa.text("SELECT pid FROM pg_stat_activity WHERE application_name=:name"),
                        {"name": application_name},
                    ).scalar_one()
                    locks = set(
                        locker.execute(
                            sa.text(
                                "SELECT classid::bigint,objid::bigint,granted FROM pg_locks "
                                "WHERE pid=:pid AND locktype='advisory'"
                            ),
                            {"pid": pid},
                        ).all()
                    )
                    relation_locks = set(
                        locker.execute(
                            sa.text(
                                "SELECT relation.relname FROM pg_locks locks "
                                "JOIN pg_class relation ON relation.oid=locks.relation "
                                "WHERE locks.pid=:pid AND locks.mode='RowShareLock'"
                            ),
                            {"pid": pid},
                        ).scalars()
                    )
                    org_hash = _unsigned32(
                        int(
                            locker.execute(
                                sa.text("SELECT hashtext(:value)"),
                                {"value": str(seed.org_id)},
                            ).scalar_one()
                        )
                    )
                    sha_hash = _unsigned32(
                        int(
                            locker.execute(
                                sa.text("SELECT hashtext(:value)"),
                                {"value": seed.blob_sha256},
                            ).scalar_one()
                        )
                    )
                    if held_prefix == "ESOR":
                        assert (1163087698, org_hash, False) in locks
                        assert not any(lock[0] == 1163088712 for lock in locks)
                    else:
                        assert (1163087698, org_hash, True) in locks
                        assert (1163088712, sha_hash, False) in locks
                    assert relation_locks.isdisjoint(
                        {
                            "blob",
                            "document_version",
                            "evidence_blob",
                            "evidence_pack",
                            "record",
                            "retention_policy",
                        }
                    )
                finally:
                    if transaction.is_active:
                        transaction.commit()
            result = future.result(timeout=10)
            assert len(result) == (1 if seam == "BLOB" else 0)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
    finally:
        owner.dispose()
        app.dispose()


def test_pinned_policy_period_trigger_is_exact_and_uses_private_owner_guard(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    try:
        with owner.connect() as connection:
            trigger = connection.execute(
                sa.text(
                    """
                    SELECT trigger.tgname,
                           trigger.tgfoid=to_regprocedure(
                               'public.easysynq_guard_worm_owner_pointer()'
                           ) AS exact_guard,
                           pg_get_triggerdef(trigger.oid,true) AS definition,
                           ARRAY(
                               SELECT attribute.attname
                               FROM unnest(trigger.tgattr::smallint[]) AS number(attnum)
                               JOIN pg_attribute attribute
                                 ON attribute.attrelid=trigger.tgrelid
                                AND attribute.attnum=number.attnum
                               ORDER BY attribute.attnum
                           ) AS update_columns
                    FROM pg_trigger trigger
                    WHERE trigger.tgrelid='public.retention_policy'::regclass
                      AND trigger.tgname='trg_retention_policy_worm_owner'
                      AND NOT trigger.tgisinternal
                    """
                )
            ).one()

        assert trigger.tgname == "trg_retention_policy_worm_owner"
        assert trigger.exact_guard is True
        assert "BEFORE UPDATE" in trigger.definition
        assert trigger.update_columns == ["duration", "worm_lock_period"]
    finally:
        owner.dispose()
