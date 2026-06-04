"""S-rec-2 unit: the pure disposition state-machine transition table (doc 06 §5.3)."""

from __future__ import annotations

import itertools

import pytest

from easysynq_api.db.models._record_enums import RecordDispositionState as S
from easysynq_api.domain.records.disposition import legal_disposition_transition

_ALLOWED = {
    (S.ACTIVE, S.DUE_FOR_REVIEW),
    (S.DUE_FOR_REVIEW, S.DISPOSED),
    (S.DUE_FOR_REVIEW, S.ACTIVE),
    (S.ACTIVE, S.ON_HOLD),
    (S.DUE_FOR_REVIEW, S.ON_HOLD),
    (S.ON_HOLD, S.ACTIVE),
}


@pytest.mark.unit
@pytest.mark.parametrize(("frm", "to"), sorted(_ALLOWED, key=lambda p: (p[0].value, p[1].value)))
def test_allowed_transitions(frm: S, to: S) -> None:
    assert legal_disposition_transition(frm, to) is True


@pytest.mark.unit
def test_disposed_is_terminal_and_no_self_loops() -> None:
    for state in S:
        assert legal_disposition_transition(S.DISPOSED, state) is False  # terminal
        assert legal_disposition_transition(state, state) is False  # no self-loops


@pytest.mark.unit
def test_all_disallowed_pairs_are_rejected() -> None:
    for frm, to in itertools.product(S, S):
        if (frm, to) in _ALLOWED:
            continue
        assert legal_disposition_transition(frm, to) is False, f"{frm} → {to} should be illegal"
