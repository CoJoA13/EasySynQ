"""S-wf-engine unit proofs — the pure condition + quorum helpers (doc 10 §2.4-2.5). No I/O."""

from __future__ import annotations

import pytest

from easysynq_api.domain.workflow import (
    evaluate_condition,
    quorum_state,
    referenced_keys,
    required_approvals,
    resolve_conditional,
)

pytestmark = pytest.mark.unit


# --- evaluate_condition (sandboxed, total) ----------------------------------------------------


def test_eq_and_membership() -> None:
    assert evaluate_condition("severity == 'Critical'", {"severity": "Critical"}) is True
    assert evaluate_condition("severity == 'Critical'", {"severity": "Minor"}) is False
    assert evaluate_condition("severity != 'Minor'", {"severity": "Critical"}) is True
    assert evaluate_condition("severity in ['Critical', 'Major']", {"severity": "Major"}) is True
    assert evaluate_condition("severity in ['Critical', 'Major']", {"severity": "Minor"}) is False
    assert evaluate_condition("severity not in ['Minor']", {"severity": "Critical"}) is True


def test_boolean_composition_and_not() -> None:
    ctx = {"severity": "Critical", "region": "EU"}
    assert evaluate_condition("severity == 'Critical' and region == 'EU'", ctx) is True
    assert evaluate_condition("severity == 'Critical' and region == 'US'", ctx) is False
    assert evaluate_condition("severity == 'Minor' or region == 'EU'", ctx) is True
    assert evaluate_condition("not severity == 'Minor'", ctx) is True


def test_total_over_missing_and_none_and_malformed() -> None:
    assert evaluate_condition("severity == 'Critical'", None) is False
    assert evaluate_condition("severity == 'Critical'", {}) is False  # missing key → False
    assert evaluate_condition("severity == 'Critical'", {"other": "x"}) is False
    assert evaluate_condition("this is not valid python (", {"severity": "x"}) is False


def test_sandbox_rejects_calls_and_attribute_access() -> None:
    # No code execution: a call / attribute / chained-compare all fail closed (False), never raise.
    assert evaluate_condition("__import__('os').system('x')", {}) is False
    assert evaluate_condition("severity.upper() == 'CRITICAL'", {"severity": "critical"}) is False
    assert evaluate_condition("1 < severity < 3", {"severity": 2}) is False  # chained → rejected


def test_referenced_keys() -> None:
    assert referenced_keys("severity == 'Critical' and region == 'EU'") == {"severity", "region"}
    assert referenced_keys("'x' == 'y'") == set()
    assert referenced_keys("broken (") == set()


# --- resolve_conditional --------------------------------------------------------------------

_COND = {
    "type": "conditional",
    "rule": [
        {"when": "severity == 'Critical'", "quorum": {"type": "N_OF_M", "n": 2, "m": 2}},
        {"when": "severity == 'Major'", "quorum": {"type": "ALL"}},
        {"default": {"type": "ANY", "n": 1}},
    ],
}


def test_flat_spec_returned_unchanged() -> None:
    flat = {"type": "N_OF_M", "n": 2}
    assert resolve_conditional(flat, {"severity": "Critical"}) is flat
    # The S5 DOCUMENT path instantiates with a NULL context + a flat quorum → returned unchanged
    # (a flat spec never consults the context, so it must not fail-closed).
    assert resolve_conditional(flat, None) is flat


def test_conditional_multikey_fail_closed_on_partial_context() -> None:
    spec = {
        "type": "conditional",
        "rule": [
            {
                "when": "severity == 'Critical' and region == 'EU'",
                "quorum": {"type": "N_OF_M", "n": 2, "m": 2},
            },
            {"default": {"type": "ANY"}},
        ],
    }
    both = resolve_conditional(spec, {"severity": "Critical", "region": "EU"})
    assert both == {"type": "N_OF_M", "n": 2, "m": 2}  # both present + matched
    # both keys present but unmatched → default
    assert resolve_conditional(spec, {"severity": "Major", "region": "EU"}) == {"type": "ANY"}
    # region MISSING (a multi-key conjunction with a partial context) → fail closed, NOT default
    assert resolve_conditional(spec, {"severity": "Critical"}) is None


def test_conditional_matches_then_default() -> None:
    crit = resolve_conditional(_COND, {"severity": "Critical"})
    assert crit == {"type": "N_OF_M", "n": 2, "m": 2}
    assert resolve_conditional(_COND, {"severity": "Major"}) == {"type": "ALL"}
    # present-but-unmatched discriminator → default
    assert resolve_conditional(_COND, {"severity": "Minor"}) == {"type": "ANY", "n": 1}


def test_conditional_fail_closed_on_absent_discriminator() -> None:
    # the discriminator key 'severity' is absent → None (NEEDS_ATTENTION), NOT the default
    assert resolve_conditional(_COND, {"other": "x"}) is None
    assert resolve_conditional(_COND, None) is None
    assert resolve_conditional({"type": "conditional", "rule": "bad"}, {}) is None
    assert resolve_conditional(None, {}) is None


def test_conditional_without_default_unmatched_is_none() -> None:
    spec = {
        "type": "conditional",
        "rule": [{"when": "severity == 'Critical'", "quorum": {"type": "ALL"}}],
    }
    # key present but unmatched and no default → None
    assert resolve_conditional(spec, {"severity": "Minor"}) is None


# --- quorum_state (tri-state, distinct approvers) -------------------------------------------


def test_any_quorum() -> None:
    spec = {"type": "ANY"}
    assert quorum_state(spec, approvals=0, rejects=0, resolved_count=3) == "PENDING"
    assert quorum_state(spec, approvals=1, rejects=0, resolved_count=3) == "MET"
    assert quorum_state(spec, approvals=0, rejects=3, resolved_count=3) == "FAILED"  # all rejected


def test_all_quorum() -> None:
    spec = {"type": "ALL"}
    assert quorum_state(spec, approvals=2, rejects=0, resolved_count=3) == "PENDING"
    assert quorum_state(spec, approvals=3, rejects=0, resolved_count=3) == "MET"
    assert quorum_state(spec, approvals=2, rejects=1, resolved_count=3) == "FAILED"


def test_n_of_m_quorum_met_pending_and_early_fail() -> None:
    spec = {"type": "N_OF_M", "n": 2, "m": 2}
    assert quorum_state(spec, approvals=1, rejects=0, resolved_count=2) == "PENDING"
    assert quorum_state(spec, approvals=2, rejects=0, resolved_count=2) == "MET"
    # one approve + one reject over 2 candidates → 2-of-2 unreachable → early FAILED (not hung)
    assert quorum_state(spec, approvals=1, rejects=1, resolved_count=2) == "FAILED"


def test_percent_quorum() -> None:
    spec = {"type": "PERCENT", "p": 60}
    assert quorum_state(spec, approvals=2, rejects=0, resolved_count=3) == "MET"  # 66% >= 60%
    assert (
        quorum_state(spec, approvals=1, rejects=0, resolved_count=3) == "PENDING"
    )  # can still reach
    assert quorum_state(spec, approvals=0, rejects=2, resolved_count=3) == "FAILED"  # max 33% < 60%


def test_required_approvals() -> None:
    assert required_approvals({"type": "ALL"}, 3) == 3
    assert required_approvals({"type": "ANY"}, 3) == 1
    assert required_approvals({"type": "N_OF_M", "n": 2}, 5) == 2
    assert required_approvals({"type": "PERCENT", "p": 60}, 5) == 3  # ceil(3.0)
    assert required_approvals({"type": "PERCENT", "p": 50}, 3) == 2  # ceil(1.5)
