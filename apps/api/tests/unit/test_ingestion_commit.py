"""Unit proofs for the S-ing-5 commit slice — the pure/in-memory bits (the DB-bound per-item commit
flow is the integration suite). Covers: the identifier parse helper (area derivation), the
state-machine membership guards (the #1 reaper trap), the fold's identifier-collidable rule (the
sentinel false-collision fix), decided_by, the commit-result enum, and the Import Report.
"""

from __future__ import annotations

import pytest

from easysynq_api.db.models._ingestion_enums import (
    IMPORT_COMMIT_RESULT_STATUS_VALUES,
    ImportCommitResultStatus,
    ImportDecisionAction,
    ImportRunStatus,
)
from easysynq_api.db.models.import_decision import ImportDecision
from easysynq_api.db.models.import_proposal_node import ImportProposalNode
from easysynq_api.domain.ingestion.import_report import (
    CommittedItem,
    FailedItem,
    ImportReportData,
    render_import_report,
)
from easysynq_api.domain.vault.identifier import format_identifier, parse_identifier
from easysynq_api.services.ingestion import repository as repo
from easysynq_api.services.ingestion import service as svc
from easysynq_api.services.ingestion.commit import _decided_by
from easysynq_api.services.ingestion.review import fold_file_decisions

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- parse_identifier


def test_parse_identifier_round_trips_format() -> None:
    assert parse_identifier("SOP-PUR-014") == ("SOP", "PUR", 14)
    assert parse_identifier(format_identifier("SOP", 14, "PUR")) == ("SOP", "PUR", 14)


def test_parse_identifier_area_omitted() -> None:
    assert parse_identifier("SOP-014") == ("SOP", None, 14)
    assert parse_identifier(format_identifier("POL", 3)) == ("POL", None, 3)


def test_parse_identifier_non_conforming_has_no_seq() -> None:
    # A preserved code with no trailing numeric segment → area/seq are None (caller defaults GEN).
    parsed = parse_identifier("QM-MANUAL")
    assert parsed.type_code == "QM"
    assert parsed.area_code is None
    assert parsed.seq is None


def test_parse_identifier_multi_segment_area() -> None:
    assert parse_identifier("SOP-PUR-A-002") == ("SOP", "PUR-A", 2)


# --------------------------------------------------------------------------- state-machine guards


def test_committing_excluded_from_lock_liveness_and_active_sets() -> None:
    # The #1 trap: a lock-free commit state must NOT be in the lock-liveness reaper's set nor the
    # active-run set (commit holds no source-root lock → the reaper would instantly FAIL it).
    for st in (ImportRunStatus.COMMITTING, ImportRunStatus.PARTIALLY_COMMITTED):
        assert st not in svc._IN_PROGRESS
        assert st not in repo._ACTIVE_STATES
        assert st not in svc._TERMINAL  # in-flight / resumable — not "done"


def test_completed_is_terminal_but_not_active() -> None:
    assert ImportRunStatus.COMPLETED in svc._TERMINAL
    assert ImportRunStatus.COMPLETED not in svc._IN_PROGRESS
    assert ImportRunStatus.COMPLETED not in repo._ACTIVE_STATES


def test_cancel_blocked_covers_commit_region() -> None:
    # Cancel must 409 once a vault write has happened (committing/committed) — §11.4 no-rollback.
    for st in (
        ImportRunStatus.COMMITTING,
        ImportRunStatus.PARTIALLY_COMMITTED,
        ImportRunStatus.COMPLETED,
    ):
        assert st in svc._CANCEL_BLOCKED
    # but a human review rest-state stays cancellable.
    assert ImportRunStatus.REVIEWING not in svc._CANCEL_BLOCKED


def test_commit_start_and_resume_sets() -> None:
    assert svc._COMMIT_START == (ImportRunStatus.PROPOSED, ImportRunStatus.REVIEWING)
    assert svc._COMMIT_RESUME == (ImportRunStatus.PARTIALLY_COMMITTED,)


# --------------------------------------------------------------------------- the fold (collidable)


def _node(identifier: str | None, source: str | None) -> ImportProposalNode:
    return ImportProposalNode(proposed_identifier=identifier, identifier_source=source)


def test_fold_preserved_code_is_collidable() -> None:
    st = fold_file_decisions([], _node("SOP-PUR-014", "preserved_doc_code"), None)
    assert st.identifier == "SOP-PUR-014"
    assert st.identifier_source == "preserved_doc_code"
    assert st.identifier_collidable is True


def test_fold_sentinel_is_not_collidable() -> None:
    # The "{type}-<new>" suggested default must NOT collide (it is allocated fresh at commit).
    st = fold_file_decisions([], _node("SOP-<new>", "suggested_default"), None)
    assert st.identifier_collidable is False


def test_fold_human_corrected_identifier_is_collidable() -> None:
    dec = ImportDecision(
        action=ImportDecisionAction.CORRECT, after={"kind": "DOCUMENT", "identifier": "SOP-QA-009"}
    )
    st = fold_file_decisions([dec], _node("SOP-<new>", "suggested_default"), None)
    assert st.identifier == "SOP-QA-009"
    assert st.identifier_source == "human"
    assert st.identifier_collidable is True
    assert st.commit_ready is True  # included (correct) + kind-confirmed


# --------------------------------------------------------------------------- decided_by


def test_decided_by_engine_vs_human() -> None:
    accept = ImportDecision(action=ImportDecisionAction.ACCEPT, after={"kind": "DOCUMENT"})
    correct = ImportDecision(action=ImportDecisionAction.CORRECT, after={"type_code": "WI"})
    assert _decided_by([accept]) == "engine_confirmed"
    assert _decided_by([correct, accept]) == "human_corrected"
    assert _decided_by([]) == "engine_confirmed"


# --------------------------------------------------------------------------- commit-result enum


def test_commit_result_status_values() -> None:
    assert IMPORT_COMMIT_RESULT_STATUS_VALUES == ("success", "failed", "noop")
    assert ImportCommitResultStatus.SUCCESS.value == "success"


# --------------------------------------------------------------------------- Import Report renderer


def _report_data(**over: object) -> ImportReportData:
    base: dict[str, object] = dict(
        run_id="run-1",
        source_root="/srv/import/source/qms",
        created_by="u-create",
        committed_by="u-commit",
        classifier_version="rule-heuristic-1",
        final_status="Completed",
        counts={"included": 3, "commit": {"committed": 2, "failed": 1}},
        committed=[
            CommittedItem("SOP-PUR-014", "DOCUMENT", "purchasing/sop.docx", "engine_confirmed"),
            CommittedItem("REC-GEN-001", "RECORD", "records/audit.pdf", "human_corrected"),
        ],
        failed=[FailedItem("forms/qm.docx", "form_template_import_unsupported")],
        star_coverage={"covered": 4, "total": 20},
    )
    base.update(over)
    return ImportReportData(**base)  # type: ignore[arg-type]


def test_import_report_render_contains_sections_and_items() -> None:
    md = render_import_report(_report_data())
    assert md.startswith("# Import Report — /srv/import/source/qms")
    for header in ("## Run", "## Counts", "## Committed items", "## Failed items"):
        assert header in md
    assert "SOP-PUR-014" in md and "REC-GEN-001" in md
    assert "form_template_import_unsupported" in md
    assert "rule-heuristic-1" in md


def test_import_report_render_handles_empty_sets() -> None:
    md = render_import_report(_report_data(committed=[], failed=[], star_coverage=None))
    assert "_(nothing committed)_" in md
    assert "_(no failures)_" in md
    # a pipe in a value must not break the table (escaped)
    md2 = render_import_report(
        _report_data(failed=[FailedItem("a|b.docx", "err|or")], committed=[])
    )
    assert "a\\|b.docx" in md2
