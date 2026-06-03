"""``resolve_retention`` — the pure retention-policy precedence + basis-date computation
(slice S-rec-1, doc 06 §5.1).

A record's retention policy is resolved at capture by most-specific-wins precedence:

    per-record override → process default → clause default → record-type default → system default

The system default (the seeded ``"System Default Retention"``) is always present, so the NOT-NULL
``record.retention_policy_id`` always resolves. Per-tier *matching* (which policy ``applies_to`` a
given record_type / clause / process, with a smallest-``policy_id`` tiebreak) is the repository's
job — an index-backed query — so this function stays pure: it receives at most one already-resolved
``PolicyCandidate`` per tier and only applies precedence + computes the basis date.

The resolved ``policy_id`` + ``retention_basis_date`` are **snapshotted** onto the record at capture
and never mutated — the one-way ratchet (doc 06 §5.2). ``basis = captured_at`` → the UTC capture
date; an ``event:*`` basis has no known event date at capture, so the basis date is ``None`` (the
deferred Beat sweep fills it when the event fires — the index already exists).
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid

from easysynq_api.db.models._retention_enums import RetentionBasis


@dataclasses.dataclass(frozen=True, slots=True)
class PolicyCandidate:
    """One resolved policy for a tier — its id and the basis that drives the basis-date compute."""

    policy_id: uuid.UUID
    basis: RetentionBasis


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionResolutionInput:
    captured_at: datetime.datetime
    system_default: PolicyCandidate  # always present (the seeded fallback)
    record_type_default: PolicyCandidate | None = None
    clause_default: PolicyCandidate | None = None
    process_default: PolicyCandidate | None = None
    override: PolicyCandidate | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RetentionResolution:
    policy_id: uuid.UUID
    tier: str  # override | process | clause | record_type | system_default
    basis: RetentionBasis
    retention_basis_date: datetime.date | None


def _basis_date(basis: RetentionBasis, captured_at: datetime.datetime) -> datetime.date | None:
    if basis is RetentionBasis.CAPTURED_AT:
        dt = captured_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        return dt.astimezone(datetime.UTC).date()
    # event:* bases — no event date is known at capture (doc 06 §5.1); the sweep fills it later.
    return None


def _resolution(
    tier: str, candidate: PolicyCandidate, captured_at: datetime.datetime
) -> RetentionResolution:
    return RetentionResolution(
        policy_id=candidate.policy_id,
        tier=tier,
        basis=candidate.basis,
        retention_basis_date=_basis_date(candidate.basis, captured_at),
    )


def resolve_retention(inp: RetentionResolutionInput) -> RetentionResolution:
    """Apply the doc-06 §5.1 most-specific-wins precedence; always resolves (system default)."""
    for tier, candidate in (
        ("override", inp.override),
        ("process", inp.process_default),
        ("clause", inp.clause_default),
        ("record_type", inp.record_type_default),
    ):
        if candidate is not None:
            return _resolution(tier, candidate, inp.captured_at)
    return _resolution("system_default", inp.system_default, inp.captured_at)
