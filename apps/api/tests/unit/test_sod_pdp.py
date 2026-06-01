"""S5 unit proofs: the separation-of-duties gate inside the pure PDP (doc 07 §7.1).

Drives ``authorize`` with hand-built SoD constraints (duck-typed via ``SimpleNamespace`` — the PDP
reads ``duty_b``/``duty_a``/``severity``/``target_binding``/``description``). Proves SoD-1 (no
self-approval), SoD-2 (no self-release; approver-release gated by ``allow_approver_release`` while
the author side stays unconditional), that deny-wins precedes SoD, and that SoD is an overlay on a
would-be ALLOW (no ALLOW → deny_by_default, never a leaked duty conflict).
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from easysynq_api.domain.authz import (
    Effect,
    RequestContext,
    ResolvedGrant,
    ResourceContext,
    ScopeLevel,
    authorize,
)

pytestmark = pytest.mark.unit

NOW = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)

SOD1 = SimpleNamespace(
    description="SoD-1",
    duty_a={"permission": "document.edit"},
    duty_b={"permission": "document.approve"},
    target_binding="SAME_VERSION",
    severity="HARD_DENY",
    org_overridable=False,
)
SOD2 = SimpleNamespace(
    description="SoD-2",
    duty_a={"permission": "document.edit"},
    duty_b={"permission": "document.release"},
    target_binding="SAME_VERSION",
    severity="HARD_DENY",
    org_overridable=True,
)


def _allow() -> ResolvedGrant:
    return ResolvedGrant(
        effect=Effect.ALLOW,
        level=ScopeLevel.SYSTEM,
        selector={},
        predicates={},
        source="role:Approver",
    )


def _resource(author: str = "alice", approvers: frozenset[str] = frozenset()) -> ResourceContext:
    return ResourceContext(
        artifact_id="D", version_id="V", author_user_id=author, approver_user_ids=approvers
    )


def _ctx(actor: str, *, allow_approver_release: bool = False) -> RequestContext:
    return RequestContext(
        now=NOW, actor_user_id=actor, allow_approver_release=allow_approver_release
    )


# --- SoD-1: no self-approval -----------------------------------------------------------


def test_sod1_author_cannot_approve_own_version() -> None:
    d = authorize(
        [_allow()], "document.approve", _resource(author="alice"), _ctx("alice"), sod=[SOD1]
    )
    assert d.allow is False
    assert d.reason == "sod_violation"
    assert d.conflicting_duty is not None
    assert d.conflicting_duty["duty_b"] == {"permission": "document.approve"}
    assert d.conflicting_duty["constraint"] == "SoD-1"


def test_sod1_non_author_may_approve() -> None:
    d = authorize(
        [_allow()], "document.approve", _resource(author="alice"), _ctx("bob"), sod=[SOD1]
    )
    assert d.allow is True


# --- SoD-2: no self-release; approver-release behind the flag --------------------------


def test_sod2_author_cannot_release_even_with_flag_on() -> None:
    """The author side is unconditional — ``allow_approver_release`` never rescues it (doc 07)."""
    d = authorize(
        [_allow()],
        "document.release",
        _resource(author="alice", approvers=frozenset({"bob"})),
        _ctx("alice", allow_approver_release=True),
        sod=[SOD2],
    )
    assert d.allow is False
    assert d.reason == "sod_violation"


def test_sod2_approver_release_blocked_when_flag_off() -> None:
    d = authorize(
        [_allow()],
        "document.release",
        _resource(author="alice", approvers=frozenset({"bob"})),
        _ctx("bob", allow_approver_release=False),
        sod=[SOD2],
    )
    assert d.allow is False
    assert d.reason == "sod_violation"


def test_sod2_approver_release_allowed_when_flag_on() -> None:
    d = authorize(
        [_allow()],
        "document.release",
        _resource(author="alice", approvers=frozenset({"bob"})),
        _ctx("bob", allow_approver_release=True),
        sod=[SOD2],
    )
    assert d.allow is True


def test_sod2_third_party_may_release() -> None:
    d = authorize(
        [_allow()],
        "document.release",
        _resource(author="alice", approvers=frozenset({"bob"})),
        _ctx("carol"),
        sod=[SOD2],
    )
    assert d.allow is True


# --- precedence + overlay semantics ----------------------------------------------------


def test_explicit_deny_precedes_sod() -> None:
    """A matching explicit DENY wins before SoD is even evaluated (deny-wins, R3)."""
    deny = ResolvedGrant(
        effect=Effect.DENY, level=ScopeLevel.SYSTEM, selector={}, predicates={}, source="override"
    )
    d = authorize(
        [_allow(), deny], "document.approve", _resource(author="alice"), _ctx("alice"), sod=[SOD1]
    )
    assert d.allow is False
    assert d.reason == "explicit_deny"


def test_sod_does_not_fire_without_a_would_be_allow() -> None:
    """No matching ALLOW → deny_by_default, NOT sod_violation (SoD is an overlay on a would-be
    ALLOW; a user lacking the permission is never told about a duty conflict)."""
    d = authorize([], "document.approve", _resource(author="alice"), _ctx("alice"), sod=[SOD1])
    assert d.allow is False
    assert d.reason == "deny_by_default"


def test_sod_no_match_for_unrelated_permission() -> None:
    """A release constraint must not block an approve (and vice-versa)."""
    d = authorize(
        [_allow()], "document.approve", _resource(author="alice"), _ctx("alice"), sod=[SOD2]
    )
    assert d.allow is True


def test_sod_flag_severity_does_not_hard_block() -> None:
    """FLAG_AND_REQUIRE_REASON is not a hard deny (no MVP constraint uses it)."""
    flag_only = SimpleNamespace(
        description="flag",
        duty_a={"permission": "document.edit"},
        duty_b={"permission": "document.approve"},
        target_binding="SAME_VERSION",
        severity="FLAG_AND_REQUIRE_REASON",
        org_overridable=True,
    )
    d = authorize(
        [_allow()], "document.approve", _resource(author="alice"), _ctx("alice"), sod=[flag_only]
    )
    assert d.allow is True
