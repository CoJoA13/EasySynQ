"""S-capa-1 unit proofs — the pure CAPA ``close_state`` lifecycle FSM (doc 10 §6)."""

from __future__ import annotations

import pytest

from easysynq_api.db.models._capa_enums import CapaCloseState as S
from easysynq_api.domain.capa import allowed_targets, is_terminal, transition_allowed

pytestmark = pytest.mark.unit


def test_forward_chain_is_legal() -> None:
    chain = [
        (S.Raised, S.Containment),
        (S.Containment, S.RootCause),
        (S.RootCause, S.ActionPlan),
        (S.ActionPlan, S.Implement),
        (S.Implement, S.Verify),
        (S.Verify, S.Closed),
    ]
    for cur, nxt in chain:
        assert transition_allowed(cur, nxt), f"{cur} → {nxt} should be legal"


def test_effectiveness_loop_verify_back_to_root_cause() -> None:
    # The effectiveness loop routes Verify → RootCause (NOT directly to ActionPlan), so the revised
    # plan is re-proposed + re-approved (RootCause → ActionPlan) — the owner's re-approval choice
    # (S-capa-3). A direct Verify → ActionPlan is therefore illegal.
    assert transition_allowed(S.Verify, S.RootCause)
    assert not transition_allowed(S.Verify, S.ActionPlan)
    assert allowed_targets(S.Verify) == frozenset({S.Closed, S.RootCause})


def test_reject_is_legal_from_every_working_stage() -> None:
    for working in (S.Raised, S.Containment, S.RootCause, S.ActionPlan, S.Implement):
        assert transition_allowed(working, S.Rejected), f"{working} → Rejected should be legal"
    # …but NOT from Verify (a verified CAPA closes or re-plans; it is not rejected) or terminals.
    assert not transition_allowed(S.Verify, S.Rejected)


def test_no_skips_no_rewind() -> None:
    assert not transition_allowed(S.Raised, S.RootCause)  # skip Containment
    assert not transition_allowed(S.Raised, S.Closed)  # skip everything
    assert not transition_allowed(S.Containment, S.Raised)  # rewind
    assert not transition_allowed(S.Implement, S.RootCause)  # rewind
    assert not transition_allowed(S.Raised, S.Raised)  # self


def test_terminals_have_no_outgoing() -> None:
    assert is_terminal(S.Closed)
    assert is_terminal(S.Rejected)
    assert allowed_targets(S.Closed) == frozenset()
    assert allowed_targets(S.Rejected) == frozenset()
    for term in (S.Closed, S.Rejected):
        for target in S:
            assert not transition_allowed(term, target)


def test_non_terminals_are_not_terminal() -> None:
    for working in (S.Raised, S.Containment, S.RootCause, S.ActionPlan, S.Implement, S.Verify):
        assert not is_terminal(working)


def test_every_state_is_covered() -> None:
    # totality: every close_state is a key (terminals map to the empty set, not missing).
    from easysynq_api.domain.capa.fsm import CAPA_TRANSITIONS

    assert set(CAPA_TRANSITIONS) == set(S)


def test_capa_family_enum_values() -> None:
    """The S-capa-1 enum tokens + the new audit-log values are exactly as the migration creates them
    (the canonical lowercase capa_source / R20 ncr_disposition tokens; the new ncr object type +
    CAPA/COMPLAINT/NCR event types)."""
    from easysynq_api.db.models._audit_enums import AuditObjectType, EventType
    from easysynq_api.db.models._capa_enums import (
        CapaSource,
        NcrDisposition,
        NcrSource,
        NcSeverity,
    )

    assert [m.value for m in CapaSource] == ["audit", "process", "complaint", "review_output"]
    assert [m.value for m in NcrSource] == ["audit", "process", "complaint", "internal"]
    assert [m.value for m in NcSeverity] == ["Critical", "Major", "Minor"]
    # R20 verbatim (note the `return` keyword → member RETURN_, value "return").
    assert NcrDisposition.RETURN_.value == "return"
    assert {m.value for m in NcrDisposition} == {
        "use_as_is",
        "rework",
        "scrap",
        "return",
        "concession",
        "regrade",
    }
    assert AuditObjectType.ncr.value == "ncr"
    for name in (
        "CAPA_RAISED",
        "CAPA_TRANSITIONED",
        "COMPLAINT_CAPTURED",
        "COMPLAINT_SPAWNED_CAPA",
        "NCR_CREATED",
        "NCR_DISPOSITIONED",
    ):
        assert hasattr(EventType, name)
