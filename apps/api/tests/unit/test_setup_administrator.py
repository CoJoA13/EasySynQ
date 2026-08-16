"""Pure first-administrator bootstrap state-machine proofs (ADR 0005 / R64 / R66)."""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace

import pytest

from easysynq_api.db.models._audit_enums import ActorType, AuditObjectType, EventType
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.system_config import SetupState
from easysynq_api.problems import ProblemException
from easysynq_api.services.setup import administrator as administrator_service
from easysynq_api.services.setup.administrator import (
    ALLOWED_BOOTSTRAP_AFTER_KEYS,
    FirstAdministratorProfile,
    _bootstrap_audit_event,
    _public_summary,
    _require_bound_username,
    _validate_bootstrap_secret,
)
from easysynq_api.services.setup.bootstrap import mint_secret


def test_profile_normalizes_required_and_optional_fields() -> None:
    profile = FirstAdministratorProfile(
        username="  first-admin  ",
        display_name="  First Administrator  ",
        email="  admin@example.local  ",
        first_name="   ",
        last_name="  Operator  ",
    )

    assert profile.normalized() == FirstAdministratorProfile(
        username="first-admin",
        display_name="First Administrator",
        email="admin@example.local",
        first_name=None,
        last_name="Operator",
    )


def test_profile_canonicalizes_username_without_lowercasing_display_name() -> None:
    profile = FirstAdministratorProfile(
        username="  First.Admin  ",
        display_name="  First Administrator  ",
        email=None,
        first_name=None,
        last_name=None,
    )

    assert profile.normalized().username == "first.admin"
    assert profile.normalized().display_name == "First Administrator"


@pytest.mark.parametrize("field", ["username", "display_name"])
def test_profile_rejects_blank_required_fields(field: str) -> None:
    values = {
        "username": "first-admin",
        "display_name": "First Administrator",
        "email": None,
        "first_name": None,
        "last_name": None,
    }
    values[field] = "   "

    with pytest.raises(ProblemException) as excinfo:
        FirstAdministratorProfile(**values).normalized()  # type: ignore[arg-type]

    assert excinfo.value.status == 422
    assert excinfo.value.code == "validation_error"


def test_bound_username_comparison_is_exact_after_normalization() -> None:
    _require_bound_username("first-admin", "first-admin")

    with pytest.raises(ProblemException) as excinfo:
        _require_bound_username("first-admin", "First-Admin")

    assert excinfo.value.status == 409
    assert excinfo.value.code == "bootstrap_identity_bound"
    assert excinfo.value.members == {"bound_username": "first-admin"}


def test_secret_is_verified_before_advanced_state_is_disclosed() -> None:
    secret, stored_hash = mint_secret()
    cfg = SimpleNamespace(
        bootstrap_secret_hash=stored_hash,
        bootstrap_expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        setup_state=SetupState.OPERATIONAL,
        bootstrap_consumed_at=datetime.datetime.now(datetime.UTC),
    )

    with pytest.raises(ProblemException) as invalid:
        _validate_bootstrap_secret(cfg, "wrong", now=datetime.datetime.now(datetime.UTC))
    assert invalid.value.status == 403
    assert invalid.value.code == "bootstrap_invalid"

    with pytest.raises(ProblemException) as advanced:
        _validate_bootstrap_secret(cfg, secret, now=datetime.datetime.now(datetime.UTC))
    assert advanced.value.status == 409
    assert advanced.value.code == "setup_already_complete"


@pytest.mark.parametrize("proof_state", ["missing", "expired", "bad"])
@pytest.mark.parametrize(
    "validator_name",
    ["_validate_request_proof", "_validate_acknowledgment_proof"],
)
async def test_every_failed_proof_runs_comparison_and_counts_generic_denial(
    monkeypatch: pytest.MonkeyPatch,
    proof_state: str,
    validator_name: str,
) -> None:
    valid_secret, stored_hash = mint_secret()
    now = datetime.datetime.now(datetime.UTC)
    cfg = SimpleNamespace(
        bootstrap_secret_hash=None if proof_state == "missing" else stored_hash,
        bootstrap_expires_at=(
            now - datetime.timedelta(minutes=1)
            if proof_state == "expired"
            else now + datetime.timedelta(hours=1)
        ),
        setup_state=SetupState.UNINITIALIZED,
        bootstrap_consumed_at=None,
    )
    presented = "wrong-secret" if proof_state in {"missing", "bad"} else valid_secret
    compared_hashes: list[str | None] = []
    failure_count = 0
    real_verify = administrator_service.verify_secret

    def observed_verify(secret: str, candidate_hash: str | None) -> bool:
        compared_hashes.append(candidate_hash)
        return real_verify(secret, candidate_hash)

    async def record_failure() -> None:
        nonlocal failure_count
        failure_count += 1

    monkeypatch.setattr(administrator_service, "verify_secret", observed_verify)
    monkeypatch.setattr(administrator_service, "_record_failure", record_failure)

    with pytest.raises(ProblemException) as excinfo:
        validator = getattr(administrator_service, validator_name)
        await validator(cfg, presented)

    assert excinfo.value.status == 403
    assert excinfo.value.code == "bootstrap_invalid"
    assert excinfo.value.title == "Invalid bootstrap secret"
    assert len(compared_hashes) == 1
    assert compared_hashes[0] is not None
    assert failure_count == 1


def test_public_summary_defaults_missing_display_name_to_bound_username() -> None:
    user = AppUser(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        org_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        keycloak_subject="not-public",
        display_name=None,
        email="admin@example.local",
        status=UserStatus.INVITED,
    )

    assert _public_summary(user, username="first-admin") == {
        "id": "11111111-1111-1111-1111-111111111111",
        "username": "first-admin",
        "display_name": "first-admin",
        "email": "admin@example.local",
        "status": "INVITED",
    }


def test_system_bootstrap_audit_payloads_are_allowlisted_and_unattributed() -> None:
    org_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
    object_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    payloads = {
        EventType.BOOTSTRAP_IDENTITY_CLAIMED: {"username": "first-admin"},
        EventType.USER_CREATED: {
            "status": "INVITED",
            "email": "admin@example.local",
            "provisioning": "keycloak_created",
        },
        EventType.ADMIN_BOOTSTRAPPED: {"role": "System Administrator"},
        EventType.USER_CREDENTIAL_ISSUED: {"credential_issued": True},
        EventType.BOOTSTRAP_CONSUMED: None,
    }

    assert ALLOWED_BOOTSTRAP_AFTER_KEYS == {
        EventType.BOOTSTRAP_IDENTITY_CLAIMED: {"username"},
        EventType.USER_CREATED: {"status", "email", "provisioning"},
        EventType.ADMIN_BOOTSTRAPPED: {"role"},
        EventType.USER_CREDENTIAL_ISSUED: {"credential_issued"},
        EventType.BOOTSTRAP_CONSUMED: set(),
    }
    for event_type, after in payloads.items():
        event = _bootstrap_audit_event(
            org_id=org_id,
            event_type=event_type,
            object_type=(
                AuditObjectType.config
                if event_type
                in {EventType.BOOTSTRAP_IDENTITY_CLAIMED, EventType.BOOTSTRAP_CONSUMED}
                else AuditObjectType.user
            ),
            object_id=object_id,
            after=after,
        )
        assert event.actor_type is ActorType.system
        assert event.actor_id is None
        assert event.org_id == org_id
        assert event.after == after


def test_system_bootstrap_audit_rejects_unapproved_payload_keys() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _bootstrap_audit_event(
            org_id=uuid.uuid4(),
            event_type=EventType.USER_CREDENTIAL_ISSUED,
            object_type=AuditObjectType.user,
            object_id=uuid.uuid4(),
            after={"credential_issued": True, "subject": "must-not-escape"},
        )
