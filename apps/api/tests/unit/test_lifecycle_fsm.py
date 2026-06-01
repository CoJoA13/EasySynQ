"""S4 unit proofs — the pure lifecycle FSM (``domain.vault.lifecycle``).

Drives the transition table with hand-built states (no DB), proving the shipped transitions
(T1-T4, T6, T7, T9-T12) map correctly and that everything else — including the deferred T5/T8 —
raises ``IllegalTransition`` with the right ``allowed`` set.
"""

from __future__ import annotations

import pytest

from easysynq_api.db.models._vault_enums import DocumentCurrentState as D
from easysynq_api.db.models._vault_enums import VersionState as V
from easysynq_api.domain.vault.lifecycle import (
    Action,
    IllegalTransition,
    allowed_actions,
    apply_transition,
)


def test_submit_review_from_draft() -> None:  # T2
    t = apply_transition(D.Draft, Action.submit_review)
    assert (t.from_version_state, t.to_version_state, t.to_doc_state) == (
        V.Draft,
        V.InReview,
        D.InReview,
    )


def test_submit_review_from_under_revision() -> None:  # T9
    t = apply_transition(D.UnderRevision, Action.submit_review)
    assert (t.from_version_state, t.to_version_state, t.to_doc_state) == (
        V.Draft,
        V.InReview,
        D.InReview,
    )


def test_approve() -> None:  # T4
    t = apply_transition(D.InReview, Action.approve)
    assert (t.from_version_state, t.to_version_state, t.to_doc_state) == (
        V.InReview,
        V.Approved,
        D.Approved,
    )


def test_request_changes() -> None:  # T3
    t = apply_transition(D.InReview, Action.request_changes)
    assert (t.from_version_state, t.to_version_state, t.to_doc_state) == (
        V.InReview,
        V.Draft,
        D.Draft,
    )


def test_release() -> None:  # T6
    t = apply_transition(D.Approved, Action.release)
    assert (t.from_version_state, t.to_version_state, t.to_doc_state) == (
        V.Approved,
        V.Effective,
        D.Effective,
    )


def test_start_revision_changes_only_doc_state() -> None:  # T7
    t = apply_transition(D.Effective, Action.start_revision)
    assert t.from_version_state is None  # the Effective version keeps governing (unchanged)
    assert t.to_version_state is None
    assert t.to_doc_state is D.UnderRevision


def test_obsolete_from_effective() -> None:  # T11
    t = apply_transition(D.Effective, Action.obsolete)
    assert (t.to_version_state, t.to_doc_state) == (V.Obsolete, D.Obsolete)


@pytest.mark.parametrize(
    ("doc_state", "action"),
    [
        (D.Draft, Action.approve),
        (D.Draft, Action.release),
        (D.Draft, Action.obsolete),
        (D.InReview, Action.release),
        (D.InReview, Action.submit_review),
        (D.Approved, Action.approve),
        (D.Approved, Action.start_revision),
        (D.Effective, Action.approve),
        (D.Effective, Action.release),
        (D.UnderRevision, Action.release),
        (D.Obsolete, Action.submit_review),
        (D.Obsolete, Action.obsolete),
        (D.Superseded, Action.release),
    ],
)
def test_illegal_transitions_raise(doc_state: D, action: Action) -> None:
    with pytest.raises(IllegalTransition) as ei:
        apply_transition(doc_state, action)
    # the exception advertises exactly the legal actions from this state
    assert ei.value.allowed == allowed_actions(doc_state)
    assert action.value not in ei.value.allowed


def test_t5_rescind_and_t8_discard_are_deferred() -> None:
    # No Action exists for T5 (rescind-approval) / T8 (discard-draft) — deferred to v1 (D-5).
    assert {a.value for a in Action} == {
        "submit_review",
        "approve",
        "request_changes",
        "release",
        "start_revision",
        "obsolete",
    }
    # And the would-be source states offer no escape hatch beyond the shipped transitions.
    assert allowed_actions(D.Approved) == ["release"]  # not rescind (T5)
    assert allowed_actions(D.UnderRevision) == ["submit_review"]  # not discard (T8)


def test_allowed_actions_are_sorted_and_complete() -> None:
    assert allowed_actions(D.Draft) == ["submit_review"]
    assert allowed_actions(D.InReview) == ["approve", "request_changes"]
    assert allowed_actions(D.Approved) == ["release"]
    assert allowed_actions(D.Effective) == ["obsolete", "start_revision"]
    assert allowed_actions(D.UnderRevision) == ["submit_review"]
    assert allowed_actions(D.Superseded) == []  # T12 is version-level, not document-state-keyed
    assert allowed_actions(D.Obsolete) == []  # terminal
