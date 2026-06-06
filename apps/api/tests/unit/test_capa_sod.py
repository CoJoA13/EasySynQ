"""S-capa-3 unit proofs — the pure severity-aware SoD-4 predicate + the implementer-set derivation
(domain/capa/sod.py; doc 10 §6.2/§6.3)."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.db.models._capa_enums import CapaCloseState as S
from easysynq_api.db.models._capa_enums import NcSeverity
from easysynq_api.domain.capa import capa_self_verify_blocked, derive_implementer_ids

pytestmark = pytest.mark.unit

_ALICE = uuid.uuid4()
_BOB = uuid.uuid4()
_CAROL = uuid.uuid4()


def test_non_implementer_is_never_blocked() -> None:
    # A verifier outside the implementer set verifies any severity, flag either way.
    for sev in NcSeverity:
        for flag in (True, False):
            assert not capa_self_verify_blocked(
                _CAROL, {_ALICE, _BOB}, severity=sev, allow_capa_self_verify=flag
            )


def test_critical_and_major_hard_enforce_regardless_of_flag() -> None:
    # An implementer cannot verify a Critical/Major CAPA even if the org relaxed self-verify.
    for sev in (NcSeverity.Critical, NcSeverity.Major):
        assert capa_self_verify_blocked(_ALICE, {_ALICE}, severity=sev, allow_capa_self_verify=True)
        assert capa_self_verify_blocked(
            _ALICE, {_ALICE}, severity=sev, allow_capa_self_verify=False
        )


def test_minor_respects_the_flag() -> None:
    # Minor: the implementer may self-verify ONLY when the org enabled it (the default is strict).
    assert capa_self_verify_blocked(
        _ALICE, {_ALICE}, severity=NcSeverity.Minor, allow_capa_self_verify=False
    )
    assert not capa_self_verify_blocked(
        _ALICE, {_ALICE}, severity=NcSeverity.Minor, allow_capa_self_verify=True
    )


def test_derive_implementer_ids_is_implement_creators_and_action_owners() -> None:
    owner = uuid.uuid4()
    stages = [
        (S.Raised, _CAROL, {"problem": "x"}),  # Raised creator is NOT an implementer
        (S.Containment, _CAROL, {"correction": "y"}),  # nor Containment
        (S.RootCause, _CAROL, {"root_cause": "z"}),  # nor RootCause
        # ActionPlan.created_by (_ALICE) is the APPROVER — NOT an implementer; its UUID action-item
        # owner IS, while the free-text "diego" owner is ignored.
        (S.ActionPlan, _ALICE, {"action_items": [{"owner": str(owner)}, {"owner": "diego"}]}),
        (S.Implement, _BOB, {"actions_done": "w"}),  # the implementation recorder IS
    ]
    ids = derive_implementer_ids(stages)
    assert ids == {_BOB, owner}
    assert _ALICE not in ids  # the plan approver may still verify
    assert _CAROL not in ids


def test_derive_implementer_ids_tolerates_messy_blocks() -> None:
    # Non-dict items, missing owners, None blocks, non-UUID owners → all skipped without error; only
    # the Implement creator (_CAROL) survives (the ActionPlan creators are approvers, not doers).
    stages = [
        (S.ActionPlan, _ALICE, {"action_items": ["bad", {"no_owner": 1}, {"owner": None}]}),
        (S.ActionPlan, _BOB, None),
        (S.Implement, _CAROL, {}),
    ]
    assert derive_implementer_ids(stages) == {_CAROL}
