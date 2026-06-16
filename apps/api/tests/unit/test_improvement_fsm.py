"""S-improvement-1 unit proofs — the pure Improvement-Initiative lifecycle FSM
(``domain/improvement/fsm.py``).

These pin the FSM to the R46 §4 edge map: the simple stage-completion close
``Open → InProgress → Completed → Closed`` (+ ``Cancelled`` from the pre-completion states only);
``Closed`` / ``Cancelled`` are terminal. Exhaustive: every legal edge, every terminal, and every
illegal edge.
"""

from __future__ import annotations

import itertools

import pytest

from easysynq_api.db.models._improvement_enums import ImprovementStage
from easysynq_api.domain.improvement import (
    IMPROVEMENT_TRANSITIONS,
    allowed_targets,
    is_terminal,
    transition_allowed,
)

pytestmark = pytest.mark.unit

_S = ImprovementStage

# The authoritative legal edge set (R46 §4) — the single source the tests assert against.
_LEGAL_EDGES: frozenset[tuple[ImprovementStage, ImprovementStage]] = frozenset(
    {
        (_S.Open, _S.InProgress),
        (_S.Open, _S.Cancelled),
        (_S.InProgress, _S.Completed),
        (_S.InProgress, _S.Cancelled),
        (_S.Completed, _S.Closed),
    }
)


def test_canonical_forward_path() -> None:
    assert transition_allowed(_S.Open, _S.InProgress)
    assert transition_allowed(_S.InProgress, _S.Completed)
    assert transition_allowed(_S.Completed, _S.Closed)


def test_cancellable_only_before_completion() -> None:
    assert transition_allowed(_S.Open, _S.Cancelled)
    assert transition_allowed(_S.InProgress, _S.Cancelled)
    # A Completed initiative is filed (Closed), never cancelled — past the cancel window.
    assert not transition_allowed(_S.Completed, _S.Cancelled)
    assert allowed_targets(_S.Completed) == frozenset({_S.Closed})


def test_allowed_targets_per_state() -> None:
    assert allowed_targets(_S.Open) == frozenset({_S.InProgress, _S.Cancelled})
    assert allowed_targets(_S.InProgress) == frozenset({_S.Completed, _S.Cancelled})
    assert allowed_targets(_S.Completed) == frozenset({_S.Closed})
    assert allowed_targets(_S.Closed) == frozenset()
    assert allowed_targets(_S.Cancelled) == frozenset()


def test_terminal_states_have_no_outgoing() -> None:
    for term in (_S.Closed, _S.Cancelled):
        assert is_terminal(term)
        assert allowed_targets(term) == frozenset()


def test_non_terminal_states_are_not_terminal() -> None:
    for state in (_S.Open, _S.InProgress, _S.Completed):
        assert not is_terminal(state)


def test_no_skipping_or_backward_edges() -> None:
    # A few representative illegal moves spelled out for documentation value.
    assert not transition_allowed(_S.Open, _S.Completed)  # no skipping InProgress
    assert not transition_allowed(_S.Open, _S.Closed)  # no skipping straight to filed
    assert not transition_allowed(_S.InProgress, _S.Closed)  # must pass through Completed
    assert not transition_allowed(_S.Completed, _S.InProgress)  # no backward edge
    assert not transition_allowed(_S.InProgress, _S.Open)  # no backward edge


def test_exhaustive_edge_matrix_matches_the_canon() -> None:
    # Every (from, to) pair over the full state space is allowed iff it is in the legal edge set —
    # this catches both a missing legal edge AND any accidental extra edge (incl. self-loops).
    for src, dst in itertools.product(_S, _S):
        expected = (src, dst) in _LEGAL_EDGES
        assert transition_allowed(src, dst) is expected, f"{src.value} -> {dst.value}"


def test_no_self_loops() -> None:
    for state in _S:
        assert not transition_allowed(state, state)


def test_every_state_is_in_the_map() -> None:
    # Totality: the FSM declares an entry for every ImprovementStage (no KeyError on an unknown
    # state).
    assert set(IMPROVEMENT_TRANSITIONS) == set(ImprovementStage)
