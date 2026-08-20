"""Pure proofs for the closed WORM-owner requirement calculation."""

from __future__ import annotations

import dataclasses
import datetime
import importlib
import inspect
import uuid
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from easysynq_api.services.vault.worm import WormObjectLocator, WormObjectState

_ORG_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_SHA256 = "1" * 64
_BASIS = datetime.date(2024, 2, 29)
_READ_AT = datetime.datetime(2024, 3, 1, tzinfo=datetime.UTC)
_LOCATOR = WormObjectLocator("records", f"records/{_SHA256}", "version-1")


def _subject() -> ModuleType:
    """Keep every RED independently collectable while the production module is absent."""
    try:
        return importlib.import_module("easysynq_api.services.vault.retention")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Task 4 WORM retention module is absent: {exc}")


def _owner(**overrides: Any) -> Any:
    subject = _subject()
    values: dict[str, Any] = {
        "kind": subject.WormOwnerKind.RECORD_EVIDENCE,
        "owner_id": uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "org_id": _ORG_ID,
        "blob_sha256": _SHA256,
        "basis_date": _BASIS,
        "duration": "P3Y",
        "domain_hold": False,
        "permanent": False,
        "worm_lock_period": None,
    }
    values.update(overrides)
    return subject.WormOwner(**values)


def _state(*, retain_until: datetime.datetime, legal_hold: bool = False) -> WormObjectState:
    return WormObjectState(
        locator=_LOCATOR,
        mode="GOVERNANCE",
        retain_until=retain_until,
        legal_hold=legal_hold,
        read_at=_READ_AT,
    )


def test_finite_owner_requirement_uses_utc_end_of_day_and_calendar_leap_clamp() -> None:
    subject = _subject()

    requirement = subject.owner_requirement(_owner(duration="P1Y"))

    assert requirement.retain_until == datetime.datetime(
        2025, 2, 28, 23, 59, 59, 999000, tzinfo=datetime.UTC
    )
    assert requirement.legal_hold is False


@pytest.mark.parametrize(
    ("logical_duration", "worm_lock_period", "expected"),
    (
        ("P10Y", "P3Y", datetime.date(2027, 2, 28)),
        ("P3Y", "P10Y", datetime.date(2034, 2, 28)),
    ),
)
def test_worm_lock_period_explicitly_wins_even_when_shorter_or_longer(
    logical_duration: str,
    worm_lock_period: str,
    expected: datetime.date,
) -> None:
    subject = _subject()

    requirement = subject.owner_requirement(
        _owner(duration=logical_duration, worm_lock_period=worm_lock_period)
    )

    assert requirement.retain_until is not None
    assert requirement.retain_until.date() == expected


def test_permanent_worm_lock_period_wins_over_finite_logical_duration() -> None:
    subject = _subject()

    requirement = subject.owner_requirement(
        _owner(duration="P3Y", worm_lock_period="PERMANENT", permanent=True)
    )

    assert requirement.retain_until is None
    assert requirement.legal_hold is True


def test_owner_type_is_immutable() -> None:
    instance = _owner()

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        instance.duration = "P99Y"


@pytest.mark.parametrize(
    ("overrides", "expected_hold"),
    (
        ({"domain_hold": True}, True),
        (
            {
                "duration": "PERMANENT",
                "worm_lock_period": None,
                "permanent": True,
            },
            True,
        ),
    ),
)
def test_permanent_and_domain_hold_map_to_hold_on(
    overrides: dict[str, Any], expected_hold: bool
) -> None:
    subject = _subject()

    requirement = subject.owner_requirement(_owner(**overrides))

    assert requirement.legal_hold is expected_hold


def test_aggregate_uses_strongest_of_current_and_two_same_domain_owners() -> None:
    subject = _subject()
    current = _state(
        retain_until=datetime.datetime(2027, 2, 28, 23, 59, 59, 999000, tzinfo=datetime.UTC)
    )
    owners = [
        _owner(duration="P3Y"),
        _owner(
            owner_id=uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
            duration="P10Y",
        ),
    ]

    requirement = subject.aggregate_requirements(current, owners)

    assert requirement.retain_until == datetime.datetime(
        2034, 2, 28, 23, 59, 59, 999000, tzinfo=datetime.UTC
    )
    assert requirement.legal_hold is False


def test_aggregate_never_shortens_a_later_current_retention_or_turns_hold_off() -> None:
    subject = _subject()
    later = datetime.datetime(2045, 1, 1, tzinfo=datetime.UTC)
    current = _state(retain_until=later, legal_hold=True)

    requirement = subject.aggregate_requirements(current, [_owner(duration="P1Y")])

    assert requirement.retain_until == later
    assert requirement.legal_hold is True


def test_later_real_event_ratchets_and_earlier_event_cannot_reduce_physical_floor() -> None:
    subject = _subject()
    provisional = subject.owner_requirement(
        _owner(basis_date=datetime.date(2026, 1, 1), duration="P3Y")
    )
    assert provisional.retain_until is not None
    current = _state(retain_until=provisional.retain_until)

    later = subject.aggregate_requirements(
        current,
        [_owner(basis_date=datetime.date(2028, 1, 1), duration="P3Y")],
    )
    earlier = subject.aggregate_requirements(
        current,
        [_owner(basis_date=datetime.date(2024, 1, 1), duration="P3Y")],
    )

    assert later.retain_until == datetime.datetime(
        2031, 1, 1, 23, 59, 59, 999000, tzinfo=datetime.UTC
    )
    assert earlier.retain_until == provisional.retain_until


@pytest.mark.parametrize(
    "overrides",
    (
        {"kind": "RECORD_EVIDENCE"},
        {"owner_id": "not-a-uuid"},
        {"org_id": "not-a-uuid"},
        {"blob_sha256": "A" * 64},
        {"blob_sha256": "1" * 63},
        {"basis_date": datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)},
        {"duration": ""},
        {"duration": "P"},
        {"worm_lock_period": "not-a-duration"},
        {"domain_hold": 1},
        {"permanent": 1},
        {"permanent": True, "duration": "P3Y"},
        {"permanent": False, "duration": "PERMANENT"},
        {"permanent": False, "worm_lock_period": "PERMANENT"},
    ),
)
def test_invalid_or_impossible_owner_shapes_raise_typed_bounded_failure(
    overrides: dict[str, Any],
) -> None:
    subject = _subject()

    with pytest.raises(subject.WormOwnerIntegrityError) as error:
        _owner(**overrides)

    assert str(error.value) == "invalid WORM owner state"


async def test_single_new_copy_convenience_delegates_to_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _subject()
    owner = subject.ProposedRecordEvidence(
        owner_id=uuid.uuid4(),
        record_id=uuid.uuid4(),
        org_id=_ORG_ID,
        blob_sha256=_SHA256,
    )
    session = object()
    sentinel = object()
    observed: list[Any] = []

    async def batch(observed_session: object, *, proposals: list[Any]) -> list[object]:
        assert observed_session is session
        observed.extend(proposals)
        return [sentinel]

    async def promote() -> None:
        raise AssertionError("the delegating wrapper must not run storage itself")

    monkeypatch.setattr(subject, "protect_proposed_owners", batch)

    result = await subject.protect_proposed_owner(
        session,
        owner=owner,
        target_bucket="records",
        target_key=_SHA256,
        promote=promote,
    )

    assert result is sentinel
    assert len(observed) == 1
    assert isinstance(observed[0], subject.NewCopyProposal)


def test_existing_version_entry_point_cannot_accept_loose_locator_or_promotion() -> None:
    subject = _subject()

    parameters = inspect.signature(subject.protect_existing_owner).parameters

    assert "owner" in parameters
    assert "locator" not in parameters
    assert "promotion" not in parameters


def test_module_public_exports_are_an_exact_closed_allowlist() -> None:
    subject = _subject()

    expected = {
        "ExistingVersionProposal",
        "NewCopyProposal",
        "ProtectedPromotion",
        "ProposedDocumentSource",
        "ProposedRecordEvidence",
        "ProposedSealedPack",
        "ProposedWormOwner",
        "ResolvedDocumentAuthority",
        "ResolvedRecordEvidenceAuthority",
        "ResolvedSealedPackAuthority",
        "ResolvedWormOwnerAuthority",
        "WormOwner",
        "WormOwnerIntegrityError",
        "WormOwnerKind",
        "aggregate_requirements",
        "list_live_worm_owners",
        "owner_requirement",
        "protect_existing_owner",
        "protect_proposed_owner",
        "protect_proposed_owners",
        "reconcile_exact_version",
    }
    assert set(subject.__all__) == expected


def test_proposed_authority_family_carries_only_stable_references() -> None:
    subject = _subject()

    expected = {
        subject.ProposedRecordEvidence: {
            "owner_id",
            "record_id",
            "org_id",
            "blob_sha256",
        },
        subject.ProposedDocumentSource: {
            "owner_id",
            "document_id",
            "org_id",
            "blob_sha256",
            "authority_kind",
            "authority_id",
        },
        subject.ProposedSealedPack: {
            "owner_id",
            "pack_record_id",
            "evidence_blob_id",
            "org_id",
            "blob_sha256",
        },
    }

    for proposal_type, expected_fields in expected.items():
        fields = {field.name for field in dataclasses.fields(proposal_type)}
        assert fields == expected_fields
        assert fields.isdisjoint(
            {
                "basis_date",
                "duration",
                "worm_lock_period",
                "domain_hold",
                "legal_hold",
                "permanent",
            }
        )

    assert "basis_date" not in inspect.signature(subject.ProposedDocumentSource).parameters


def test_resolved_persistence_authority_types_are_exact_and_immutable() -> None:
    subject = _subject()

    expected = {
        subject.ResolvedRecordEvidenceAuthority: {
            "evidence_blob_id",
            "record_id",
            "retention_policy_id",
        },
        subject.ResolvedDocumentAuthority: {
            "document_version_id",
            "document_id",
            "authority_kind",
            "authority_id",
            "basis_date",
        },
        subject.ResolvedSealedPackAuthority: {
            "evidence_pack_id",
            "pack_record_id",
            "evidence_blob_id",
            "retention_policy_id",
        },
    }

    for authority_type, expected_fields in expected.items():
        assert {field.name for field in dataclasses.fields(authority_type)} == expected_fields
        assert authority_type.__dataclass_params__.frozen is True

    assert {field.name for field in dataclasses.fields(subject.ProtectedPromotion)} == {
        "promotion",
        "assertion",
        "owner",
        "authority",
    }
    assert subject.ProtectedPromotion.__dataclass_params__.frozen is True
    assert set(subject.ProtectedPromotion.__slots__) == {
        "promotion",
        "assertion",
        "owner",
        "authority",
    }
    assert subject.ProtectedPromotion.__dictoffset__ == 0


@pytest.mark.parametrize(
    ("sqlstate", "message", "expected"),
    (
        ("P0001", "document_worm_config_lock_refused", True),
        ("42501", "document_worm_config_lock_refused", False),
        ("P0001", "required_argument_is_null", False),
        ("55P03", "lock timeout", False),
    ),
)
def test_document_config_lock_refusal_translation_is_exact(
    sqlstate: str,
    message: str,
    expected: bool,
) -> None:
    subject = _subject()
    original = RuntimeError(message)
    original.sqlstate = sqlstate  # type: ignore[attr-defined]
    original.diag = SimpleNamespace(message_primary=message)  # type: ignore[attr-defined]
    error = sa.exc.DBAPIError("SELECT config lock", {}, original)

    assert subject._is_document_config_lock_refusal(error) is expected


@pytest.mark.parametrize(
    ("sqlstate", "message", "expected"),
    (
        ("P0001", "worm_blob_lock_refused", True),
        ("42501", "worm_blob_lock_refused", False),
        ("P0001", "document_worm_config_lock_refused", False),
        ("55P03", "lock timeout", False),
    ),
)
def test_blob_lock_refusal_translation_is_exact(
    sqlstate: str,
    message: str,
    expected: bool,
) -> None:
    subject = _subject()
    original = RuntimeError(message)
    original.sqlstate = sqlstate  # type: ignore[attr-defined]
    original.diag = SimpleNamespace(message_primary=message)  # type: ignore[attr-defined]
    error = sa.exc.DBAPIError("SELECT blob lock", {}, original)

    assert subject._is_blob_lock_refusal(error) is expected


@pytest.mark.parametrize(
    ("sqlstate", "message", "expected"),
    (
        ("P0001", "worm_proposed_owner_liveness_refused", True),
        ("42501", "worm_proposed_owner_liveness_refused", False),
        ("P0001", "worm_blob_lock_refused", False),
        ("55P03", "lock timeout", False),
    ),
)
def test_proposed_owner_liveness_refusal_translation_is_exact(
    sqlstate: str,
    message: str,
    expected: bool,
) -> None:
    subject = _subject()
    original = RuntimeError(message)
    original.sqlstate = sqlstate  # type: ignore[attr-defined]
    original.diag = SimpleNamespace(message_primary=message)  # type: ignore[attr-defined]
    error = sa.exc.DBAPIError("SELECT proposed owner liveness", {}, original)

    assert subject._is_proposed_owner_liveness_refusal(error) is expected


async def test_blob_lock_result_must_repeat_the_exact_requested_organization() -> None:
    subject = _subject()
    now = datetime.datetime.now(datetime.UTC)
    row = {
        "blob_sha256": _SHA256,
        "org_id": uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        "bucket": "records",
        "object_key": _SHA256,
        "object_version_id": "exact-version",
        "worm_locked": True,
        "worm_enforced_mode": "GOVERNANCE",
        "worm_asserted_retain_until": now + datetime.timedelta(days=30),
        "worm_asserted_at": now,
        "worm_retain_until": now + datetime.timedelta(days=30),
        "worm_retention_verified_at": now,
        "worm_legal_hold": False,
        "worm_legal_hold_verified_at": now,
        "purged_at": None,
        "purge_execution_id": None,
    }

    class Result:
        def mappings(self) -> Result:
            return self

        def one_or_none(self) -> dict[str, Any]:
            return row

    class Session:
        class NestedTransaction:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_args: Any) -> None:
                return None

        def begin_nested(self) -> NestedTransaction:
            return self.NestedTransaction()

        async def execute(self, *_args: Any, **_kwargs: Any) -> Result:
            return Result()

    with pytest.raises(subject.WormOwnerIntegrityError):
        await subject._lock_blob(
            Session(),
            org_id=_ORG_ID,
            blob_sha256=_SHA256,
        )


def test_provider_observation_can_report_an_already_expired_exact_version() -> None:
    """Task 4 must be able to observe and ratchet an expired provider version."""
    expired = datetime.datetime(2024, 2, 29, tzinfo=datetime.UTC)

    observed = WormObjectState(
        locator=_LOCATOR,
        mode="GOVERNANCE",
        retain_until=expired,
        legal_hold=False,
        read_at=_READ_AT,
    )

    assert observed.retain_until == expired
    assert observed.read_at == _READ_AT


@pytest.mark.parametrize(
    "overrides",
    (
        {"basis_date": datetime.date.max, "duration": "P1Y"},
        {"duration": "P999999999999999999999Y"},
        {"worm_lock_period": "P999999999999999999999D"},
    ),
    ids=("calendar-year-overflow", "duration-overflow", "worm-period-overflow"),
)
def test_period_overflow_is_a_bounded_owner_integrity_failure(
    overrides: dict[str, Any],
) -> None:
    subject = _subject()

    with pytest.raises(subject.WormOwnerIntegrityError) as error:
        owner = _owner(**overrides)
        subject.owner_requirement(owner)

    assert str(error.value) == "invalid WORM owner state"


def _preflight_record(
    subject: ModuleType,
    *,
    owner_id: uuid.UUID,
    blob_sha256: str,
) -> Any:
    return subject.ProposedRecordEvidence(
        owner_id=owner_id,
        record_id=uuid.uuid4(),
        org_id=_ORG_ID,
        blob_sha256=blob_sha256,
    )


def _preflight_document(
    subject: ModuleType,
    *,
    owner_id: uuid.UUID,
    blob_sha256: str,
) -> Any:
    return subject.ProposedDocumentSource(
        owner_id=owner_id,
        document_id=uuid.uuid4(),
        org_id=_ORG_ID,
        blob_sha256=blob_sha256,
        authority_kind="POLICY",
        authority_id=uuid.uuid4(),
    )


@pytest.mark.parametrize(
    "case",
    (
        "DUPLICATE_ASSOCIATION",
        "DUPLICATE_SHA_CROSS_DOMAIN",
        "DUPLICATE_SHA_AND_DESTINATION",
        "NON_CONTENT_ADDRESSED_KEY",
        "WRONG_DOMAIN_BUCKET",
    ),
)
async def test_batch_rejects_ambiguous_or_noncanonical_graph_before_any_lock_or_callback(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    subject = _subject()
    sha_a = "a" * 64
    sha_b = "b" * 64
    owner_a = uuid.uuid4()
    callback_calls = 0
    lock_calls = 0

    async def callback() -> Any:
        nonlocal callback_calls
        callback_calls += 1
        raise AssertionError("preflight failure must precede storage")

    async def forbidden_pack_lock(*_args: Any, **_kwargs: Any) -> None:
        nonlocal lock_calls
        lock_calls += 1
        raise AssertionError("preflight failure must precede every database lock")

    if case == "DUPLICATE_ASSOCIATION":
        proposals = [
            subject.NewCopyProposal(
                owner=_preflight_record(subject, owner_id=owner_a, blob_sha256=sha_a),
                target_bucket="records",
                target_key=sha_a,
                promote=callback,
            ),
            subject.NewCopyProposal(
                owner=_preflight_record(subject, owner_id=owner_a, blob_sha256=sha_b),
                target_bucket="records",
                target_key=sha_b,
                promote=callback,
            ),
        ]
    elif case == "DUPLICATE_SHA_CROSS_DOMAIN":
        proposals = [
            subject.NewCopyProposal(
                owner=_preflight_record(subject, owner_id=owner_a, blob_sha256=sha_a),
                target_bucket="records",
                target_key=sha_a,
                promote=callback,
            ),
            subject.NewCopyProposal(
                owner=_preflight_document(
                    subject,
                    owner_id=uuid.uuid4(),
                    blob_sha256=sha_a,
                ),
                target_bucket="documents",
                target_key=sha_a,
                promote=callback,
            ),
        ]
    elif case == "DUPLICATE_SHA_AND_DESTINATION":
        proposals = [
            subject.NewCopyProposal(
                owner=_preflight_record(subject, owner_id=owner_a, blob_sha256=sha_a),
                target_bucket="records",
                target_key=sha_a,
                promote=callback,
            ),
            subject.NewCopyProposal(
                owner=_preflight_record(
                    subject,
                    owner_id=uuid.uuid4(),
                    blob_sha256=sha_a,
                ),
                target_bucket="records",
                target_key=sha_a,
                promote=callback,
            ),
        ]
    elif case == "NON_CONTENT_ADDRESSED_KEY":
        proposals = [
            subject.NewCopyProposal(
                owner=_preflight_record(subject, owner_id=owner_a, blob_sha256=sha_a),
                target_bucket="records",
                target_key="not-the-content-sha",
                promote=callback,
            )
        ]
    else:
        assert case == "WRONG_DOMAIN_BUCKET"
        proposals = [
            subject.NewCopyProposal(
                owner=_preflight_record(subject, owner_id=owner_a, blob_sha256=sha_a),
                target_bucket="documents",
                target_key=sha_a,
                promote=callback,
            )
        ]

    monkeypatch.setattr(subject, "lock_pack_build_shared", forbidden_pack_lock)

    with pytest.raises(subject.WormOwnerIntegrityError) as error:
        await subject.protect_proposed_owners(object(), proposals=proposals)

    assert str(error.value) == "invalid WORM owner state"
    assert callback_calls == 0
    assert lock_calls == 0


@pytest.mark.parametrize(
    "control",
    ("SAME_UUID_DIFFERENT_FAMILY", "MULTIPLE_EXISTING_SAME_SHA"),
)
async def test_batch_preflight_does_not_overreject_valid_shared_identities(
    monkeypatch: pytest.MonkeyPatch,
    control: str,
) -> None:
    subject = _subject()
    shared_id = uuid.uuid4()
    sha_a = "a" * 64
    sha_b = "b" * 64
    coordination_calls = 0
    callback_calls = 0

    async def coordination_sentinel(*_args: Any, **_kwargs: Any) -> None:
        nonlocal coordination_calls
        coordination_calls += 1
        raise RuntimeError("valid-preflight-reached-coordination")

    async def callback() -> Any:
        nonlocal callback_calls
        callback_calls += 1
        raise AssertionError("coordination sentinel must stop before callback")

    if control == "SAME_UUID_DIFFERENT_FAMILY":
        proposals = [
            subject.NewCopyProposal(
                owner=_preflight_record(subject, owner_id=shared_id, blob_sha256=sha_a),
                target_bucket="records",
                target_key=sha_a,
                promote=callback,
            ),
            subject.NewCopyProposal(
                owner=_preflight_document(subject, owner_id=shared_id, blob_sha256=sha_b),
                target_bucket="documents",
                target_key=sha_b,
                promote=callback,
            ),
        ]
    else:
        proposals = [
            subject.ExistingVersionProposal(
                owner=_preflight_record(
                    subject,
                    owner_id=uuid.uuid4(),
                    blob_sha256=sha_a,
                )
            ),
            subject.ExistingVersionProposal(
                owner=_preflight_record(
                    subject,
                    owner_id=uuid.uuid4(),
                    blob_sha256=sha_a,
                )
            ),
        ]

    monkeypatch.setattr(subject, "lock_pack_build_shared", coordination_sentinel)
    with pytest.raises(RuntimeError, match="valid-preflight-reached-coordination"):
        await subject.protect_proposed_owners(object(), proposals=proposals)

    assert coordination_calls == 1
    assert callback_calls == 0
