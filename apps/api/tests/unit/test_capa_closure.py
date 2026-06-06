"""S-capa-3 unit proofs — the pure M4 closure gate (domain/capa/closure.py; doc 10 §6.4)."""

from __future__ import annotations

import pytest

from easysynq_api.domain.capa import ClosureOutcome, adjudicate_capa_closure

pytestmark = pytest.mark.unit


def _adj(decision: str, rc: bool, impl: bool, eff: bool) -> tuple[ClosureOutcome, list[str]]:
    return adjudicate_capa_closure(
        decision=decision,
        has_root_cause=rc,
        has_implemented_with_evidence=impl,
        has_effectiveness_evidence=eff,
    )


def test_effective_with_all_evidence_closes() -> None:
    outcome, missing = _adj("effective", True, True, True)
    assert outcome is ClosureOutcome.CLOSE
    assert missing == []


def test_not_effective_loops_regardless_of_evidence() -> None:
    # A not-effective verification always loops (the effectiveness loop), even with evidence.
    for ev in (True, False):
        outcome, missing = _adj("not_effective", ev, ev, ev)
        assert outcome is ClosureOutcome.LOOP
        assert missing == []


def test_effective_missing_each_clause_is_incomplete() -> None:
    assert _adj("effective", False, True, True) == (
        ClosureOutcome.INCOMPLETE,
        ["root_cause"],
    )
    assert _adj("effective", True, False, True) == (
        ClosureOutcome.INCOMPLETE,
        ["implemented_action_with_evidence"],
    )
    assert _adj("effective", True, True, False) == (
        ClosureOutcome.INCOMPLETE,
        ["effectiveness_evidence"],
    )


def test_effective_missing_all_lists_every_clause_in_order() -> None:
    outcome, missing = _adj("effective", False, False, False)
    assert outcome is ClosureOutcome.INCOMPLETE
    assert missing == [
        "root_cause",
        "implemented_action_with_evidence",
        "effectiveness_evidence",
    ]
