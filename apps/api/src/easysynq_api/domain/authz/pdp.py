"""The Policy Decision Point — a pure function implementing register R3 verbatim.

`authorize(...)` answers "may principal P perform action A on target X in context C?"
It is **deny-by-default** and **deny-always-wins**. The grants are pre-gathered and
pre-filtered to the requested permission key by the PEP/repository; this function does
the scope/predicate matching and the precedence resolution. No DB, no I/O — every
acceptance proof drives it with hand-built inputs (doc 18 §5.2, §7 S2 row).

Resolution order (doc 07 §6.3, register R3):
  1. scope-filter: keep grants whose scope matches X and whose predicates pass
     (predicates only ever *narrow* — AZ-INV-8).
  2. any matching DENY -> DENY immediately (deny-wins; independent of specificity/SoD).
  3. SoD constraints (no-op in S2; gate wired in S5) — independent of scope.
  4. no matching ALLOW -> DENY (deny-by-default).
  5. specificity breaks ALLOW-vs-ALLOW ties only (provenance + which predicate applies);
     a per-user override outranks a role grant within the same level.
  6. sig-hook step-up gate (v1: authenticated session) -> ALLOW.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence
from typing import Any

from .types import (
    Decision,
    Effect,
    RequestContext,
    ResolvedGrant,
    ResourceContext,
    ScopeLevel,
    specificity,
)


def _as_set(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, Iterable):
        return frozenset(str(v) for v in value)
    return frozenset({str(value)})


def _as_dt(value: Any) -> datetime.datetime | None:
    if value is None:
        return None
    dt = (
        value
        if isinstance(value, datetime.datetime)
        else datetime.datetime.fromisoformat(str(value))
    )
    # A predicate value may arrive naive (e.g. a bare ISO string in JSONB). Treat naive as UTC
    # so it compares cleanly against the tz-aware request clock instead of raising TypeError.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.UTC)


def _is_read_action(permission_key: str) -> bool:
    """Actions a ``read_only`` predicate permits: any read/print-controlled (doc 07 §5.4)."""
    _, _, action = permission_key.partition(".")
    if action in ("print_controlled",):
        return True
    return any(seg == "read" or seg.startswith("read_") for seg in action.split("."))


def _matches_scope(grant: ResolvedGrant, resource: ResourceContext) -> bool:
    """Structural scope match (doc 07 §5.2). DENY uses the *same* match rule as ALLOW —
    for PROCESS that makes deny conservative (matches if linked to any in-scope process)."""
    sel = grant.selector or {}
    level = grant.level

    if level is ScopeLevel.SYSTEM:
        return True
    if level is ScopeLevel.FRAMEWORK:
        fw = sel.get("framework_id")
        return fw is not None and resource.framework_id == fw
    if level is ScopeLevel.PROCESS:
        scoped = _as_set(sel.get("process_id") or sel.get("process_ids"))
        return bool(scoped and (scoped & resource.process_ids))
    if level is ScopeLevel.FOLDER:
        grant_path = sel.get("folder_path")
        rp = resource.folder_path
        if grant_path is None or rp is None:
            return False
        # ltree ancestor / subtree-prefix (register R6): /SOPs covers /SOPs/Purchasing.
        return rp == grant_path or rp.startswith(f"{grant_path}.")
    if level is ScopeLevel.DOC_CLASS:
        dc = sel.get("document_level")
        if dc is None or resource.document_level is None or resource.document_level != dc:
            return False
        if "kind" in sel and sel["kind"] != resource.kind:
            return False
        return not ("concrete_type" in sel and sel["concrete_type"] != resource.concrete_type)
    if level is ScopeLevel.ARTIFACT:
        aid = sel.get("artifact_id")
        return aid is not None and resource.artifact_id == aid
    return False


def _predicates_pass(
    grant: ResolvedGrant,
    resource: ResourceContext,
    context: RequestContext,
    permission_key: str,
) -> bool:
    """ABAC predicate gate — narrowing only (AZ-INV-8). A predicate can never widen a
    grant; failing any predicate drops the grant from the matching set (doc 07 §5.1/§5.4)."""
    p = grant.predicates or {}

    valid_from = _as_dt(p.get("valid_from"))
    valid_until = _as_dt(p.get("valid_until"))
    if valid_from is not None and context.now < valid_from:
        return False
    if valid_until is not None and context.now > valid_until:
        return False

    if p.get("read_only") and not _is_read_action(permission_key):
        return False

    lifecycle = p.get("lifecycle_state")
    if lifecycle is not None:
        allowed = lifecycle if isinstance(lifecycle, (list, tuple, set)) else [lifecycle]
        if resource.lifecycle_state not in allowed:
            return False

    requirement_source = p.get("requirement_source")
    if requirement_source is not None and resource.requirement_source != requirement_source:
        return False

    ip_allow = p.get("ip_allow")
    if ip_allow and (context.source_ip is None or context.source_ip not in set(ip_allow)):
        return False

    return True


def _rank(grant: ResolvedGrant) -> tuple[int, int]:
    """Tie-break key: more-specific scope first, then a per-user override over a role
    grant within the same level (doc 07 §6.3 steps 5-6)."""
    return (specificity(grant.level), 1 if grant.is_override else 0)


def authorize(
    grants: Sequence[ResolvedGrant],
    permission_key: str,
    resource: ResourceContext,
    context: RequestContext,
    *,
    sig_hook: bool = False,
    sod: Sequence[Any] = (),
) -> Decision:
    """Resolve a single access question to ALLOW/DENY. See module docstring for the
    ordered pipeline. ``grants`` must already be filtered to ``permission_key``."""
    # 1. scope-filter (+ narrowing predicates).
    matching = [
        g
        for g in grants
        if _matches_scope(g, resource) and _predicates_pass(g, resource, context, permission_key)
    ]

    # 2. explicit DENY short-circuits — deny-wins, regardless of specificity or SoD.
    denies = [g for g in matching if g.effect is Effect.DENY]
    if denies:
        chosen = max(denies, key=_rank)
        return Decision(allow=False, reason="explicit_deny", source=chosen.source)

    # 3. SoD constraints (independent of scope). No-op in S2 — the gate lands in S5.
    sod_block = _evaluate_sod(sod, permission_key, resource, context)
    if sod_block is not None:
        return Decision(allow=False, reason="sod_violation", source=sod_block)

    # 4. allow-present? else deny-by-default (AZ-INV-1).
    allows = [g for g in matching if g.effect is Effect.ALLOW]
    if not allows:
        return Decision(allow=False, reason="deny_by_default", source=None)

    # 5. specificity breaks ALLOW-vs-ALLOW ties (provenance + which constraint applies).
    winner = max(allows, key=_rank)

    # 6. sig-hook step-up gate (v1 policy = authenticated session).
    if sig_hook and not context.step_up_satisfied:
        return Decision(allow=False, reason="step_up_required", source=winner.source)

    require_reason = bool((winner.predicates or {}).get("require_reason"))
    return Decision(allow=True, reason="allow", source=winner.source, require_reason=require_reason)


def _evaluate_sod(
    sod: Sequence[Any],
    permission_key: str,
    resource: ResourceContext,
    context: RequestContext,
) -> str | None:
    """Separation-of-duties gate. **No-op in S2** (constraints are seeded and enforced
    against immutable version/audit history in S5 — doc 18 §7 S5 row, doc 07 §7). The
    seam exists so S5 wires the check here without touching the precedence pipeline."""
    return None
