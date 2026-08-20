# ruff: noqa: S608

"""Real PostgreSQL authority proofs for purpose-separated R27 roles."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from tests.integration.test_ordinary_authority_transitions import (
    OrdinarySeed,
    _authorize,
    _claim_hold_operations,
    _insert_hold_operation,
    _seed_ordinary_owner,
)

NEW_RUNTIME_ROLES = {
    "easysynq_retention",
    "easysynq_hold_authorizer",
    "easysynq_hold_maintenance",
    "easysynq_r27_authorizer",
    "easysynq_r27_maintenance",
    "easysynq_r27_authorizer_key_manager",
    "easysynq_recovery_key_manager",
    "easysynq_r27_role_manager",
    "easysynq_audit_signer",
    "easysynq_backup",
}
ALL_RUNTIME_ROLES = NEW_RUNTIME_ROLES | {"easysynq_app", "easysynq_linker"}

TASK2_SENSITIVE_TABLES = {
    "audit_chain_cursor",
    "audit_checkpoint",
    "audit_checkpoint_sink",
    "audit_event",
    "audit_maintenance_schedule",
    "backup_maintenance_operation",
    "blob",
    "disposition_event",
    "document_version",
    "evidence_blob",
    "pending_blob_purge",
    "r27_action_challenge",
    "r27_attestation",
    "r27_authorizer_key",
    "r27_execution",
    "r27_execution_target_result",
    "r27_manifest",
    "r27_manifest_derivative",
    "r27_manifest_target",
    "r27_request",
    "r27_role_membership_operation",
    "recovery_generation_verifier_key",
    "recovery_generation_witness",
    "retention_operation",
    "retention_operation_target",
    "retention_revision",
    "signature_event",
    "worm_hold_release_authorization",
    "worm_hold_release_operation",
}

FUNCTION_EXECUTORS = {
    "easysynq_assert_worm_record_live": "easysynq_app",
    "easysynq_lock_document_worm_config": "easysynq_app",
    "easysynq_lock_worm_blob": ("easysynq_app", "easysynq_retention"),
    "easysynq_lock_worm_owners": ("easysynq_app", "easysynq_retention"),
    "easysynq_record_worm_assertion": "easysynq_app",
    "easysynq_claim_retention_targets": "easysynq_retention",
    "easysynq_fail_retention_target": "easysynq_retention",
    "easysynq_ratchet_worm_assertion": "easysynq_retention",
    "easysynq_enqueue_ordinary_exact_purge": "easysynq_retention",
    "easysynq_claim_ordinary_exact_purges": "easysynq_retention",
    "easysynq_fail_ordinary_exact_purge": "easysynq_retention",
    "easysynq_record_ordinary_exact_purge": "easysynq_retention",
    "easysynq_authorize_hold_release": "easysynq_hold_authorizer",
    "easysynq_claim_hold_releases": "easysynq_hold_maintenance",
    "easysynq_fail_hold_release": "easysynq_hold_maintenance",
    "easysynq_record_ordinary_hold_release": "easysynq_hold_maintenance",
    "easysynq_accept_r27_request": "easysynq_r27_authorizer",
    "easysynq_accept_r27_approval": "easysynq_r27_authorizer",
    "easysynq_cancel_r27_request": "easysynq_r27_authorizer",
    "easysynq_mark_r27_stale": "easysynq_r27_authorizer",
    "easysynq_claim_r27_finalizations": "easysynq_r27_maintenance",
    "easysynq_fail_r27_execution": "easysynq_r27_maintenance",
    "easysynq_record_r27_hold_release": "easysynq_r27_maintenance",
    "easysynq_claim_r27_exact_purges": "easysynq_r27_maintenance",
    "easysynq_fail_r27_exact_purge": "easysynq_r27_maintenance",
    "easysynq_record_r27_purge": "easysynq_r27_maintenance",
    "easysynq_record_r27_surviving_owner": "easysynq_r27_maintenance",
    "easysynq_install_r27_authorizer_key": "easysynq_r27_authorizer_key_manager",
    "easysynq_retire_r27_authorizer_key": "easysynq_r27_authorizer_key_manager",
    "easysynq_revoke_r27_authorizer_key": "easysynq_r27_authorizer_key_manager",
    "easysynq_install_recovery_verifier_key": "easysynq_recovery_key_manager",
    "easysynq_retire_recovery_verifier_key": "easysynq_recovery_key_manager",
    "easysynq_revoke_recovery_verifier_key": "easysynq_recovery_key_manager",
    "easysynq_begin_r27_role_membership": "easysynq_r27_role_manager",
    "easysynq_complete_r27_role_membership": "easysynq_r27_role_manager",
    "easysynq_fail_r27_role_membership": "easysynq_r27_role_manager",
}

FUNCTION_SIGNATURES = {
    "easysynq_assert_worm_record_live(uuid,uuid)",
    "easysynq_lock_document_worm_config(uuid,uuid)",
    "easysynq_lock_worm_blob(uuid,text)",
    "easysynq_lock_worm_owners(uuid,text)",
    "easysynq_record_worm_assertion(uuid,text,text,text,text,timestamptz,boolean,timestamptz)",
    "easysynq_claim_retention_targets(integer,timestamptz)",
    "easysynq_fail_retention_target(uuid,text,text,timestamptz)",
    "easysynq_ratchet_worm_assertion(text,text,timestamptz,boolean,timestamptz,uuid)",
    "easysynq_enqueue_ordinary_exact_purge(uuid,uuid,text)",
    "easysynq_claim_ordinary_exact_purges(integer,timestamptz)",
    "easysynq_fail_ordinary_exact_purge(uuid,text,text,timestamptz)",
    "easysynq_record_ordinary_exact_purge(uuid,timestamptz)",
    "easysynq_authorize_hold_release(uuid,text,text,timestamptz)",
    "easysynq_claim_hold_releases(integer,timestamptz)",
    "easysynq_fail_hold_release(uuid,text,text,timestamptz)",
    "easysynq_record_ordinary_hold_release(text,text,uuid,timestamptz)",
    "easysynq_accept_r27_request(uuid,timestamptz)",
    "easysynq_accept_r27_approval(uuid,timestamptz)",
    "easysynq_cancel_r27_request(uuid,timestamptz)",
    "easysynq_mark_r27_stale(uuid,text,text,timestamptz)",
    "easysynq_claim_r27_finalizations(integer,timestamptz)",
    "easysynq_fail_r27_execution(uuid,text,text,timestamptz)",
    "easysynq_record_r27_hold_release(text,text,uuid,timestamptz)",
    "easysynq_claim_r27_exact_purges(uuid,integer,timestamptz)",
    "easysynq_fail_r27_exact_purge(uuid,uuid,text,text,timestamptz)",
    "easysynq_record_r27_purge(text,text,uuid,timestamptz)",
    "easysynq_record_r27_surviving_owner(text,text,uuid,timestamptz)",
    "easysynq_install_r27_authorizer_key(text,bytea,text,timestamptz,text)",
    "easysynq_retire_r27_authorizer_key(text,timestamptz,text)",
    "easysynq_revoke_r27_authorizer_key(text,timestamptz,text)",
    "easysynq_install_recovery_verifier_key(text,bytea,text,timestamptz,text)",
    "easysynq_retire_recovery_verifier_key(text,timestamptz,text)",
    "easysynq_revoke_recovery_verifier_key(text,timestamptz,text)",
    "easysynq_begin_r27_role_membership(uuid,uuid,text,text,timestamptz)",
    "easysynq_complete_r27_role_membership(uuid,timestamptz)",
    "easysynq_fail_r27_role_membership(uuid,text,text,timestamptz)",
}

GUARD_FUNCTION_SIGNATURES = {
    "easysynq_guard_app_disposition_insert()",
    "easysynq_guard_blob_worm_identity()",
    "easysynq_guard_hold_release_authorization_history()",
    "easysynq_guard_hold_release_authorization_insert()",
    "easysynq_guard_key_registry_history()",
    "easysynq_guard_r27_result_history()",
    "easysynq_guard_recovery_witness_history()",
    "easysynq_guard_role_membership_history()",
    "easysynq_guard_worm_owner_pointer()",
}

HOLD_TRIGGER_FUNCTIONS = {
    "trg_worm_hold_release_authorize": "easysynq_guard_hold_release_authorization_insert",
    "trg_worm_hold_release_authorization_immutable": (
        "easysynq_guard_hold_release_authorization_history"
    ),
}

OBSERVATION_CALLS = (
    (
        "easysynq_app",
        "SELECT easysynq_record_worm_assertion("
        "gen_random_uuid(),repeat('a',64),'bucket','key','version',"
        "clock_timestamp(),true,:observed)",
    ),
    ("easysynq_retention", "SELECT easysynq_claim_retention_targets(1,:observed)"),
    (
        "easysynq_retention",
        "SELECT easysynq_fail_retention_target(gen_random_uuid(),'retry',NULL,:observed)",
    ),
    (
        "easysynq_retention",
        "SELECT easysynq_ratchet_worm_assertion("
        "repeat('a',64),'v',clock_timestamp(),false,:observed,gen_random_uuid())",
    ),
    ("easysynq_retention", "SELECT easysynq_claim_ordinary_exact_purges(1,:observed)"),
    (
        "easysynq_retention",
        "SELECT easysynq_fail_ordinary_exact_purge(gen_random_uuid(),'retry',NULL,:observed)",
    ),
    (
        "easysynq_retention",
        "SELECT easysynq_record_ordinary_exact_purge(gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_hold_authorizer",
        "SELECT easysynq_authorize_hold_release(gen_random_uuid(),repeat('a',64),'host',:observed)",
    ),
    ("easysynq_hold_maintenance", "SELECT easysynq_claim_hold_releases(1,:observed)"),
    (
        "easysynq_hold_maintenance",
        "SELECT easysynq_fail_hold_release(gen_random_uuid(),'retry',NULL,:observed)",
    ),
    (
        "easysynq_hold_maintenance",
        "SELECT easysynq_record_ordinary_hold_release("
        "repeat('a',64),'v',gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_authorizer",
        "SELECT easysynq_accept_r27_request(gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_authorizer",
        "SELECT easysynq_accept_r27_approval(gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_authorizer",
        "SELECT easysynq_cancel_r27_request(gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_authorizer",
        "SELECT easysynq_mark_r27_stale(gen_random_uuid(),'stale',NULL,:observed)",
    ),
    (
        "easysynq_r27_maintenance",
        "SELECT easysynq_claim_r27_finalizations(1,:observed)",
    ),
    (
        "easysynq_r27_maintenance",
        "SELECT easysynq_fail_r27_execution(gen_random_uuid(),'retry',NULL,:observed)",
    ),
    (
        "easysynq_r27_maintenance",
        "SELECT easysynq_record_r27_hold_release(repeat('a',64),'v',gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_maintenance",
        "SELECT easysynq_claim_r27_exact_purges(gen_random_uuid(),1,:observed)",
    ),
    (
        "easysynq_r27_maintenance",
        "SELECT easysynq_fail_r27_exact_purge("
        "gen_random_uuid(),gen_random_uuid(),'retry',NULL,:observed)",
    ),
    (
        "easysynq_r27_maintenance",
        "SELECT easysynq_record_r27_purge(repeat('a',64),'v',gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_maintenance",
        "SELECT easysynq_record_r27_surviving_owner("
        "repeat('a',64),'v',gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_authorizer_key_manager",
        "SELECT easysynq_install_r27_authorizer_key("
        "'key',decode('00','hex'),repeat('a',64),:observed,'host')",
    ),
    (
        "easysynq_r27_authorizer_key_manager",
        "SELECT easysynq_retire_r27_authorizer_key('key',:observed,'host')",
    ),
    (
        "easysynq_r27_authorizer_key_manager",
        "SELECT easysynq_revoke_r27_authorizer_key('key',:observed,'host')",
    ),
    (
        "easysynq_recovery_key_manager",
        "SELECT easysynq_install_recovery_verifier_key("
        "'key',decode('00','hex'),repeat('a',64),:observed,'host')",
    ),
    (
        "easysynq_recovery_key_manager",
        "SELECT easysynq_retire_recovery_verifier_key('key',:observed,'host')",
    ),
    (
        "easysynq_recovery_key_manager",
        "SELECT easysynq_revoke_recovery_verifier_key('key',:observed,'host')",
    ),
    (
        "easysynq_r27_role_manager",
        "SELECT easysynq_begin_r27_role_membership("
        "gen_random_uuid(),gen_random_uuid(),'ASSIGN','host',:observed)",
    ),
    (
        "easysynq_r27_role_manager",
        "SELECT easysynq_complete_r27_role_membership(gen_random_uuid(),:observed)",
    ),
    (
        "easysynq_r27_role_manager",
        "SELECT easysynq_fail_r27_role_membership(gen_random_uuid(),'retry',NULL,:observed)",
    ),
)

RETENTION_REQUIRED_NULL_CALLS = (
    "SELECT easysynq_fail_retention_target(NULL,'retry',NULL,clock_timestamp())",
    "SELECT easysynq_fail_retention_target(gen_random_uuid(),NULL,NULL,clock_timestamp())",
    "SELECT easysynq_ratchet_worm_assertion("
    "NULL,'version',clock_timestamp(),false,clock_timestamp(),gen_random_uuid())",
    "SELECT easysynq_ratchet_worm_assertion("
    "repeat('a',64),NULL,clock_timestamp(),false,clock_timestamp(),gen_random_uuid())",
    "SELECT easysynq_ratchet_worm_assertion("
    "repeat('a',64),'version',NULL,false,clock_timestamp(),gen_random_uuid())",
    "SELECT easysynq_ratchet_worm_assertion("
    "repeat('a',64),'version',clock_timestamp(),NULL,clock_timestamp(),gen_random_uuid())",
    "SELECT easysynq_ratchet_worm_assertion("
    "repeat('a',64),'version',clock_timestamp(),false,clock_timestamp(),NULL)",
)

_NULL_CALL_SPECS = (
    (
        "easysynq_app",
        "easysynq_lock_document_worm_config",
        (("p_org_id", "gen_random_uuid()"), ("p_config_id", "gen_random_uuid()")),
    ),
    (
        "easysynq_app",
        "easysynq_lock_worm_blob",
        (("p_org_id", "gen_random_uuid()"), ("p_blob_sha256", "repeat('a',64)")),
    ),
    (
        "easysynq_app",
        "easysynq_lock_worm_owners",
        (("p_org_id", "gen_random_uuid()"), ("p_blob_sha256", "repeat('a',64)")),
    ),
    (
        "easysynq_app",
        "easysynq_record_worm_assertion",
        (
            ("p_org_id", "gen_random_uuid()"),
            ("p_blob_sha256", "repeat('a',64)"),
            ("p_bucket", "'bucket'"),
            ("p_object_key", "'key'"),
            ("p_object_version_id", "'version'"),
            ("p_retain_until", "clock_timestamp()"),
            ("p_legal_hold", "false"),
            ("p_verified_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_retention",
        "easysynq_claim_retention_targets",
        (("p_limit", "1"), ("p_claimed_at", "clock_timestamp()")),
    ),
    (
        "easysynq_retention",
        "easysynq_fail_retention_target",
        (
            ("p_target_id", "gen_random_uuid()"),
            ("p_code", "'retry'"),
            ("p_detail", "NULL"),
            ("p_failed_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_retention",
        "easysynq_ratchet_worm_assertion",
        (
            ("p_blob_sha256", "repeat('a',64)"),
            ("p_object_version_id", "'version'"),
            ("p_retain_until", "clock_timestamp()"),
            ("p_legal_hold", "false"),
            ("p_verified_at", "clock_timestamp()"),
            ("p_operation_id", "gen_random_uuid()"),
        ),
    ),
    (
        "easysynq_retention",
        "easysynq_enqueue_ordinary_exact_purge",
        (
            ("p_record_id", "gen_random_uuid()"),
            ("p_event_id", "gen_random_uuid()"),
            ("p_blob_sha", "repeat('a',64)"),
        ),
    ),
    (
        "easysynq_retention",
        "easysynq_claim_ordinary_exact_purges",
        (("p_limit", "1"), ("p_at", "clock_timestamp()")),
    ),
    (
        "easysynq_retention",
        "easysynq_fail_ordinary_exact_purge",
        (
            ("p_id", "gen_random_uuid()"),
            ("p_code", "'retry'"),
            ("p_detail", "NULL"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_retention",
        "easysynq_record_ordinary_exact_purge",
        (("p_id", "gen_random_uuid()"), ("p_at", "clock_timestamp()")),
    ),
    (
        "easysynq_hold_authorizer",
        "easysynq_authorize_hold_release",
        (
            ("p_id", "gen_random_uuid()"),
            ("p_digest", "repeat('a',64)"),
            ("p_identity", "'host'"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_hold_maintenance",
        "easysynq_claim_hold_releases",
        (("p_limit", "1"), ("p_at", "clock_timestamp()")),
    ),
    (
        "easysynq_hold_maintenance",
        "easysynq_fail_hold_release",
        (
            ("p_id", "gen_random_uuid()"),
            ("p_code", "'retry'"),
            ("p_detail", "NULL"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_hold_maintenance",
        "easysynq_record_ordinary_hold_release",
        (
            ("p_sha", "repeat('a',64)"),
            ("p_version", "'version'"),
            ("p_id", "gen_random_uuid()"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    *(
        (
            "easysynq_r27_authorizer",
            function_name,
            (("p_id", "gen_random_uuid()"), ("p_at", "clock_timestamp()")),
        )
        for function_name in (
            "easysynq_accept_r27_request",
            "easysynq_accept_r27_approval",
            "easysynq_cancel_r27_request",
        )
    ),
    (
        "easysynq_r27_authorizer",
        "easysynq_mark_r27_stale",
        (
            ("p_id", "gen_random_uuid()"),
            ("p_code", "'stale'"),
            ("p_detail", "NULL"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_r27_maintenance",
        "easysynq_claim_r27_finalizations",
        (("p_limit", "1"), ("p_at", "clock_timestamp()")),
    ),
    (
        "easysynq_r27_maintenance",
        "easysynq_fail_r27_execution",
        (
            ("p_id", "gen_random_uuid()"),
            ("p_code", "'retry'"),
            ("p_detail", "NULL"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_r27_maintenance",
        "easysynq_record_r27_hold_release",
        (
            ("p_sha", "repeat('a',64)"),
            ("p_version", "'version'"),
            ("p_id", "gen_random_uuid()"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_r27_maintenance",
        "easysynq_claim_r27_exact_purges",
        (
            ("p_id", "gen_random_uuid()"),
            ("p_limit", "1"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_r27_maintenance",
        "easysynq_fail_r27_exact_purge",
        (
            ("p_execution", "gen_random_uuid()"),
            ("p_marker", "gen_random_uuid()"),
            ("p_code", "'retry'"),
            ("p_detail", "NULL"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    *(
        (
            "easysynq_r27_maintenance",
            function_name,
            (
                ("p_sha", "repeat('a',64)"),
                ("p_version", "'version'"),
                ("p_id", "gen_random_uuid()"),
                ("p_at", "clock_timestamp()"),
            ),
        )
        for function_name in (
            "easysynq_record_r27_purge",
            "easysynq_record_r27_surviving_owner",
        )
    ),
    (
        "easysynq_r27_authorizer_key_manager",
        "easysynq_install_r27_authorizer_key",
        (
            ("p_key_id", "'key'"),
            ("p_public_key", "decode('01','hex')"),
            ("p_fingerprint", "repeat('a',64)"),
            ("p_active_at", "clock_timestamp()"),
            ("p_operator_identity", "'host'"),
        ),
    ),
    *(
        (
            "easysynq_r27_authorizer_key_manager",
            function_name,
            (
                ("p_key_id", "'key'"),
                ("p_at", "clock_timestamp()"),
                ("p_operator_identity", "'host'"),
            ),
        )
        for function_name in (
            "easysynq_retire_r27_authorizer_key",
            "easysynq_revoke_r27_authorizer_key",
        )
    ),
    (
        "easysynq_recovery_key_manager",
        "easysynq_install_recovery_verifier_key",
        (
            ("p_key_id", "'key'"),
            ("p_public_key", "decode('01','hex')"),
            ("p_fingerprint", "repeat('a',64)"),
            ("p_active_at", "clock_timestamp()"),
            ("p_operator_identity", "'host'"),
        ),
    ),
    *(
        (
            "easysynq_recovery_key_manager",
            function_name,
            (
                ("p_key_id", "'key'"),
                ("p_at", "clock_timestamp()"),
                ("p_operator_identity", "'host'"),
            ),
        )
        for function_name in (
            "easysynq_retire_recovery_verifier_key",
            "easysynq_revoke_recovery_verifier_key",
        )
    ),
    (
        "easysynq_r27_role_manager",
        "easysynq_begin_r27_role_membership",
        (
            ("p_operation", "gen_random_uuid()"),
            ("p_user_id", "gen_random_uuid()"),
            ("p_action", "'ASSIGN'"),
            ("p_identity", "'host'"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
    (
        "easysynq_r27_role_manager",
        "easysynq_complete_r27_role_membership",
        (("p_operation", "gen_random_uuid()"), ("p_at", "clock_timestamp()")),
    ),
    (
        "easysynq_r27_role_manager",
        "easysynq_fail_r27_role_membership",
        (
            ("p_operation", "gen_random_uuid()"),
            ("p_code", "'retry'"),
            ("p_detail", "NULL"),
            ("p_at", "clock_timestamp()"),
        ),
    ),
)


def _required_null_cases() -> tuple[tuple[str, str, str, str], ...]:
    cases: list[tuple[str, str, str, str]] = []
    observation_parameters = {
        "p_at",
        "p_active_at",
        "p_claimed_at",
        "p_failed_at",
        "p_verified_at",
    }
    for role, function_name, parameters in _NULL_CALL_SPECS:
        for index, (parameter_name, _) in enumerate(parameters):
            if parameter_name == "p_detail":
                continue
            arguments = [value for _, value in parameters]
            arguments[index] = "NULL"
            expected = (
                "observation_time_refused"
                if parameter_name in observation_parameters
                else "required_argument_is_null"
            )
            cases.append(
                (
                    role,
                    f"SELECT {function_name}({','.join(arguments)})",
                    expected,
                    f"{function_name}-{parameter_name}",
                )
            )
    return tuple(cases)


REQUIRED_NULL_CASES = _required_null_cases()


def _add_permanent_document_owner(
    connection: sa.Connection,
    seed: OrdinarySeed,
    authority_kind: str,
    *,
    duration: str = "PERMANENT",
    worm_lock_period: str | None = "PERMANENT",
    active_period: str = "PERMANENT",
) -> None:
    document_id = uuid.uuid4()
    connection.execute(
        sa.text(
            """
            INSERT INTO documented_information
                (id,org_id,framework_id,kind,identifier,title,owner_user_id,current_state,
                 is_singleton,classification,acknowledgement_required,created_by)
            VALUES
                (:id,:org_id,:framework_id,'DOCUMENT',:identifier,'Permanent document owner',
                 :user_id,'Draft',false,'Internal',false,:user_id)
            """
        ),
        {
            "id": document_id,
            "org_id": seed.org_id,
            "framework_id": seed.framework_id,
            "identifier": f"PERM-DOC-{document_id}",
            "user_id": seed.user_id,
        },
    )
    policy_id: uuid.UUID | None = None
    config_id: uuid.UUID | None = None
    if authority_kind == "POLICY":
        policy_id = uuid.uuid4()
        connection.execute(
            sa.text(
                """
                INSERT INTO retention_policy
                    (id,org_id,name,duration,worm_lock_period,disposition_action)
                VALUES (:id,:org_id,:name,:duration,:worm_lock_period,'RETAIN_PERMANENT')
                """
            ),
            {
                "id": policy_id,
                "org_id": seed.org_id,
                "name": f"permanent-{policy_id}",
                "duration": duration,
                "worm_lock_period": worm_lock_period,
            },
        )
    else:
        config_id = uuid.uuid4()
        connection.execute(
            sa.text(
                "INSERT INTO document_worm_config (id,org_id,active_period) "
                "VALUES (:id,:org_id,:active_period)"
            ),
            {"id": config_id, "org_id": seed.org_id, "active_period": active_period},
        )
    connection.execute(
        sa.text(
            """
            INSERT INTO document_version
                (id,org_id,document_id,version_seq,revision_label,change_significance,
                 change_reason,version_state,retention_authority_kind,retention_policy_id,
                 document_worm_config_id,retention_basis_date,source_blob_sha256,
                 metadata_snapshot,imported,author_user_id,created_by)
            VALUES
                (:id,:org_id,:document_id,1,'A','MINOR','permanent owner','Draft',
                 :authority_kind,:policy_id,:config_id,current_date,:blob_sha256,
                 '{}'::jsonb,false,:user_id,:user_id)
            """
        ),
        {
            "id": uuid.uuid4(),
            "org_id": seed.org_id,
            "document_id": document_id,
            "authority_kind": authority_kind,
            "policy_id": policy_id,
            "config_id": config_id,
            "blob_sha256": seed.blob_sha256,
            "user_id": seed.user_id,
        },
    )


def test_password_ddl_never_renders_or_uses_sqlalchemy_execution() -> None:
    migration = (
        Path(__file__).parents[4] / "migrations/versions/0089_worm_retention_container_identity.py"
    ).read_text()
    password_block = migration.split("def _create_and_normalize_authority_roles", 1)[1].split(
        "def upgrade", 1
    )[0]
    assert "as_string" not in password_block
    assert "exec_driver_sql" not in password_block
    assert "PASSWORD {}" in password_block
    assert "psycopg_sql.Literal" in password_block


def test_corrected_r27_retry_and_result_schema_exists(
    database_authority_dsns: dict[str, str],
) -> None:
    engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT table_name,column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name IN
                          ('recovery_generation_witness','r27_execution',
                           'disposition_event','r27_execution_target_result',
                           'r27_role_membership_operation')
                        """
                    )
                ).all()
            )
            assert ("recovery_generation_witness", "invalidated_at") in columns
            assert ("recovery_generation_witness", "invalidation_audit_event_id") in columns
            assert ("recovery_generation_witness", "invalidation_reason") in columns
            assert ("r27_execution", "next_attempt_at") in columns
            assert ("disposition_event", "r27_request_id") in columns
            assert ("disposition_event", "r27_execution_id") in columns
            assert ("r27_execution_target_result", "manifest_target_id") in columns
            assert ("r27_role_membership_operation", "state") in columns
            active_index = connection.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND indexname='uq_recovery_generation_witness_active_request'"
                )
            ).scalar_one()
            assert "WHERE (invalidated_at IS NULL)" in active_index
    finally:
        engine.dispose()


def test_app_cannot_assume_linker_role(database_authority_dsns: dict[str, str]) -> None:
    app_engine = sa.create_engine(database_authority_dsns["easysynq_app"])
    try:
        with pytest.raises(sa.exc.ProgrammingError):
            with app_engine.begin() as connection:
                connection.execute(sa.text("SET ROLE easysynq_linker"))
    finally:
        app_engine.dispose()


def test_runtime_roles_are_independent_no_admin_logins_with_distinct_secrets(
    database_authority_dsns: dict[str, str],
) -> None:
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    """
                    SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,
                           rolcreaterole,rolreplication,rolbypassrls
                    FROM pg_roles WHERE rolname = ANY(:roles) ORDER BY rolname
                    """
                ),
                {"roles": sorted(ALL_RUNTIME_ROLES)},
            ).all()
            assert {row.rolname for row in rows} == ALL_RUNTIME_ROLES
            assert all(row[1:] == (True, False, False, False, False, False, False) for row in rows)
            memberships = connection.execute(
                sa.text(
                    """
                    SELECT member.rolname, granted.rolname
                    FROM pg_auth_members membership
                    JOIN pg_roles member ON member.oid=membership.member
                    JOIN pg_roles granted ON granted.oid=membership.roleid
                    WHERE member.rolname=ANY(:roles) OR granted.rolname=ANY(:roles)
                    """
                ),
                {"roles": sorted(ALL_RUNTIME_ROLES)},
            ).all()
            assert memberships == []
    finally:
        owner_engine.dispose()

    for role in sorted(ALL_RUNTIME_ROLES):
        engine = sa.create_engine(database_authority_dsns[role])
        try:
            with engine.connect() as connection:
                assert connection.execute(sa.text("SELECT session_user")).scalar_one() == role
                for other_role in sorted(ALL_RUNTIME_ROLES - {role}):
                    with pytest.raises(sa.exc.ProgrammingError) as denied:
                        connection.execute(sa.text(f'SET ROLE "{other_role}"'))
                    assert denied.value.orig.sqlstate == "42501"
                    connection.rollback()
        finally:
            engine.dispose()

        other_role = next(candidate for candidate in sorted(ALL_RUNTIME_ROLES) if candidate != role)
        wrong_url = make_url(database_authority_dsns[role]).set(
            password=make_url(database_authority_dsns[other_role]).password
        )
        wrong_engine = sa.create_engine(wrong_url)
        try:
            with pytest.raises(sa.exc.OperationalError):
                with wrong_engine.connect():
                    pass
        finally:
            wrong_engine.dispose()


def test_app_disposition_insert_preserves_ordinary_events_but_refuses_r27_authority(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    app = sa.create_engine(database_authority_dsns["easysynq_app"])
    ordinary_event_id = uuid.uuid4()
    forged_event_id = uuid.uuid4()
    request_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
            connection.execute(
                sa.text(
                    "INSERT INTO r27_request "
                    "(id,org_id,record_id,normalized_legal_basis,legal_basis_sha256) "
                    "VALUES (:id,:org,:record,'review-forgery',:digest)"
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
                    "id": execution_id,
                    "request": request_id,
                    "public_id": uuid.uuid4(),
                },
            )

        with app.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO disposition_event
                        (id,org_id,record_id,action,tombstone,policy_id,approved_by,
                         is_worm_destroy,legal_basis)
                    VALUES
                        (:id,:org,:record,'DESTROY',true,:policy,:user,false,
                         'ordinary application disposition')
                    """
                ),
                {
                    "id": ordinary_event_id,
                    "org": seed.org_id,
                    "record": seed.record_id,
                    "policy": seed.policy_id,
                    "user": seed.user_id,
                },
            )

        with pytest.raises(sa.exc.DBAPIError, match="app_r27_disposition_insert_refused") as denied:
            with app.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO disposition_event
                            (id,org_id,record_id,action,tombstone,approved_by,requested_by,
                             is_worm_destroy,legal_basis,r27_request_id,r27_execution_id)
                        VALUES
                            (:id,:org,:record,'DESTROY',true,:user,:user,true,
                             'forged R27 authority',:request,:execution)
                        """
                    ),
                    {
                        "id": forged_event_id,
                        "org": seed.org_id,
                        "record": seed.record_id,
                        "user": seed.user_id,
                        "request": request_id,
                        "execution": execution_id,
                    },
                )
        assert denied.value.orig.sqlstate == "P0001"

        with owner.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT id FROM disposition_event WHERE id IN (:ordinary,:forged) ORDER BY id"
                ),
                {"ordinary": ordinary_event_id, "forged": forged_event_id},
            ).scalars()
            assert set(rows) == {ordinary_event_id}
    finally:
        owner.dispose()
        app.dispose()


def test_r27_authorizer_insert_columns_allow_only_prepared_unconsumed_authority(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    authorizer = sa.create_engine(database_authority_dsns["easysynq_r27_authorizer"])
    request_id = uuid.uuid4()
    terminal_request_id = uuid.uuid4()
    challenge_id = uuid.uuid4()
    consumed_challenge_id = uuid.uuid4()
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)

        with authorizer.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO r27_request
                        (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256)
                    VALUES (:id,:org,:record,'prepared shell',:digest)
                    """
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
                    """
                    INSERT INTO r27_action_challenge
                        (id,action,request_id,record_id,issuer,token_jti,action_nonce,
                         accepted_claims,manifest_sha256,expires_at)
                    VALUES
                        (:id,'REQUEST',:request,:record,'https://issuer.test',:jti,:nonce,
                         '{}'::jsonb,NULL,clock_timestamp()+interval '1 hour')
                    """
                ),
                {
                    "id": challenge_id,
                    "request": request_id,
                    "record": seed.record_id,
                    "jti": f"prepared-{uuid.uuid4()}",
                    "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:11],
                },
            )

        with pytest.raises(sa.exc.ProgrammingError) as terminal_denied:
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO r27_request
                            (id,org_id,record_id,normalized_legal_basis,legal_basis_sha256,
                             state,stale_at)
                        VALUES
                            (:id,:org,:record,'terminal shell',:digest,'STALE',clock_timestamp())
                        """
                    ),
                    {
                        "id": terminal_request_id,
                        "org": seed.org_id,
                        "record": seed.record_id,
                        "digest": uuid.uuid4().hex * 2,
                    },
                )
        assert terminal_denied.value.orig.sqlstate == "42501"

        with pytest.raises(sa.exc.ProgrammingError) as consumed_denied:
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO r27_action_challenge
                            (id,action,request_id,record_id,issuer,token_jti,action_nonce,
                             accepted_claims,manifest_sha256,expires_at,consumed_at)
                        VALUES
                            (:id,'REQUEST',:request,:record,'https://issuer.test',:jti,:nonce,
                             '{}'::jsonb,NULL,clock_timestamp()+interval '1 hour',
                             clock_timestamp())
                        """
                    ),
                    {
                        "id": consumed_challenge_id,
                        "request": request_id,
                        "record": seed.record_id,
                        "jti": f"consumed-{uuid.uuid4()}",
                        "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:11],
                    },
                )
        assert consumed_denied.value.orig.sqlstate == "42501"

        with owner.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT array_agg(id ORDER BY id) FROM r27_request "
                    "WHERE id IN (:prepared,:terminal)"
                ),
                {"prepared": request_id, "terminal": terminal_request_id},
            ).scalar_one() == [request_id]
            assert connection.execute(
                sa.text(
                    "SELECT array_agg(id ORDER BY id) FROM r27_action_challenge "
                    "WHERE id IN (:prepared,:consumed)"
                ),
                {"prepared": challenge_id, "consumed": consumed_challenge_id},
            ).scalar_one() == [challenge_id]
    finally:
        owner.dispose()
        authorizer.dispose()


def test_r27_authorizer_cannot_insert_preconsumed_challenge(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    authorizer = sa.create_engine(database_authority_dsns["easysynq_r27_authorizer"])
    request_id = uuid.uuid4()
    challenge_id = uuid.uuid4()
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
        with authorizer.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO r27_request "
                    "(id,org_id,record_id,normalized_legal_basis,legal_basis_sha256) "
                    "VALUES (:id,:org,:record,'prepared shell',:digest)"
                ),
                {
                    "id": request_id,
                    "org": seed.org_id,
                    "record": seed.record_id,
                    "digest": uuid.uuid4().hex * 2,
                },
            )
        with pytest.raises(sa.exc.ProgrammingError) as denied:
            with authorizer.begin() as connection:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO r27_action_challenge
                            (id,action,request_id,record_id,issuer,token_jti,action_nonce,
                             accepted_claims,manifest_sha256,expires_at,consumed_at)
                        VALUES
                            (:id,'REQUEST',:request,:record,'https://issuer.test',:jti,:nonce,
                             '{}'::jsonb,NULL,clock_timestamp()+interval '1 hour',
                             clock_timestamp())
                        """
                    ),
                    {
                        "id": challenge_id,
                        "request": request_id,
                        "record": seed.record_id,
                        "jti": f"preconsumed-{uuid.uuid4()}",
                        "nonce": uuid.uuid4().hex + uuid.uuid4().hex[:11],
                    },
                )
        assert denied.value.orig.sqlstate == "42501"
        with owner.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM r27_action_challenge WHERE id=:id)"),
                {"id": challenge_id},
            ).scalar_one()
    finally:
        owner.dispose()
        authorizer.dispose()


def test_every_authority_function_exists_hardened_and_has_one_executor(
    database_authority_dsns: dict[str, str],
) -> None:
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner_engine.connect() as connection:
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
            resolved_signatures = {
                signature: connection.execute(
                    sa.text("SELECT to_regprocedure(:signature)::oid"), {"signature": signature}
                ).scalar_one()
                for signature in FUNCTION_SIGNATURES
            }
            assert all(oid is not None for oid in resolved_signatures.values())
            assert len(set(resolved_signatures.values())) == len(FUNCTION_SIGNATURES)
            functions = connection.execute(
                sa.text(
                    """
                    SELECT p.oid,p.proname,p.prosecdef,p.proconfig,
                           pg_get_userbyid(p.proowner) AS owner_name
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND p.proname=ANY(:names)
                    """
                ),
                {"names": sorted(FUNCTION_EXECUTORS)},
            ).all()
            assert {row.proname for row in functions} == set(FUNCTION_EXECUTORS)
            assert len(functions) == len(FUNCTION_SIGNATURES)
            assert {row.oid for row in functions} == set(resolved_signatures.values())
            assert all(row.prosecdef for row in functions)
            assert all(row.proconfig == ["search_path=public, pg_temp"] for row in functions)
            assert all(row.owner_name == migration_owner for row in functions)
            assert migration_owner not in ALL_RUNTIME_ROLES

            for function in functions:
                executors = (
                    connection.execute(
                        sa.text(
                            """
                        SELECT grantee.rolname
                        FROM aclexplode(COALESCE(
                            (SELECT proacl FROM pg_proc WHERE oid=:oid),
                            acldefault('f',(SELECT proowner FROM pg_proc WHERE oid=:oid)))) acl
                        LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee
                        WHERE acl.privilege_type='EXECUTE'
                          AND acl.grantee <> (SELECT proowner FROM pg_proc WHERE oid=:oid)
                        """
                        ),
                        {"oid": function.oid},
                    )
                    .scalars()
                    .all()
                )
                expected_executors = FUNCTION_EXECUTORS[function.proname]
                if isinstance(expected_executors, str):
                    expected_executors = (expected_executors,)
                assert set(executors) == set(expected_executors)

            for signature in GUARD_FUNCTION_SIGNATURES:
                guard = connection.execute(
                    sa.text(
                        """
                        SELECT p.oid,p.prosecdef,p.proconfig,
                               pg_get_userbyid(p.proowner) AS owner_name
                        FROM pg_proc p
                        WHERE p.oid=to_regprocedure(:signature)
                        """
                    ),
                    {"signature": signature},
                ).one()
                assert guard.owner_name == migration_owner
                assert guard.proconfig == ["search_path=public, pg_temp"]
                assert not guard.prosecdef
                non_owner_executors = connection.execute(
                    sa.text(
                        """
                        SELECT acl.grantee
                        FROM pg_proc p
                        CROSS JOIN LATERAL aclexplode(
                            COALESCE(p.proacl,acldefault('f',p.proowner))) acl
                        WHERE p.oid=:oid AND acl.privilege_type='EXECUTE'
                          AND acl.grantee<>p.proowner
                        """
                    ),
                    {"oid": guard.oid},
                ).all()
                assert non_owner_executors == []

            trigger_bindings = connection.execute(
                sa.text(
                    """
                    SELECT trigger.tgname,trigger.tgfoid
                    FROM pg_trigger trigger
                    JOIN pg_class relation ON relation.oid=trigger.tgrelid
                    WHERE relation.oid=to_regclass('public.worm_hold_release_authorization')
                      AND NOT trigger.tgisinternal
                    ORDER BY trigger.tgname
                    """
                )
            ).all()
            assert len(trigger_bindings) == len(HOLD_TRIGGER_FUNCTIONS) == 2
            assert {row.tgname for row in trigger_bindings} == set(HOLD_TRIGGER_FUNCTIONS)
            for binding in trigger_bindings:
                expected_oid = connection.execute(
                    sa.text("SELECT to_regprocedure(:signature)::oid"),
                    {"signature": (f"public.{HOLD_TRIGGER_FUNCTIONS[binding.tgname]}()")},
                ).scalar_one()
                assert binding.tgfoid == expected_oid
            assert connection.execute(
                sa.text(
                    """
                    SELECT
                      to_regprocedure('public.authorize_worm_hold_release()') IS NULL
                      AND to_regprocedure(
                        'public.refuse_worm_hold_release_authorization_change()'
                      ) IS NULL
                    """
                )
            ).scalar_one()

            partition_factory = connection.execute(
                sa.text(
                    """
                    SELECT p.oid,p.prosecdef,p.proconfig,
                           pg_get_userbyid(p.proowner) AS owner_name
                    FROM pg_proc p
                    WHERE p.oid=to_regprocedure('easysynq_create_audit_partition(date)')
                    """
                )
            ).one()
            assert partition_factory.owner_name == migration_owner
            assert partition_factory.prosecdef
            assert partition_factory.proconfig == ["search_path=public, pg_temp"]
            factory_executors = (
                connection.execute(
                    sa.text(
                        """
                    SELECT grantee.rolname
                    FROM pg_proc p
                    CROSS JOIN LATERAL aclexplode(
                        COALESCE(p.proacl,acldefault('f',p.proowner))) acl
                    LEFT JOIN pg_roles grantee ON grantee.oid=acl.grantee
                    WHERE p.oid=:oid AND acl.privilege_type='EXECUTE'
                      AND acl.grantee<>p.proowner
                    """
                    ),
                    {"oid": partition_factory.oid},
                )
                .scalars()
                .all()
            )
            assert factory_executors == ["easysynq_audit_signer"]
    finally:
        owner_engine.dispose()


def test_current_table_column_and_sequence_acls_match_the_authority_allowlist(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    expected_table_grants = {
        ("easysynq_retention", table, "SELECT")
        for table in ("blob", "retention_operation", "retention_operation_target")
    }
    expected_table_grants |= {
        (role, table, "SELECT")
        for role in ("easysynq_hold_authorizer", "easysynq_hold_maintenance")
        for table in ("blob", "record", "worm_hold_release_operation")
    }
    expected_table_grants |= {
        ("easysynq_r27_authorizer", table, privilege)
        for table, privileges in {
            "r27_action_challenge": ("SELECT",),
            "r27_attestation": ("INSERT", "SELECT"),
            "r27_authorizer_key": ("SELECT",),
            "r27_manifest": ("INSERT", "SELECT"),
            "r27_manifest_derivative": ("INSERT",),
            "r27_manifest_target": ("INSERT", "SELECT"),
            "r27_request": ("SELECT",),
        }.items()
        for privilege in privileges
    }
    expected_table_grants |= {
        ("easysynq_r27_maintenance", table, "SELECT")
        for table in (
            "blob",
            "pending_blob_purge",
            "r27_attestation",
            "r27_execution",
            "r27_execution_target_result",
            "r27_manifest",
            "r27_manifest_target",
            "r27_request",
            "recovery_generation_witness",
        )
    }
    expected_table_grants |= {
        (
            "easysynq_r27_authorizer_key_manager",
            "r27_authorizer_key",
            "SELECT",
        ),
        (
            "easysynq_recovery_key_manager",
            "recovery_generation_verifier_key",
            "SELECT",
        ),
        ("easysynq_r27_role_manager", "app_user", "SELECT"),
        (
            "easysynq_r27_role_manager",
            "r27_role_membership_operation",
            "SELECT",
        ),
    }
    expected_table_grants |= {
        ("easysynq_audit_signer", table, "SELECT")
        for table in (
            "audit_chain_cursor",
            "audit_checkpoint",
            "audit_checkpoint_sink",
            "audit_event",
            "audit_maintenance_schedule",
            "organization",
            "system_config",
        )
    }
    expected_table_grants |= {
        ("easysynq_audit_signer", "audit_event", "INSERT"),
        ("easysynq_audit_signer", "audit_checkpoint", "INSERT"),
        ("easysynq_audit_signer", "audit_chain_cursor", "INSERT"),
        ("easysynq_audit_signer", "audit_chain_cursor", "UPDATE"),
    }
    purpose_roles = NEW_RUNTIME_ROLES - {"easysynq_backup"}
    try:
        with owner.connect() as connection:
            actual_table_grants = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT grantee,table_name,privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema='public' AND grantee=ANY(:roles)
                          AND table_name NOT LIKE 'audit_event_2%'
                        """
                    ),
                    {"roles": sorted(purpose_roles)},
                ).all()
            )
            assert actual_table_grants == expected_table_grants

            app_sensitive = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT table_name,privilege_type
                        FROM information_schema.role_table_grants
                        WHERE table_schema='public' AND grantee='easysynq_app'
                          AND table_name=ANY(:tables)
                        """
                    ),
                    {"tables": sorted(TASK2_SENSITIVE_TABLES)},
                ).all()
            )
            assert app_sensitive == {
                ("audit_checkpoint", "SELECT"),
                ("audit_checkpoint_sink", "SELECT"),
                ("audit_event", "INSERT"),
                ("audit_event", "SELECT"),
                ("blob", "DELETE"),
                ("blob", "SELECT"),
                ("document_version", "DELETE"),
                ("document_version", "INSERT"),
                ("document_version", "SELECT"),
                ("document_version", "UPDATE"),
                ("disposition_event", "INSERT"),
                ("disposition_event", "SELECT"),
                ("evidence_blob", "INSERT"),
                ("evidence_blob", "SELECT"),
                ("pending_blob_purge", "SELECT"),
                ("signature_event", "INSERT"),
                ("signature_event", "SELECT"),
            }
            linker_grants = connection.execute(
                sa.text(
                    "SELECT table_name,privilege_type "
                    "FROM information_schema.role_table_grants "
                    "WHERE table_schema='public' AND grantee='easysynq_linker'"
                )
            ).all()
            assert linker_grants == []

            sensitive_updates = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT grantee,table_name,column_name
                        FROM information_schema.role_column_grants
                        WHERE table_schema='public' AND privilege_type='UPDATE'
                          AND grantee IN ('easysynq_app','easysynq_audit_signer')
                          AND table_name IN ('audit_chain_cursor','audit_checkpoint',
                                             'audit_checkpoint_sink','audit_event','blob')
                        """
                    )
                ).all()
            )
            cursor_columns = {
                row.column_name
                for row in connection.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name='audit_chain_cursor'"
                    )
                )
            }
            assert sensitive_updates == {
                ("easysynq_app", "blob", "verified_at"),
                ("easysynq_app", "blob", "verify_failed_at"),
                ("easysynq_audit_signer", "audit_event", "prev_hash"),
                ("easysynq_audit_signer", "audit_event", "row_hash"),
                ("easysynq_audit_signer", "audit_event", "chained_at"),
            } | {
                ("easysynq_audit_signer", "audit_chain_cursor", column) for column in cursor_columns
            }

            sensitive_inserts = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT grantee,table_name,column_name
                        FROM information_schema.role_column_grants
                        WHERE table_schema='public' AND privilege_type='INSERT'
                          AND ((grantee='easysynq_app' AND table_name='blob')
                            OR (grantee='easysynq_r27_authorizer'
                                AND table_name IN ('r27_request','r27_action_challenge')))
                        """
                    )
                ).all()
            )
            assert sensitive_inserts == {
                ("easysynq_app", "blob", column)
                for column in (
                    "sha256",
                    "org_id",
                    "size_bytes",
                    "mime_type",
                    "bucket",
                    "object_key",
                    "object_version_id",
                    "worm_locked",
                    "worm_enforced_mode",
                    "worm_asserted_retain_until",
                    "worm_asserted_at",
                    "worm_retain_until",
                    "worm_retention_verified_at",
                    "worm_legal_hold",
                    "worm_legal_hold_verified_at",
                    "sse",
                )
            } | {
                ("easysynq_r27_authorizer", "r27_request", column)
                for column in (
                    "id",
                    "org_id",
                    "record_id",
                    "normalized_legal_basis",
                    "legal_basis_sha256",
                )
            } | {
                ("easysynq_r27_authorizer", "r27_action_challenge", column)
                for column in (
                    "id",
                    "action",
                    "request_id",
                    "record_id",
                    "issuer",
                    "token_jti",
                    "action_nonce",
                    "accepted_claims",
                    "manifest_sha256",
                    "expires_at",
                )
            }

            child_grants = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT grants.grantee,grants.table_name,grants.privilege_type
                        FROM information_schema.role_table_grants grants
                        JOIN pg_class child ON child.relname=grants.table_name
                        JOIN pg_inherits inheritance ON inheritance.inhrelid=child.oid
                        JOIN pg_class parent ON parent.oid=inheritance.inhparent
                        WHERE grants.table_schema='public' AND parent.relname='audit_event'
                          AND grants.grantee=ANY(:roles)
                        """
                    ),
                    {
                        "roles": [
                            "easysynq_app",
                            "easysynq_audit_signer",
                            "easysynq_backup",
                            "easysynq_linker",
                        ]
                    },
                ).all()
            )
            child_names = {table for _, table, _ in child_grants}
            assert child_names
            assert child_grants == {
                (role, table, privilege)
                for table in child_names
                for role, privileges in {
                    "easysynq_app": ("INSERT", "SELECT"),
                    "easysynq_audit_signer": ("INSERT", "SELECT"),
                    "easysynq_backup": ("SELECT",),
                }.items()
                for privilege in privileges
            }
            child_update_columns = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT grants.grantee,grants.table_name,grants.column_name
                        FROM information_schema.role_column_grants grants
                        JOIN pg_class child ON child.relname=grants.table_name
                        JOIN pg_inherits inheritance ON inheritance.inhrelid=child.oid
                        JOIN pg_class parent ON parent.oid=inheritance.inhparent
                        WHERE grants.table_schema='public' AND parent.relname='audit_event'
                          AND grants.privilege_type='UPDATE'
                          AND grants.grantee=ANY(:roles)
                        """
                    ),
                    {"roles": sorted(ALL_RUNTIME_ROLES)},
                ).all()
            )
            assert child_update_columns == {
                ("easysynq_audit_signer", table, column)
                for table in child_names
                for column in ("chained_at", "prev_hash", "row_hash")
            }

            public_tables = {
                row.relname
                for row in connection.execute(
                    sa.text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
                        "AND c.relkind IN ('r','p')"
                    )
                )
            }
            backup_tables = set(
                connection.execute(
                    sa.text(
                        "SELECT table_name,privilege_type "
                        "FROM information_schema.role_table_grants "
                        "WHERE table_schema='public' AND grantee='easysynq_backup'"
                    )
                ).all()
            )
            assert backup_tables == {(table, "SELECT") for table in public_tables}

            sequence_acls = set(
                connection.execute(
                    sa.text(
                        """
                        SELECT grantee.rolname,sequence.relname,acl.privilege_type
                        FROM pg_class sequence
                        JOIN pg_namespace namespace ON namespace.oid=sequence.relnamespace
                        CROSS JOIN LATERAL aclexplode(sequence.relacl) acl
                        JOIN pg_roles grantee ON grantee.oid=acl.grantee
                        WHERE namespace.nspname='public' AND sequence.relkind='S'
                          AND grantee.rolname=ANY(:roles)
                        """
                    ),
                    {"roles": sorted(ALL_RUNTIME_ROLES)},
                ).all()
            )
            sequences = {
                row.relname
                for row in connection.execute(
                    sa.text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relkind='S'"
                    )
                )
            }
            assert sequence_acls == {
                ("easysynq_app", sequence, privilege)
                for sequence in sequences
                for privilege in ("SELECT", "USAGE")
            } | {("easysynq_backup", sequence, "SELECT") for sequence in sequences} | {
                ("easysynq_audit_signer", "audit_event_id_seq", privilege)
                for privilege in ("SELECT", "USAGE")
            }
    finally:
        owner.dispose()


def test_default_acl_sentinel_stays_closed_to_app_and_public(
    database_authority_dsns: dict[str, str],
) -> None:
    suffix = uuid.uuid4().hex[:12]
    table_name = f"authority_sentinel_{suffix}"
    function_name = f"authority_sentinel_fn_{suffix}"
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    app_engine = sa.create_engine(database_authority_dsns["easysynq_app"])
    try:
        with owner_engine.begin() as connection:
            connection.execute(
                sa.text(f'CREATE TABLE "{table_name}" (id bigint GENERATED ALWAYS AS IDENTITY)')
            )
            connection.execute(
                sa.text(
                    f'CREATE FUNCTION "{function_name}"() RETURNS integer '
                    "LANGUAGE sql AS $$ SELECT 1 $$"
                )
            )

        with pytest.raises(sa.exc.ProgrammingError):
            with app_engine.connect() as connection:
                connection.execute(sa.text(f'SELECT * FROM "{table_name}"'))
        with pytest.raises(sa.exc.ProgrammingError):
            with app_engine.connect() as connection:
                connection.execute(sa.text(f'SELECT "{function_name}"()'))
    finally:
        with owner_engine.begin() as connection:
            connection.execute(sa.text(f'DROP TABLE IF EXISTS "{table_name}"'))
            connection.execute(sa.text(f'DROP FUNCTION IF EXISTS "{function_name}"()'))
        owner_engine.dispose()
        app_engine.dispose()


def test_key_managers_only_change_their_own_registry_through_audited_functions(
    database_authority_dsns: dict[str, str],
) -> None:
    authorizer_manager = sa.create_engine(
        database_authority_dsns["easysynq_r27_authorizer_key_manager"]
    )
    recovery_manager = sa.create_engine(database_authority_dsns["easysynq_recovery_key_manager"])
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    try:
        with authorizer_manager.begin() as connection:
            authorizer_key_id = connection.execute(
                sa.text(
                    "SELECT easysynq_install_r27_authorizer_key"
                    "(:key_id,:public_key,:fingerprint,now(),:operator_identity)"
                ),
                {
                    "key_id": f"authorizer-{uuid.uuid4().hex}",
                    "public_key": b"authorizer-public-key",
                    "fingerprint": "a" * 64,
                    "operator_identity": "test-host-operator",
                },
            ).scalar_one()

        with recovery_manager.begin() as connection:
            recovery_key_id = connection.execute(
                sa.text(
                    "SELECT easysynq_install_recovery_verifier_key"
                    "(:key_id,:public_key,:fingerprint,now(),:operator_identity)"
                ),
                {
                    "key_id": f"recovery-{uuid.uuid4().hex}",
                    "public_key": b"recovery-public-key",
                    "fingerprint": "b" * 64,
                    "operator_identity": "test-host-operator",
                },
            ).scalar_one()

        with owner_engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT installed_by_identity,installed_audit_event_id "
                        "FROM r27_authorizer_key WHERE id=:id"
                    ),
                    {"id": authorizer_key_id},
                ).one()[0]
                == "test-host-operator"
            )
            assert (
                connection.execute(
                    sa.text(
                        "SELECT installed_by_identity,installed_audit_event_id "
                        "FROM recovery_generation_verifier_key WHERE id=:id"
                    ),
                    {"id": recovery_key_id},
                ).one()[0]
                == "test-host-operator"
            )

        with pytest.raises(sa.exc.ProgrammingError):
            with authorizer_manager.begin() as connection:
                connection.execute(sa.text("DELETE FROM r27_authorizer_key"))
        with pytest.raises(sa.exc.ProgrammingError):
            with recovery_manager.begin() as connection:
                connection.execute(sa.text("DELETE FROM recovery_generation_verifier_key"))
        with pytest.raises(sa.exc.ProgrammingError):
            with authorizer_manager.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_install_recovery_verifier_key"
                        "('wrong-registry',:public_key,:fingerprint,now(),'operator')"
                    ),
                    {"public_key": b"key", "fingerprint": "c" * 64},
                )
    finally:
        authorizer_manager.dispose()
        recovery_manager.dispose()
        owner_engine.dispose()


def test_key_registry_trigger_blocks_owner_rewrite_and_delete_of_installation_history(
    database_authority_dsns: dict[str, str],
) -> None:
    authorizer_manager = sa.create_engine(
        database_authority_dsns["easysynq_r27_authorizer_key_manager"]
    )
    recovery_manager = sa.create_engine(database_authority_dsns["easysynq_recovery_key_manager"])
    owner = sa.create_engine(database_authority_dsns["owner"])
    try:
        with authorizer_manager.begin() as connection:
            authorizer_id = connection.execute(
                sa.text(
                    "SELECT easysynq_install_r27_authorizer_key"
                    "(:key_id,:material,:fingerprint,clock_timestamp(),:identity)"
                ),
                {
                    "key_id": f"owner-guard-{uuid.uuid4()}",
                    "material": b"authorizer-material",
                    "fingerprint": "c" * 64,
                    "identity": "owner-guard-host",
                },
            ).scalar_one()
        with recovery_manager.begin() as connection:
            recovery_id = connection.execute(
                sa.text(
                    "SELECT easysynq_install_recovery_verifier_key"
                    "(:key_id,:material,:fingerprint,clock_timestamp(),:identity)"
                ),
                {
                    "key_id": f"owner-guard-{uuid.uuid4()}",
                    "material": b"recovery-material",
                    "fingerprint": "d" * 64,
                    "identity": "owner-guard-host",
                },
            ).scalar_one()

        with owner.connect() as connection:
            authorizer_before = connection.execute(
                sa.text("SELECT * FROM r27_authorizer_key WHERE id=:id"),
                {"id": authorizer_id},
            ).one()
            recovery_before = connection.execute(
                sa.text("SELECT * FROM recovery_generation_verifier_key WHERE id=:id"),
                {"id": recovery_id},
            ).one()

        mutations = (
            ("r27_authorizer_key", authorizer_id, "public_key=decode('00','hex')"),
            ("r27_authorizer_key", authorizer_id, "installed_by_identity='rewritten'"),
            (
                "r27_authorizer_key",
                authorizer_id,
                "installed_audit_event_id=installed_audit_event_id+1",
            ),
            ("r27_authorizer_key", authorizer_id, "active_at=active_at+interval '1 second'"),
            (
                "recovery_generation_verifier_key",
                recovery_id,
                "public_key=decode('00','hex')",
            ),
            (
                "recovery_generation_verifier_key",
                recovery_id,
                "installed_by_identity='rewritten'",
            ),
            (
                "recovery_generation_verifier_key",
                recovery_id,
                "installed_audit_event_id=installed_audit_event_id+1",
            ),
            ("recovery_generation_verifier_key", recovery_id, "algorithm='OTHER'"),
            (
                "recovery_generation_verifier_key",
                recovery_id,
                "not_before=not_before+interval '1 second'",
            ),
        )
        for table, key_id, assignment in mutations:
            with pytest.raises(sa.exc.DBAPIError, match="key_registry_history_is_immutable"):
                with owner.begin() as connection:
                    connection.execute(
                        sa.text(f"UPDATE {table} SET {assignment} WHERE id=:id"),
                        {"id": key_id},
                    )
        for table, key_id in (
            ("r27_authorizer_key", authorizer_id),
            ("recovery_generation_verifier_key", recovery_id),
        ):
            with pytest.raises(sa.exc.DBAPIError, match="key_registry_history_is_immutable"):
                with owner.begin() as connection:
                    connection.execute(sa.text(f"DELETE FROM {table} WHERE id=:id"), {"id": key_id})

        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT * FROM r27_authorizer_key WHERE id=:id"),
                    {"id": authorizer_id},
                ).one()
                == authorizer_before
            )
            assert (
                connection.execute(
                    sa.text("SELECT * FROM recovery_generation_verifier_key WHERE id=:id"),
                    {"id": recovery_id},
                ).one()
                == recovery_before
            )
    finally:
        authorizer_manager.dispose()
        recovery_manager.dispose()
        owner.dispose()


@pytest.mark.parametrize(
    "role,install_function,retire_function,revoke_function,table_name,start_column",
    (
        (
            "easysynq_r27_authorizer_key_manager",
            "easysynq_install_r27_authorizer_key",
            "easysynq_retire_r27_authorizer_key",
            "easysynq_revoke_r27_authorizer_key",
            "r27_authorizer_key",
            "active_at",
        ),
        (
            "easysynq_recovery_key_manager",
            "easysynq_install_recovery_verifier_key",
            "easysynq_retire_recovery_verifier_key",
            "easysynq_revoke_recovery_verifier_key",
            "recovery_generation_verifier_key",
            "not_before",
        ),
    ),
)
def test_key_lifecycle_functions_enforce_monotone_irreversible_timestamps(
    database_authority_dsns: dict[str, str],
    role: str,
    install_function: str,
    retire_function: str,
    revoke_function: str,
    table_name: str,
    start_column: str,
) -> None:
    manager = sa.create_engine(database_authority_dsns[role])
    owner = sa.create_engine(database_authority_dsns["owner"])
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    try:
        first_key = f"monotone-{uuid.uuid4()}"
        with manager.begin() as connection:
            connection.execute(
                sa.text(
                    f"SELECT {install_function}"
                    "(:key_id,:material,:fingerprint,:started_at,:identity)"
                ),
                {
                    "key_id": first_key,
                    "material": b"monotone-key",
                    "fingerprint": uuid.uuid4().hex * 2,
                    "started_at": started_at,
                    "identity": "monotone-host",
                },
            )
        retired_at = datetime.now(UTC)
        with manager.begin() as connection:
            connection.execute(
                sa.text(f"SELECT {retire_function}(:key_id,:at,:identity)"),
                {"key_id": first_key, "at": retired_at, "identity": "monotone-host"},
            )
        with owner.connect() as connection:
            audit_count = connection.execute(
                sa.text("SELECT count(*) FROM audit_event")
            ).scalar_one()
        with pytest.raises(sa.exc.DBAPIError, match="key_lifecycle_refused"):
            with manager.begin() as connection:
                connection.execute(
                    sa.text(f"SELECT {revoke_function}(:key_id,:at,:identity)"),
                    {
                        "key_id": first_key,
                        "at": retired_at - timedelta(seconds=1),
                        "identity": "monotone-host",
                    },
                )
        with owner.connect() as connection:
            assert (
                connection.execute(sa.text("SELECT count(*) FROM audit_event")).scalar_one()
                == audit_count
            )
        revoked_at = retired_at + timedelta(seconds=1)
        with manager.begin() as connection:
            connection.execute(
                sa.text(f"SELECT {revoke_function}(:key_id,:at,:identity)"),
                {"key_id": first_key, "at": revoked_at, "identity": "monotone-host"},
            )

        second_key = f"revoke-first-{uuid.uuid4()}"
        with manager.begin() as connection:
            connection.execute(
                sa.text(
                    f"SELECT {install_function}"
                    "(:key_id,:material,:fingerprint,:started_at,:identity)"
                ),
                {
                    "key_id": second_key,
                    "material": b"revoke-first-key",
                    "fingerprint": uuid.uuid4().hex * 2,
                    "started_at": started_at,
                    "identity": "monotone-host",
                },
            )
        second_revoked_at = datetime.now(UTC)
        with manager.begin() as connection:
            connection.execute(
                sa.text(f"SELECT {revoke_function}(:key_id,:at,:identity)"),
                {
                    "key_id": second_key,
                    "at": second_revoked_at,
                    "identity": "monotone-host",
                },
            )
        with pytest.raises(sa.exc.DBAPIError, match="key_lifecycle_refused"):
            with manager.begin() as connection:
                connection.execute(
                    sa.text(f"SELECT {retire_function}(:key_id,:at,:identity)"),
                    {
                        "key_id": second_key,
                        "at": revoked_at,
                        "identity": "monotone-host",
                    },
                )

        with owner.connect() as connection:
            first = connection.execute(
                sa.text(
                    f"SELECT {start_column},retired_at,revoked_at "
                    f"FROM {table_name} WHERE key_id=:key_id"
                ),
                {"key_id": first_key},
            ).one()
            assert first == (started_at, retired_at, revoked_at)
            second = connection.execute(
                sa.text(f"SELECT retired_at,revoked_at FROM {table_name} WHERE key_id=:key_id"),
                {"key_id": second_key},
            ).one()
            assert second == (None, second_revoked_at)
    finally:
        manager.dispose()
        owner.dispose()


@pytest.mark.parametrize(
    "role,function_name,table_name",
    (
        (
            "easysynq_r27_authorizer_key_manager",
            "easysynq_install_r27_authorizer_key",
            "r27_authorizer_key",
        ),
        (
            "easysynq_recovery_key_manager",
            "easysynq_install_recovery_verifier_key",
            "recovery_generation_verifier_key",
        ),
    ),
)
@pytest.mark.parametrize(
    "key_id,public_key,fingerprint,operator_identity",
    (
        (None, b"key", "a" * 64, "host"),
        ("key", None, "a" * 64, "host"),
        ("key", b"", "a" * 64, "host"),
        ("key", b"key", None, "host"),
        ("key", b"key", "a" * 64, None),
    ),
)
def test_key_install_rejects_missing_material_without_audit_or_registry_write(
    database_authority_dsns: dict[str, str],
    role: str,
    function_name: str,
    table_name: str,
    key_id: str | None,
    public_key: bytes | None,
    fingerprint: str | None,
    operator_identity: str | None,
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    manager = sa.create_engine(database_authority_dsns[role])
    snapshot = sa.text(
        f"SELECT (SELECT count(*) FROM audit_event),(SELECT count(*) FROM {table_name})"
    )
    try:
        with owner.connect() as connection:
            before = connection.execute(snapshot).one()
        expected_error = "key_install_refused" if public_key == b"" else "required_argument_is_null"
        with pytest.raises(sa.exc.DBAPIError, match=expected_error):
            with manager.begin() as connection:
                connection.execute(
                    sa.text(
                        f"SELECT {function_name}"
                        "(:key_id,:public_key,:fingerprint,clock_timestamp(),:operator_identity)"
                    ),
                    {
                        "key_id": key_id,
                        "public_key": public_key,
                        "fingerprint": fingerprint,
                        "operator_identity": operator_identity,
                    },
                )
        with owner.connect() as connection:
            assert connection.execute(snapshot).one() == before
    finally:
        owner.dispose()
        manager.dispose()


def test_claim_retention_targets_rejects_null_limit_without_claiming_rows(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    retention = sa.create_engine(database_authority_dsns["easysynq_retention"])
    try:
        with owner.connect() as connection:
            before = connection.execute(
                sa.text(
                    "SELECT count(*) FILTER (WHERE state='RUNNING'),count(*) "
                    "FROM retention_operation_target"
                )
            ).one()
        with pytest.raises(sa.exc.DBAPIError, match="required_argument_is_null"):
            with retention.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT * FROM easysynq_claim_retention_targets(NULL,clock_timestamp())"
                    )
                )
        with owner.connect() as connection:
            assert (
                connection.execute(
                    sa.text(
                        "SELECT count(*) FILTER (WHERE state='RUNNING'),count(*) "
                        "FROM retention_operation_target"
                    )
                ).one()
                == before
            )
    finally:
        owner.dispose()
        retention.dispose()


@pytest.mark.parametrize("statement", RETENTION_REQUIRED_NULL_CALLS)
def test_retention_functions_reject_required_nulls_without_writes(
    database_authority_dsns: dict[str, str], statement: str
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    retention = sa.create_engine(database_authority_dsns["easysynq_retention"])
    snapshot = sa.text(
        "SELECT (SELECT count(*) FROM retention_operation),"
        "(SELECT count(*) FROM retention_operation_target),"
        "(SELECT count(*) FROM audit_event)"
    )
    try:
        with owner.connect() as connection:
            before = connection.execute(snapshot).one()
        with pytest.raises(sa.exc.DBAPIError, match="required_argument_is_null"):
            with retention.begin() as connection:
                connection.execute(sa.text(statement))
        with owner.connect() as connection:
            assert connection.execute(snapshot).one() == before
    finally:
        owner.dispose()
        retention.dispose()


@pytest.mark.parametrize(
    "role,statement,expected_error,case_name",
    REQUIRED_NULL_CASES,
    ids=[case[3] for case in REQUIRED_NULL_CASES],
)
def test_every_authority_callable_rejects_each_required_null_without_writes(
    database_authority_dsns: dict[str, str],
    role: str,
    statement: str,
    expected_error: str,
    case_name: str,
) -> None:
    del case_name
    owner = sa.create_engine(database_authority_dsns["owner"])
    caller = sa.create_engine(database_authority_dsns[role])
    snapshot_sql = sa.text(
        """
        SELECT
          (SELECT count(*) FROM audit_event),
          (SELECT count(*) FROM retention_operation_target WHERE state<>'PENDING'),
          (SELECT count(*) FROM pending_blob_purge WHERE state<>'PENDING'),
          (SELECT count(*) FROM worm_hold_release_operation
           WHERE state<>'PENDING_AUTHORIZATION'),
          (SELECT count(*) FROM r27_request WHERE state IS NOT NULL),
          (SELECT count(*) FROM r27_execution),
          (SELECT count(*) FROM r27_execution_target_result),
          (SELECT count(*) FROM r27_authorizer_key),
          (SELECT count(*) FROM recovery_generation_verifier_key),
          (SELECT count(*) FROM r27_role_membership_operation)
        """
    )
    try:
        with owner.connect() as connection:
            before = connection.execute(snapshot_sql).one()
        with pytest.raises(sa.exc.DBAPIError, match=expected_error) as refused:
            with caller.begin() as connection:
                connection.execute(sa.text(statement))
        assert refused.value.orig.sqlstate == "P0001"
        with owner.connect() as connection:
            assert connection.execute(snapshot_sql).one() == before
    finally:
        owner.dispose()
        caller.dispose()


@pytest.mark.parametrize("owner_table", ("evidence_blob", "document_version"))
def test_worm_owner_org_cannot_change_while_pointer_is_retained(
    database_authority_dsns: dict[str, str], owner_table: str
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner.begin() as connection:
            seed = _seed_ordinary_owner(connection)
            if owner_table == "document_version":
                _add_permanent_document_owner(connection, seed, "POLICY")
                owner_id = connection.execute(
                    sa.text(
                        "SELECT id FROM document_version WHERE source_blob_sha256=:sha "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"sha": seed.blob_sha256},
                ).scalar_one()
            else:
                owner_id = connection.execute(
                    sa.text("SELECT id FROM evidence_blob WHERE blob_sha256=:sha"),
                    {"sha": seed.blob_sha256},
                ).scalar_one()
            other_org = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO organization (id,legal_name,short_code) VALUES (:id,:name,:code)"
                ),
                {"id": other_org, "name": f"Other {other_org}", "code": f"OT-{other_org.hex[:12]}"},
            )

        with pytest.raises(sa.exc.DBAPIError, match="worm_owner_pointer_is_immutable"):
            with owner.begin() as connection:
                connection.execute(
                    sa.text(f"UPDATE {owner_table} SET org_id=:org_id WHERE id=:id"),
                    {"org_id": other_org, "id": owner_id},
                )
        pointer_column = (
            "source_blob_sha256" if owner_table == "document_version" else "blob_sha256"
        )
        with pytest.raises(sa.exc.DBAPIError, match="worm_owner_pointer_is_immutable"):
            with owner.begin() as connection:
                connection.execute(
                    sa.text(f"UPDATE {owner_table} SET {pointer_column}=:sha WHERE id=:id"),
                    {"sha": "f" * 64, "id": owner_id},
                )
        with pytest.raises(sa.exc.DBAPIError, match="worm_owner_pointer_is_immutable"):
            with owner.begin() as connection:
                connection.execute(
                    sa.text(f"DELETE FROM {owner_table} WHERE id=:id"), {"id": owner_id}
                )
        with owner.connect() as connection:
            assert connection.execute(
                sa.text(f"SELECT org_id,{pointer_column} FROM {owner_table} WHERE id=:id"),
                {"id": owner_id},
            ).one() == (seed.org_id, seed.blob_sha256)
    finally:
        owner.dispose()


def test_r27_result_functions_revalidate_coordinates_and_source_disposition_atomically(
    database_authority_dsns: dict[str, str],
) -> None:
    from tests.integration.test_r27_authority_transitions import (
        _claim_r27_physical_marker,
        _seed_source_execution,
    )

    owner = sa.create_engine(database_authority_dsns["owner"])
    maintenance = sa.create_engine(database_authority_dsns["easysynq_r27_maintenance"])
    try:
        hold_source = _seed_source_execution(database_authority_dsns, owner)
        hold_target = hold_source.request.targets[0]
        with maintenance.begin() as connection:
            _claim_r27_physical_marker(connection, hold_source)
        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE r27_manifest_target SET bucket='drifted' WHERE id=:id"),
                {"id": hold_target.id},
            )
        with pytest.raises(sa.exc.DBAPIError, match="r27_hold_release_refused"):
            with maintenance.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_r27_hold_release"
                        "(:sha,:version,:execution,clock_timestamp())"
                    ),
                    {
                        "sha": hold_target.sha256,
                        "version": hold_target.object_version_id,
                        "execution": hold_source.public_execution_id,
                    },
                )
        with owner.connect() as connection:
            assert connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": hold_target.sha256},
            ).scalar_one()

        source = _seed_source_execution(database_authority_dsns, owner)
        physical, logical = source.request.targets
        with maintenance.begin() as connection:
            marker_id = connection.execute(
                sa.text(
                    "SELECT marker_id FROM easysynq_claim_r27_exact_purges"
                    "(:execution,1,clock_timestamp())"
                ),
                {"execution": source.public_execution_id},
            ).scalar_one()
            assert marker_id == source.physical_marker_id

        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE r27_manifest_target SET object_key='drifted' WHERE id=:id"),
                {"id": physical.id},
            )
        with pytest.raises(sa.exc.DBAPIError, match="r27_purge_result_refused"):
            with maintenance.begin() as connection:
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
            assert connection.execute(
                sa.text(
                    "SELECT p.state::text,b.purged_at,"
                    "(SELECT count(*) FROM r27_execution_target_result "
                    "WHERE execution_id=:execution) "
                    "FROM pending_blob_purge p JOIN blob b ON b.sha256=p.sha256 WHERE p.id=:marker"
                ),
                {"execution": source.internal_execution_id, "marker": source.physical_marker_id},
            ).one() == ("RUNNING", None, 0)
            connection.execute(
                sa.text("UPDATE r27_manifest_target SET object_key=:object_key WHERE id=:id"),
                {"object_key": physical.object_key, "id": physical.id},
            )

        with maintenance.begin() as connection:
            assert connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": physical.sha256},
            ).scalar_one()
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
            assert not connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": physical.sha256},
            ).scalar_one()
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

        with owner.begin() as connection:
            connection.execute(
                sa.text("UPDATE r27_manifest_target SET bucket='drifted' WHERE id=:id"),
                {"id": logical.id},
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
        with owner.begin() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT state::text,(SELECT count(*) FROM r27_execution_target_result "
                    "WHERE execution_id=:id) FROM r27_execution WHERE id=:id"
                ),
                {"id": source.internal_execution_id},
            ).one() == ("PURGING", 1)
            connection.execute(
                sa.text("UPDATE r27_manifest_target SET bucket=:bucket WHERE id=:id"),
                {"bucket": logical.bucket, "id": logical.id},
            )
            connection.execute(
                sa.text("UPDATE disposition_event SET action='RETAIN_PERMANENT' WHERE id=:id"),
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
            assert connection.execute(
                sa.text(
                    "SELECT state::text,(SELECT count(*) FROM r27_execution_target_result "
                    "WHERE execution_id=:id) FROM r27_execution WHERE id=:id"
                ),
                {"id": source.internal_execution_id},
            ).one() == ("PURGING", 1)
    finally:
        owner.dispose()
        maintenance.dispose()


@pytest.mark.parametrize("authority_kind", ("POLICY", "INSTALLATION_MINIMUM"))
def test_hold_claim_and_result_recheck_permanent_document_owners(
    database_authority_dsns: dict[str, str],
    authority_kind: str,
) -> None:
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    authorizer_engine = sa.create_engine(database_authority_dsns["easysynq_hold_authorizer"])
    maintenance_engine = sa.create_engine(database_authority_dsns["easysynq_hold_maintenance"])
    try:
        with owner_engine.begin() as connection:
            claim_seed = _seed_ordinary_owner(connection)
            claim_operation, claim_digest = _insert_hold_operation(connection, claim_seed)
        with authorizer_engine.begin() as connection:
            _authorize(connection, claim_operation, claim_digest)
        with owner_engine.begin() as connection:
            _add_permanent_document_owner(connection, claim_seed, authority_kind)
        with maintenance_engine.begin() as connection:
            claimed = _claim_hold_operations(connection)
            assert claim_operation not in {row["operation_id"] for row in claimed}

        with owner_engine.begin() as connection:
            result_seed = _seed_ordinary_owner(connection)
            result_operation, result_digest = _insert_hold_operation(connection, result_seed)
        with authorizer_engine.begin() as connection:
            _authorize(connection, result_operation, result_digest)
        with maintenance_engine.begin() as connection:
            claimed = _claim_hold_operations(connection)
            assert result_operation in {row["operation_id"] for row in claimed}
        with owner_engine.begin() as connection:
            _add_permanent_document_owner(connection, result_seed, authority_kind)
        with pytest.raises(sa.exc.DBAPIError, match="ordinary_hold_release_refused"):
            with maintenance_engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "SELECT easysynq_record_ordinary_hold_release"
                        "(:sha,:version,:operation,clock_timestamp())"
                    ),
                    {
                        "sha": result_seed.blob_sha256,
                        "version": result_seed.object_version_id,
                        "operation": result_operation,
                    },
                )
        with owner_engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT worm_legal_hold FROM blob WHERE sha256=:sha"),
                {"sha": result_seed.blob_sha256},
            ).scalar_one()
    finally:
        owner_engine.dispose()
        authorizer_engine.dispose()
        maintenance_engine.dispose()


def test_audit_signer_owns_only_append_chain_checkpoint_and_partition_authority(
    database_authority_dsns: dict[str, str],
) -> None:
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    signer_engine = sa.create_engine(database_authority_dsns["easysynq_audit_signer"])
    try:
        with owner_engine.begin() as connection:
            org_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO organization (id,legal_name,short_code) VALUES (:id,:name,:code)"
                ),
                {"id": org_id, "name": f"Signer {org_id}", "code": f"SG-{org_id.hex[:12]}"},
            )
        with signer_engine.begin() as connection:
            partition_start = connection.execute(
                sa.text(
                    "SELECT (date_trunc('month',clock_timestamp()) + interval '2 months')::date"
                )
            ).scalar_one()
            connection.execute(
                sa.text("SELECT easysynq_create_audit_partition(:start)"),
                {"start": partition_start},
            )
            event_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO audit_event
                        (org_id,occurred_at,actor_type,event_type,object_type,object_id,
                         scope_ref,reason,after)
                    VALUES
                        (:org_id,:occurred_at,'system','CONFIG_UPDATED','config',:org_id,
                         'audit-signer-test','future-partition-append','{}'::jsonb)
                    RETURNING id
                    """
                ),
                {"org_id": org_id, "occurred_at": partition_start},
            ).scalar_one()
            row_hash = b"r" * 32
            connection.execute(
                sa.text(
                    "UPDATE audit_event SET prev_hash=:previous,row_hash=:row_hash,"
                    "chained_at=clock_timestamp() WHERE id=:id"
                ),
                {"previous": b"p" * 32, "row_hash": row_hash, "id": event_id},
            )
            connection.execute(
                sa.text(
                    "UPDATE audit_chain_cursor SET safe_watermark=:id,updated_at=clock_timestamp() "
                    "WHERE id=1"
                ),
                {"id": event_id},
            )
            checkpoint_id = connection.execute(
                sa.text(
                    """
                    INSERT INTO audit_checkpoint
                        (org_id,latest_id,latest_row_hash,timestamp)
                    VALUES (:org_id,:latest_id,:row_hash,clock_timestamp()) RETURNING id
                    """
                ),
                {"org_id": org_id, "latest_id": event_id, "row_hash": row_hash},
            ).scalar_one()
            assert (
                connection.execute(
                    sa.text("SELECT count(*) FROM audit_maintenance_schedule")
                ).scalar_one()
                >= 0
            )

        with owner_engine.connect() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT row_hash,chained_at IS NOT NULL FROM audit_event WHERE id=:event_id"
                ),
                {"event_id": event_id},
            ).one() == (row_hash, True)
            assert (
                connection.execute(
                    sa.text("SELECT latest_id FROM audit_checkpoint WHERE id=:id"),
                    {"id": checkpoint_id},
                ).scalar_one()
                == event_id
            )

        denied = (
            (
                "easysynq_app",
                "UPDATE audit_event SET chained_at=clock_timestamp() WHERE id=-1",
            ),
            (
                "easysynq_app",
                "INSERT INTO audit_checkpoint "
                "(org_id,latest_id,latest_row_hash,timestamp) "
                f"VALUES ('{org_id}',0,decode('00','hex'),clock_timestamp())",
            ),
            ("easysynq_linker", "SELECT count(*) FROM audit_event"),
            (
                "easysynq_backup",
                "INSERT INTO audit_checkpoint (org_id,latest_id,latest_row_hash,timestamp) "
                f"VALUES ('{org_id}',0,decode('00','hex'),clock_timestamp())",
            ),
            (
                "easysynq_r27_role_manager",
                "INSERT INTO audit_event "
                "(org_id,occurred_at,actor_type,event_type,object_type) "
                f"VALUES ('{org_id}',clock_timestamp(),'system','CONFIG_UPDATED','config')",
            ),
        )
        for role, statement in denied:
            engine = sa.create_engine(database_authority_dsns[role])
            try:
                with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
                    with engine.begin() as connection:
                        connection.execute(sa.text(statement))
            finally:
                engine.dispose()
    finally:
        owner_engine.dispose()
        signer_engine.dispose()


def test_audit_partition_factory_rejects_null_without_creating_a_child(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    signer = sa.create_engine(database_authority_dsns["easysynq_audit_signer"])
    child_count = sa.text(
        """
        SELECT count(*)
        FROM pg_inherits AS inheritance
        WHERE inheritance.inhparent='public.audit_event'::regclass
        """
    )
    try:
        with owner.connect() as connection:
            before = connection.execute(child_count).scalar_one()
        with pytest.raises(sa.exc.DBAPIError, match="audit_partition_start_refused") as denied:
            with signer.begin() as connection:
                connection.execute(sa.text("SELECT easysynq_create_audit_partition(NULL)"))
        assert denied.value.orig.sqlstate == "P0001"
        with owner.connect() as connection:
            assert connection.execute(child_count).scalar_one() == before
    finally:
        owner.dispose()
        signer.dispose()


@pytest.mark.parametrize("role", sorted(ALL_RUNTIME_ROLES - {"easysynq_audit_signer"}))
def test_every_non_signer_role_is_denied_chain_checkpoint_and_partition_authority(
    database_authority_dsns: dict[str, str], role: str
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    candidate = sa.create_engine(database_authority_dsns[role])
    try:
        with owner.connect() as connection:
            org_id = connection.execute(
                sa.text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
            ).scalar_one()
            assert connection.execute(
                sa.text(
                    "SELECT to_regprocedure('easysynq_create_audit_partition(date)') IS NOT NULL"
                )
            ).scalar_one()

        statements = [
            sa.text("UPDATE audit_event SET chained_at=clock_timestamp() WHERE id=-1"),
            sa.text("INSERT INTO audit_chain_cursor (id,safe_watermark) VALUES (2,0)"),
            sa.text("UPDATE audit_chain_cursor SET safe_watermark=safe_watermark WHERE id=1"),
            sa.text(
                "INSERT INTO audit_checkpoint "
                "(org_id,latest_id,latest_row_hash,timestamp) "
                "VALUES (:org_id,0,decode('00','hex'),clock_timestamp())"
            ),
            sa.text(
                "SELECT easysynq_create_audit_partition("
                "(date_trunc('month',clock_timestamp())+interval '3 months')::date)"
            ),
        ]
        if role != "easysynq_app":
            statements.append(
                sa.text(
                    "INSERT INTO audit_event "
                    "(org_id,occurred_at,actor_type,event_type,object_type) "
                    "VALUES (:org_id,clock_timestamp(),'system','CONFIG_UPDATED','config')"
                )
            )
        for statement in statements:
            with pytest.raises(sa.exc.DBAPIError) as denied:
                with candidate.begin() as connection:
                    connection.execute(statement, {"org_id": org_id})
            assert denied.value.orig.sqlstate == "42501"
    finally:
        owner.dispose()
        candidate.dispose()


def test_backup_role_can_dump_source_but_cannot_create_database_or_mutate(
    database_authority_dsns: dict[str, str], tmp_path: Path
) -> None:
    backup_url = make_url(database_authority_dsns["easysynq_backup"])
    archive = tmp_path / "source-read-only.dump"
    environment = os.environ.copy()
    environment["PGPASSWORD"] = backup_url.password or ""
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    assert pg_dump is not None
    assert pg_restore is not None
    command = [
        pg_dump,
        "--host",
        backup_url.host or "",
        "--port",
        str(backup_url.port or 5432),
        "--username",
        backup_url.username or "",
        "--dbname",
        backup_url.database or "",
        "--format=custom",
        "--file",
        str(archive),
    ]
    dumped = subprocess.run(  # noqa: S603 - fixed pg_dump path and fixed argument grammar
        command, env=environment, capture_output=True, text=True, check=False
    )
    assert dumped.returncode == 0, dumped.stderr
    assert archive.stat().st_size > 0
    listed = subprocess.run(  # noqa: S603 - fixed pg_restore path and fixed argument grammar
        [pg_restore, "--list", str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert "TABLE DATA public" in listed.stdout

    backup = sa.create_engine(database_authority_dsns["easysynq_backup"])
    owner = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner.connect() as connection:
            assert connection.execute(
                sa.text(
                    """
                    SELECT count(*)=1
                    FROM pg_database database
                    CROSS JOIN LATERAL aclexplode(database.datacl) acl
                    JOIN pg_roles grantee ON grantee.oid=acl.grantee
                    WHERE database.datname=current_database()
                      AND grantee.rolname='easysynq_backup'
                      AND acl.privilege_type='CONNECT'
                    """
                )
            ).scalar_one()
        with pytest.raises(sa.exc.ProgrammingError, match="permission denied to create database"):
            with backup.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(sa.text(f'CREATE DATABASE "denied_{uuid.uuid4().hex}"'))
        with pytest.raises(sa.exc.ProgrammingError, match="permission denied"):
            with backup.begin() as connection:
                connection.execute(
                    sa.text("UPDATE organization SET legal_name=legal_name WHERE false")
                )
    finally:
        owner.dispose()
        backup.dispose()


@pytest.mark.parametrize("role,statement", OBSERVATION_CALLS)
@pytest.mark.parametrize("offset", (-timedelta(days=1), timedelta(days=1)))
def test_authority_functions_reject_unbounded_observation_times_without_writes(
    database_authority_dsns: dict[str, str],
    role: str,
    statement: str,
    offset: timedelta,
) -> None:
    owner_engine = sa.create_engine(database_authority_dsns["owner"])
    role_engine = sa.create_engine(database_authority_dsns[role])
    snapshot_sql = sa.text(
        """
        SELECT
          (SELECT count(*) FROM audit_event),
          (SELECT count(*) FROM blob),
          (SELECT count(*) FROM pending_blob_purge),
          (SELECT count(*) FROM worm_hold_release_operation),
          (SELECT count(*) FROM r27_execution),
          (SELECT count(*) FROM r27_execution_target_result),
          (SELECT count(*) FROM r27_authorizer_key),
          (SELECT count(*) FROM recovery_generation_verifier_key),
          (SELECT count(*) FROM r27_role_membership_operation)
        """
    )
    try:
        with owner_engine.connect() as connection:
            before = connection.execute(snapshot_sql).one()
        with pytest.raises(sa.exc.DBAPIError, match="observation_time_refused"):
            with role_engine.begin() as connection:
                connection.execute(
                    sa.text(statement),
                    {"observed": datetime.now(UTC) + offset},
                )
        with owner_engine.connect() as connection:
            assert connection.execute(snapshot_sql).one() == before
    finally:
        owner_engine.dispose()
        role_engine.dispose()


def test_role_membership_failed_state_requires_nonnull_error_code_in_database(
    database_authority_dsns: dict[str, str],
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    operation_id = uuid.uuid4()
    user_id = uuid.uuid4()
    try:
        with owner.begin() as connection:
            org_id = connection.execute(
                sa.text("SELECT id FROM organization ORDER BY created_at LIMIT 1")
            ).scalar_one()
            connection.execute(
                sa.text(
                    "INSERT INTO app_user(id,org_id,keycloak_subject,display_name) "
                    "VALUES (:id,:org,:subject,'Role state-shape actor')"
                ),
                {"id": user_id, "org": org_id, "subject": f"role-shape-{user_id}"},
            )
        valid_operation_id = uuid.uuid4()
        with owner.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO r27_role_membership_operation
                            (id,user_id,org_id,action,operator_identity,state,
                             requested_at,completed_at,error_code,error_detail)
                        VALUES
                            (:id,:user,:org,'ASSIGN','host-role-manager','FAILED',
                             clock_timestamp(),clock_timestamp(),'HOST_DENIED','valid failure')
                        """
                    ),
                    {"id": valid_operation_id, "user": user_id, "org": org_id},
                )
                assert connection.execute(
                    sa.text(
                        "SELECT state,error_code FROM r27_role_membership_operation WHERE id=:id"
                    ),
                    {"id": valid_operation_id},
                ).one() == ("FAILED", "HOST_DENIED")
            finally:
                transaction.rollback()

        with owner.connect() as connection:
            transaction = connection.begin()
            try:
                with pytest.raises(sa.exc.IntegrityError, match="state_shape"):
                    connection.execute(
                        sa.text(
                            """
                            INSERT INTO r27_role_membership_operation
                                (id,user_id,org_id,action,operator_identity,state,
                                 requested_at,completed_at,error_code,error_detail)
                            VALUES
                                (:id,:user,:org,'ASSIGN','host-role-manager','FAILED',
                                 clock_timestamp(),clock_timestamp(),NULL,'missing code')
                            """
                        ),
                        {"id": operation_id, "user": user_id, "org": org_id},
                    )
            finally:
                transaction.rollback()
        with owner.connect() as connection:
            assert not connection.execute(
                sa.text("SELECT EXISTS (SELECT 1 FROM r27_role_membership_operation WHERE id=:id)"),
                {"id": operation_id},
            ).scalar_one()
    finally:
        owner.dispose()


def test_role_membership_model_requires_nonnull_failed_error_code() -> None:
    from easysynq_api.db.models.r27_role_membership_operation import (
        R27RoleMembershipOperation,
    )

    constraints = {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in R27RoleMembershipOperation.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert constraints["ck_r27_role_membership_operation_state_shape"] == (
        "(state='REQUESTED' AND audit_event_id IS NULL AND completed_at IS NULL "
        "AND error_code IS NULL AND error_detail IS NULL) OR "
        "(state='AUDITED' AND audit_event_id IS NOT NULL AND completed_at IS NOT NULL "
        "AND error_code IS NULL AND error_detail IS NULL) OR "
        "(state='FAILED' AND audit_event_id IS NULL AND completed_at IS NOT NULL "
        "AND error_code IS NOT NULL AND btrim(error_code)<>'' AND length(error_code)<=64 "
        "AND length(COALESCE(error_detail,''))<=512)"
    )


def test_r27_surviving_owner_model_declares_the_exact_closed_kind_registry() -> None:
    from easysynq_api.db.models.r27_execution_target_result import (
        R27ExecutionTargetResult,
    )

    constraints = {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in R27ExecutionTargetResult.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert constraints["ck_r27_execution_target_result_authority_shape"] == (
        "(result_code='PHYSICAL_ERASED' AND purge_marker_id IS NOT NULL "
        "AND surviving_owner_kind IS NULL AND surviving_owner_id IS NULL) OR "
        "(result_code='LOGICAL_ONLY_SURVIVING_OWNER' AND purge_marker_id IS NULL "
        "AND surviving_owner_kind IN "
        "('DOCUMENT_VERSION','EVIDENCE_BLOB','SEALED_PACK') "
        "AND surviving_owner_id IS NOT NULL)"
    )


@pytest.mark.parametrize(
    ("owner_kind", "accepted"),
    (
        ("DOCUMENT_VERSION", True),
        ("EVIDENCE_BLOB", True),
        ("SEALED_PACK", True),
        ("UNKNOWN_OWNER", False),
    ),
)
def test_r27_surviving_owner_database_check_accepts_only_the_closed_kind_registry(
    database_authority_dsns: dict[str, str],
    owner_kind: str,
    accepted: bool,
) -> None:
    owner = sa.create_engine(database_authority_dsns["owner"])
    try:
        with owner.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(sa.text("SET LOCAL session_replication_role=replica"))
                statement = sa.text(
                    """
                    INSERT INTO r27_execution_target_result
                        (id,execution_id,manifest_target_id,result_code,verified_at,
                         surviving_owner_kind,surviving_owner_id)
                    VALUES
                        (:id,:execution,:target,'LOGICAL_ONLY_SURVIVING_OWNER',
                         clock_timestamp(),:kind,:owner)
                    """
                )
                parameters = {
                    "id": uuid.uuid4(),
                    "execution": uuid.uuid4(),
                    "target": uuid.uuid4(),
                    "kind": owner_kind,
                    "owner": uuid.uuid4(),
                }
                if accepted:
                    connection.execute(statement, parameters)
                    assert (
                        connection.execute(
                            sa.text(
                                "SELECT surviving_owner_kind FROM r27_execution_target_result "
                                "WHERE id=:id"
                            ),
                            {"id": parameters["id"]},
                        ).scalar_one()
                        == owner_kind
                    )
                else:
                    with pytest.raises(
                        sa.exc.IntegrityError,
                        match="ck_r27_execution_target_result_authority_shape",
                    ):
                        connection.execute(statement, parameters)
            finally:
                transaction.rollback()
    finally:
        owner.dispose()
