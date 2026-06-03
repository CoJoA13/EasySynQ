"""S-rec-1 unit proofs — the pure retention precedence + basis-date compute (doc 06 §5.1). No DB.

The applies_to *matching* + smallest-id tiebreak within a tier is the repository's job (index-backed
query); this function receives at most one already-resolved candidate per tier and only applies
precedence + computes the basis date — so these tests hand-build candidates per tier.
"""

from __future__ import annotations

import datetime
import uuid

from easysynq_api.db.models._retention_enums import RetentionBasis
from easysynq_api.domain.records.retention import (
    PolicyCandidate,
    RetentionResolutionInput,
    resolve_retention,
)

_AT = datetime.datetime(2026, 6, 3, 15, 30, tzinfo=datetime.UTC)


def _cand(n: int, basis: RetentionBasis = RetentionBasis.CAPTURED_AT) -> PolicyCandidate:
    return PolicyCandidate(policy_id=uuid.UUID(int=n), basis=basis)


_SYSTEM = _cand(5)


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


def test_basis_event_yields_null_date() -> None:
    out = resolve_retention(
        RetentionResolutionInput(
            captured_at=_AT,
            system_default=PolicyCandidate(uuid.UUID(int=5), RetentionBasis.EMPLOYMENT_END),
        )
    )
    assert out.retention_basis_date is None
    assert out.basis is RetentionBasis.EMPLOYMENT_END
