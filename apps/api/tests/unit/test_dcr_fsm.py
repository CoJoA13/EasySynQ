"""S-dcr-1 unit proofs — the pure DCR lifecycle FSM (``domain/dcr/fsm.py``).

These pin the FSM to the reconciled doc 05 §5.5 + doc 15 §8.7 edge map: the InApproval
changes-requested loop targets ``Open`` (owner decision), ``Approved`` has NO Cancelled edge, and
Cancelled/Closed/Rejected are terminal.
"""

from __future__ import annotations

import pytest

from easysynq_api.db.models._dcr_enums import DcrState
from easysynq_api.domain.dcr import (
    DCR_TRANSITIONS,
    allowed_targets,
    is_terminal,
    transition_allowed,
)

pytestmark = pytest.mark.unit

_S = DcrState


def test_canonical_forward_path() -> None:
    assert transition_allowed(_S.Open, _S.Assessed)
    assert transition_allowed(_S.Assessed, _S.Routed)
    assert transition_allowed(_S.Routed, _S.InApproval)
    assert transition_allowed(_S.InApproval, _S.Approved)
    assert transition_allowed(_S.Approved, _S.Implemented)
    assert transition_allowed(_S.Implemented, _S.Closed)


def test_inapproval_changes_requested_loops_to_open() -> None:
    # The owner decision (doc 15 §8.7): a changes-requested rejection returns to Open (re-assess +
    # re-route), NOT doc 05 §5.5's Routed.
    assert transition_allowed(_S.InApproval, _S.Open)
    assert transition_allowed(_S.InApproval, _S.Rejected)
    assert not transition_allowed(_S.InApproval, _S.Routed)


def test_cancellable_only_before_approval() -> None:
    assert transition_allowed(_S.Open, _S.Cancelled)
    assert transition_allowed(_S.Assessed, _S.Cancelled)
    assert transition_allowed(_S.Routed, _S.Cancelled)
    # Past the cancel window — no Approved → Cancelled edge (both docs agree).
    assert not transition_allowed(_S.Approved, _S.Cancelled)
    assert allowed_targets(_S.Approved) == frozenset({_S.Implemented})


def test_terminal_states_have_no_outgoing() -> None:
    for term in (_S.Closed, _S.Cancelled, _S.Rejected):
        assert is_terminal(term)
        assert allowed_targets(term) == frozenset()


def test_non_terminal_states_are_not_terminal() -> None:
    for state in (_S.Open, _S.Assessed, _S.Routed, _S.InApproval, _S.Approved, _S.Implemented):
        assert not is_terminal(state)


def test_every_state_is_in_the_map() -> None:
    # Totality: the FSM declares an entry for every DcrState (no KeyError on an unknown state).
    assert set(DCR_TRANSITIONS) == set(DcrState)
