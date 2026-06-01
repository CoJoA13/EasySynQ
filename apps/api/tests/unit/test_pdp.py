"""S2 headline proofs: the PDP is deny-by-default and deny-always-wins (register R3).

Pure unit tests — no DB, no network. They drive ``authorize`` with hand-built grants,
exactly the way doc 18 §5.2 intends the load-bearing proofs to run. The two named
acceptance proofs here are AC#3 (per-user DENY beats role ALLOW) and AC#4 (a system
super-admin is denied content authority by default); the integration suite re-proves
AC#3 through the real DB→PDP path and proves the seed makes AC#4 true.
"""

from __future__ import annotations

import datetime

import pytest

from easysynq_api.domain.authz import (
    Decision,
    Effect,
    RequestContext,
    ResolvedGrant,
    ResourceContext,
    ScopeLevel,
    authorize,
)

pytestmark = pytest.mark.unit

NOW = datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC)
CTX = RequestContext(now=NOW)


def _grant(
    effect: Effect,
    level: ScopeLevel,
    *,
    selector: dict | None = None,
    predicates: dict | None = None,
    source: str = "role:Test",
    is_override: bool = False,
) -> ResolvedGrant:
    return ResolvedGrant(
        effect=effect,
        level=level,
        selector=selector or {},
        predicates=predicates or {},
        source=source,
        is_override=is_override,
    )


def _decide(grants, key, resource, *, ctx=CTX, **kw) -> Decision:
    return authorize(grants, key, resource, ctx, **kw)


# --- deny-by-default -------------------------------------------------------------------


def test_no_grants_is_deny_by_default() -> None:
    d = _decide([], "document.read", ResourceContext(artifact_id="X"))
    assert d.allow is False
    assert d.reason == "deny_by_default"


# --- AC#3: per-user DENY beats role ALLOW ----------------------------------------------


def test_per_user_deny_beats_role_allow() -> None:
    """doc 18 §7.1 AC#3 / doc 07 §6.4: a role ALLOW @PROCESS and a per-user DENY @ARTIFACT
    on the same target -> DENY. The broad role ALLOW must not rescue it."""
    resource = ResourceContext(artifact_id="SOP-PUR-009", process_ids=frozenset({"purchasing"}))
    grants = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.PROCESS,
            selector={"process_id": "purchasing"},
            source="role:Author",
        ),
        _grant(
            Effect.DENY,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "SOP-PUR-009"},
            source="user_override",
            is_override=True,
        ),
    ]
    d = _decide(grants, "document.edit", resource)
    assert d.allow is False
    assert d.reason == "explicit_deny"
    assert d.source == "user_override"


def test_artifact_deny_is_a_carve_out_not_a_blanket() -> None:
    """doc 07 §6.4: the same Author ALLOW still permits a *different* artifact in folder."""
    grants = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.FOLDER,
            selector={"folder_path": "SOPs.Purchasing"},
            source="role:Author",
        ),
        _grant(
            Effect.DENY,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "SOP-PUR-009"},
            source="user_override",
            is_override=True,
        ),
    ]
    denied = _decide(
        grants,
        "document.edit",
        ResourceContext(artifact_id="SOP-PUR-009", folder_path="SOPs.Purchasing"),
    )
    allowed = _decide(
        grants,
        "document.edit",
        ResourceContext(artifact_id="SOP-PUR-003", folder_path="SOPs.Purchasing"),
    )
    assert denied.allow is False
    assert allowed.allow is True
    assert allowed.source == "role:Author"


# --- AC#4: system super-admin denied content authority by default ----------------------


def test_admin_system_star_denied_content() -> None:
    """doc 18 §7.1 AC#4 / doc 07 §2.1: a System Administrator holds system perms but NO
    ``document.approve`` in its bundle, so the gathered grant set for that key is empty
    and the PDP denies by default. (The integration suite proves the seed bundle is empty.)"""
    d = _decide([], "document.approve", ResourceContext(artifact_id="SOP-PUR-014"))
    assert d.allow is False
    assert d.reason == "deny_by_default"


def test_content_authority_is_about_the_bundle_not_the_scope() -> None:
    """Contrast: had ``document.approve`` actually been granted (even SYSTEM-wide), it would
    be allowed — proving AC#4's denial is the *absence* of the grant, not a scope quirk."""
    grants = [_grant(Effect.ALLOW, ScopeLevel.SYSTEM, source="role:QualityApprover")]
    d = _decide(grants, "document.approve", ResourceContext(artifact_id="SOP-PUR-014"))
    assert d.allow is True


# --- specificity only ever ranks ALLOW-vs-ALLOW ----------------------------------------


def test_specificity_never_overrides_a_deny() -> None:
    """A broad DENY @SYSTEM beats a narrow ALLOW @ARTIFACT (deny-wins, not most-specific)."""
    grants = [
        _grant(Effect.DENY, ScopeLevel.SYSTEM, source="role:Restricted"),
        _grant(
            Effect.ALLOW,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            source="user_override",
            is_override=True,
        ),
    ]
    d = _decide(grants, "document.read", ResourceContext(artifact_id="X"))
    assert d.allow is False
    assert d.reason == "explicit_deny"
    assert d.source == "role:Restricted"


def test_same_level_deny_beats_allow() -> None:
    """Deny-first (step 2) runs before any specificity/override ranking: an ALLOW and a DENY
    at the *same* scope and selector resolve to DENY — proving the deny check is not bypassed
    by scope filtering or a tie-break."""
    grants = [
        _grant(
            Effect.ALLOW, ScopeLevel.ARTIFACT, selector={"artifact_id": "X"}, source="role:Author"
        ),
        _grant(
            Effect.DENY,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            source="user_override",
            is_override=True,
        ),
    ]
    d = _decide(grants, "document.edit", ResourceContext(artifact_id="X"))
    assert d.allow is False
    assert d.reason == "explicit_deny"
    assert d.source == "user_override"


def test_specificity_breaks_allow_vs_allow_ties() -> None:
    """Two ALLOWs, no DENY -> ALLOW; the more-specific (ARTIFACT) grant wins provenance."""
    grants = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.FOLDER,
            selector={"folder_path": "SOPs.Purchasing"},
            source="role:Author",
        ),
        _grant(
            Effect.ALLOW,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            source="user_override",
            is_override=True,
        ),
    ]
    d = _decide(
        grants, "document.read", ResourceContext(artifact_id="X", folder_path="SOPs.Purchasing")
    )
    assert d.allow is True
    assert d.source == "user_override"


def test_override_outranks_role_within_same_level() -> None:
    """doc 07 §6.3 step 6: at the same scope level, a per-user override wins the tie."""
    grants = [
        _grant(Effect.ALLOW, ScopeLevel.SYSTEM, source="role:Wide"),
        _grant(Effect.ALLOW, ScopeLevel.SYSTEM, source="user_override", is_override=True),
    ]
    d = _decide(grants, "document.read", ResourceContext.system())
    assert d.allow is True
    assert d.source == "user_override"


# --- scope matching per level ----------------------------------------------------------


def test_system_scope_matches_everything() -> None:
    g = [_grant(Effect.ALLOW, ScopeLevel.SYSTEM, source="role:Admin")]
    assert _decide(g, "role.read", ResourceContext.system()).allow is True
    assert _decide(g, "role.read", ResourceContext(artifact_id="anything")).allow is True


@pytest.mark.parametrize(
    ("resource_processes", "expected"),
    [
        (frozenset({"purchasing"}), True),
        (frozenset({"purchasing", "prod"}), True),
        (frozenset({"prod"}), False),
    ],
)
def test_process_scope_membership(resource_processes: frozenset[str], expected: bool) -> None:
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.PROCESS,
            selector={"process_id": "purchasing"},
            source="role:PO",
        )
    ]
    d = _decide(g, "document.edit", ResourceContext(process_ids=resource_processes))
    assert d.allow is expected


def test_process_deny_is_conservative() -> None:
    """doc 07 §5.3: a PROCESS DENY matches if linked to ANY denied process."""
    grants = [
        _grant(Effect.ALLOW, ScopeLevel.SYSTEM, source="role:Wide"),
        _grant(
            Effect.DENY,
            ScopeLevel.PROCESS,
            selector={"process_id": "purchasing"},
            source="user_override",
            is_override=True,
        ),
    ]
    linked = _decide(
        grants, "document.edit", ResourceContext(process_ids=frozenset({"purchasing", "prod"}))
    )
    unlinked = _decide(grants, "document.edit", ResourceContext(process_ids=frozenset({"prod"})))
    assert linked.allow is False
    assert unlinked.allow is True


@pytest.mark.parametrize(
    ("grant_path", "resource_path", "expected"),
    [
        ("SOPs", "SOPs.Purchasing.SOP-PUR-002", True),
        ("SOPs", "SOPs", True),
        ("SOPs.Purchasing", "SOPs.Purchasing", True),
        ("SOPs", "Forms.F1", False),
        ("SOPs", "SOPsExtra.X", False),  # must be exact label or prefix + dot
    ],
)
def test_folder_scope_is_ltree_ancestor(
    grant_path: str, resource_path: str, expected: bool
) -> None:
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.FOLDER,
            selector={"folder_path": grant_path},
            source="role:Author",
        )
    ]
    d = _decide(g, "document.edit", ResourceContext(folder_path=resource_path))
    assert d.allow is expected


def test_doc_class_scope_matches_document_level() -> None:
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.DOC_CLASS,
            selector={"document_level": "L2_PROCEDURE"},
            source="role:Approver",
        )
    ]
    assert (
        _decide(g, "document.approve", ResourceContext(document_level="L2_PROCEDURE")).allow is True
    )
    assert (
        _decide(g, "document.approve", ResourceContext(document_level="L3_WORK_INSTRUCTION")).allow
        is False
    )


def test_doc_class_optional_kind_narrowing() -> None:
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.DOC_CLASS,
            selector={"document_level": "L2_PROCEDURE", "kind": "DOCUMENT"},
            source="role:Approver",
        )
    ]
    assert (
        _decide(
            g, "document.approve", ResourceContext(document_level="L2_PROCEDURE", kind="DOCUMENT")
        ).allow
        is True
    )
    assert (
        _decide(
            g, "document.approve", ResourceContext(document_level="L2_PROCEDURE", kind="RECORD")
        ).allow
        is False
    )


def test_artifact_scope_exact_id() -> None:
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            source="user_override",
            is_override=True,
        )
    ]
    assert _decide(g, "document.read", ResourceContext(artifact_id="X")).allow is True
    assert _decide(g, "document.read", ResourceContext(artifact_id="Y")).allow is False


# --- predicates only ever narrow (AZ-INV-8) --------------------------------------------


def test_time_window_predicate() -> None:
    art = ResourceContext(artifact_id="X")
    future = {"valid_from": datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)}
    past = {"valid_until": datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC)}
    window = {
        "valid_from": datetime.datetime(2026, 5, 1, tzinfo=datetime.UTC),
        "valid_until": datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
    }
    assert (
        _decide(
            [
                _grant(
                    Effect.ALLOW,
                    ScopeLevel.ARTIFACT,
                    selector={"artifact_id": "X"},
                    predicates=future,
                    source="o",
                    is_override=True,
                )
            ],
            "document.read",
            art,
        ).allow
        is False
    )
    assert (
        _decide(
            [
                _grant(
                    Effect.ALLOW,
                    ScopeLevel.ARTIFACT,
                    selector={"artifact_id": "X"},
                    predicates=past,
                    source="o",
                    is_override=True,
                )
            ],
            "document.read",
            art,
        ).allow
        is False
    )
    assert (
        _decide(
            [
                _grant(
                    Effect.ALLOW,
                    ScopeLevel.ARTIFACT,
                    selector={"artifact_id": "X"},
                    predicates=window,
                    source="o",
                    is_override=True,
                )
            ],
            "document.read",
            art,
        ).allow
        is True
    )


def test_naive_datetime_predicate_does_not_crash() -> None:
    """A naive ISO string in a predicate (e.g. from JSONB) is treated as UTC, never a 500."""
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            predicates={"valid_until": "2026-05-01T00:00:00"},  # naive, already past
            source="user_override",
            is_override=True,
        )
    ]
    d = _decide(g, "document.read", ResourceContext(artifact_id="X"))
    assert d.allow is False  # window lapsed; compared as UTC without raising
    future = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            predicates={"valid_until": "2026-12-31T00:00:00"},  # naive, future
            source="user_override",
            is_override=True,
        )
    ]
    assert _decide(future, "document.read", ResourceContext(artifact_id="X")).allow is True


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("document.read", True),
        ("document.read_draft", True),
        ("document.print_controlled", True),
        ("report.compliance_checklist.read", True),
        ("document.edit", False),
        ("document.approve", False),
    ],
)
def test_read_only_predicate_strips_non_read(key: str, expected: bool) -> None:
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            predicates={"read_only": True},
            source="user_override",
            is_override=True,
        )
    ]
    assert _decide(g, key, ResourceContext(artifact_id="X")).allow is expected


def test_lifecycle_and_ip_predicates() -> None:
    g = [
        _grant(
            Effect.ALLOW,
            ScopeLevel.ARTIFACT,
            selector={"artifact_id": "X"},
            predicates={"lifecycle_state": ["Effective"], "ip_allow": ["10.0.0.1"]},
            source="user_override",
            is_override=True,
        )
    ]
    resource_eff = ResourceContext(artifact_id="X", lifecycle_state="Effective")
    resource_draft = ResourceContext(artifact_id="X", lifecycle_state="Draft")
    assert (
        _decide(
            g, "document.read", resource_eff, ctx=RequestContext(now=NOW, source_ip="10.0.0.1")
        ).allow
        is True
    )
    assert (
        _decide(
            g, "document.read", resource_eff, ctx=RequestContext(now=NOW, source_ip="10.0.0.2")
        ).allow
        is False
    )
    assert (
        _decide(
            g, "document.read", resource_draft, ctx=RequestContext(now=NOW, source_ip="10.0.0.1")
        ).allow
        is False
    )


# --- doc 07 §10 worked example: Reza (time-boxed read-only) and Ken (require_reason) ----


def test_reza_time_boxed_read_only_grant() -> None:
    """doc 07 §10.2 OV-2: read-only on one artifact, valid 14 days, Released only."""
    grant = _grant(
        Effect.ALLOW,
        ScopeLevel.ARTIFACT,
        selector={"artifact_id": "SOP-PUR-014"},
        predicates={
            "read_only": True,
            "valid_until": datetime.datetime(2026, 6, 14, tzinfo=datetime.UTC),
            "lifecycle_state": ["Effective"],
        },
        source="user_override",
        is_override=True,
    )
    target = ResourceContext(artifact_id="SOP-PUR-014", lifecycle_state="Effective")
    day3 = RequestContext(now=datetime.datetime(2026, 6, 3, tzinfo=datetime.UTC))
    day20 = RequestContext(now=datetime.datetime(2026, 6, 20, tzinfo=datetime.UTC))
    assert _decide([grant], "document.read", target, ctx=day3).allow is True
    assert _decide([grant], "document.read", target, ctx=day20).allow is False  # window lapsed
    other = ResourceContext(artifact_id="SOP-PUR-003", lifecycle_state="Effective")
    assert (
        _decide([grant], "document.read", other, ctx=day3).allow is False
    )  # scope is one artifact


def test_ken_constrained_allow_propagates_require_reason() -> None:
    """doc 07 §10.2 OV-3: an artifact ALLOW carrying ``require_reason`` surfaces it."""
    grant = _grant(
        Effect.ALLOW,
        ScopeLevel.ARTIFACT,
        selector={"artifact_id": "SOP-PUR-014"},
        predicates={"require_reason": True},
        source="user_override",
        is_override=True,
    )
    d = _decide(
        [grant], "document.approve", ResourceContext(artifact_id="SOP-PUR-014"), sig_hook=True
    )
    assert d.allow is True
    assert d.require_reason is True


def test_sig_hook_step_up_gate() -> None:
    """Part-11 seam: a sig-hook action with step-up unsatisfied is denied (v1 leaves it on)."""
    g = [
        _grant(
            Effect.ALLOW, ScopeLevel.ARTIFACT, selector={"artifact_id": "X"}, source="role:Approver"
        )
    ]
    ctx = RequestContext(now=NOW, step_up_satisfied=False)
    d = _decide(g, "document.approve", ResourceContext(artifact_id="X"), ctx=ctx, sig_hook=True)
    assert d.allow is False
    assert d.reason == "step_up_required"
