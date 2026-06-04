"""S-rec-2 unit: the pure disposition state-machine transition table (doc 06 §5.3)."""

from __future__ import annotations

import itertools
import uuid

import pytest

from easysynq_api.db.models._record_enums import RecordDispositionState as S
from easysynq_api.domain.records.disposition import (
    legal_disposition_transition,
    self_disposition_blocked,
)

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


# --- SoD-6 (creator-not-disposer) pure predicate (S-rec-4, doc 07 §7) --------------------

_A = uuid.uuid4()
_B = uuid.uuid4()


@pytest.mark.unit
def test_sod6_blocks_capturer_when_flag_off() -> None:
    # Default (STRICT): the capturer (== actor) may not dispose their own record.
    assert self_disposition_blocked(_A, _A, allow_self_disposition=False) is True


@pytest.mark.unit
def test_sod6_allows_capturer_when_flag_on() -> None:
    # The org relaxed SoD-6 → the capturer may self-dispose.
    assert self_disposition_blocked(_A, _A, allow_self_disposition=True) is False


@pytest.mark.unit
@pytest.mark.parametrize("flag", [True, False])
def test_sod6_never_blocks_a_distinct_disposer(flag: bool) -> None:
    # A distinct disposer is always allowed (identity check is the whole gate), flag irrelevant.
    assert self_disposition_blocked(_B, _A, allow_self_disposition=flag) is False
