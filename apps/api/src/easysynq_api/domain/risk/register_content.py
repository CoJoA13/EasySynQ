"""The versioned clause-6.1 risk register content (S-risk-1b, R49) — pure, no I/O.

``build_register`` produces the canonical dict that is BOTH the register version's WORM source blob
(``rfc8785.dumps`` — JCS) AND the ``metadata_snapshot.risk_register`` fold, so the bytes and the
snapshot can never diverge (the S-rec-3 invariant; the ``domain/objectives/commitment`` sibling).
The scoring ``criteria`` are frozen INTO the snapshot so the live band resolves against the
GOVERNING version's frozen criteria, never a live module constant — the L2 derive-and-freeze (a
code-level band-threshold edit cannot re-grade a published row). The live satellite rows are read
back as-is; only the *band* basis is resolved from ``governing`` (the working risk_rating itself is
re-derived on every write, S-risk-1).
"""

from __future__ import annotations

from typing import Any

from easysynq_api.db.models._risk_enums import ScoringMethod
from easysynq_api.db.models._vault_enums import VersionState
from easysynq_api.domain.risk.rules import default_criteria


def criteria_for_methods(methods: set[ScoringMethod]) -> dict[str, Any]:
    """The frozen criteria map for the scoring methods PRESENT in a register —
    ``{method_value: default_criteria(method)}``. Keyed by the ``ScoringMethod.value`` so
    ``resolve_criteria`` can look it up from a row's ``scoring_method.value`` on read-back. Only the
    present methods are frozen so an unchanged-rows re-publish is byte-stable (the criteria map
    depends only on the rows, not on the full enum)."""
    return {m.value: default_criteria(m) for m in methods}


def build_register(*, rows: list[dict[str, Any]], criteria: dict[str, Any]) -> dict[str, Any]:
    """The canonical register content frozen at publish. Rows are sorted by ``id`` for a stable,
    reproducible serialization (rfc8785/JCS canonicalizes each row's KEYS; this canonicalizes the
    LIST order). ``criteria`` is the per-method map from ``criteria_for_methods``. Mirrors
    ``build_commitment`` — the SAME dict becomes the source blob AND the snapshot fold."""
    return {
        "rows": sorted(rows, key=lambda r: str(r["id"])),
        "criteria": criteria,
    }


def resolve_criteria(
    governing: dict[str, Any] | None, scoring_method: ScoringMethod
) -> dict[str, Any]:
    """The read-back switch (the ``resolve_commitment(governing)`` precedent): grade the live band
    against the GOVERNING version's FROZEN per-method criteria when an Effective version exists,
    else the v1 code default (pre-first-release, or a method minted after the freeze — a row added
    in an open revision whose method the prior Effective snapshot never carried). Every band read
    resolves through here, so a code band edit can never re-grade the live register (R49 L2)."""
    if governing is not None:
        frozen = governing.get("criteria")
        if isinstance(frozen, dict):
            entry = frozen.get(scoring_method.value)
            if isinstance(entry, dict):
                return entry
    return default_criteria(scoring_method)


def register_needs_freeze(
    *,
    latest_version_state: VersionState | None,
    latest_register: dict[str, Any] | None,
    working: dict[str, Any],
) -> bool:
    """True when publish must mint a NEW frozen register version (the ``commitment_needs_freeze``
    switch).

    - no version at all → first publish
    - latest is not a Draft → a revision (the latest version is the governing Effective one, whose
      snapshot CARRIES a register; typically Effective; the FSM, not this predicate, guards the
      other states)
    - the latest Draft's frozen register ≠ the working register → an edit happened since the last
      freeze (or the latest Draft is a register-less legacy byte-version) → re-freeze so the
      approver always signs the CURRENT register.

    Equal dicts on a Draft → skip (the no-edit re-publish after request_changes dedups). Both sides
    MUST come from ``build_register`` — never a hand-built dict (string/order canonicalization
    differs)."""
    if latest_version_state is None:
        return True
    if latest_version_state is not VersionState.Draft:
        return True
    return latest_register != working
