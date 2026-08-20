"""S-rec-1 unit proofs — the pure retention precedence + basis-date compute (doc 06 §5.1). No DB.

The applies_to *matching* + smallest-id tiebreak within a tier is the repository's job (index-backed
query); this function receives at most one already-resolved candidate per tier and only applies
precedence + computes the basis date — so these tests hand-build candidates per tier.
"""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace

import pytest

from easysynq_api.db.models._retention_enums import RetentionBasis
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.domain.records.retention import (
    PolicyCandidate,
    RetentionResolutionInput,
    resolve_retention,
)
from easysynq_api.problems import ProblemException
from easysynq_api.services.records import retention_policies
from easysynq_api.services.records import service as record_service

_AT = datetime.datetime(2026, 6, 3, 15, 30, tzinfo=datetime.UTC)


def _cand(n: int, basis: RetentionBasis = RetentionBasis.CAPTURED_AT) -> PolicyCandidate:
    return PolicyCandidate(policy_id=uuid.UUID(int=n), basis=basis)


_SYSTEM = _cand(5)


@pytest.mark.parametrize(
    "duration",
    ("P999999999999999999999Y", "P999999999999999999999D"),
)
def test_policy_duration_overflow_is_rejected_as_bounded_validation(duration: str) -> None:
    with pytest.raises(ProblemException) as error:
        retention_policies._validate_duration("duration", duration)

    assert error.value.status == 422
    assert error.value.code == "validation_error"
    assert error.value.errors == [
        {
            "field": "duration",
            "code": "invalid_duration",
            "message": f"Not an ISO-8601 duration: {duration!r}",
        }
    ]


def test_policy_worm_period_overflow_is_rejected_as_bounded_validation() -> None:
    duration = "P999999999999999999999D"

    with pytest.raises(ProblemException) as error:
        retention_policies._validate_worm_lock(duration, "P1Y")

    assert error.value.status == 422
    assert error.value.code == "validation_error"
    assert error.value.errors == [
        {
            "field": "worm_lock_period",
            "code": "invalid_duration",
            "message": f"Not an ISO-8601 duration: {duration!r}",
        }
    ]


def test_override_wins_over_all_lower_tiers() -> None:
    out = resolve_retention(
        RetentionResolutionInput(
            captured_at=_AT,
            system_default=_SYSTEM,
            record_type_default=_cand(4),
            clause_default=_cand(3),
            process_default=_cand(2),
            override=_cand(1),
        )
    )
    assert out.tier == "override"
    assert out.policy_id == uuid.UUID(int=1)


def test_process_tier_when_no_override() -> None:
    out = resolve_retention(
        RetentionResolutionInput(
            captured_at=_AT,
            system_default=_SYSTEM,
            record_type_default=_cand(4),
            clause_default=_cand(3),
            process_default=_cand(2),
        )
    )
    assert out.tier == "process"
    assert out.policy_id == uuid.UUID(int=2)


def test_clause_tier_when_no_override_or_process() -> None:
    out = resolve_retention(
        RetentionResolutionInput(
            captured_at=_AT,
            system_default=_SYSTEM,
            record_type_default=_cand(4),
            clause_default=_cand(3),
        )
    )
    assert out.tier == "clause"
    assert out.policy_id == uuid.UUID(int=3)


def test_record_type_default_tier() -> None:
    out = resolve_retention(
        RetentionResolutionInput(
            captured_at=_AT, system_default=_SYSTEM, record_type_default=_cand(4)
        )
    )
    assert out.tier == "record_type"
    assert out.policy_id == uuid.UUID(int=4)


def test_falls_through_to_system_default() -> None:
    # No higher tier matched → the seeded fallback (guarantees the NOT-NULL retention_policy_id).
    out = resolve_retention(RetentionResolutionInput(captured_at=_AT, system_default=_SYSTEM))
    assert out.tier == "system_default"
    assert out.policy_id == uuid.UUID(int=5)


def test_basis_captured_at_yields_utc_date() -> None:
    # A non-UTC captured_at normalises to the UTC calendar date.
    at = datetime.datetime(
        2026, 6, 3, 23, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=-5))
    )
    out = resolve_retention(
        RetentionResolutionInput(
            captured_at=at,
            system_default=PolicyCandidate(uuid.UUID(int=5), RetentionBasis.CAPTURED_AT),
        )
    )
    # 2026-06-03 23:30 -05:00 == 2026-06-04 04:30 UTC → date 2026-06-04.
    assert out.retention_basis_date == datetime.date(2026, 6, 4)
    assert out.retention_basis_provisional is False


def test_basis_event_uses_capture_date_as_a_provisional_physical_floor() -> None:
    out = resolve_retention(
        RetentionResolutionInput(
            captured_at=_AT,
            system_default=PolicyCandidate(uuid.UUID(int=5), RetentionBasis.EMPLOYMENT_END),
        )
    )
    assert out.retention_basis_date == _AT.date()
    assert out.retention_basis_provisional is True
    assert out.basis is RetentionBasis.EMPLOYMENT_END


async def test_capture_persists_the_resolvers_provisional_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution = resolve_retention(
        RetentionResolutionInput(
            captured_at=_AT,
            system_default=PolicyCandidate(
                uuid.UUID(int=5),
                RetentionBasis.EMPLOYMENT_END,
            ),
        )
    )

    class CaptureSession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, value: object) -> None:
            self.added.append(value)

        async def flush(self) -> None:
            for value in self.added:
                if isinstance(value, DocumentedInformation) and value.id is None:
                    value.id = uuid.UUID(int=101)

    async def get_framework(*_args: object) -> object:
        return SimpleNamespace(id=uuid.UUID(int=102))

    async def resolve_source(*_args: object, **_kwargs: object) -> tuple[None, bool]:
        return None, False

    async def resolve_capture(*_args: object, **_kwargs: object) -> object:
        return resolution

    async def allocate_seq(*_args: object) -> int:
        return 1

    async def attach_evidence(*_args: object, **_kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr(record_service, "_now", lambda: _AT)
    monkeypatch.setattr(record_service.vault_repo, "get_framework", get_framework)
    monkeypatch.setattr(record_service, "_resolve_source_version", resolve_source)
    monkeypatch.setattr(record_service, "resolve_capture_retention", resolve_capture)
    monkeypatch.setattr(record_service.vault_repo, "allocate_seq", allocate_seq)
    monkeypatch.setattr(record_service, "_attach_evidence", attach_evidence)

    session = CaptureSession()
    actor = AppUser(
        id=uuid.UUID(int=103),
        org_id=uuid.UUID(int=104),
        keycloak_subject="provisional-capture-proof",
    )
    record = await record_service.capture_record(  # type: ignore[arg-type]
        session,
        actor,
        record_type="EVIDENCE",
        title="Provisional event-basis capture",
        _commit=False,
    )

    assert resolution.retention_basis_provisional is True
    assert record.retention_basis_date == resolution.retention_basis_date
    assert record.retention_basis_provisional == resolution.retention_basis_provisional
