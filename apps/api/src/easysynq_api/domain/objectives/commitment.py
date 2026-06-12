"""The versioned Quality-Objective commitment (S-obj-3, clause 6.2).

``build_commitment`` produces the canonical dict that is BOTH the version's WORM source blob
(``rfc8785.dumps`` — JCS) AND the ``metadata_snapshot.objective_commitment`` fold, so the bytes and
the snapshot can never diverge (the S-rec-3 invariant). Decimals serialize as STRINGS (never float)
so the WORM bytes are exact + reproducible. ``current_value`` is the operational rollup OUTSIDE the
version and is deliberately NOT part of the commitment.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid
from decimal import Decimal
from typing import Any

from ...db.models._objective_enums import ObjectiveDirection
from ...db.models._vault_enums import VersionState


def build_commitment(
    *,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    at_risk_threshold: Decimal | None,
    baseline_value: Decimal | None,
    policy_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "target_value": str(target_value),
        "unit": unit,
        "direction": direction.value,
        "due_date": due_date.isoformat(),
        "at_risk_threshold": str(at_risk_threshold) if at_risk_threshold is not None else None,
        "baseline_value": str(baseline_value) if baseline_value is not None else None,
        "policy_id": str(policy_id) if policy_id is not None else None,
    }


@dataclasses.dataclass(frozen=True)
class Commitment:
    """The typed view of a commitment dict (build_commitment's output / a version snapshot's
    ``objective_commitment``) — parsed back to domain types for the pure rules (rules.py compares
    Decimals; feeding it the snapshot's STRINGS would TypeError)."""

    target_value: Decimal
    unit: str
    direction: ObjectiveDirection
    due_date: datetime.date
    at_risk_threshold: Decimal | None
    baseline_value: Decimal | None
    policy_id: uuid.UUID | None


def parse_commitment(snapshot: dict[str, Any]) -> Commitment:
    """The strict inverse of ``build_commitment``. Only ever fed dicts that build_commitment
    minted (the S-obj-4 byte-path guard makes a foreign governing snapshot unconstructible), so a
    malformed dict is a drift-class event — raise, never paper over."""
    return Commitment(
        target_value=Decimal(snapshot["target_value"]),
        unit=str(snapshot["unit"]),
        direction=ObjectiveDirection(snapshot["direction"]),
        due_date=datetime.date.fromisoformat(snapshot["due_date"]),
        at_risk_threshold=(
            Decimal(snapshot["at_risk_threshold"])
            if snapshot["at_risk_threshold"] is not None
            else None
        ),
        baseline_value=(
            Decimal(snapshot["baseline_value"]) if snapshot["baseline_value"] is not None else None
        ),
        policy_id=(uuid.UUID(snapshot["policy_id"]) if snapshot["policy_id"] is not None else None),
    )


def resolve_commitment(
    governing: dict[str, Any] | None,
    *,
    target_value: Decimal,
    unit: str,
    direction: ObjectiveDirection,
    due_date: datetime.date,
    at_risk_threshold: Decimal | None,
    baseline_value: Decimal | None,
    policy_id: uuid.UUID | None,
) -> Commitment:
    """The read-back switch (S-obj-4, O-3): the GOVERNING frozen commitment when one exists, else
    the working-row fields (pre-first-release — bit-identical to the S-obj-3 read). Every grading
    read (register/scorecard/detail/record_measurement) resolves through here so an in-flight
    revision edit can never re-grade the live scorecard (the F-2 deferred half, closed)."""
    if governing is not None:
        return parse_commitment(governing)
    return Commitment(
        target_value=target_value,
        unit=unit,
        direction=direction,
        due_date=due_date,
        at_risk_threshold=at_risk_threshold,
        baseline_value=baseline_value,
        policy_id=policy_id,
    )


def commitment_needs_freeze(
    *,
    latest_version_state: VersionState | None,
    latest_commitment: dict[str, Any] | None,
    working: dict[str, Any],
) -> bool:
    """True when submit must mint a NEW frozen commitment version (S-obj-4).

    - no version at all → first submit (the S-obj-3 path)
    - latest is not a Draft → a revision (the latest version is the governing Effective one,
      whose snapshot CARRIES a commitment — the S-obj-3 ``is None`` guard would invert here;
      typically Effective; the FSM, not this predicate, guards the other states)
    - the latest Draft's commitment ≠ the working commitment → a PATCH happened since the last
      freeze (or the latest Draft is a commitment-less legacy byte-version) → re-freeze so the
      approver always signs the CURRENT commitment.

    Equal dicts on a Draft → skip (the no-edit re-submit after request_changes dedups: T3
    reverted the version_state, the same Draft version re-advances). Both sides MUST come from
    ``build_commitment``/the snapshot it minted — never a hand-built dict (string
    canonicalization differs)."""
    if latest_version_state is None:
        return True
    if latest_version_state is not VersionState.Draft:
        return True
    return latest_commitment != working
