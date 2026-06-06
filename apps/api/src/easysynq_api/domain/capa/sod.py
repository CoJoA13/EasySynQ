"""Severity-aware SoD-4 (CAPA verifier ≠ action implementer) — pure predicates (slice S-capa-3;
doc 10 §6.2/§6.3, doc 07 §7, decisions-register R39).

Mirrors ``domain/records/disposition.py::self_disposition_blocked`` (the SoD-6 precedent): pure (no
DB), so unit-testable in isolation. The service computes the implementer set under the ``capa`` FOR
UPDATE and calls :func:`capa_self_verify_blocked` **unconditionally before any permission
short-circuit** — a SYSTEM ``capa.verify`` grant never bypasses SoD (audited-then-409). Severity is
the discriminator: **Critical / Major HARD-enforce** (verifier ≠ implementer, always); **Minor**
respects the per-org ``allow_capa_self_verify`` flag (default OFF → still enforced; flipping it lets
a small/solo install self-verify a Minor CAPA, audited).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from ...db.models._capa_enums import CapaCloseState, NcSeverity

# Critical / Major never relax SoD-4 — only Minor honours the per-org flag (doc 10 §6.3).
_HARD_SEVERITIES = frozenset({NcSeverity.Critical, NcSeverity.Major})


def derive_implementer_ids(
    stages: Iterable[tuple[CapaCloseState, uuid.UUID, Mapping[str, Any] | None]],
) -> set[uuid.UUID]:
    """The set of users who implemented this CAPA's corrective action across the **whole** stage
    trail (every cycle — the strictest SoD: anyone who ever did an action cannot be its independent
    verifier). From an iterable of ``(stage, created_by, content_block)`` tuples:

    - the ``created_by`` of every **Implement** stage (whoever recorded that the actions were done),
      and
    - each **ActionPlan** block's ``action_items[].owner`` that parses as a UUID (a free-text owner
      like "diego" is an advisory label, not an identity, so it is ignored — it can't collide with a
      real ``app_user.id``).

    Deliberately NOT the ActionPlan stage's ``created_by``: in S-capa-2 that is the APPROVER (the QM
    who signed the plan), and approving a plan is a distinct duty from implementing it — counting it
    would wrongly bar an approving QM from verifying, and a single-QM install could never close a
    Major+ CAPA. SoD-4 is verifier ≠ *implementer*, not verifier ≠ approver.
    """
    out: set[uuid.UUID] = set()
    for stage, created_by, block in stages:
        if stage is CapaCloseState.Implement:
            out.add(created_by)
        if stage is CapaCloseState.ActionPlan:
            for item in (block or {}).get("action_items") or []:
                owner = item.get("owner") if isinstance(item, dict) else None
                if isinstance(owner, str):
                    try:
                        out.add(uuid.UUID(owner))
                    except ValueError:
                        continue
    return out


def capa_self_verify_blocked(
    verifier_id: uuid.UUID,
    implementer_ids: set[uuid.UUID],
    *,
    severity: NcSeverity,
    allow_capa_self_verify: bool,
) -> bool:
    """``True`` iff the verifier may NOT verify (SoD-4): the verifier is among the CAPA's
    implementers AND the org has not relaxed the rule for this severity. Critical / Major always
    block a self-verify; Minor blocks only when ``allow_capa_self_verify`` is False (the strict
    default). Pure (no DB) so the table is unit-testable; the caller raises 409 ``sod_self_verify``
    when this returns True, BEFORE any permission short-circuit (the SoD-6 precedent)."""
    if verifier_id not in implementer_ids:
        return False
    if severity in _HARD_SEVERITIES:
        return True
    return not allow_capa_self_verify
