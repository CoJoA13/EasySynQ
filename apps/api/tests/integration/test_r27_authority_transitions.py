"""Executable PostgreSQL state-machine proofs for the isolated R27 principals.

The migration owner is used only to stage immutable inputs whose application producers land in
later tasks.  Every authority transition under test is invoked through the independently
authenticated runtime role that owns that transition.
"""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

_R27_CLIENT_ID = "easysynq-r27-authorizer"
_R27_ACR = "urn:easysynq:acr:r27-webauthn"


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
    authorizer_key_ids: dict[str, uuid.UUID]


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
    logical_owner_record_id: uuid.UUID
    recovery_key_db_id: uuid.UUID
    witness_id: uuid.UUID


def _engine(dsns: dict[str, str], role: str) -> Engine:
    return sa.create_engine(dsns[role])


def _wait_for_named_lock(engine: Engine, application_name: str) -> bool:
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

        framework_id = uuid.uuid4()
        policy_id = uuid.uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO framework (id,org_id,code,name,is_active,is_authorable) "
                "VALUES (:id,:org,:code,'R27 transition framework',true,false)"
            ),
            {"id": framework_id, "org": actors.org_id, "code": f"r27:{framework_id}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO retention_policy "
                "(id,org_id,name,duration,worm_lock_period,disposition_action) "
                "VALUES (:id,:org,:name,'P1Y','P1Y','DESTROY')"
            ),
            {"id": policy_id, "org": actors.org_id, "name": f"r27-{policy_id}"},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO documented_information
                    (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                     current_state,is_singleton,classification,
                     acknowledgement_required,created_by)
                VALUES
                    (:id,:org,:framework,'RECORD',:identifier,'R27 target record',:user,
                     'Draft',false,'Internal',false,:user)
                """
            ),
            {
                "id": actors.record_id,
                "org": actors.org_id,
                "framework": framework_id,
                "identifier": f"R27-REC-{actors.record_id}",
                "user": actors.requester_id,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO record
                    (id,org_id,record_type,captured_by,content_hash_version,
                     retention_policy_id,disposition_state)
                VALUES
                    (:id,:org_id,'EVIDENCE',:captured_by,2,:policy_id,'ACTIVE')
                """
            ),
            {
                "id": actors.record_id,
                "org_id": actors.org_id,
                "captured_by": actors.requester_id,
                "policy_id": policy_id,
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
    authorizer_key_ids: dict[str, uuid.UUID] | None = None,
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
            session_id = f"session-{uuid.uuid4()}"
            actor_subject = connection.execute(
                sa.text("SELECT keycloak_subject FROM app_user WHERE id=:id"),
                {"id": actor_id},
            ).scalar_one()
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
                         :manifest_sha256,clock_timestamp()+interval '90 seconds',
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
                        {
                            "iss": "https://issuer.test",
                            "sub": actor_subject,
                            "sid": session_id,
                            "jti": token_jti,
                            "azp": _R27_CLIENT_ID,
                            "acr": _R27_ACR,
                        }
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
                         jsonb_build_array(CAST(:client_id AS text)),
                         CAST(:client_id AS varchar),
                         :acr,'now'::text::timestamptz,
                         '["pwd","otp"]'::jsonb,true,clock_timestamp(),
                         clock_timestamp()+interval '90 seconds')
                    """
                ),
                {
                    "id": attestation_id,
                    "challenge_id": challenge_id,
                    "request_id": request_id,
                    "action": action,
                    "canonical_bytes": b"independent-r27-attestation",
                    "canonical_sha256": _hex_digest(),
                    "key_id": (authorizer_key_ids or {}).get(action, authorizer_key_id),
                    "signature": b"independent-signature",
                    "app_user_id": actor_id,
                    "subject": actor_subject,
                    "session_id": session_id,
                    "token_jti": token_jti,
                    "client_id": _R27_CLIENT_ID,
                    "acr": _R27_ACR,
                },
            )

        matched_action_authorities = connection.execute(
            sa.text(
                """
                SELECT count(*)
                FROM r27_attestation attestation
                JOIN r27_action_challenge challenge ON challenge.id=attestation.challenge_id
                WHERE attestation.request_id=:request
                  AND challenge.accepted_claims->>'azp'=attestation.authorized_party
                  AND challenge.accepted_claims->>'acr'=attestation.acr
                  AND attestation.authorized_party=:client_id
                  AND attestation.acr=:acr
                """
            ),
            {"request": request_id, "client_id": _R27_CLIENT_ID, "acr": _R27_ACR},
        ).scalar_one()
        assert matched_action_authorities == len(actions)

    return RequestAuthority(
        request_id=request_id,
        manifest_id=manifest_id,
        manifest_sha256=manifest_sha256,
        excluded_set_sha256=excluded_set_sha256,
        targets=requested_targets,
        attestations=attestations,
        challenges=challenges,
        authorizer_key_ids={
            action: (authorizer_key_ids or {}).get(action, authorizer_key_id) for action in actions
        },
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


def _make_ready_authority(
    dsns: dict[str, str],
    owner: Engine,
    *,
    targets: tuple[Target, ...] | None = None,
    shared_authorizer_key: bool = False,
) -> ReadyAuthority:
    actors = _seed_actors(owner)
    request_key_id, _ = _install_authorizer_key(dsns)
    approval_key_id = request_key_id
    if not shared_authorizer_key:
        approval_key_id, _ = _install_authorizer_key(dsns)
    request = _seed_request_authority(
        owner,
        actors,
        request_key_id,
        targets=targets,
        authorizer_key_ids={"REQUEST": request_key_id, "APPROVE": approval_key_id},
    )
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


def _age_accepted_action_artifacts(owner: Engine, request: RequestAuthority) -> None:
    request_accepted_at = datetime.now(UTC) - timedelta(minutes=10)
    approval_accepted_at = datetime.now(UTC) - timedelta(minutes=7)
    with owner.begin() as connection:
        connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
        request_audit_id, approval_audit_id = connection.execute(
            sa.text(
                "SELECT requester_audit_event_id,approver_audit_event_id "
                "FROM r27_request WHERE id=:id"
            ),
            {"id": request.request_id},
        ).one()
        for action, accepted_at, audit_id in (
            ("REQUEST", request_accepted_at, request_audit_id),
            ("APPROVE", approval_accepted_at, approval_audit_id),
        ):
            connection.execute(
                sa.text(
                    "UPDATE r27_attestation SET issued_at=:issued,auth_time=:auth_time,"
                    "expires_at=:expires WHERE id=:id"
                ),
                {
                    "issued": accepted_at - timedelta(seconds=30),
                    "auth_time": accepted_at - timedelta(seconds=60),
                    "expires": accepted_at + timedelta(seconds=30),
                    "id": request.attestations[action],
                },
            )
            connection.execute(
                sa.text(
                    "UPDATE r27_action_challenge SET created_at=:created,"
                    "consumed_at=:accepted,expires_at=:expires WHERE id=:id"
                ),
                {
                    "created": accepted_at - timedelta(seconds=60),
                    "accepted": accepted_at,
                    "expires": accepted_at + timedelta(seconds=30),
                    "id": request.challenges[action],
                },
            )
            connection.execute(
                sa.text("UPDATE audit_event SET occurred_at=:at WHERE id=:id"),
                {"at": accepted_at, "id": audit_id},
            )
        connection.execute(
            sa.text(
                "UPDATE r27_request SET requested_at=:requested,approved_at=:approved,"
                "updated_at=:approved WHERE id=:id"
            ),
            {
                "requested": request_accepted_at,
                "approved": approval_accepted_at,
                "id": request.request_id,
            },
        )
        connection.execute(
            sa.text("UPDATE r27_manifest SET issued_at=:issued,expires_at=:expires WHERE id=:id"),
            {
                "issued": request_accepted_at - timedelta(minutes=1),
                "expires": request_accepted_at - timedelta(minutes=1) + timedelta(days=1),
                "id": request.manifest_id,
            },
        )
        connection.execute(
            sa.text(
                "UPDATE r27_authorizer_key SET active_at=:active "
                "WHERE id IN (:request_key,:approval_key)"
            ),
            {
                "active": request_accepted_at - timedelta(days=1),
                "request_key": request.authorizer_key_ids["REQUEST"],
                "approval_key": request.authorizer_key_ids["APPROVE"],
            },
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
    physical_target = _target(1)
    logical_target = _target(2)
    ready = _make_ready_authority(
        dsns,
        owner,
        targets=(physical_target, logical_target),
    )
    actors = ready.actors
    request = ready.request
    maintenance = _engine(dsns, "easysynq_r27_maintenance")
    try:
        with maintenance.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(100,clock_timestamp())")
            ).all()
        public_execution_id = next(
            row.execution_id for row in claimed if row.request_id == request.request_id
        )
    finally:
        maintenance.dispose()

    with owner.connect() as connection:
        internal_execution_id = connection.execute(
            sa.text("SELECT id FROM r27_execution WHERE request_id=:request"),
            {"request": request.request_id},
        ).scalar_one()
    disposition_event_id = uuid.uuid4()
    physical_marker_id = uuid.uuid4()
    logical_owner_id, logical_target_edge_id = sorted((uuid.uuid4(), uuid.uuid4()))
    logical_owner_record_id = uuid.uuid4()

    with owner.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE r27_execution SET state='SOURCE_COMMITTED',"
                "source_committed_at=clock_timestamp(),updated_at=clock_timestamp() "
                "WHERE id=:id AND state='CLAIMED'"
            ),
            {"id": internal_execution_id},
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
        parent = connection.execute(
            sa.text(
                "SELECT information.framework_id,record.retention_policy_id "
                "FROM documented_information information "
                "JOIN record ON record.id=information.id WHERE information.id=:id"
            ),
            {"id": actors.record_id},
        ).one()
        connection.execute(
            sa.text(
                """
                INSERT INTO documented_information
                    (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                     current_state,is_singleton,classification,
                     acknowledgement_required,created_by)
                VALUES
                    (:id,:org,:framework,'RECORD',:identifier,'R27 surviving record',:user,
                     'Draft',false,'Internal',false,:user)
                """
            ),
            {
                "id": logical_owner_record_id,
                "org": actors.org_id,
                "framework": parent.framework_id,
                "identifier": f"R27-SURVIVOR-{logical_owner_record_id}",
                "user": actors.canceller_id,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO record
                    (id,org_id,record_type,captured_by,content_hash_version,
                     retention_policy_id,disposition_state,legal_hold)
                VALUES
                    (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',false)
                """
            ),
            {
                "id": logical_owner_record_id,
                "org": actors.org_id,
                "user": actors.canceller_id,
                "policy": parent.retention_policy_id,
            },
        )
        for target in (physical_target, logical_target):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO evidence_blob
                        (id,org_id,record_id,blob_sha256,is_original,created_by)
                    VALUES
                        (:id,:org_id,:record_id,:sha256,true,:created_by)
                    """
                ),
                {
                    "id": (
                        logical_target_edge_id if target.id == logical_target.id else uuid.uuid4()
                    ),
                    "org_id": actors.org_id,
                    "record_id": actors.record_id,
                    "sha256": target.sha256,
                    "created_by": actors.requester_id,
                },
            )
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
                "surviving_record_id": logical_owner_record_id,
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
        logical_owner_record_id=logical_owner_record_id,
        recovery_key_db_id=ready.recovery_key_db_id,
        witness_id=ready.witness_id,
    )


def _claim_r27_physical_marker(
    connection: sa.Connection,
    source: SourceExecution,
) -> None:
    claimed_ids = {
        row.marker_id
        for row in connection.execute(
            sa.text(
                "SELECT * FROM easysynq_claim_r27_exact_purges(:execution,10,clock_timestamp())"
            ),
            {"execution": source.public_execution_id},
        )
    }
    assert source.physical_marker_id in claimed_ids


def _add_r27_evidence_owner(
    connection: sa.Connection,
    source: SourceExecution,
    target: Target,
    *,
    state: str,
    legal_hold: bool = False,
    permanent: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    record_id = uuid.uuid4()
    evidence_id = uuid.uuid4()
    parent = connection.execute(
        sa.text(
            "SELECT information.framework_id FROM documented_information information "
            "WHERE information.id=:id"
        ),
        {"id": source.actors.record_id},
    ).one()
    policy_id = uuid.uuid4()
    duration = "PERMANENT" if permanent else "P1Y"
    connection.execute(
        sa.text(
            "INSERT INTO retention_policy "
            "(id,org_id,name,duration,worm_lock_period,disposition_action) "
            "VALUES (:id,:org,:name,:duration,:duration,'RETAIN_PERMANENT')"
        ),
        {
            "id": policy_id,
            "org": source.actors.org_id,
            "name": f"r27-survivor-{policy_id}",
            "duration": duration,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                 current_state,is_singleton,classification,
                 acknowledgement_required,created_by)
            VALUES
                (:id,:org,:framework,'RECORD',:identifier,'R27 additional owner',:user,
                 'Draft',false,'Internal',false,:user)
            """
        ),
        {
            "id": record_id,
            "org": source.actors.org_id,
            "framework": parent.framework_id,
            "identifier": f"R27-OWNER-{record_id}",
            "user": source.actors.canceller_id,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO record
                (id,org_id,record_type,captured_by,content_hash_version,
                 retention_policy_id,disposition_state,legal_hold)
            VALUES (:id,:org,'EVIDENCE',:user,2,:policy,:state,:legal_hold)
            """
        ),
        {
            "id": record_id,
            "org": source.actors.org_id,
            "user": source.actors.canceller_id,
            "policy": policy_id,
            "state": state,
            "legal_hold": legal_hold,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO evidence_blob
                (id,org_id,record_id,blob_sha256,is_original,created_by)
            VALUES (:id,:org,:record,:sha,true,:user)
            """
        ),
        {
            "id": evidence_id,
            "org": source.actors.org_id,
            "record": record_id,
            "sha": target.sha256,
            "user": source.actors.canceller_id,
        },
    )
    return record_id, evidence_id


def _add_r27_document_owner(
    connection: sa.Connection,
    source: SourceExecution,
    target: Target,
) -> uuid.UUID:
    document_id = uuid.uuid4()
    version_id = uuid.uuid4()
    parent = connection.execute(
        sa.text(
            "SELECT information.framework_id,record.retention_policy_id "
            "FROM documented_information information "
            "JOIN record ON record.id=information.id WHERE information.id=:id"
        ),
        {"id": source.actors.record_id},
    ).one()
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                 current_state,is_singleton,classification,
                 acknowledgement_required,created_by)
            VALUES
                (:id,:org,:framework,'DOCUMENT',:identifier,'R27 document owner',:user,
                 'Draft',false,'Internal',false,:user)
            """
        ),
        {
            "id": document_id,
            "org": source.actors.org_id,
            "framework": parent.framework_id,
            "identifier": f"R27-DOC-{document_id}",
            "user": source.actors.canceller_id,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO document_version
                (id,org_id,document_id,version_seq,revision_label,change_significance,
                 change_reason,version_state,retention_authority_kind,
                 retention_policy_id,retention_basis_date,source_blob_sha256,
                 metadata_snapshot,imported,author_user_id,created_by)
            VALUES
                (:id,:org,:document,1,'A','MINOR','R27 live owner','Draft','POLICY',
                 :policy,current_date,:sha,'{}'::jsonb,false,:user,:user)
            """
        ),
        {
            "id": version_id,
            "org": source.actors.org_id,
            "document": document_id,
            "policy": parent.retention_policy_id,
            "sha": target.sha256,
            "user": source.actors.canceller_id,
        },
    )
    return version_id


def _add_corrupt_cross_org_owner(
    connection: sa.Connection,
    source: SourceExecution,
    target: Target,
    *,
    kind: str,
) -> uuid.UUID:
    target_policy_id = connection.execute(
        sa.text("SELECT retention_policy_id FROM record WHERE id=:record"),
        {"record": source.actors.record_id},
    ).scalar_one()
    other_org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    framework_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    target_permanent_policy_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    edge_id = uuid.uuid4()
    connection.execute(
        sa.text("INSERT INTO organization(id,legal_name,short_code) VALUES (:id,:name,:code)"),
        {
            "id": other_org_id,
            "name": f"R27 cross organization {other_org_id}",
            "code": f"R27-X-{other_org_id.hex[:10]}",
        },
    )
    connection.execute(
        sa.text(
            "INSERT INTO app_user(id,org_id,keycloak_subject,display_name) "
            "VALUES (:id,:org,:subject,'R27 cross organization actor')"
        ),
        {"id": user_id, "org": other_org_id, "subject": f"r27-cross-{user_id}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO framework(id,org_id,code,name,is_active,is_authorable) "
            "VALUES (:id,:org,:code,'R27 cross framework',true,false)"
        ),
        {"id": framework_id, "org": other_org_id, "code": f"r27-x:{framework_id}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO retention_policy"
            "(id,org_id,name,duration,worm_lock_period,disposition_action) "
            "VALUES (:id,:org,:name,'PERMANENT','PERMANENT','RETAIN_PERMANENT')"
        ),
        {"id": policy_id, "org": other_org_id, "name": f"r27-x-{policy_id}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO retention_policy"
            "(id,org_id,name,duration,worm_lock_period,disposition_action) "
            "VALUES (:id,:org,:name,'PERMANENT','PERMANENT','RETAIN_PERMANENT')"
        ),
        {
            "id": target_permanent_policy_id,
            "org": source.actors.org_id,
            "name": f"r27-target-permanent-{target_permanent_policy_id}",
        },
    )
    parent_kind = "DOCUMENT" if kind.endswith("DOCUMENT") else "RECORD"
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,owner_user_id,
                 current_state,is_singleton,classification,
                 acknowledgement_required,created_by)
            VALUES
                (:id,:org,:framework,CAST(:kind AS document_kind),:identifier,
                 'R27 cross owner',:user,'Draft',false,'Internal',false,:user)
            """
        ),
        {
            "id": parent_id,
            "org": other_org_id,
            "framework": framework_id,
            "kind": parent_kind,
            "identifier": f"R27-X-{parent_id}",
            "user": user_id,
        },
    )
    if kind.endswith("DOCUMENT"):
        edge_org_id = source.actors.org_id if kind == "TENANT_ORPHAN_DOCUMENT" else other_org_id
        edge_policy_id = (
            target_permanent_policy_id if kind == "TENANT_ORPHAN_DOCUMENT" else policy_id
        )
        edge_user_id = source.actors.canceller_id if kind == "TENANT_ORPHAN_DOCUMENT" else user_id
        connection.execute(
            sa.text(
                """
                INSERT INTO document_version
                    (id,org_id,document_id,version_seq,revision_label,change_significance,
                     change_reason,version_state,retention_authority_kind,
                     retention_policy_id,retention_basis_date,source_blob_sha256,
                     metadata_snapshot,imported,author_user_id,created_by)
                VALUES
                    (:id,:org,:document,1,'A','MINOR','cross organization corruption',
                     'Draft','POLICY',:policy,current_date,:sha,'{}'::jsonb,false,:user,:user)
                """
            ),
            {
                "id": edge_id,
                "org": edge_org_id,
                "document": parent_id,
                "policy": edge_policy_id,
                "sha": target.sha256,
                "user": edge_user_id,
            },
        )
        return edge_id

    record_policy_id = (
        target_permanent_policy_id
        if kind == "TENANT_ORPHAN_EVIDENCE_PERMANENT"
        else target_policy_id
        if kind.startswith("TENANT_ORPHAN_EVIDENCE")
        else policy_id
    )
    record_user_id = (
        source.actors.canceller_id if kind.startswith("TENANT_ORPHAN_EVIDENCE") else user_id
    )
    connection.execute(
        sa.text(
            "INSERT INTO record"
            "(id,org_id,record_type,captured_by,content_hash_version,retention_policy_id,"
            "disposition_state,legal_hold) "
            "VALUES (:id,:org,'EVIDENCE',:user,2,:policy,'ACTIVE',:legal_hold)"
        ),
        {
            "id": parent_id,
            "org": other_org_id,
            "user": record_user_id,
            "policy": record_policy_id,
            "legal_hold": kind != "TENANT_ORPHAN_EVIDENCE_PERMANENT",
        },
    )
    edge_org_id = (
        source.actors.org_id if kind.startswith("TENANT_ORPHAN_EVIDENCE") else other_org_id
    )
    connection.execute(
        sa.text(
            "INSERT INTO evidence_blob"
            "(id,org_id,record_id,blob_sha256,is_original,created_by) "
            "VALUES (:id,:org,:record,:sha,true,:user)"
        ),
        {
            "id": edge_id,
            "org": edge_org_id,
            "record": parent_id,
            "sha": target.sha256,
            "user": record_user_id,
        },
    )
    return edge_id


@pytest.mark.parametrize("transition", ("REQUEST", "APPROVE", "CANCEL"))
def test_r27_prefinalization_transitions_refuse_expired_manifest_atomically(
    database_authority_dsns: dict[str, str], transition: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        key_id, _ = _install_authorizer_key(database_authority_dsns)
        actions = ("REQUEST", "APPROVE", "CANCEL")
        authority = _seed_request_authority(owner, actors, key_id, actions=actions)

        if transition != "REQUEST":
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                    {"id": authority.attestations["REQUEST"]},
                ).scalar_one()

        with owner.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE r27_manifest SET issued_at=clock_timestamp()-interval '2 hours',"
                    "expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                ),
                {"id": authority.manifest_id},
            )
        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(r) FROM r27_request r WHERE id=:request_id),
              (SELECT consumed_at FROM r27_action_challenge WHERE id=:challenge_id),
              (SELECT count(*) FROM audit_event)
            """
        )
        with owner.connect() as connection:
            before = connection.execute(
                snapshot_sql,
                {
                    "request_id": authority.request_id,
                    "challenge_id": authority.challenges[transition],
                },
            ).one()

        function_name = {
            "REQUEST": "easysynq_accept_r27_request",
            "APPROVE": "easysynq_accept_r27_approval",
            "CANCEL": "easysynq_cancel_r27_request",
        }[transition]
        with pytest.raises(sa.exc.DBAPIError, match=r"r27_.*refused"):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text(f"SELECT {function_name}(:id,clock_timestamp())"),
                    {"id": authority.attestations[transition]},
                ).scalar_one()

        with owner.connect() as connection:
            assert (
                connection.execute(
                    snapshot_sql,
                    {
                        "request_id": authority.request_id,
                        "challenge_id": authority.challenges[transition],
                    },
                ).one()
                == before
            )
    finally:
        owner.dispose()
        authorizer.dispose()


_ACCEPTANCE_RELATION_MUTATIONS = (
    "subject_mismatch",
    "claims_iss_mismatch",
    "claims_sub_mismatch",
    "claims_sid_mismatch",
    "claims_jti_mismatch",
    "claims_azp_mismatch",
    "claims_acr_mismatch",
    "audience_mismatch",
    "authorized_party_mismatch",
    "acr_mismatch",
    "auth_time_after_acceptance",
    "attestation_lifetime_too_long",
    "challenge_lifetime_too_long",
    "actor_org_mismatch",
    "record_org_mismatch",
    "manifest_schema_version",
    "manifest_expected_state",
)


@pytest.mark.parametrize("action", ("REQUEST", "APPROVE", "CANCEL"))
@pytest.mark.parametrize("mutation", _ACCEPTANCE_RELATION_MUTATIONS)
def test_r27_acceptance_revalidates_identity_and_manifest_without_partial_writes(
    database_authority_dsns: dict[str, str], action: str, mutation: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        key_id, _ = _install_authorizer_key(database_authority_dsns)
        authority = _seed_request_authority(
            owner,
            actors,
            key_id,
            actions=("REQUEST", "APPROVE", "CANCEL"),
        )
        if action != "REQUEST":
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                    {"id": authority.attestations["REQUEST"]},
                )

        actor_id = {
            "REQUEST": actors.requester_id,
            "APPROVE": actors.approver_id,
            "CANCEL": actors.canceller_id,
        }[action]
        with owner.begin() as connection:
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            if mutation == "subject_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET subject='wrong-subject' WHERE id=:id"),
                    {"id": authority.attestations[action]},
                )
            elif mutation.startswith("claims_"):
                claim_key = mutation.removeprefix("claims_").removesuffix("_mismatch")
                connection.execute(
                    sa.text(
                        "UPDATE r27_action_challenge SET accepted_claims="
                        "jsonb_set(accepted_claims,ARRAY[:key],to_jsonb(CAST(:value AS text))) "
                        "WHERE id=:id"
                    ),
                    {
                        "key": claim_key,
                        "value": f"mismatch-{uuid.uuid4()}",
                        "id": authority.challenges[action],
                    },
                )
            elif mutation == "audience_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET audience='[\"wrong-client\"]'::jsonb "
                        "WHERE id=:id"
                    ),
                    {"id": authority.attestations[action]},
                )
            elif mutation == "authorized_party_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET authorized_party='wrong-client' WHERE id=:id"
                    ),
                    {"id": authority.attestations[action]},
                )
            elif mutation == "acr_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET acr='wrong-acr' WHERE id=:id"),
                    {"id": authority.attestations[action]},
                )
            elif mutation == "auth_time_after_acceptance":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET auth_time=clock_timestamp()+interval '1 hour' "
                        "WHERE id=:id"
                    ),
                    {"id": authority.attestations[action]},
                )
            elif mutation == "attestation_lifetime_too_long":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET "
                        "issued_at=clock_timestamp()-interval '1 second',"
                        "expires_at=clock_timestamp()+interval '121 seconds' WHERE id=:id"
                    ),
                    {"id": authority.attestations[action]},
                )
            elif mutation == "challenge_lifetime_too_long":
                connection.execute(
                    sa.text(
                        "UPDATE r27_action_challenge SET "
                        "created_at=clock_timestamp()-interval '1 second',"
                        "expires_at=clock_timestamp()+interval '121 seconds' WHERE id=:id"
                    ),
                    {"id": authority.challenges[action]},
                )
            elif mutation in {"actor_org_mismatch", "record_org_mismatch"}:
                other_org_id = uuid.uuid4()
                connection.execute(
                    sa.text(
                        "INSERT INTO organization(id,legal_name,short_code) "
                        "VALUES (:id,:name,:code)"
                    ),
                    {
                        "id": other_org_id,
                        "name": f"R27 acceptance mismatch {other_org_id}",
                        "code": f"R27-AM-{other_org_id.hex[:8]}",
                    },
                )
                row_id = actor_id if mutation == "actor_org_mismatch" else actors.record_id
                statement = (
                    sa.text("UPDATE app_user SET org_id=:org WHERE id=:id")
                    if mutation == "actor_org_mismatch"
                    else sa.text("UPDATE record SET org_id=:org WHERE id=:id")
                )
                connection.execute(
                    statement,
                    {"org": other_org_id, "id": row_id},
                )
            elif mutation == "manifest_schema_version":
                connection.execute(
                    sa.text("UPDATE r27_manifest SET schema_version=2 WHERE id=:id"),
                    {"id": authority.manifest_id},
                )
            elif mutation == "manifest_expected_state":
                connection.execute(
                    sa.text(
                        "UPDATE r27_manifest SET expected_state='READY_FOR_FINALIZATION' "
                        "WHERE id=:id"
                    ),
                    {"id": authority.manifest_id},
                )
            else:  # pragma: no cover - mutation table is closed above
                raise AssertionError(mutation)

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT to_jsonb(challenge) FROM r27_action_challenge challenge WHERE id=:challenge),
              (SELECT count(*) FROM audit_event)
            """
        )
        parameters = {
            "request": authority.request_id,
            "challenge": authority.challenges[action],
        }
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()

        function_name = {
            "REQUEST": "easysynq_accept_r27_request",
            "APPROVE": "easysynq_accept_r27_approval",
            "CANCEL": "easysynq_cancel_r27_request",
        }[action]
        expected_error = {
            "REQUEST": "r27_request_authority_refused",
            "APPROVE": "r27_approval_authority_refused",
            "CANCEL": "r27_cancel_refused",
        }[action]
        with pytest.raises(sa.exc.DBAPIError, match=expected_error):
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text(f"SELECT {function_name}(:id,clock_timestamp())"),
                    {"id": authority.attestations[action]},
                )
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner.dispose()
        authorizer.dispose()


_FINALIZATION_CHAIN_MUTATIONS = (
    "manifest_schema_version",
    "manifest_expected_state",
    "witness_schema_version",
    "record_org_mismatch",
    "witness_manifest_digest",
    "witness_excluded_digest",
    "witness_chronology",
    "witness_before_second_approval",
    "witness_consumed_by_other_execution",
    "manifest_expired",
    "recovery_key_not_yet_valid",
    "recovery_key_retired_before_witness",
    "request_permission_revoked",
    "request_subject_mismatch",
    "request_claims_iss_mismatch",
    "request_claims_sub_mismatch",
    "request_claims_sid_mismatch",
    "request_claims_jti_mismatch",
    "request_claims_azp_mismatch",
    "request_claims_acr_mismatch",
    "request_audience_mismatch",
    "request_authorized_party_mismatch",
    "request_acr_mismatch",
    "request_auth_time_after_acceptance",
    "request_attestation_lifetime_too_long",
    "request_challenge_lifetime_too_long",
    "request_audit_on_behalf_of",
    "request_audit_after_acceptance",
    "request_attestation_expired",
    "request_challenge_unconsumed",
    "request_challenge_record_mismatch",
    "request_challenge_manifest_mismatch",
    "request_challenge_action_mismatch",
    "request_challenge_request_mismatch",
    "request_attestation_request_mismatch",
    "request_attestation_challenge_mismatch",
    "request_issuer_mismatch",
    "request_jti_mismatch",
    "request_action_mismatch",
    "request_actor_mismatch",
    "stored_requester_mismatch",
    "request_audit_actor_mismatch",
    "request_authorizer_key_not_active",
    "request_authorizer_key_retired",
    "request_authorizer_key_revoked",
    "approval_permission_revoked",
    "approval_subject_mismatch",
    "approval_claims_iss_mismatch",
    "approval_claims_sub_mismatch",
    "approval_claims_sid_mismatch",
    "approval_claims_jti_mismatch",
    "approval_claims_azp_mismatch",
    "approval_claims_acr_mismatch",
    "approval_audience_mismatch",
    "approval_authorized_party_mismatch",
    "approval_acr_mismatch",
    "approval_auth_time_after_acceptance",
    "approval_attestation_lifetime_too_long",
    "approval_challenge_lifetime_too_long",
    "approval_audit_on_behalf_of",
    "approval_audit_after_acceptance",
    "approval_attestation_expired",
    "approval_challenge_unconsumed",
    "approval_challenge_record_mismatch",
    "approval_challenge_manifest_mismatch",
    "approval_challenge_action_mismatch",
    "approval_challenge_request_mismatch",
    "approval_attestation_request_mismatch",
    "approval_attestation_challenge_mismatch",
    "approval_issuer_mismatch",
    "approval_jti_mismatch",
    "approval_action_mismatch",
    "approval_actor_mismatch",
    "approval_reuses_requester",
    "stored_approver_mismatch",
    "approval_audit_actor_mismatch",
    "approval_authorizer_key_not_active",
    "approval_authorizer_key_retired",
    "approval_authorizer_key_revoked",
)


@pytest.mark.parametrize("mutation", _FINALIZATION_CHAIN_MUTATIONS)
def test_finalization_claim_revalidates_complete_accepted_authority_chain(
    database_authority_dsns: dict[str, str], mutation: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    ready: ReadyAuthority | None = None
    try:
        ready = _make_ready_authority(database_authority_dsns, owner)
        request_attestation = ready.request.attestations["REQUEST"]
        approval_attestation = ready.request.attestations["APPROVE"]
        request_challenge = ready.request.challenges["REQUEST"]
        approval_challenge = ready.request.challenges["APPROVE"]
        other_record_id: uuid.UUID | None = None
        other_execution_id: uuid.UUID | None = None
        other_request: RequestAuthority | None = None
        if mutation in {
            "request_challenge_record_mismatch",
            "approval_challenge_record_mismatch",
            "witness_consumed_by_other_execution",
            "request_challenge_request_mismatch",
            "request_attestation_request_mismatch",
            "request_attestation_challenge_mismatch",
            "approval_challenge_request_mismatch",
            "approval_attestation_request_mismatch",
            "approval_attestation_challenge_mismatch",
        }:
            other_actors = _seed_actors(owner)
            other_record_id = other_actors.record_id
            other_request = _seed_request_authority(
                owner,
                other_actors,
                ready.request.authorizer_key_ids["REQUEST"],
                actions=("REQUEST", "APPROVE"),
            )
            if mutation == "witness_consumed_by_other_execution":
                other_execution_id = uuid.uuid4()
                with owner.begin() as connection:
                    connection.execute(
                        sa.text(
                            "INSERT INTO r27_execution "
                            "(id,request_id,execution_id,state,claimed_at,attempt_count,"
                            "updated_at) "
                            "VALUES (:id,:request,:public_id,'CLAIMED',clock_timestamp(),1,"
                            "clock_timestamp())"
                        ),
                        {
                            "id": other_execution_id,
                            "request": other_request.request_id,
                            "public_id": uuid.uuid4(),
                        },
                    )
        with owner.begin() as connection:
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            if mutation == "manifest_schema_version":
                connection.execute(
                    sa.text("UPDATE r27_manifest SET schema_version=2 WHERE id=:id"),
                    {"id": ready.request.manifest_id},
                )
            elif mutation == "manifest_expected_state":
                connection.execute(
                    sa.text(
                        "UPDATE r27_manifest SET expected_state='READY_FOR_FINALIZATION' "
                        "WHERE id=:id"
                    ),
                    {"id": ready.request.manifest_id},
                )
            elif mutation == "witness_schema_version":
                connection.execute(
                    sa.text("UPDATE recovery_generation_witness SET schema_version=2 WHERE id=:id"),
                    {"id": ready.witness_id},
                )
            elif mutation == "record_org_mismatch":
                other_org_id = uuid.uuid4()
                connection.execute(
                    sa.text(
                        "INSERT INTO organization(id,legal_name,short_code) "
                        "VALUES (:id,:name,:code)"
                    ),
                    {
                        "id": other_org_id,
                        "name": f"R27 record mismatch {other_org_id}",
                        "code": f"R27-RM-{other_org_id.hex[:8]}",
                    },
                )
                connection.execute(
                    sa.text("UPDATE record SET org_id=:org WHERE id=:id"),
                    {"org": other_org_id, "id": ready.actors.record_id},
                )
            elif mutation == "witness_manifest_digest":
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_witness SET manifest_sha256=:digest "
                        "WHERE id=:id"
                    ),
                    {"digest": _hex_digest(), "id": ready.witness_id},
                )
            elif mutation == "witness_excluded_digest":
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_witness SET excluded_set_sha256=:digest "
                        "WHERE id=:id"
                    ),
                    {"digest": _hex_digest(), "id": ready.witness_id},
                )
            elif mutation == "witness_chronology":
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_verifier_key key "
                        "SET not_before=request.approved_at "
                        "FROM r27_request request WHERE key.id=:key AND request.id=:request"
                    ),
                    {
                        "key": ready.recovery_key_db_id,
                        "request": ready.request.request_id,
                    },
                )
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_witness witness "
                        "SET issued_at=request.approved_at+interval '2 milliseconds',"
                        "verified_at=request.approved_at+interval '1 millisecond' "
                        "FROM r27_request request "
                        "WHERE witness.id=:witness AND request.id=:request"
                    ),
                    {
                        "witness": ready.witness_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "witness_before_second_approval":
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_verifier_key key "
                        "SET not_before=request.approved_at-interval '3 hours' "
                        "FROM r27_request request WHERE key.id=:key AND request.id=:request"
                    ),
                    {
                        "key": ready.recovery_key_db_id,
                        "request": ready.request.request_id,
                    },
                )
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_witness witness "
                        "SET issued_at=request.approved_at-interval '2 hours',"
                        "verified_at=request.approved_at-interval '1 hour' "
                        "FROM r27_request request "
                        "WHERE witness.id=:witness AND request.id=:request"
                    ),
                    {
                        "witness": ready.witness_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "witness_consumed_by_other_execution":
                assert other_execution_id is not None
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_witness SET consumed_execution_id=:id "
                        "WHERE id=:witness"
                    ),
                    {"id": other_execution_id, "witness": ready.witness_id},
                )
            elif mutation == "manifest_expired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_manifest SET issued_at=clock_timestamp()-interval '2 hours',"
                        "expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": ready.request.manifest_id},
                )
            elif mutation == "recovery_key_not_yet_valid":
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_verifier_key key "
                        "SET not_before=witness.issued_at+interval '1 hour' "
                        "FROM recovery_generation_witness witness "
                        "WHERE key.id=:key AND witness.id=:witness"
                    ),
                    {"key": ready.recovery_key_db_id, "witness": ready.witness_id},
                )
            elif mutation == "recovery_key_retired_before_witness":
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_verifier_key key "
                        "SET not_before=witness.issued_at-interval '2 hours',"
                        "retired_at=witness.issued_at-interval '1 hour' "
                        "FROM recovery_generation_witness witness "
                        "WHERE key.id=:key AND witness.id=:witness"
                    ),
                    {"key": ready.recovery_key_db_id, "witness": ready.witness_id},
                )
            elif mutation == "request_permission_revoked":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET permission_granted=false WHERE id=:id"),
                    {"id": request_attestation},
                )
            elif mutation.startswith("request_claims_"):
                claim_key = mutation.removeprefix("request_claims_").removesuffix("_mismatch")
                connection.execute(
                    sa.text(
                        "UPDATE r27_action_challenge SET accepted_claims="
                        "jsonb_set(accepted_claims,ARRAY[:key],to_jsonb(CAST(:value AS text))) "
                        "WHERE id=:id"
                    ),
                    {
                        "key": claim_key,
                        "value": f"mismatch-{uuid.uuid4()}",
                        "id": request_challenge,
                    },
                )
            elif mutation == "request_subject_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET subject='wrong-subject' WHERE id=:id"),
                    {"id": request_attestation},
                )
            elif mutation == "request_audience_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET audience='[\"wrong-client\"]'::jsonb "
                        "WHERE id=:id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_authorized_party_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET authorized_party='wrong-client' WHERE id=:id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_acr_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET acr='wrong-acr' WHERE id=:id"),
                    {"id": request_attestation},
                )
            elif mutation == "request_auth_time_after_acceptance":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET auth_time=issued_at+interval '31 seconds' "
                        "WHERE id=:id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_attestation_lifetime_too_long":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation attestation SET "
                        "issued_at=request.requested_at-interval '1 second',"
                        "expires_at=request.requested_at+interval '121 seconds' "
                        "FROM r27_request request "
                        "WHERE attestation.id=:id AND request.id=attestation.request_id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_challenge_lifetime_too_long":
                connection.execute(
                    sa.text(
                        "UPDATE r27_action_challenge challenge SET "
                        "created_at=request.requested_at-interval '1 second',"
                        "expires_at=request.requested_at+interval '121 seconds' "
                        "FROM r27_request request "
                        "WHERE challenge.id=:id AND request.id=challenge.request_id"
                    ),
                    {"id": request_challenge},
                )
            elif mutation in {"request_audit_on_behalf_of", "request_audit_after_acceptance"}:
                statement = (
                    sa.text(
                        "UPDATE audit_event event SET on_behalf_of=:actor "
                        "FROM r27_request request WHERE request.id=:request "
                        "AND event.id=request.requester_audit_event_id"
                    )
                    if mutation == "request_audit_on_behalf_of"
                    else sa.text(
                        "UPDATE audit_event event SET "
                        "occurred_at=request.requested_at+interval '1 second' "
                        "FROM r27_request request WHERE request.id=:request "
                        "AND event.id=request.requester_audit_event_id"
                    )
                )
                connection.execute(
                    statement,
                    {
                        "actor": ready.actors.canceller_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "request_attestation_expired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key "
                        "SET active_at=clock_timestamp()-interval '3 hours' "
                        "FROM r27_attestation attestation "
                        "WHERE key.id=attestation.authorizer_key_id AND attestation.id=:id"
                    ),
                    {"id": request_attestation},
                )
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET "
                        "issued_at=clock_timestamp()-interval '2 hours',"
                        "expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_challenge_unconsumed":
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET consumed_at=NULL WHERE id=:id"),
                    {"id": request_challenge},
                )
            elif mutation == "request_challenge_record_mismatch":
                assert other_record_id is not None
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET record_id=:record WHERE id=:id"),
                    {"record": other_record_id, "id": request_challenge},
                )
            elif mutation == "request_challenge_manifest_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET manifest_sha256=:digest WHERE id=:id"),
                    {"digest": _hex_digest(), "id": request_challenge},
                )
            elif mutation == "request_challenge_action_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET action='CANCEL' WHERE id=:id"),
                    {"id": request_challenge},
                )
            elif mutation == "request_challenge_request_mismatch":
                assert other_request is not None
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET request_id=:request WHERE id=:id"),
                    {"request": other_request.request_id, "id": request_challenge},
                )
            elif mutation == "request_attestation_request_mismatch":
                assert other_request is not None
                connection.execute(
                    sa.text("DELETE FROM r27_attestation WHERE id=:id"),
                    {"id": other_request.attestations["REQUEST"]},
                )
                connection.execute(
                    sa.text("UPDATE r27_attestation SET request_id=:request WHERE id=:id"),
                    {"request": other_request.request_id, "id": request_attestation},
                )
            elif mutation == "request_attestation_challenge_mismatch":
                assert other_request is not None
                connection.execute(
                    sa.text("DELETE FROM r27_attestation WHERE id=:id"),
                    {"id": other_request.attestations["REQUEST"]},
                )
                connection.execute(
                    sa.text("UPDATE r27_attestation SET challenge_id=:challenge WHERE id=:id"),
                    {
                        "challenge": other_request.challenges["REQUEST"],
                        "id": request_attestation,
                    },
                )
            elif mutation == "request_issuer_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET issuer='https://other-issuer.test' WHERE id=:id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_jti_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET token_jti=:jti WHERE id=:id"),
                    {"jti": f"mismatch-{uuid.uuid4()}", "id": request_attestation},
                )
            elif mutation == "request_action_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET action='CANCEL' WHERE id=:attestation"),
                    {"attestation": request_attestation},
                )
            elif mutation == "request_actor_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET app_user_id=:user WHERE id=:attestation"),
                    {
                        "user": ready.actors.canceller_id,
                        "attestation": request_attestation,
                    },
                )
            elif mutation == "stored_requester_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_request SET requester_user_id=:user WHERE id=:request"),
                    {
                        "user": ready.actors.canceller_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "request_audit_actor_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE audit_event event SET actor_id=:actor "
                        "FROM r27_request request "
                        "WHERE request.id=:request "
                        "AND event.id=request.requester_audit_event_id"
                    ),
                    {
                        "actor": ready.actors.canceller_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "request_authorizer_key_not_active":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key "
                        "SET active_at=attestation.issued_at+interval '1 hour' "
                        "FROM r27_attestation attestation "
                        "WHERE key.id=attestation.authorizer_key_id AND attestation.id=:id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_authorizer_key_retired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key "
                        "SET active_at=attestation.issued_at-interval '2 hours',"
                        "retired_at=attestation.issued_at-interval '1 hour' "
                        "FROM r27_attestation attestation "
                        "WHERE key.id=attestation.authorizer_key_id AND attestation.id=:id"
                    ),
                    {"id": request_attestation},
                )
            elif mutation == "request_authorizer_key_revoked":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key SET revoked_at=clock_timestamp() "
                        "FROM r27_attestation attestation "
                        "WHERE attestation.id=:attestation "
                        "AND key.id=attestation.authorizer_key_id"
                    ),
                    {"attestation": request_attestation},
                )
            elif mutation == "approval_permission_revoked":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET permission_granted=false WHERE id=:id"),
                    {"id": approval_attestation},
                )
            elif mutation.startswith("approval_claims_"):
                claim_key = mutation.removeprefix("approval_claims_").removesuffix("_mismatch")
                connection.execute(
                    sa.text(
                        "UPDATE r27_action_challenge SET accepted_claims="
                        "jsonb_set(accepted_claims,ARRAY[:key],to_jsonb(CAST(:value AS text))) "
                        "WHERE id=:id"
                    ),
                    {
                        "key": claim_key,
                        "value": f"mismatch-{uuid.uuid4()}",
                        "id": approval_challenge,
                    },
                )
            elif mutation == "approval_subject_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET subject='wrong-subject' WHERE id=:id"),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_audience_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET audience='[\"wrong-client\"]'::jsonb "
                        "WHERE id=:id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_authorized_party_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET authorized_party='wrong-client' WHERE id=:id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_acr_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET acr='wrong-acr' WHERE id=:id"),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_auth_time_after_acceptance":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET auth_time=issued_at+interval '31 seconds' "
                        "WHERE id=:id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_attestation_lifetime_too_long":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation attestation SET "
                        "issued_at=request.approved_at-interval '1 second',"
                        "expires_at=request.approved_at+interval '121 seconds' "
                        "FROM r27_request request "
                        "WHERE attestation.id=:id AND request.id=attestation.request_id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_challenge_lifetime_too_long":
                connection.execute(
                    sa.text(
                        "UPDATE r27_action_challenge challenge SET "
                        "created_at=request.approved_at-interval '1 second',"
                        "expires_at=request.approved_at+interval '121 seconds' "
                        "FROM r27_request request "
                        "WHERE challenge.id=:id AND request.id=challenge.request_id"
                    ),
                    {"id": approval_challenge},
                )
            elif mutation in {"approval_audit_on_behalf_of", "approval_audit_after_acceptance"}:
                statement = (
                    sa.text(
                        "UPDATE audit_event event SET on_behalf_of=:actor "
                        "FROM r27_request request WHERE request.id=:request "
                        "AND event.id=request.approver_audit_event_id"
                    )
                    if mutation == "approval_audit_on_behalf_of"
                    else sa.text(
                        "UPDATE audit_event event SET "
                        "occurred_at=request.approved_at+interval '1 second' "
                        "FROM r27_request request WHERE request.id=:request "
                        "AND event.id=request.approver_audit_event_id"
                    )
                )
                connection.execute(
                    statement,
                    {
                        "actor": ready.actors.canceller_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "approval_attestation_expired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key "
                        "SET active_at=clock_timestamp()-interval '3 hours' "
                        "FROM r27_attestation attestation "
                        "WHERE key.id=attestation.authorizer_key_id AND attestation.id=:id"
                    ),
                    {"id": approval_attestation},
                )
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET "
                        "issued_at=clock_timestamp()-interval '2 hours',"
                        "expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_challenge_unconsumed":
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET consumed_at=NULL WHERE id=:id"),
                    {"id": approval_challenge},
                )
            elif mutation == "approval_challenge_record_mismatch":
                assert other_record_id is not None
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET record_id=:record WHERE id=:id"),
                    {"record": other_record_id, "id": approval_challenge},
                )
            elif mutation == "approval_challenge_manifest_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET manifest_sha256=:digest WHERE id=:id"),
                    {"digest": _hex_digest(), "id": approval_challenge},
                )
            elif mutation == "approval_challenge_action_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET action='REQUEST' WHERE id=:id"),
                    {"id": approval_challenge},
                )
            elif mutation == "approval_challenge_request_mismatch":
                assert other_request is not None
                connection.execute(
                    sa.text("UPDATE r27_action_challenge SET request_id=:request WHERE id=:id"),
                    {"request": other_request.request_id, "id": approval_challenge},
                )
            elif mutation == "approval_attestation_request_mismatch":
                assert other_request is not None
                connection.execute(
                    sa.text("DELETE FROM r27_attestation WHERE id=:id"),
                    {"id": other_request.attestations["APPROVE"]},
                )
                connection.execute(
                    sa.text("UPDATE r27_attestation SET request_id=:request WHERE id=:id"),
                    {"request": other_request.request_id, "id": approval_attestation},
                )
            elif mutation == "approval_attestation_challenge_mismatch":
                assert other_request is not None
                connection.execute(
                    sa.text("DELETE FROM r27_attestation WHERE id=:id"),
                    {"id": other_request.attestations["APPROVE"]},
                )
                connection.execute(
                    sa.text("UPDATE r27_attestation SET challenge_id=:challenge WHERE id=:id"),
                    {
                        "challenge": other_request.challenges["APPROVE"],
                        "id": approval_attestation,
                    },
                )
            elif mutation == "approval_issuer_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET issuer='https://other-issuer.test' WHERE id=:id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_jti_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET token_jti=:jti WHERE id=:id"),
                    {"jti": f"mismatch-{uuid.uuid4()}", "id": approval_attestation},
                )
            elif mutation == "approval_action_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET action='CANCEL' WHERE id=:attestation"),
                    {"attestation": approval_attestation},
                )
            elif mutation == "approval_actor_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET app_user_id=:user WHERE id=:attestation"),
                    {
                        "user": ready.actors.canceller_id,
                        "attestation": approval_attestation,
                    },
                )
            elif mutation == "approval_reuses_requester":
                connection.execute(
                    sa.text("UPDATE r27_attestation SET app_user_id=:user WHERE id=:attestation"),
                    {
                        "user": ready.actors.requester_id,
                        "attestation": approval_attestation,
                    },
                )
            elif mutation == "stored_approver_mismatch":
                connection.execute(
                    sa.text("UPDATE r27_request SET approver_user_id=:user WHERE id=:request"),
                    {
                        "user": ready.actors.canceller_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "approval_audit_actor_mismatch":
                connection.execute(
                    sa.text(
                        "UPDATE audit_event event SET actor_id=:actor "
                        "FROM r27_request request "
                        "WHERE request.id=:request "
                        "AND event.id=request.approver_audit_event_id"
                    ),
                    {
                        "actor": ready.actors.canceller_id,
                        "request": ready.request.request_id,
                    },
                )
            elif mutation == "approval_authorizer_key_not_active":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key "
                        "SET active_at=attestation.issued_at+interval '1 hour' "
                        "FROM r27_attestation attestation "
                        "WHERE key.id=attestation.authorizer_key_id AND attestation.id=:id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_authorizer_key_retired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key "
                        "SET active_at=attestation.issued_at-interval '2 hours',"
                        "retired_at=attestation.issued_at-interval '1 hour' "
                        "FROM r27_attestation attestation "
                        "WHERE key.id=attestation.authorizer_key_id AND attestation.id=:id"
                    ),
                    {"id": approval_attestation},
                )
            elif mutation == "approval_authorizer_key_revoked":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key key SET revoked_at=clock_timestamp() "
                        "FROM r27_attestation attestation "
                        "WHERE attestation.id=:attestation "
                        "AND key.id=attestation.authorizer_key_id"
                    ),
                    {"attestation": approval_attestation},
                )
            else:  # pragma: no cover - parameter table is closed above
                raise AssertionError(mutation)

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT COALESCE(jsonb_agg(to_jsonb(execution) ORDER BY execution.id),'[]')
                 FROM r27_execution execution WHERE request_id=:request),
              (SELECT to_jsonb(witness) FROM recovery_generation_witness witness
                 WHERE id=:witness),
              (SELECT to_jsonb(event) FROM audit_event event
                 JOIN r27_request request ON request.requester_audit_event_id=event.id
                 WHERE request.id=:request),
              (SELECT to_jsonb(event) FROM audit_event event
                 JOIN r27_request request ON request.approver_audit_event_id=event.id
                 WHERE request.id=:request),
              (SELECT count(*) FROM audit_event)
            """
        )
        with owner.connect() as connection:
            before = connection.execute(
                snapshot_sql,
                {"request": ready.request.request_id, "witness": ready.witness_id},
            ).one()
        with maintenance.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(100,clock_timestamp())")
            ).all()
        assert ready.request.request_id not in {row.request_id for row in claimed}
        with owner.connect() as connection:
            assert (
                connection.execute(
                    snapshot_sql,
                    {"request": ready.request.request_id, "witness": ready.witness_id},
                ).one()
                == before
            )
    finally:
        if ready is not None:
            with owner.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text(
                        "UPDATE r27_request SET state='STALE',error_code='TEST_CLEANUP',"
                        "error_detail=NULL,stale_at=COALESCE(stale_at,clock_timestamp()),"
                        "updated_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": ready.request.request_id},
                )
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize("transition", ("REQUEST", "APPROVE", "CANCEL"))
def test_r27_acceptance_uses_one_database_timestamp(
    database_authority_dsns: dict[str, str], transition: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    authorizer = _engine(database_authority_dsns, "easysynq_r27_authorizer")
    try:
        actors = _seed_actors(owner)
        key_id, _ = _install_authorizer_key(database_authority_dsns)
        authority = _seed_request_authority(
            owner,
            actors,
            key_id,
            actions=("REQUEST", "APPROVE", "CANCEL"),
        )
        if transition != "REQUEST":
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text("SELECT easysynq_accept_r27_request(:id,clock_timestamp())"),
                    {"id": authority.attestations["REQUEST"]},
                )

        function_name = {
            "REQUEST": "easysynq_accept_r27_request",
            "APPROVE": "easysynq_accept_r27_approval",
            "CANCEL": "easysynq_cancel_r27_request",
        }[transition]
        with authorizer.begin() as connection:
            audit_id = connection.execute(
                sa.text(f"SELECT {function_name}(:id,clock_timestamp())"),
                {"id": authority.attestations[transition]},
            ).scalar_one()

        with owner.connect() as connection:
            timestamps = connection.execute(
                sa.text(
                    """
                    SELECT CASE :transition
                             WHEN 'REQUEST' THEN request.requested_at
                             WHEN 'APPROVE' THEN request.approved_at
                             ELSE request.cancelled_at
                           END,
                           request.updated_at,
                           challenge.consumed_at,audit.occurred_at
                    FROM r27_request request
                    JOIN r27_action_challenge challenge ON challenge.id=:challenge
                    JOIN audit_event audit ON audit.id=:audit
                    WHERE request.id=:request
                    """
                ),
                {
                    "challenge": authority.challenges[transition],
                    "audit": audit_id,
                    "request": authority.request_id,
                    "transition": transition,
                },
            ).one()
            assert timestamps[0] == timestamps[1] == timestamps[2] == timestamps[3]
    finally:
        owner.dispose()
        authorizer.dispose()


def test_finalization_accepts_expired_action_artifacts_valid_at_acceptance(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        ready = _make_ready_authority(database_authority_dsns, owner)
        _age_accepted_action_artifacts(owner, ready.request)

        with maintenance.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(100,clock_timestamp())")
            ).all()
        assert ready.request.request_id in {row.request_id for row in claimed}
    finally:
        owner.dispose()
        maintenance.dispose()


def test_finalization_allows_shared_key_retired_after_both_issuances(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        ready = _make_ready_authority(
            database_authority_dsns,
            owner,
            shared_authorizer_key=True,
        )
        shared_key_id = ready.request.authorizer_key_ids["REQUEST"]
        assert shared_key_id == ready.request.authorizer_key_ids["APPROVE"]
        with owner.begin() as connection:
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                sa.text(
                    "UPDATE r27_authorizer_key key "
                    "SET retired_at=(SELECT max(attestation.issued_at)+interval '1 second' "
                    "FROM r27_attestation attestation WHERE attestation.request_id=:request) "
                    "WHERE key.id=:key"
                ),
                {"request": ready.request.request_id, "key": shared_key_id},
            )
        with maintenance.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(100,clock_timestamp())")
            ).all()
        assert ready.request.request_id in {row.request_id for row in claimed}
    finally:
        owner.dispose()
        maintenance.dispose()


def test_transient_retry_refuses_fresh_unconsumed_witness_without_partial_writes(
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
            connection.execute(
                sa.text(
                    "SELECT easysynq_fail_r27_execution(:id,'TRANSIENT','retry',clock_timestamp())"
                ),
                {"id": first_claim.execution_id},
            )

        replacement_key_id, _ = _install_recovery_key(database_authority_dsns)
        with owner.begin() as connection:
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            audit_id = connection.execute(
                sa.text("SELECT requester_audit_event_id FROM r27_request WHERE id=:id"),
                {"id": ready.request.request_id},
            ).scalar_one()
            connection.execute(
                sa.text(
                    "UPDATE recovery_generation_witness SET invalidated_at=clock_timestamp(),"
                    "invalidation_audit_event_id=:audit,invalidation_reason='KEY_REVOKED' "
                    "WHERE id=:id"
                ),
                {"audit": audit_id, "id": ready.witness_id},
            )
            connection.execute(
                sa.text(
                    "UPDATE r27_execution SET next_attempt_at="
                    "clock_timestamp()-interval '1 second' "
                    "WHERE request_id=:request"
                ),
                {"request": ready.request.request_id},
            )
        fresh_witness_id = _insert_recovery_witness(
            owner,
            ready.request,
            replacement_key_id,
        )
        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT to_jsonb(execution) FROM r27_execution execution
                 WHERE request_id=:request),
              (SELECT to_jsonb(witness) FROM recovery_generation_witness witness
                 WHERE id=:witness)
            """
        )
        parameters = {"request": ready.request.request_id, "witness": fresh_witness_id}
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()
        with maintenance.begin() as connection:
            claimed = connection.execute(
                sa.text("SELECT * FROM easysynq_claim_r27_finalizations(100,clock_timestamp())")
            ).all()
        assert ready.request.request_id not in {row.request_id for row in claimed}
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize("boundary", ("FINALIZATION", "PURGE_CLAIM"))
def test_r27_authority_refuses_non_read_committed_isolation(
    database_authority_dsns: dict[str, str], boundary: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    ready: ReadyAuthority | None = None
    try:
        if boundary == "FINALIZATION":
            ready = _make_ready_authority(database_authority_dsns, owner)
            snapshot_sql = sa.text(
                """
                SELECT
                  (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
                  (SELECT COALESCE(jsonb_agg(to_jsonb(execution) ORDER BY execution.id),'[]')
                     FROM r27_execution execution WHERE request_id=:request),
                  (SELECT to_jsonb(witness) FROM recovery_generation_witness witness
                     WHERE id=:witness)
                """
            )
            parameters = {"request": ready.request.request_id, "witness": ready.witness_id}
            call = sa.text("SELECT * FROM easysynq_claim_r27_finalizations(1,clock_timestamp())")
        else:
            source = _seed_source_execution(database_authority_dsns, owner)
            snapshot_sql = sa.text(
                """
                SELECT
                  (SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:marker),
                  (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
                  (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request)
                """
            )
            parameters = {
                "marker": source.physical_marker_id,
                "execution": source.internal_execution_id,
                "request": source.request.request_id,
            }
            call = sa.text(
                "SELECT * FROM easysynq_claim_r27_exact_purges"
                "(:public_execution,1,clock_timestamp())"
            ).bindparams(public_execution=source.public_execution_id)

        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()
        with pytest.raises(sa.exc.DBAPIError, match="authority_requires_read_committed"):
            with maintenance.connect().execution_options(
                isolation_level="REPEATABLE READ"
            ) as connection:
                with connection.begin():
                    connection.execute(call).all()
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        if ready is not None:
            with owner.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text(
                        "UPDATE r27_request SET state='STALE',error_code='TEST_CLEANUP',"
                        "error_detail=NULL,stale_at=clock_timestamp(),"
                        "updated_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": ready.request.request_id},
                )
        owner.dispose()
        maintenance.dispose()


def test_recovery_witness_result_is_structurally_verified(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    ready: ReadyAuthority | None = None
    try:
        ready = _make_ready_authority(database_authority_dsns, owner)
        with pytest.raises(sa.exc.IntegrityError, match="result_verified"):
            with owner.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text("UPDATE recovery_generation_witness SET result='FAILED' WHERE id=:id"),
                    {"id": ready.witness_id},
                )
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT result FROM recovery_generation_witness WHERE id=:id"),
                    {"id": ready.witness_id},
                ).scalar_one()
                == "VERIFIED"
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT pg_get_constraintdef(oid) "
                        "FROM pg_constraint WHERE conrelid='recovery_generation_witness'::regclass "
                        "AND conname='ck_recovery_generation_witness_result_verified'"
                    )
                ).scalar_one()
                == "CHECK (((result)::text = 'VERIFIED'::text))"
            )
    finally:
        if ready is not None:
            with owner.begin() as connection:
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                connection.execute(
                    sa.text(
                        "UPDATE r27_request SET state='STALE',error_code='TEST_CLEANUP',"
                        "stale_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": ready.request.request_id},
                )
        owner.dispose()


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


def test_r27_downstream_accepts_action_artifacts_valid_at_acceptance_but_expired_now(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical, logical = source.request.targets
        _age_accepted_action_artifacts(owner, source.request)

        with maintenance.begin() as connection:
            _claim_r27_physical_marker(connection, source)
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
                    "SELECT easysynq_record_r27_purge(:sha,:version,:execution,clock_timestamp())"
                ),
                {
                    "sha": physical.sha256,
                    "version": physical.object_version_id,
                    "execution": source.public_execution_id,
                },
            )
        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_record_r27_surviving_owner"
                    "(:sha,:version,:execution,clock_timestamp())"
                ),
                {
                    "sha": logical.sha256,
                    "version": logical.object_version_id,
                    "execution": source.public_execution_id,
                },
            )
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM r27_execution WHERE id=:execution"),
                    {"execution": source.internal_execution_id},
                ).scalar_one()
                == "EXECUTED"
            )
    finally:
        maintenance.dispose()
        owner.dispose()


@pytest.mark.parametrize(
    "authority_drift",
    (
        "source_action",
        "source_tombstone",
        "source_non_worm",
        "source_policy",
        "source_derived_lineage",
        "source_requested_actor",
        "source_approved_actor",
        "source_legal_basis",
        "source_request_binding",
        "source_execution_binding",
        "source_org_binding",
        "source_record_binding",
        "marker_event_binding",
        "marker_request_binding",
        "marker_execution_binding",
        "request_org_binding",
        "request_record_binding",
        "execution_request_binding",
        "execution_not_source_committed",
        "source_record_not_disposed",
        "manifest_expired",
        "request_not_finalizing",
        "request_attestation_expired",
        "approval_attestation_expired",
        "request_permission_revoked",
        "approval_permission_revoked",
        "request_authorizer_key_revoked",
        "approval_authorizer_key_revoked",
        "recovery_key_revoked",
        "witness_invalidated",
    ),
)
def test_r27_purge_claim_revalidates_exact_source_and_current_authority(
    database_authority_dsns: dict[str, str], authority_drift: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        alternate_record_actors = Actors(
            org_id=source.actors.org_id,
            record_id=source.logical_owner_record_id,
            requester_id=source.actors.requester_id,
            approver_id=source.actors.approver_id,
            canceller_id=source.actors.canceller_id,
        )
        alternate_request = _seed_request_authority(
            owner,
            alternate_record_actors,
            source.request.authorizer_key_ids["REQUEST"],
            actions=(),
        )
        alternate_execution_id = uuid.uuid4()
        alternate_event_id = uuid.uuid4()
        alternate_org_id = uuid.uuid4()
        with owner.begin() as connection:
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                sa.text(
                    "INSERT INTO organization(id,legal_name,short_code) VALUES (:id,:name,:code)"
                ),
                {
                    "id": alternate_org_id,
                    "name": f"R27 alternate {alternate_org_id}",
                    "code": f"R27-ALT-{alternate_org_id.hex[:8]}",
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO r27_execution "
                    "(id,request_id,execution_id,state,claimed_at,attempt_count,updated_at) "
                    "VALUES (:id,:request,:public_id,'CLAIMED',clock_timestamp(),1,"
                    "clock_timestamp())"
                ),
                {
                    "id": alternate_execution_id,
                    "request": alternate_request.request_id,
                    "public_id": uuid.uuid4(),
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO disposition_event
                        (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                         is_worm_destroy,legal_basis)
                    SELECT :id,record.org_id,record.id,'DESTROY',true,
                           record.retention_policy_id,:actor,false,'alternate source event'
                    FROM record WHERE record.id=:record
                    """
                ),
                {
                    "id": alternate_event_id,
                    "record": source.logical_owner_record_id,
                    "actor": source.actors.canceller_id,
                },
            )
            if authority_drift == "source_action":
                connection.execute(
                    sa.text("UPDATE disposition_event SET action='ARCHIVE_COLD' WHERE id=:id"),
                    {"id": source.disposition_event_id},
                )
            elif authority_drift == "source_tombstone":
                connection.execute(
                    sa.text("UPDATE disposition_event SET tombstone=false WHERE id=:id"),
                    {"id": source.disposition_event_id},
                )
            elif authority_drift == "source_non_worm":
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET is_worm_destroy=false,"
                        "r27_request_id=NULL,r27_execution_id=NULL WHERE id=:id"
                    ),
                    {"id": source.disposition_event_id},
                )
            elif authority_drift == "source_policy":
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event event SET policy_id=record.retention_policy_id "
                        "FROM record WHERE event.id=:event AND record.id=event.record_id"
                    ),
                    {"event": source.disposition_event_id},
                )
            elif authority_drift == "source_derived_lineage":
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET derived_from_disposition_event_id=:other "
                        "WHERE id=:id"
                    ),
                    {"other": alternate_event_id, "id": source.disposition_event_id},
                )
            elif authority_drift == "source_requested_actor":
                connection.execute(
                    sa.text("UPDATE disposition_event SET requested_by=:actor WHERE id=:id"),
                    {"actor": source.actors.canceller_id, "id": source.disposition_event_id},
                )
            elif authority_drift == "source_approved_actor":
                connection.execute(
                    sa.text("UPDATE disposition_event SET approved_by=:actor WHERE id=:id"),
                    {"actor": source.actors.canceller_id, "id": source.disposition_event_id},
                )
            elif authority_drift == "source_legal_basis":
                connection.execute(
                    sa.text(
                        "UPDATE disposition_event SET legal_basis='different basis' WHERE id=:id"
                    ),
                    {"id": source.disposition_event_id},
                )
            elif authority_drift == "source_request_binding":
                connection.execute(
                    sa.text("UPDATE disposition_event SET r27_request_id=:other WHERE id=:id"),
                    {"other": alternate_request.request_id, "id": source.disposition_event_id},
                )
            elif authority_drift == "source_execution_binding":
                connection.execute(
                    sa.text("UPDATE disposition_event SET r27_execution_id=:other WHERE id=:id"),
                    {"other": alternate_execution_id, "id": source.disposition_event_id},
                )
            elif authority_drift == "source_org_binding":
                connection.execute(
                    sa.text("UPDATE disposition_event SET org_id=:other WHERE id=:id"),
                    {"other": alternate_org_id, "id": source.disposition_event_id},
                )
            elif authority_drift == "source_record_binding":
                connection.execute(
                    sa.text("UPDATE disposition_event SET record_id=:other WHERE id=:id"),
                    {"other": source.logical_owner_record_id, "id": source.disposition_event_id},
                )
            elif authority_drift == "marker_event_binding":
                connection.execute(
                    sa.text(
                        "UPDATE pending_blob_purge SET disposition_event_id=:other WHERE id=:id"
                    ),
                    {"other": alternate_event_id, "id": source.physical_marker_id},
                )
            elif authority_drift == "marker_request_binding":
                connection.execute(
                    sa.text("UPDATE pending_blob_purge SET r27_request_id=:other WHERE id=:id"),
                    {"other": alternate_request.request_id, "id": source.physical_marker_id},
                )
            elif authority_drift == "marker_execution_binding":
                connection.execute(
                    sa.text("UPDATE pending_blob_purge SET r27_execution_id=:other WHERE id=:id"),
                    {"other": alternate_execution_id, "id": source.physical_marker_id},
                )
            elif authority_drift == "request_org_binding":
                connection.execute(
                    sa.text("UPDATE r27_request SET org_id=:other WHERE id=:id"),
                    {"other": alternate_org_id, "id": source.request.request_id},
                )
            elif authority_drift == "request_record_binding":
                connection.execute(
                    sa.text(
                        "UPDATE r27_request SET state='STALE',error_code='TEST_FIXTURE',"
                        "stale_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=:id"
                    ),
                    {"id": alternate_request.request_id},
                )
                connection.execute(
                    sa.text("UPDATE r27_request SET record_id=:other WHERE id=:id"),
                    {"other": source.logical_owner_record_id, "id": source.request.request_id},
                )
            elif authority_drift == "execution_request_binding":
                connection.execute(
                    sa.text("DELETE FROM r27_execution WHERE id=:other"),
                    {"other": alternate_execution_id},
                )
                connection.execute(
                    sa.text("UPDATE r27_execution SET request_id=:other WHERE id=:id"),
                    {"other": alternate_request.request_id, "id": source.internal_execution_id},
                )
            elif authority_drift == "execution_not_source_committed":
                connection.execute(
                    sa.text("UPDATE r27_execution SET source_committed_at=NULL WHERE id=:id"),
                    {"id": source.internal_execution_id},
                )
            elif authority_drift == "source_record_not_disposed":
                connection.execute(
                    sa.text("UPDATE record SET disposition_state='ACTIVE' WHERE id=:id"),
                    {"id": source.actors.record_id},
                )
            elif authority_drift == "manifest_expired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_manifest SET issued_at=clock_timestamp()-interval '2 hours',"
                        "expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": source.request.manifest_id},
                )
            elif authority_drift == "request_not_finalizing":
                connection.execute(
                    sa.text("UPDATE r27_request SET state='EXECUTED' WHERE id=:id"),
                    {"id": source.request.request_id},
                )
            elif authority_drift == "request_attestation_expired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key "
                        "SET active_at=clock_timestamp()-interval '3 hours' "
                        "WHERE id=:key"
                    ),
                    {"key": source.request.authorizer_key_ids["REQUEST"]},
                )
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET issued_at=clock_timestamp()-interval '2 hours',"
                        "expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": source.request.attestations["REQUEST"]},
                )
            elif authority_drift == "approval_attestation_expired":
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key "
                        "SET active_at=clock_timestamp()-interval '3 hours' "
                        "WHERE id=:key"
                    ),
                    {"key": source.request.authorizer_key_ids["APPROVE"]},
                )
                connection.execute(
                    sa.text(
                        "UPDATE r27_attestation SET issued_at=clock_timestamp()-interval '2 hours',"
                        "expires_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": source.request.attestations["APPROVE"]},
                )
            elif authority_drift in {
                "request_permission_revoked",
                "approval_permission_revoked",
            }:
                action = "REQUEST" if authority_drift == "request_permission_revoked" else "APPROVE"
                connection.execute(
                    sa.text("UPDATE r27_attestation SET permission_granted=false WHERE id=:id"),
                    {"id": source.request.attestations[action]},
                )
            elif authority_drift in {
                "request_authorizer_key_revoked",
                "approval_authorizer_key_revoked",
            }:
                action = (
                    "REQUEST" if authority_drift == "request_authorizer_key_revoked" else "APPROVE"
                )
                connection.execute(
                    sa.text(
                        "UPDATE r27_authorizer_key SET revoked_at=clock_timestamp() WHERE id=:key"
                    ),
                    {"key": source.request.authorizer_key_ids[action]},
                )
            elif authority_drift == "recovery_key_revoked":
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_verifier_key "
                        "SET revoked_at=clock_timestamp() WHERE id=:key"
                    ),
                    {"key": source.recovery_key_db_id},
                )
            elif authority_drift == "witness_invalidated":
                audit_id = connection.execute(
                    sa.text("SELECT requester_audit_event_id FROM r27_request WHERE id=:request"),
                    {"request": source.request.request_id},
                ).scalar_one()
                connection.execute(
                    sa.text(
                        "UPDATE recovery_generation_witness "
                        "SET invalidated_at=clock_timestamp(),"
                        "invalidation_audit_event_id=:audit,invalidation_reason='KEY_REVOKED' "
                        "WHERE id=:witness"
                    ),
                    {"audit": audit_id, "witness": source.witness_id},
                )
            else:  # pragma: no cover
                raise AssertionError(authority_drift)

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:marker),
              (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
              (SELECT to_jsonb(blob) FROM blob blob WHERE sha256=:sha),
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT to_jsonb(event) FROM disposition_event event WHERE id=:event),
              (SELECT COALESCE(jsonb_agg(to_jsonb(result) ORDER BY result.id),'[]')
                 FROM r27_execution_target_result result WHERE execution_id=:execution),
              (SELECT count(*) FROM audit_event)
            """
        )
        snapshot_parameters = {
            "marker": source.physical_marker_id,
            "execution": source.internal_execution_id,
            "sha": source.request.targets[0].sha256,
            "request": source.request.request_id,
            "event": source.disposition_event_id,
        }
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, snapshot_parameters).one()
        try:
            with maintenance.begin() as connection:
                claimed = connection.execute(
                    sa.text(
                        "SELECT * FROM easysynq_claim_r27_exact_purges"
                        "(:execution,10,clock_timestamp())"
                    ),
                    {"execution": source.public_execution_id},
                ).all()
        except sa.exc.DBAPIError as error:
            assert "r27_purge_claim_refused" in str(error)
            claimed = []
        assert claimed == []
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, snapshot_parameters).one() == before
    finally:
        owner.dispose()
        maintenance.dispose()


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
def test_r27_purge_claim_uses_closed_live_owner_predicate(
    database_authority_dsns: dict[str, str],
    owner_kind: str,
    owner_state: str | None,
    must_block: bool,
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with owner.begin() as connection:
            if owner_kind == "DOCUMENT":
                _add_r27_document_owner(connection, source, physical)
            else:
                assert owner_state is not None
                _add_r27_evidence_owner(
                    connection,
                    source,
                    physical,
                    state=owner_state,
                )

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:marker),
              (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT to_jsonb(blob) FROM blob blob WHERE sha256=:sha),
              (SELECT COALESCE(jsonb_agg(to_jsonb(result) ORDER BY result.id),'[]')
                 FROM r27_execution_target_result result WHERE execution_id=:execution),
              (SELECT count(*) FROM audit_event WHERE reason='r27-exact-purge-complete')
            """
        )
        snapshot_parameters = {
            "marker": source.physical_marker_id,
            "execution": source.internal_execution_id,
            "request": source.request.request_id,
            "sha": physical.sha256,
        }
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, snapshot_parameters).one()
        with maintenance.begin() as connection:
            claimed = connection.execute(
                sa.text(
                    "SELECT * FROM easysynq_claim_r27_exact_purges(:execution,10,clock_timestamp())"
                ),
                {"execution": source.public_execution_id},
            ).mappings()
            claimed_ids = {row["marker_id"] for row in claimed}
        assert (source.physical_marker_id not in claimed_ids) is must_block

        with owner.connect() as connection:
            assert connection.execute(
                sa.text("SELECT state::text FROM pending_blob_purge WHERE id=:id"),
                {"id": source.physical_marker_id},
            ).scalar_one() == ("PENDING" if must_block else "RUNNING")
            if must_block:
                assert connection.execute(snapshot_sql, snapshot_parameters).one() == before
    finally:
        owner.dispose()
        maintenance.dispose()


def test_r27_purge_claim_holds_blob_lock_until_transaction_end(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with maintenance.connect() as claim_connection:
            claim_transaction = claim_connection.begin()
            try:
                _claim_r27_physical_marker(claim_connection, source)

                with owner.connect() as owner_connection:
                    owner_transaction = owner_connection.begin()
                    try:
                        owner_connection.execute(sa.text("SET LOCAL lock_timeout='250ms'"))
                        with pytest.raises(sa.exc.DBAPIError, match="lock timeout"):
                            _add_r27_evidence_owner(
                                owner_connection,
                                source,
                                physical,
                                state="ACTIVE",
                            )
                    finally:
                        owner_transaction.rollback()
            finally:
                claim_transaction.rollback()
    finally:
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize("boundary", ("CLAIM", "HOLD_RESULT", "PHYSICAL_RESULT"))
def test_r27_authority_rechecks_after_waiting_for_owner_key_share(
    database_authority_dsns: dict[str, str], boundary: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    application_name = f"r27-owner-race-{uuid.uuid4()}"
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        if boundary in {"HOLD_RESULT", "PHYSICAL_RESULT"}:
            with maintenance.begin() as connection:
                _claim_r27_physical_marker(connection, source)
                if boundary == "PHYSICAL_RESULT":
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

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:marker),
              (SELECT to_jsonb(blob) FROM blob blob WHERE sha256=:sha),
              (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT COALESCE(jsonb_agg(to_jsonb(result) ORDER BY result.id),'[]')
                 FROM r27_execution_target_result result WHERE execution_id=:execution),
              (SELECT count(*) FROM audit_event)
            """
        )
        snapshot_parameters = {
            "marker": source.physical_marker_id,
            "sha": physical.sha256,
            "execution": source.internal_execution_id,
            "request": source.request.request_id,
        }

        def invoke_boundary() -> tuple[str, object]:
            try:
                with maintenance.begin() as connection:
                    connection.execute(
                        sa.text("SELECT set_config('application_name',:name,false)"),
                        {"name": application_name},
                    )
                    connection.execute(sa.text("SET LOCAL statement_timeout='10s'"))
                    if boundary == "CLAIM":
                        rows = connection.execute(
                            sa.text(
                                "SELECT * FROM easysynq_claim_r27_exact_purges"
                                "(:execution,10,clock_timestamp())"
                            ),
                            {"execution": source.public_execution_id},
                        ).all()
                        return "rows", {row.marker_id for row in rows}
                    function_name = (
                        "easysynq_record_r27_hold_release"
                        if boundary == "HOLD_RESULT"
                        else "easysynq_record_r27_purge"
                    )
                    connection.execute(
                        sa.text(
                            f"SELECT {function_name}(:sha,:version,:execution,clock_timestamp())"
                        ),
                        {
                            "sha": physical.sha256,
                            "version": physical.object_version_id,
                            "execution": source.public_execution_id,
                        },
                    )
                    return "ok", None
            except sa.exc.DBAPIError as error:
                return "error", str(error)

        owner_connection = owner.connect()
        owner_transaction = owner_connection.begin()
        try:
            _add_r27_evidence_owner(
                owner_connection,
                source,
                physical,
                state="ON_HOLD" if boundary == "HOLD_RESULT" else "ACTIVE",
                legal_hold=boundary == "HOLD_RESULT",
            )
            with owner.connect() as connection:
                before = connection.execute(snapshot_sql, snapshot_parameters).one()
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
        if boundary == "CLAIM":
            assert outcome == "rows"
            assert source.physical_marker_id not in detail
        elif boundary == "HOLD_RESULT":
            assert outcome == "error"
            assert "r27_hold_release_refused" in detail
        else:
            assert outcome == "error"
            assert "r27_purge_result_refused" in detail
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, snapshot_parameters).one() == before
    finally:
        owner.dispose()
        maintenance.dispose()


def test_r27_hold_release_requires_running_marker_in_claim_transaction(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        statement = sa.text(
            "SELECT easysynq_record_r27_hold_release(:sha,:version,:execution,clock_timestamp())"
        )
        parameters = {
            "sha": physical.sha256,
            "version": physical.object_version_id,
            "execution": source.public_execution_id,
        }
        with pytest.raises(sa.exc.DBAPIError, match="r27_hold_release_refused"):
            with maintenance.begin() as connection:
                connection.execute(statement, parameters)
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT state::text FROM pending_blob_purge WHERE id=:id"),
                    {"id": source.physical_marker_id},
                ).scalar_one()
                == "PENDING"
            )
            assert connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": physical.sha256},
            ).scalar_one()

        with maintenance.begin() as connection:
            _claim_r27_physical_marker(connection, source)
            connection.execute(statement, parameters)
            assert not connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": physical.sha256},
            ).scalar_one()
    finally:
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize(
    ("post_claim_change", "accepted"),
    (
        ("ACTIVE_OWNER", False),
        ("DISPOSED_OWNER", True),
        ("SOURCE_ACTION", False),
    ),
)
def test_r27_physical_result_rechecks_source_and_live_owner_without_partial_writes(
    database_authority_dsns: dict[str, str],
    post_claim_change: str,
    accepted: bool,
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with maintenance.begin() as connection:
            _claim_r27_physical_marker(connection, source)
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
        with owner.begin() as connection:
            if post_claim_change in {"ACTIVE_OWNER", "DISPOSED_OWNER"}:
                _add_r27_evidence_owner(
                    connection,
                    source,
                    physical,
                    state="ACTIVE" if post_claim_change == "ACTIVE_OWNER" else "DISPOSED",
                )
            else:
                connection.execute(
                    sa.text("UPDATE disposition_event SET action='ARCHIVE_COLD' WHERE id=:id"),
                    {"id": source.disposition_event_id},
                )

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:marker),
              (SELECT to_jsonb(blob) FROM blob WHERE sha256=:sha),
              (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT COALESCE(jsonb_agg(to_jsonb(result) ORDER BY result.id),'[]')
                 FROM r27_execution_target_result result WHERE execution_id=:execution),
              (SELECT count(*) FROM audit_event)
            """
        )
        snapshot_parameters = {
            "marker": source.physical_marker_id,
            "sha": physical.sha256,
            "execution": source.internal_execution_id,
            "request": source.request.request_id,
        }
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, snapshot_parameters).one()

        statement = sa.text(
            "SELECT easysynq_record_r27_purge(:sha,:version,:execution,clock_timestamp())"
        )
        parameters = {
            "sha": physical.sha256,
            "version": physical.object_version_id,
            "execution": source.public_execution_id,
        }
        if accepted:
            with maintenance.begin() as connection:
                connection.execute(statement, parameters)
        else:
            with pytest.raises(sa.exc.DBAPIError, match="r27_purge_result_refused"):
                with maintenance.begin() as connection:
                    connection.execute(statement, parameters)

        with owner.connect() as connection:
            after = connection.execute(snapshot_sql, snapshot_parameters).one()
            if accepted:
                assert after[0]["state"] == "VERIFIED"
                assert after[0]["completed_at"] is not None
                assert after[1]["purged_at"] is not None
                assert after[1]["purge_execution_id"] == str(source.internal_execution_id)
                assert len(after[4]) == 1
                assert after[4][0]["result_code"] == "PHYSICAL_ERASED"
                assert after[5] == before[5]
            else:
                assert after == before
    finally:
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize(
    ("owner_state", "accepted"),
    (
        ("ACTIVE", True),
        ("DUE_FOR_REVIEW", True),
        ("ON_HOLD", True),
        ("DISPOSED", False),
    ),
)
def test_r27_logical_result_requires_real_live_same_org_owner(
    database_authority_dsns: dict[str, str], owner_state: str, accepted: bool
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        logical = source.request.targets[1]
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE record SET disposition_state=:state WHERE id=:id"),
                {"state": owner_state, "id": source.logical_owner_record_id},
            )
        statement = sa.text(
            "SELECT easysynq_record_r27_surviving_owner(:sha,:version,:execution,clock_timestamp())"
        )
        parameters = {
            "sha": logical.sha256,
            "version": logical.object_version_id,
            "execution": source.public_execution_id,
        }
        if accepted:
            with maintenance.begin() as connection:
                connection.execute(statement, parameters)
        else:
            with pytest.raises(sa.exc.DBAPIError, match="r27_surviving_owner_refused"):
                with maintenance.begin() as connection:
                    connection.execute(statement, parameters)

        with owner.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT surviving_owner_kind,surviving_owner_id "
                    "FROM r27_execution_target_result WHERE execution_id=:execution "
                    "AND manifest_target_id=:target"
                ),
                {"execution": source.internal_execution_id, "target": logical.id},
            ).all()
            assert rows == ([("EVIDENCE_BLOB", source.logical_owner_id)] if accepted else [])
    finally:
        owner.dispose()
        maintenance.dispose()


def test_r27_logical_result_prefers_same_org_document_owner(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        logical = source.request.targets[1]
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                {"id": source.logical_owner_record_id},
            )
            document_version_id = _add_r27_document_owner(connection, source, logical)

        with maintenance.begin() as connection:
            connection.execute(
                sa.text(
                    "SELECT easysynq_record_r27_surviving_owner"
                    "(:sha,:version,:execution,clock_timestamp())"
                ),
                {
                    "sha": logical.sha256,
                    "version": logical.object_version_id,
                    "execution": source.public_execution_id,
                },
            )

        with owner.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT surviving_owner_kind,surviving_owner_id "
                    "FROM r27_execution_target_result WHERE execution_id=:execution "
                    "AND manifest_target_id=:target"
                ),
                {"execution": source.internal_execution_id, "target": logical.id},
            ).one() == ("DOCUMENT_VERSION", document_version_id)
    finally:
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize(
    "corrupt_owner_kind",
    (
        "TENANT_ORPHAN_EVIDENCE",
        "CROSS_ORG_EVIDENCE",
        "CROSS_ORG_DOCUMENT",
        "TENANT_ORPHAN_DOCUMENT",
    ),
)
def test_r27_logical_result_ignores_cross_org_and_tenant_orphan_edges(
    database_authority_dsns: dict[str, str],
    corrupt_owner_kind: str,
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        logical = source.request.targets[1]
        with owner.begin() as connection:
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                sa.text("UPDATE record SET disposition_state='DISPOSED' WHERE id=:id"),
                {"id": source.logical_owner_record_id},
            )
            corrupt_owner_id = _add_corrupt_cross_org_owner(
                connection,
                source,
                logical,
                kind=corrupt_owner_kind,
            )

        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT COALESCE(jsonb_agg(to_jsonb(result) ORDER BY result.id),'[]')
                 FROM r27_execution_target_result result WHERE execution_id=:execution),
              (SELECT count(*) FROM audit_event)
            """
        )
        parameters = {
            "execution": source.internal_execution_id,
            "request": source.request.request_id,
        }
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()
        with pytest.raises(sa.exc.DBAPIError, match="r27_surviving_owner_refused"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_surviving_owner"
                        "(:sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": logical.sha256,
                        "version": logical.object_version_id,
                        "execution": source.public_execution_id,
                    },
                )
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
            statement = (
                sa.text("SELECT count(*) FROM document_version WHERE id=:id")
                if corrupt_owner_kind.endswith("DOCUMENT")
                else sa.text("SELECT count(*) FROM evidence_blob WHERE id=:id")
            )
            assert (
                connection.execute(
                    statement,
                    {"id": corrupt_owner_id},
                ).scalar_one()
                == 1
            )
    finally:
        owner.dispose()
        maintenance.dispose()


def test_r27_logical_result_revalidates_exact_source_disposition(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        logical = source.request.targets[1]
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE disposition_event SET action='ARCHIVE_COLD' WHERE id=:id"),
                {"id": source.disposition_event_id},
            )
        with pytest.raises(sa.exc.DBAPIError, match="r27_surviving_owner_refused"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_surviving_owner"
                        "(:sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": logical.sha256,
                        "version": logical.object_version_id,
                        "execution": source.public_execution_id,
                    },
                )
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FROM r27_execution_target_result "
                        "WHERE execution_id=:execution AND manifest_target_id=:target"
                    ),
                    {"execution": source.internal_execution_id, "target": logical.id},
                ).scalar_one()
                == 0
            )
    finally:
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize("obligation", ("LEGAL_HOLD", "PERMANENT"))
def test_r27_hold_release_preserves_hold_for_other_current_obligation(
    database_authority_dsns: dict[str, str], obligation: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with maintenance.begin() as connection:
            _claim_r27_physical_marker(connection, source)
        with owner.begin() as connection:
            _add_r27_evidence_owner(
                connection,
                source,
                physical,
                state="ON_HOLD" if obligation == "LEGAL_HOLD" else "ACTIVE",
                legal_hold=obligation == "LEGAL_HOLD",
                permanent=obligation == "PERMANENT",
            )
        with pytest.raises(sa.exc.DBAPIError, match="r27_hold_release_refused"):
            with maintenance.begin() as connection:
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
        with owner.connect() as connection:
            assert connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": physical.sha256},
            ).scalar_one()
    finally:
        owner.dispose()
        maintenance.dispose()


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
def test_r27_hold_release_ignores_noncurrent_or_cross_tenant_historical_evidence(
    database_authority_dsns: dict[str, str], owner_shape: str
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with owner.begin() as connection:
            if owner_shape == "DISPOSED_PERMANENT":
                _add_r27_evidence_owner(
                    connection,
                    source,
                    physical,
                    state="DISPOSED",
                    permanent=True,
                )
            else:
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                _add_corrupt_cross_org_owner(
                    connection,
                    source,
                    physical,
                    kind=owner_shape,
                )

        with maintenance.begin() as connection:
            _claim_r27_physical_marker(connection, source)
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
        with owner.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": physical.sha256},
            ).scalar_one()
    finally:
        owner.dispose()
        maintenance.dispose()


def test_r27_hold_release_revalidates_exact_source_disposition_atomically(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = _engine(database_authority_dsns, "owner")
    maintenance = _engine(database_authority_dsns, "easysynq_r27_maintenance")
    try:
        source = _seed_source_execution(database_authority_dsns, owner)
        physical = source.request.targets[0]
        with maintenance.begin() as connection:
            _claim_r27_physical_marker(connection, source)
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE disposition_event SET action='ARCHIVE_COLD' WHERE id=:id"),
                {"id": source.disposition_event_id},
            )
        snapshot_sql = sa.text(
            """
            SELECT
              (SELECT to_jsonb(blob) FROM blob blob WHERE sha256=:sha),
              (SELECT to_jsonb(execution) FROM r27_execution execution WHERE id=:execution),
              (SELECT to_jsonb(request) FROM r27_request request WHERE id=:request),
              (SELECT to_jsonb(marker) FROM pending_blob_purge marker WHERE id=:marker),
              (SELECT COALESCE(jsonb_agg(to_jsonb(result) ORDER BY result.id),'[]')
                 FROM r27_execution_target_result result WHERE execution_id=:execution),
              (SELECT count(*) FROM audit_event)
            """
        )
        parameters = {
            "sha": physical.sha256,
            "execution": source.internal_execution_id,
            "request": source.request.request_id,
            "marker": source.physical_marker_id,
        }
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql, parameters).one()
        with pytest.raises(sa.exc.DBAPIError, match="r27_hold_release_refused"):
            with maintenance.begin() as connection:
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
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql, parameters).one() == before
    finally:
        owner.dispose()
        maintenance.dispose()


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
