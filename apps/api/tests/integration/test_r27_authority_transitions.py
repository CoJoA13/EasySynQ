"""Executable PostgreSQL state-machine proofs for the isolated R27 principals.

The migration owner is used only to stage immutable inputs whose application producers land in
later tasks.  Every authority transition under test is invoked through the independently
authenticated runtime role that owns that transition.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class Actors:
    org_id: uuid.UUID
    record_id: uuid.UUID
    requester_id: uuid.UUID
    approver_id: uuid.UUID
    canceller_id: uuid.UUID


@dataclass(frozen=True)
class Target:
    id: uuid.UUID
    sha256: str
    bucket: str
    object_key: str
    object_version_id: str


@dataclass(frozen=True)
class RequestAuthority:
    request_id: uuid.UUID
    manifest_id: uuid.UUID
    manifest_sha256: str
    excluded_set_sha256: str
    targets: tuple[Target, ...]
    attestations: dict[str, uuid.UUID]
    challenges: dict[str, uuid.UUID]


@dataclass(frozen=True)
class ReadyAuthority:
    actors: Actors
    request: RequestAuthority
    recovery_key_db_id: uuid.UUID
    recovery_key_name: str
    witness_id: uuid.UUID


@dataclass(frozen=True)
class SourceExecution:
    actors: Actors
    request: RequestAuthority
    internal_execution_id: uuid.UUID
    public_execution_id: uuid.UUID
    disposition_event_id: uuid.UUID
    physical_marker_id: uuid.UUID
    logical_owner_id: uuid.UUID


def _engine(dsns: dict[str, str], role: str) -> Engine:
    return sa.create_engine(dsns[role])


def _hex_digest() -> str:
    return uuid.uuid4().hex * 2


def _nonce() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex[:11]


def _target(index: int = 0) -> Target:
    identity = uuid.uuid4().hex
    return Target(
        id=uuid.uuid4(),
        sha256=_hex_digest(),
        bucket=f"r27-bucket-{index}-{identity[:8]}",
        object_key=f"r27/{identity}",
        object_version_id=f"version-{identity}",
    )


def _seed_actors(owner: Engine) -> Actors:
    actors = Actors(
        org_id=uuid.UUID(int=0),
        record_id=uuid.uuid4(),
        requester_id=uuid.uuid4(),
        approver_id=uuid.uuid4(),
        canceller_id=uuid.uuid4(),
    )
    with owner.begin() as connection:
        org_id = connection.execute(
            sa.text("SELECT id FROM organization WHERE short_code='DEFAULT'")
        ).scalar_one()
        actors = Actors(
            org_id=org_id,
            record_id=actors.record_id,
            requester_id=actors.requester_id,
            approver_id=actors.approver_id,
            canceller_id=actors.canceller_id,
        )
        for label, user_id in (
            ("requester", actors.requester_id),
            ("approver", actors.approver_id),
            ("canceller", actors.canceller_id),
        ):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO app_user (id,org_id,keycloak_subject,display_name)
                    VALUES (:id,:org_id,:subject,:display_name)
                    """
                ),
                {
                    "id": user_id,
                    "org_id": actors.org_id,
                    "subject": f"r27-transition-{label}-{user_id}",
                    "display_name": f"R27 transition {label}",
                },
            )

        # The documented-information producer is outside Task 2.  Preserve all local constraints
        # while bypassing only the upstream shared-primary-key/FK triggers for this immutable row.
        connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            sa.text(
                """
                INSERT INTO record
                    (id,org_id,record_type,captured_by,retention_policy_id,disposition_state)
                VALUES
                    (:id,:org_id,'EVIDENCE',:captured_by,:policy_id,'ACTIVE')
                """
            ),
            {
                "id": actors.record_id,
                "org_id": actors.org_id,
                "captured_by": actors.requester_id,
                "policy_id": uuid.uuid4(),
            },
        )
    return actors


def _install_authorizer_key(dsns: dict[str, str]) -> tuple[uuid.UUID, str]:
    key_name = f"r27-authorizer-{uuid.uuid4().hex}"
    manager = _engine(dsns, "easysynq_r27_authorizer_key_manager")
    try:
        with manager.begin() as connection:
            key_db_id = connection.execute(
                sa.text(
                    """
                    SELECT easysynq_install_r27_authorizer_key(
                        :key_name,:public_key,:fingerprint,clock_timestamp(),:operator)
                    """
                ),
                {
                    "key_name": key_name,
                    "public_key": b"independent-r27-authorizer-public-key",
                    "fingerprint": _hex_digest(),
                    "operator": "integration-host-authorizer-key-manager",
                },
            ).scalar_one()
    finally:
        manager.dispose()
    return key_db_id, key_name


def _install_recovery_key(dsns: dict[str, str]) -> tuple[uuid.UUID, str]:
    key_name = f"recovery-verifier-{uuid.uuid4().hex}"
    manager = _engine(dsns, "easysynq_recovery_key_manager")
    try:
        with manager.begin() as connection:
            key_db_id = connection.execute(
                sa.text(
                    """
                    SELECT easysynq_install_recovery_verifier_key(
                        :key_name,:public_key,:fingerprint,clock_timestamp(),:operator)
                    """
                ),
                {
                    "key_name": key_name,
                    "public_key": b"independent-recovery-verifier-public-key",
                    "fingerprint": _hex_digest(),
                    "operator": "integration-host-recovery-key-manager",
                },
            ).scalar_one()
    finally:
        manager.dispose()
    return key_db_id, key_name


def _seed_request_authority(
    owner: Engine,
    actors: Actors,
    authorizer_key_id: uuid.UUID,
    *,
    actions: tuple[str, ...] = ("REQUEST", "APPROVE"),
    state: str | None = None,
    targets: tuple[Target, ...] | None = None,
    approval_user_id: uuid.UUID | None = None,
    consume_challenges: bool = False,
    challenge_manifest_sha256: str | None = None,
) -> RequestAuthority:
    request_id = uuid.uuid4()
    manifest_id = uuid.uuid4()
    manifest_sha256 = _hex_digest()
    excluded_set_sha256 = _hex_digest()
    requested_targets = targets or (_target(),)
    attestations: dict[str, uuid.UUID] = {}
    challenges: dict[str, uuid.UUID] = {}

    actor_by_action = {
        "REQUEST": actors.requester_id,
        "APPROVE": approval_user_id or actors.approver_id,
        "CANCEL": actors.canceller_id,
    }
    with owner.begin() as connection:
        connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
        requester_audit_event_id = None
        if state is not None:
            requester_audit_event_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO audit_event
                        (org_id,occurred_at,actor_id,actor_type,event_type,object_type,
                         object_id,scope_ref,reason)
                    VALUES
                        (:org_id,clock_timestamp(),:requester_id,'user',
                         'RECORD_WORM_DESTROY_REQUESTED','record',:record_id,'record',
                         'independent-r27-authority-fixture')
                    RETURNING id
                    """
                ),
                {
                    "org_id": actors.org_id,
                    "requester_id": actors.requester_id,
                    "record_id": actors.record_id,
                },
            ).scalar_one()
        connection.execute(
            sa.text(
                """
                INSERT INTO r27_request
                    (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256,state,
                     requester_user_id,requester_audit_event_id,approver_user_id,
                     requested_at,approved_at)
                VALUES
                    (:id,:org_id,:record_id,'court-ordered erasure',:legal_basis_sha256,
                     CAST(:state AS r27_request_state),:requester_user_id,
                     :requester_audit_event_id,:approver_user_id,
                     CASE WHEN :state IS NULL THEN NULL ELSE clock_timestamp() END,
                     CASE WHEN :approved THEN clock_timestamp() ELSE NULL END)
                """
            ),
            {
                "id": request_id,
                "org_id": actors.org_id,
                "record_id": actors.record_id,
                "legal_basis_sha256": _hex_digest(),
                "state": state,
                "requester_user_id": actors.requester_id if state is not None else None,
                "requester_audit_event_id": requester_audit_event_id,
                "approver_user_id": (
                    actors.approver_id
                    if state
                    in {
                        "WAITING_FOR_RECOVERY_GENERATION",
                        "READY_FOR_FINALIZATION",
                        "FINALIZING",
                        "EXECUTED",
                        "FAILED",
                    }
                    else None
                ),
                "approved": state
                in {
                    "WAITING_FOR_RECOVERY_GENERATION",
                    "READY_FOR_FINALIZATION",
                    "FINALIZING",
                    "EXECUTED",
                    "FAILED",
                },
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO r27_manifest
                    (id,request_id,schema_version,manifest_nonce,canonical_bytes,sha256,
                     excluded_set_sha256,expected_state,issued_at,expires_at)
                VALUES
                    (:id,:request_id,1,:nonce,:canonical_bytes,:sha256,:excluded_sha,
                     'WAITING_FOR_SECOND_APPROVER',clock_timestamp(),
                     clock_timestamp()+interval '1 hour')
                """
            ),
            {
                "id": manifest_id,
                "request_id": request_id,
                "nonce": _nonce(),
                "canonical_bytes": b"independent-r27-manifest",
                "sha256": manifest_sha256,
                "excluded_sha": excluded_set_sha256,
            },
        )
        for order, target in enumerate(requested_targets):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO r27_manifest_target
                        (id,manifest_id,target_order,blob_sha256,bucket,object_key,
                         object_version_id)
                    VALUES
                        (:id,:manifest_id,:target_order,:sha256,:bucket,:object_key,:version_id)
                    """
                ),
                {
                    "id": target.id,
                    "manifest_id": manifest_id,
                    "target_order": order,
                    "sha256": target.sha256,
                    "bucket": target.bucket,
                    "object_key": target.object_key,
                    "version_id": target.object_version_id,
                },
            )

        for action in actions:
            actor_id = actor_by_action[action]
            challenge_id = uuid.uuid4()
            attestation_id = uuid.uuid4()
            token_jti = f"r27-{action.lower()}-{uuid.uuid4()}"
            challenges[action] = challenge_id
            attestations[action] = attestation_id
            connection.execute(
                sa.text(
                    """
                    INSERT INTO r27_action_challenge
                        (id,action,request_id,record_id,issuer,token_jti,action_nonce,
                         accepted_claims,manifest_sha256,expires_at,consumed_at)
                    VALUES
                        (:id,CAST(:action AS r27_action_kind),:request_id,:record_id,
                         'https://issuer.test',:token_jti,:nonce,CAST(:claims AS jsonb),
                         :manifest_sha256,clock_timestamp()+interval '1 hour',
                         CASE WHEN :consumed THEN clock_timestamp() ELSE NULL END)
                    """
                ),
                {
                    "id": challenge_id,
                    "action": action,
                    "request_id": request_id,
                    "record_id": actors.record_id,
                    "token_jti": token_jti,
                    "nonce": _nonce(),
                    "claims": json.dumps(
                        {"iss": "https://issuer.test", "sub": str(actor_id), "jti": token_jti}
                    ),
                    "manifest_sha256": challenge_manifest_sha256 or manifest_sha256,
                    "consumed": consume_challenges,
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO r27_attestation
                        (id,challenge_id,request_id,action,canonical_bytes,canonical_sha256,
                         authorizer_key_id,signature,app_user_id,issuer,subject,session_id,
                         token_jti,audience,authorized_party,acr,auth_time,amr,
                         permission_granted,issued_at,expires_at)
                    VALUES
                        (:id,:challenge_id,:request_id,CAST(:action AS r27_action_kind),
                         :canonical_bytes,:canonical_sha256,:key_id,:signature,:app_user_id,
                         'https://issuer.test',:subject,:session_id,:token_jti,
                         '["easysynq-r27-authorizer"]'::jsonb,'easysynq-r27-authorizer',
                         'urn:easysynq:acr:r27','now'::text::timestamptz,
                         '["pwd","otp"]'::jsonb,true,clock_timestamp(),
                         clock_timestamp()+interval '1 hour')
                    """
                ),
                {
                    "id": attestation_id,
                    "challenge_id": challenge_id,
                    "request_id": request_id,
                    "action": action,
                    "canonical_bytes": b"independent-r27-attestation",
                    "canonical_sha256": _hex_digest(),
                    "key_id": authorizer_key_id,
                    "signature": b"independent-signature",
                    "app_user_id": actor_id,
                    "subject": f"subject-{actor_id}",
                    "session_id": f"session-{uuid.uuid4()}",
                    "token_jti": token_jti,
                },
            )

    return RequestAuthority(
        request_id=request_id,
        manifest_id=manifest_id,
        manifest_sha256=manifest_sha256,
        excluded_set_sha256=excluded_set_sha256,
        targets=requested_targets,
        attestations=attestations,
        challenges=challenges,
    )


def _insert_recovery_witness(
    owner: Engine,
    request: RequestAuthority,
    recovery_key_id: uuid.UUID,
    *,
    invalidated: bool = False,
) -> uuid.UUID:
    witness_id = uuid.uuid4()
    with owner.begin() as connection:
        connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            sa.text(
                """
                INSERT INTO recovery_generation_witness
                    (id,schema_version,key_id,witness_nonce,request_id,manifest_sha256,
                     generation_id,generation_identity,excluded_set_sha256,result,
                     canonical_bytes,signature,issued_at,verified_at,invalidated_at,
                     invalidation_audit_event_id,invalidation_reason)
                VALUES
                    (:id,1,:key_id,:nonce,:request_id,:manifest_sha256,:generation_id,
                     :generation_identity,:excluded_set_sha256,'VERIFIED',:canonical_bytes,
                     :signature,clock_timestamp(),clock_timestamp(),
                     CASE WHEN :invalidated THEN clock_timestamp() ELSE NULL END,
                     CASE WHEN :invalidated THEN 1 ELSE NULL END,
                     CASE WHEN :invalidated THEN 'KEY_REVOKED' ELSE NULL END)
                """
            ),
            {
                "id": witness_id,
                "key_id": recovery_key_id,
                "nonce": _nonce(),
                "request_id": request.request_id,
                "manifest_sha256": request.manifest_sha256,
                "generation_id": f"generation-{uuid.uuid4()}",
                "generation_identity": f"recovery-generation-{uuid.uuid4()}",
                "excluded_set_sha256": request.excluded_set_sha256,
                "canonical_bytes": b"independent-recovery-witness",
                "signature": b"independent-recovery-signature",
                "invalidated": invalidated,
            },
        )
    return witness_id


def _make_ready_authority(dsns: dict[str, str], owner: Engine) -> ReadyAuthority:
    actors = _seed_actors(owner)
    authorizer_key_id, _ = _install_authorizer_key(dsns)
    request = _seed_request_authority(owner, actors, authorizer_key_id)
    authorizer = _engine(dsns, "easysynq_r27_authorizer")
    try:
        with authorizer.begin() as connection:
            connection.execute(
                sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                {"id": request.attestations["REQUEST"]},
            ).scalar_one()
        with authorizer.begin() as connection:
            connection.execute(
                sa.text("SELECT easysynq_accept_r27_approval(:id,clock_timestamp())"),
                {"id": request.attestations["APPROVE"]},
            ).scalar_one()
    finally:
        authorizer.dispose()

    recovery_key_id, recovery_key_name = _install_recovery_key(dsns)
    witness_id = _insert_recovery_witness(owner, request, recovery_key_id)
    with owner.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE r27_request SET state='READY_FOR_FINALIZATION',"
                "updated_at=clock_timestamp() "
                "WHERE id=:id AND state='WAITING_FOR_RECOVERY_GENERATION'"
            ),
            {"id": request.request_id},
        )
    return ReadyAuthority(
        actors=actors,
        request=request,
        recovery_key_db_id=recovery_key_id,
        recovery_key_name=recovery_key_name,
        witness_id=witness_id,
    )


def _insert_worm_blob(connection: sa.Connection, actors: Actors, target: Target) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO blob
                (sha256,org_id,size_bytes,mime_type,bucket,object_key,object_version_id,
                 worm_locked,worm_enforced_mode,worm_asserted_retain_until,worm_asserted_at,
                 worm_retain_until,worm_retention_verified_at,worm_legal_hold,
                 worm_legal_hold_verified_at,sse)
            VALUES
                (:sha256,:org_id,1,'application/octet-stream',:bucket,:object_key,:version_id,
                 true,'GOVERNANCE',statement_timestamp()+interval '30 days',
                 statement_timestamp(),statement_timestamp()+interval '30 days',
                 statement_timestamp(),true,statement_timestamp(),false)
            """
        ),
        {
            "sha256": target.sha256,
            "org_id": actors.org_id,
            "bucket": target.bucket,
            "object_key": target.object_key,
            "version_id": target.object_version_id,
        },
    )


def _seed_source_execution(dsns: dict[str, str], owner: Engine) -> SourceExecution:
    actors = _seed_actors(owner)
    authorizer_key_id, _ = _install_authorizer_key(dsns)
    physical_target = _target(1)
    logical_target = _target(2)
    request = _seed_request_authority(
        owner,
        actors,
        authorizer_key_id,
        state="FINALIZING",
        targets=(physical_target, logical_target),
        consume_challenges=True,
    )
    internal_execution_id = uuid.uuid4()
    public_execution_id = uuid.uuid4()
    disposition_event_id = uuid.uuid4()
    physical_marker_id = uuid.uuid4()
    logical_owner_id = uuid.uuid4()

    with owner.begin() as connection:
        connection.execute(sa.text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            sa.text(
                """
                INSERT INTO r27_execution
                    (id,request_id,execution_id,state,claimed_at,attempt_count,
                     source_committed_at,updated_at)
                VALUES
                    (:id,:request_id,:execution_id,'SOURCE_COMMITTED',clock_timestamp(),1,
                     clock_timestamp(),clock_timestamp())
                """
            ),
            {
                "id": internal_execution_id,
                "request_id": request.request_id,
                "execution_id": public_execution_id,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO disposition_event
                    (id,org_id,record_id,action,tombstone,approved_by,requested_by,
                     is_worm_destroy,legal_basis,r27_request_id,r27_execution_id)
                VALUES
                    (:id,:org_id,:record_id,'DESTROY',true,:approved_by,:requested_by,true,
                     'court-ordered erasure',:request_id,:execution_id)
                """
            ),
            {
                "id": disposition_event_id,
                "org_id": actors.org_id,
                "record_id": actors.record_id,
                "approved_by": actors.approver_id,
                "requested_by": actors.requester_id,
                "request_id": request.request_id,
                "execution_id": internal_execution_id,
            },
        )
        connection.execute(
            sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
            {"id": actors.record_id},
        )
        _insert_worm_blob(connection, actors, physical_target)
        _insert_worm_blob(connection, actors, logical_target)
        connection.execute(
            sa.text(
                """
                INSERT INTO evidence_blob
                    (id,org_id,record_id,blob_sha256,is_original,created_by)
                VALUES
                    (:id,:org_id,:surviving_record_id,:sha256,true,:created_by)
                """
            ),
            {
                "id": logical_owner_id,
                "org_id": actors.org_id,
                "surviving_record_id": uuid.uuid4(),
                "sha256": logical_target.sha256,
                "created_by": actors.canceller_id,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO pending_blob_purge
                    (id,org_id,sha256,bucket,object_key,bypass_governance,record_id,
                     disposition_event_id,r27_request_id,object_version_id,r27_execution_id)
                VALUES
                    (:id,:org_id,:sha256,:bucket,:object_key,true,:record_id,
                     :disposition_event_id,:request_id,:version_id,:execution_id)
                """
            ),
            {
                "id": physical_marker_id,
                "org_id": actors.org_id,
                "sha256": physical_target.sha256,
                "bucket": physical_target.bucket,
                "object_key": physical_target.object_key,
                "record_id": actors.record_id,
                "disposition_event_id": disposition_event_id,
                "request_id": request.request_id,
                "version_id": physical_target.object_version_id,
                "execution_id": internal_execution_id,
            },
        )
    return SourceExecution(
        actors=actors,
        request=request,
        internal_execution_id=internal_execution_id,
        public_execution_id=public_execution_id,
        disposition_event_id=disposition_event_id,
        physical_marker_id=physical_marker_id,
        logical_owner_id=logical_owner_id,
    )


def test_request_approval_replay_and_cross_role_denial_are_atomic(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        actors = _seed_actors(owner)
        key_id, _ = _install_authorizer_key(database_authority_dsns)
        accepted = _seed_request_authority(owner, actors, key_id)
        cross_role_actors = _seed_actors(owner)
        cross_role = _seed_request_authority(owner, cross_role_actors, key_id, actions=("REQUEST",))

        with authorizer.begin() as connection:
            request_audit_id = connection.execute(
                sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                {"id": accepted.attestations["REQUEST"]},
            ).scalar_one()
        with authorizer.begin() as connection:
            approval_audit_id = connection.execute(
                sa.text("SELECT easysynq_accept_r27_approval(:id,clock_timestamp())"),
                {"id": accepted.attestations["APPROVE"]},
            ).scalar_one()

        with pytest.raises(sa.exc.DBAPIError, match="r27_request_authority_refused"):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                    {"id": accepted.attestations["REQUEST"]},
                )

        with pytest.raises(sa.exc.DBAPIError, match="permission denied for function"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                    {"id": cross_role.attestations["REQUEST"]},
                )

        with owner.connect() as connection:
            accepted_row = connection.execute(
                sa.text(
                    """
                    SELECT state::text,requester_user_id,approver_user_id,
                           requester_audit_event_id,approver_audit_event_id
                    FROM r27_request WHERE id=:id
                    """
                ),
                {"id": accepted.request_id},
            ).one()
            assert accepted_row == (
                "WAITING_FOR_RECOVERY_GENERATION",
                actors.requester_id,
                actors.approver_id,
                request_audit_id,
                approval_audit_id,
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event WHERE id IN (:request_id,:approval_id)"
                    ),
                    {"request_id": request_audit_id, "approval_id": approval_audit_id},
                ).scalar_one()
                == 2
            )
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM r27_request WHERE id=:id"),
                    {"id": cross_role.request_id},
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    sa.text("SELECT consumed_at FROM r27_action_challenge WHERE id=:id"),
                    {"id": cross_role.challenges["REQUEST"]},
                ).scalar_one_or_none()
                is None
            )
    finally:
        maintenance.dispose()
        authorizer.dispose()
        owner.dispose()


def test_approval_requires_a_distinct_actor_without_partial_writes(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        key_id, _ = _install_authorizer_key(database_authority_dsns)
        request = _seed_request_authority(
            owner,
            actors,
            key_id,
            approval_user_id=actors.requester_id,
        )
        with authorizer.begin() as connection:
            requester_audit_id = connection.execute(
                sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                {"id": request.attestations["REQUEST"]},
            ).scalar_one()

        with pytest.raises(sa.exc.DBAPIError, match="r27_approval_authority_refused"):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_approval(:id,clock_timestamp())"),
                    {"id": request.attestations["APPROVE"]},
                )

        with owner.connect() as connection:
            row = connection.execute(
                sa.text(
                    """
                    SELECT state::text,requester_audit_event_id,approver_user_id,
                           approver_audit_event_id
                    FROM r27_request WHERE id=:id
                    """
                ),
                {"id": request.request_id},
            ).one()
            assert row == ("WAITING_FOR_SECOND_APPROVER", requester_audit_id, None, None)
            assert (
                connection.execute(
                    sa.text("SELECT consumed_at FROM r27_action_challenge WHERE id=:id"),
                    {"id": request.challenges["APPROVE"]},
                ).scalar_one_or_none()
                is None
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event "
                        "WHERE reason='r27-second-approval-authorized' "
                        "AND object_id=:record_id"
                    ),
                    {"record_id": actors.record_id},
                ).scalar_one()
                == 0
            )
    finally:
        authorizer.dispose()
        owner.dispose()


def test_request_accept_rejects_mismatched_challenge_manifest_without_partial_writes(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        key_id, _ = _install_authorizer_key(database_authority_dsns)
        mismatched_digest = "f" * 64
        request = _seed_request_authority(
            owner,
            actors,
            key_id,
            actions=("REQUEST",),
            challenge_manifest_sha256=mismatched_digest,
        )
        assert request.manifest_sha256 != mismatched_digest

        with pytest.raises(sa.exc.DBAPIError, match="r27_request_authority_refused"):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                    {"id": request.attestations["REQUEST"]},
                )

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM r27_request WHERE id=:id"),
                    {"id": request.request_id},
                ).scalar_one()
                is None
            )
            assert (
                connection.execute(
                    sa.text("SELECT consumed_at FROM r27_action_challenge WHERE id=:id"),
                    {"id": request.challenges["REQUEST"]},
                ).scalar_one_or_none()
                is None
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event "
                        "WHERE reason='r27-requester-authorized' "
                        "AND object_id=:record_id"
                    ),
                    {"record_id": actors.record_id},
                ).scalar_one()
                == 0
            )
    finally:
        authorizer.dispose()
        owner.dispose()


def test_cancel_and_stale_close_only_prefinalization_requests(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        key_id, _ = _install_authorizer_key(database_authority_dsns)
        cancellable = _seed_request_authority(owner, actors, key_id, actions=("REQUEST", "CANCEL"))
        stale_actors = _seed_actors(owner)
        ready_to_stale = _seed_request_authority(
            owner, stale_actors, key_id, actions=(), state="READY_FOR_FINALIZATION"
        )
        finalizing_actors = _seed_actors(owner)
        finalizing = _seed_request_authority(
            owner,
            finalizing_actors,
            key_id,
            actions=("CANCEL",),
            state="FINALIZING",
        )

        with authorizer.begin() as connection:
            connection.execute(
                sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                {"id": cancellable.attestations["REQUEST"]},
            ).scalar_one()
        with authorizer.begin() as connection:
            cancellation_audit_id = connection.execute(
                sa.text("SELECT easysynq_cancel_r27_request(:id,clock_timestamp())"),
                {"id": cancellable.attestations["CANCEL"]},
            ).scalar_one()
        with authorizer.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_mark_r27_stale(:id,'MANIFEST_STALE','inventory changed',"
                    "clock_timestamp())"
                ),
                {"id": ready_to_stale.request_id},
            )

        with pytest.raises(sa.exc.DBAPIError, match="r27_cancel_refused"):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_cancel_r27_request(:id,clock_timestamp())"),
                    {"id": finalizing.attestations["CANCEL"]},
                )
        with pytest.raises(sa.exc.DBAPIError, match="r27_stale_refused"):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_mark_r27_stale(:id,'LATE','too late',clock_timestamp())"
                    ),
                    {"id": finalizing.request_id},
                )

        with owner.connect() as connection:
            cancelled = connection.execute(
                sa.text(
                    """
                    SELECT state::text,cancelled_by_user_id,cancellation_audit_event_id
                    FROM r27_request WHERE id=:id
                    """
                ),
                {"id": cancellable.request_id},
            ).one()
            assert cancelled == ("CANCELLED", actors.canceller_id, cancellation_audit_id)
            assert connection.execute(
                sa.text("SELECT state::text,error_code FROM r27_request WHERE id=:id"),
                {"id": ready_to_stale.request_id},
            ).one() == ("STALE", "MANIFEST_STALE")
            assert connection.execute(
                sa.text(
                    "SELECT state::text,cancelled_by_user_id,error_code "
                    "FROM r27_request WHERE id=:id"
                ),
                {"id": finalizing.request_id},
            ).one() == ("FINALIZING", None, None)
            assert (
                connection.execute(
                    sa.text("SELECT consumed_at FROM r27_action_challenge WHERE id=:id"),
                    {"id": finalizing.challenges["CANCEL"]},
                ).scalar_one_or_none()
                is None
            )
    finally:
        authorizer.dispose()
        owner.dispose()


def test_finalization_failure_retries_the_same_public_execution_identity(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        ready = _make_ready_authority(database_authority_dsns, owner)
        with maintenance.begin() as connection:
            first_claim = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(1,clock_timestamp())")
            ).one()
        assert first_claim.request_id == ready.request.request_id

        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_fail_r27_execution(:id,'TRANSIENT','retry me',"
                    "clock_timestamp())"
                ),
                {"id": first_claim.execution_id},
            )

        with owner.begin() as connection:
            failed = connection.execute(
                sa.text(
                    """
                    SELECT e.id,e.execution_id,e.state::text AS execution_state,
                           e.attempt_count,e.next_attempt_at,
                           r.state::text AS request_state,w.consumed_execution_id
                    FROM r27_execution e
                    JOIN r27_request r ON r.id=e.request_id
                    JOIN recovery_generation_witness w ON w.request_id=r.id
                    WHERE e.request_id=:request_id AND w.invalidated_at IS NULL
                    """
                ),
                {"request_id": ready.request.request_id},
            ).one()
            assert failed.execution_id == first_claim.execution_id
            assert failed.execution_state == "FAILED"
            assert failed.attempt_count == 1
            assert failed.next_attempt_at is not None
            assert failed.request_state == "FINALIZING"
            assert failed.consumed_execution_id == failed.id
            connection.execute(
                sa.text(
                    "UPDATE r27_execution "
                    "SET next_attempt_at=clock_timestamp()-interval '1 second' "
                    "WHERE id=:id"
                ),
                {"id": failed.id},
            )

        with maintenance.begin() as connection:
            retry_claim = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(1,clock_timestamp())")
            ).one()
        assert retry_claim == (ready.request.request_id, first_claim.execution_id)

        with owner.connect() as connection:
            retried = connection.execute(
                sa.text(
                    """
                    SELECT state::text,attempt_count,error_code,error_detail,next_attempt_at,
                           execution_id
                    FROM r27_execution WHERE request_id=:request_id
                    """
                ),
                {"request_id": ready.request.request_id},
            ).one()
            assert retried == ("CLAIMED", 2, None, None, None, first_claim.execution_id)
            assert (
                connection.execute(
                    sa.text("SELECT count(*) FROM r27_execution WHERE request_id=:request_id"),
                    {"request_id": ready.request.request_id},
                ).scalar_one()
                == 1
            )
    finally:
        maintenance.dispose()
        owner.dispose()


def test_recovery_key_revoke_invalidates_witness_and_resumes_same_execution(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    recovery_manager = _engine(database_authority_dsns, "easysynq_recovery_key_manager")
    try:
        ready = _make_ready_authority(database_authority_dsns, owner)
        with maintenance.begin() as connection:
            initial_claim = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(1,clock_timestamp())")
            ).one()

        with recovery_manager.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_revoke_recovery_verifier_key("
                    ":key_name,clock_timestamp(),:operator)"
                ),
                {
                    "key_name": ready.recovery_key_name,
                    "operator": "integration-host-recovery-key-manager",
                },
            )

        replacement_key_id, _ = _install_recovery_key(database_authority_dsns)
        replacement_witness_id = _insert_recovery_witness(owner, ready.request, replacement_key_id)
        with owner.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE r27_request SET state='READY_FOR_FINALIZATION',"
                    "updated_at=clock_timestamp() "
                    "WHERE id=:id AND state='WAITING_FOR_RECOVERY_GENERATION'"
                ),
                {"id": ready.request.request_id},
            )

        with maintenance.begin() as connection:
            resumed_claim = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(1,clock_timestamp())")
            ).one()
        assert resumed_claim == (ready.request.request_id, initial_claim.execution_id)

        with owner.connect() as connection:
            revoked = connection.execute(
                sa.text(
                    """
                    SELECT w.invalidated_at,w.invalidation_reason,w.invalidation_audit_event_id,
                           k.revoked_at
                    FROM recovery_generation_witness w
                    JOIN recovery_generation_verifier_key k ON k.id=w.key_id
                    WHERE w.id=:witness_id
                    """
                ),
                {"witness_id": ready.witness_id},
            ).one()
            assert revoked.invalidated_at is not None
            assert revoked.invalidation_reason == "KEY_REVOKED"
            assert revoked.invalidation_audit_event_id is not None
            assert revoked.revoked_at is not None
            active = connection.execute(
                sa.text(
                    """
                    SELECT w.id AS witness_id,w.consumed_execution_id,
                           e.id AS internal_execution_id,e.execution_id,e.state::text,
                           e.attempt_count,r.state::text AS request_state
                    FROM recovery_generation_witness w
                    JOIN r27_request r ON r.id=w.request_id
                    JOIN r27_execution e ON e.request_id=r.id
                    WHERE w.id=:witness_id
                    """
                ),
                {"witness_id": replacement_witness_id},
            ).one()
            assert active.witness_id == replacement_witness_id
            assert active.consumed_execution_id == active.internal_execution_id
            assert active.execution_id == initial_claim.execution_id
            assert active.state == "CLAIMED"
            assert active.attempt_count == 2
            assert active.request_state == "FINALIZING"
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM recovery_generation_witness "
                        "WHERE request_id=:id AND invalidated_at IS NULL"
                    ),
                    {"id": ready.request.request_id},
                ).scalar_one()
                == 1
            )
    finally:
        recovery_manager.dispose()
        maintenance.dispose()
        owner.dispose()


def test_r27_physical_and_surviving_owner_results_complete_as_mixed_outcome(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical, logical = source.request.targets

        with pytest.raises(sa.exc.DBAPIError, match="r27_hold_release_refused"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_hold_release("
                        ":sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": physical.sha256,
                        "version": physical.object_version_id,
                        "execution": uuid.uuid4(),
                    },
                )
        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_record_r27_hold_release("
                    ":sha,:version,:execution,clock_timestamp())"
                ),
                {
                    "sha": physical.sha256,
                    "version": physical.object_version_id,
                    "execution": source.public_execution_id,
                },
            )

        with maintenance.begin() as connection:
            claimed = (
                connection.execute(
                    sa.text(
                        "SELECT * FROM easysynq_claim_r27_exact_purges("
                        ":execution,10,clock_timestamp())"
                    ),
                    {"execution": source.public_execution_id},
                )
                .mappings()
                .all()
            )
        assert claimed == [
            {
                "marker_id": source.physical_marker_id,
                "blob_sha256": physical.sha256,
                "bucket": physical.bucket,
                "object_key": physical.object_key,
                "object_version_id": physical.object_version_id,
            }
        ]

        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_fail_r27_exact_purge("
                    ":execution,:marker,'OBJECT_STORE_TIMEOUT','retry',clock_timestamp())"
                ),
                {
                    "execution": source.public_execution_id,
                    "marker": source.physical_marker_id,
                },
            )
        with maintenance.begin() as connection:
            retry = connection.execute(
                sa.text(
                    "SELECT * FROM easysynq_claim_r27_exact_purges(:execution,1,clock_timestamp())"
                ),
                {"execution": source.public_execution_id},
            ).one()
        assert retry.marker_id == source.physical_marker_id

        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_record_r27_purge(:sha,:version,:execution,clock_timestamp())"
                ),
                {
                    "sha": physical.sha256,
                    "version": physical.object_version_id,
                    "execution": source.public_execution_id,
                },
            )

        with owner.connect() as connection:
            assert connection.execute(
                sa.text("SELECT state::text,result_code::text FROM r27_execution WHERE id=:id"),
                {"id": source.internal_execution_id},
            ).one() == ("PURGING", None)
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event WHERE reason='r27-exact-purge-complete' "
                        "AND after->>'execution_id'=:execution"
                    ),
                    {"execution": str(source.public_execution_id)},
                ).scalar_one()
                == 0
            )

        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_record_r27_surviving_owner("
                    ":sha,:version,:execution,clock_timestamp())"
                ),
                {
                    "sha": logical.sha256,
                    "version": logical.object_version_id,
                    "execution": source.public_execution_id,
                },
            )

        with pytest.raises(sa.exc.DBAPIError, match="r27_surviving_owner_refused"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_surviving_owner("
                        ":sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": logical.sha256,
                        "version": logical.object_version_id,
                        "execution": source.public_execution_id,
                    },
                )

        with owner.connect() as connection:
            execution = connection.execute(
                sa.text(
                    """
                    SELECT e.state::text,e.result_code::text,e.completed_at,r.state::text
                    FROM r27_execution e JOIN r27_request r ON r.id=e.request_id
                    WHERE e.id=:id
                    """
                ),
                {"id": source.internal_execution_id},
            ).one()
            assert execution[0:2] == ("EXECUTED", "MIXED_OUTCOME")
            assert execution.completed_at is not None
            assert execution[3] == "EXECUTED"
            results = connection.execute(
                sa.text(
                    """
                    SELECT result_code::text,purge_marker_id,surviving_owner_kind,
                           surviving_owner_id
                    FROM r27_execution_target_result
                    WHERE execution_id=:id ORDER BY result_code::text
                    """
                ),
                {"id": source.internal_execution_id},
            ).all()
            assert results == [
                ("LOGICAL_ONLY_SURVIVING_OWNER", None, "EVIDENCE_BLOB", source.logical_owner_id),
                ("PHYSICAL_ERASED", source.physical_marker_id, None, None),
            ]
            marker = connection.execute(
                sa.text("SELECT state::text,attempt_count FROM pending_blob_purge WHERE id=:id"),
                {"id": source.physical_marker_id},
            ).one()
            assert marker == ("VERIFIED", 2)
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event WHERE reason='r27-exact-purge-complete' "
                        "AND after->>'execution_id'=:execution"
                    ),
                    {"execution": str(source.public_execution_id)},
                ).scalar_one()
                == 1
            )
    finally:
        maintenance.dispose()
        owner.dispose()


def test_r27_purge_claim_rejects_marker_with_wrong_physical_coordinates(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with owner.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE pending_blob_purge SET bucket='wrong-bucket',object_key='wrong-key' "
                    "WHERE id=:id"
                ),
                {"id": source.physical_marker_id},
            )

        with maintenance.begin() as connection:
            claimed = connection.execute(
                sa.text(
                    "SELECT * FROM easysynq_claim_r27_exact_purges(:execution,10,clock_timestamp())"
                ),
                {"execution": source.public_execution_id},
            ).all()
        assert claimed == []

        with owner.connect() as connection:
            assert connection.execute(
                sa.text("SELECT state::text,attempt_count FROM pending_blob_purge WHERE id=:id"),
                {"id": source.physical_marker_id},
            ).one() == ("PENDING", 0)
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM r27_execution WHERE id=:id"),
                    {"id": source.internal_execution_id},
                ).scalar_one()
                == "SOURCE_COMMITTED"
            )
            assert (
                connection.execute(
                    sa.text("SELECT purged_at FROM blob WHERE sha256=:sha"),
                    {"sha": physical.sha256},
                ).scalar_one_or_none()
                is None
            )
    finally:
        maintenance.dispose()
        owner.dispose()


def test_role_membership_ledger_is_idempotent_and_audits_exactly_once(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    role_manager = _engine(database_authority_dsns, "easysynq_r27_role_manager")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        operation_id = uuid.uuid4()
        denied_operation_id = uuid.uuid4()
        begin = sa.text(
            "SELECT easysynq_begin_r27_role_membership("
            ":operation,:user_id,:action,:operator,clock_timestamp())"
        )
        values = {
            "operation": operation_id,
            "user_id": actors.approver_id,
            "action": "ASSIGN",
            "operator": "integration-host-role-manager",
        }
        with role_manager.begin() as connection:
            connection.execute(begin, values)
            connection.execute(begin, values)

        with pytest.raises(sa.exc.DBAPIError, match="role_membership_idempotency_conflict"):
            with role_manager.begin() as connection:
                connection.execute(begin, {**values, "action": "REVOKE"})
        with pytest.raises(sa.exc.DBAPIError, match="permission denied for function"):
            with authorizer.begin() as connection:
                connection.execute(
                    begin,
                    {
                        **values,
                        "operation": denied_operation_id,
                    },
                )

        with role_manager.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_fail_r27_role_membership("
                    ":operation,'KEYCLOAK_TIMEOUT','retry',clock_timestamp())"
                ),
                {"operation": operation_id},
            )
        with role_manager.begin() as connection:
            first_audit_id = connection.execute(
                sa.text(
                    "SELECT easysynq_complete_r27_role_membership(:operation,clock_timestamp())"
                ),
                {"operation": operation_id},
            ).scalar_one()
        with role_manager.begin() as connection:
            replay_audit_id = connection.execute(
                sa.text(
                    "SELECT easysynq_complete_r27_role_membership(:operation,clock_timestamp())"
                ),
                {"operation": operation_id},
            ).scalar_one()
        assert replay_audit_id == first_audit_id

        with pytest.raises(sa.exc.DBAPIError, match="role_membership_failure_refused"):
            with role_manager.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_fail_r27_role_membership("
                        ":operation,'LATE_FAILURE','must not reopen',clock_timestamp())"
                    ),
                    {"operation": operation_id},
                )

        with owner.connect() as connection:
            operation = connection.execute(
                sa.text(
                    """
                    SELECT user_id,org_id,action,state,audit_event_id,error_code,error_detail
                    FROM r27_role_membership_operation WHERE id=:id
                    """
                ),
                {"id": operation_id},
            ).one()
            assert operation == (
                actors.approver_id,
                actors.org_id,
                "ASSIGN",
                "AUDITED",
                first_audit_id,
                None,
                None,
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event WHERE id=:audit_id "
                        "AND event_type='ROLE_ASSIGN' AND object_id=:user_id "
                        "AND scope_ref='r27-approver' AND reason='host-r27-role-assignment' "
                        "AND after->>'operation_id'=:operation_id"
                    ),
                    {
                        "audit_id": first_audit_id,
                        "user_id": actors.approver_id,
                        "operation_id": str(operation_id),
                    },
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    sa.text("SELECT count(*) FROM r27_role_membership_operation WHERE id=:id"),
                    {"id": denied_operation_id},
                ).scalar_one()
                == 0
            )
    finally:
        authorizer.dispose()
        role_manager.dispose()
        owner.dispose()


def test_authorizer_key_revoke_closes_prepared_and_post_source_authority(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    key_manager = _engine(database_authority_dsns, "easysynq_r27_authorizer_key_manager")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        key_id, key_name = _install_authorizer_key(database_authority_dsns)
        prepared = _seed_request_authority(owner, actors, key_id, actions=("REQUEST",), state=None)
        post_source_actors = _seed_actors(owner)
        post_source = _seed_request_authority(
            owner,
            post_source_actors,
            key_id,
            state="FINALIZING",
            consume_challenges=True,
        )
        internal_execution_id = uuid.uuid4()
        public_execution_id = uuid.uuid4()
        with owner.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO r27_execution
                        (id,request_id,execution_id,state,claimed_at,attempt_count,
                         source_committed_at,updated_at)
                    VALUES
                        (:id,:request_id,:execution_id,'SOURCE_COMMITTED',clock_timestamp(),1,
                         clock_timestamp(),clock_timestamp())
                    """
                ),
                {
                    "id": internal_execution_id,
                    "request_id": post_source.request_id,
                    "execution_id": public_execution_id,
                },
            )

        with key_manager.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_revoke_r27_authorizer_key("
                    ":key_name,clock_timestamp(),:operator)"
                ),
                {
                    "key_name": key_name,
                    "operator": "integration-host-authorizer-key-manager",
                },
            )

        with pytest.raises(sa.exc.DBAPIError, match="r27_request_authority_refused"):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                    {"id": prepared.attestations["REQUEST"]},
                )

        with owner.connect() as connection:
            assert connection.execute(
                sa.text("SELECT state::text,error_code FROM r27_request WHERE id=:id"),
                {"id": prepared.request_id},
            ).one() == ("STALE", "AUTHORIZER_KEY_REVOKED")
            assert (
                connection.execute(
                    sa.text("SELECT consumed_at FROM r27_action_challenge WHERE id=:id"),
                    {"id": prepared.challenges["REQUEST"]},
                ).scalar_one_or_none()
                is None
            )
            assert connection.execute(
                sa.text("SELECT state::text,error_code FROM r27_request WHERE id=:id"),
                {"id": post_source.request_id},
            ).one() == ("FAILED", "AUTHORIZER_KEY_REVOKED")
            execution = connection.execute(
                sa.text(
                    "SELECT state::text,error_code,next_attempt_at,source_committed_at "
                    "FROM r27_execution WHERE id=:id"
                ),
                {"id": internal_execution_id},
            ).one()
            assert execution[0:3] == ("FAILED", "AUTHORIZER_KEY_REVOKED", None)
            assert execution.source_committed_at is not None
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM audit_event WHERE reason='r27-requester-authorized' "
                        "AND object_id=:record_id"
                    ),
                    {"record_id": actors.record_id},
                ).scalar_one()
                == 0
            )
    finally:
        authorizer.dispose()
        key_manager.dispose()
        owner.dispose()
