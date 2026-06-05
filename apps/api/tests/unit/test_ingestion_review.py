"""Unit tests for the S-ing-4 review fold + validation (pure, no DB).

The effective-state fold is the single source of a keep-item's committed-or-not state (R10), so it
is unit-tested directly on in-memory ORM instances. The ``after``-payload validation enforces the
R10 kind-confirm rule + the closed dimension set."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.db.models._ingestion_enums import (
    ImportConfidenceBand,
    ImportDecisionAction,
    ImportKind,
)
from easysynq_api.db.models.import_classification import ImportClassification
from easysynq_api.db.models.import_decision import ImportDecision
from easysynq_api.db.models.import_proposal_node import ImportProposalNode
from easysynq_api.problems import ProblemException
from easysynq_api.services.ingestion.review import (
    _coerce_action,
    _validate_after,
    fold_file_decisions,
)
from easysynq_api.services.reports.checklist import coverage_status


def _decision(action: ImportDecisionAction, after: dict | None = None) -> ImportDecision:
    return ImportDecision(action=action, after=after, file_id=uuid.uuid4())


def _node(identifier: str | None = "SOP-QA-001", owner: str | None = "Mara") -> ImportProposalNode:
    return ImportProposalNode(proposed_identifier=identifier, proposed_owner=owner)


def _cls(
    *,
    kind: ImportKind = ImportKind.DOCUMENT,
    type_code: str | None = "SOP",
    clauses: list[str] | None = None,
) -> ImportClassification:
    return ImportClassification(
        kind=kind,
        type_code=type_code,
        clause_numbers=clauses if clauses is not None else ["8.4"],
        process_names=None,
        band=ImportConfidenceBand.HIGH,
        ambiguous=False,
    )


def test_fold_no_decisions_is_undecided_kind_unconfirmed() -> None:
    st = fold_file_decisions([], _node(), _cls())
    assert st.disposition == "undecided"
    assert st.kind == "UNCONFIRMED"  # R10: the engine kind is NEVER auto-confirmed
    assert st.identifier == "SOP-QA-001"  # from the engine node
    assert st.type_code == "SOP"
    assert st.clause_numbers == ["8.4"]
    assert st.commit_ready is False


def test_fold_accept_without_kind_is_included_but_not_commit_ready() -> None:
    st = fold_file_decisions([_decision(ImportDecisionAction.ACCEPT, {})], _node(), _cls())
    assert st.disposition == "included"
    assert st.kind == "UNCONFIRMED"
    assert st.commit_ready is False  # R10: included but kind not confirmed → not committable


def test_fold_accept_with_kind_confirm_is_commit_ready() -> None:
    st = fold_file_decisions(
        [_decision(ImportDecisionAction.ACCEPT, {"kind": "DOCUMENT"})], _node(), _cls()
    )
    assert st.disposition == "included"
    assert st.kind == "DOCUMENT"
    assert st.commit_ready is True


def test_fold_exclude_wins_over_a_prior_accept() -> None:
    # newest-first: the latest decision (exclude) sets the disposition even after an accept+kind.
    decisions = [
        _decision(ImportDecisionAction.EXCLUDE, {}),  # newest
        _decision(ImportDecisionAction.ACCEPT, {"kind": "DOCUMENT"}),  # older
    ]
    st = fold_file_decisions(decisions, _node(), _cls())
    assert st.disposition == "excluded"
    assert st.commit_ready is False  # excluded → never commit-ready (the design-critic case)


def test_fold_defer_is_deferred() -> None:
    st = fold_file_decisions([_decision(ImportDecisionAction.DEFER, {})], _node(), _cls())
    assert st.disposition == "deferred"
    assert st.commit_ready is False


def test_fold_correct_overrides_identifier_and_clauses() -> None:
    decisions = [
        _decision(
            ImportDecisionAction.CORRECT,
            {"identifier": "SOP-QA-099", "clause_numbers": ["7.5"], "kind": "RECORD"},
        )
    ]
    st = fold_file_decisions(decisions, _node(identifier="SOP-QA-001"), _cls(clauses=["8.4"]))
    assert st.identifier == "SOP-QA-099"  # human correction wins over the engine node
    assert st.clause_numbers == ["7.5"]
    assert st.kind == "RECORD"
    assert st.commit_ready is True


def test_fold_latest_kind_confirm_wins() -> None:
    # A later correct re-confirming kind=DOCUMENT overrides an earlier RECORD confirm.
    decisions = [
        _decision(ImportDecisionAction.CORRECT, {"kind": "DOCUMENT"}),  # newest
        _decision(ImportDecisionAction.CORRECT, {"kind": "RECORD"}),  # older
    ]
    st = fold_file_decisions(decisions, _node(), _cls())
    assert st.kind == "DOCUMENT"


def test_validate_after_rejects_unknown_dimension() -> None:
    with pytest.raises(ProblemException) as exc:
        _validate_after(ImportDecisionAction.CORRECT, {"bogus": 1})
    assert exc.value.status == 422


def test_validate_after_kind_must_be_document_or_record() -> None:
    with pytest.raises(ProblemException):
        _validate_after(
            ImportDecisionAction.CORRECT, {"kind": "UNKNOWN"}
        )  # R10: not a confirmation
    # DOCUMENT/RECORD are accepted.
    assert _validate_after(ImportDecisionAction.ACCEPT, {"kind": "DOCUMENT"}) == {
        "kind": "DOCUMENT"
    }


def test_validate_after_correct_needs_a_dimension() -> None:
    with pytest.raises(ProblemException):
        _validate_after(ImportDecisionAction.CORRECT, {})  # a correct that changes nothing
    # accept/exclude/defer may carry an empty after.
    assert _validate_after(ImportDecisionAction.EXCLUDE, None) == {}


def test_validate_after_list_dimensions_must_be_string_lists() -> None:
    with pytest.raises(ProblemException):
        _validate_after(ImportDecisionAction.CORRECT, {"clause_numbers": [1, 2]})
    assert _validate_after(ImportDecisionAction.CORRECT, {"clause_numbers": ["8.4"]}) == {
        "clause_numbers": ["8.4"]
    }


def test_coerce_action_rejects_garbage_and_accepts_the_closed_set() -> None:
    with pytest.raises(ProblemException):
        _coerce_action("frobnicate")
    assert _coerce_action("merge") is ImportDecisionAction.MERGE
    assert _coerce_action("accept") is ImportDecisionAction.ACCEPT


def test_coverage_projection_status_logic() -> None:
    # The projection only ever IMPROVES coverage: GAP/PARTIAL → COVERED if the clause is projected.
    assert coverage_status(0, 0) == "GAP"
    assert coverage_status(2, 0) == "PARTIAL"
    assert coverage_status(2, 1) == "COVERED"
