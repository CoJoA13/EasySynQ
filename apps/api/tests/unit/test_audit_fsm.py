"""S-aud-1 unit proofs — the pure internal-audit lifecycle FSM (doc 10 §5.1, doc 14 §14)."""

from __future__ import annotations

import itertools

import pytest

from easysynq_api.db.models._iso_audit_enums import AuditState
from easysynq_api.domain.audits import AUDIT_TRANSITIONS, next_state, transition_allowed

pytestmark = pytest.mark.unit

_ORDER = [
    AuditState.Scheduled,
    AuditState.Planned,
    AuditState.InProgress,
    AuditState.FindingsDraft,
    AuditState.Reported,
    AuditState.Closing,
    AuditState.Closed,
]


def test_each_forward_step_is_allowed() -> None:
    for cur, nxt in itertools.pairwise(_ORDER):
        assert transition_allowed(cur, nxt) is True
        assert next_state(cur) is nxt


def test_closed_is_terminal() -> None:
    assert next_state(AuditState.Closed) is None
    for s in _ORDER:
        assert transition_allowed(AuditState.Closed, s) is False


def test_no_skipping_states() -> None:
    # Scheduled may only go to Planned — never jump straight to InProgress/Closed.
    assert transition_allowed(AuditState.Scheduled, AuditState.InProgress) is False
    assert transition_allowed(AuditState.Scheduled, AuditState.Closed) is False
    assert transition_allowed(AuditState.Planned, AuditState.FindingsDraft) is False


def test_no_rewind() -> None:
    assert transition_allowed(AuditState.InProgress, AuditState.Planned) is False
    assert transition_allowed(AuditState.Reported, AuditState.InProgress) is False
    assert transition_allowed(AuditState.Closing, AuditState.Reported) is False


def test_no_self_transition() -> None:
    for s in _ORDER:
        assert transition_allowed(s, s) is False


def test_transition_map_covers_every_non_terminal_state() -> None:
    # Every state except the terminal Closed has exactly one forward edge.
    assert set(AUDIT_TRANSITIONS) == set(_ORDER[:-1])
    assert AuditState.Closed not in AUDIT_TRANSITIONS
