"""S-records-C unit proof for ``_grant_from_role``: a SYSTEM-finest role grant is NOT clamped by a
bound Process-Owner's PROCESS ``bound_scope`` (so a bound owner's ``clauseMap.read`` stays SYSTEM,
matching the SYSTEM clause-map resource), while a PARAMETERIZED (PROCESS) template is still
concretized by the bound_scope exactly as before. Pure unit — no DB; in-memory ORM objects."""

from __future__ import annotations

import pytest

from easysynq_api.db.models.role import RoleAssignment, RoleGrant
from easysynq_api.domain.authz import ResolvedGrant, ScopeLevel
from easysynq_api.services.authz.repository import _grant_from_role

pytestmark = pytest.mark.unit

# A bound Process-Owner's minted scope (the S-owner-assignment marker is PDP-ignored; here it must
# also be ignored by the SYSTEM-grant exemption).
_PROCESS_BOUND = {
    "level": "PROCESS",
    "selector": {"process_ids": ["p1"]},
    "managed_by": "owner_assignment",
}
_PROCESS_TEMPLATE = {"level": "PROCESS", "selector": {"process_id": ":assignment_process"}}


def _resolve(scope_template: dict, bound_scope: dict | None) -> ResolvedGrant:
    grant = RoleGrant(scope_template=scope_template)
    assignment = RoleAssignment(bound_scope=bound_scope)
    return _grant_from_role("Process Owner", grant, assignment)


def test_system_grant_not_clamped_by_process_bound_scope() -> None:
    # The fix: clauseMap.read is SYSTEM-finest, so a PROCESS bound_scope must NOT narrow it (which
    # would make it unsatisfiable against the SYSTEM clause-map resource → GET /clauses 403).
    resolved = _resolve({"level": "SYSTEM"}, _PROCESS_BOUND)
    assert resolved.level is ScopeLevel.SYSTEM


def test_process_template_still_concretized_by_bound_scope() -> None:
    # Unchanged: a parameterized PROCESS template is still concretized by the concrete bound_scope.
    resolved = _resolve(_PROCESS_TEMPLATE, _PROCESS_BOUND)
    assert resolved.level is ScopeLevel.PROCESS
    assert resolved.selector.get("process_ids") == ["p1"]


def test_process_template_defers_to_system_bound_scope() -> None:
    # Unchanged common case: a PROCESS template under a SYSTEM bound_scope still resolves to SYSTEM.
    resolved = _resolve(_PROCESS_TEMPLATE, {"level": "SYSTEM"})
    assert resolved.level is ScopeLevel.SYSTEM


def test_system_grant_without_bound_scope_stays_system() -> None:
    # No bound_scope → the role's own SYSTEM scope_template is used (also SYSTEM).
    resolved = _resolve({"level": "SYSTEM"}, None)
    assert resolved.level is ScopeLevel.SYSTEM


def test_levelless_template_defers_to_bound_scope() -> None:
    # Conservative direction: only an EXPLICIT level=="SYSTEM" template is exempt. A level-less
    # template is NOT silently treated as SYSTEM, so it defers to the bound_scope (no reachable
    # grant has this shape, but the exemption must not widen the unsafe direction).
    resolved = _resolve({}, _PROCESS_BOUND)
    assert resolved.level is ScopeLevel.PROCESS
