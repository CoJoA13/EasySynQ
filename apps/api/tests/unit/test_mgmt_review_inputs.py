"""S-mr-1 Task 15 — the pure 9.3.2 input projections (read-shape → JSON-safe summary dict).

Each ``summarize_*`` takes the EXACT shape of its source read (verified against the live service
signatures — the 4-tuple list_audits/list_capas, the 2-tuple list_complaints, the bare-row
list_ncrs, the no-org_id drift_status, the scorecard-dict objectives loop) and returns a plain
JSON-safe dict. Pure, no I/O — every leaf must be a JSON primitive (rfc8785 freezes them at submit,
so a Decimal/UUID leaf would TypeError there)."""

from __future__ import annotations

import datetime

import rfc8785

from easysynq_api.domain.mgmt_review.inputs import (
    summarize_audits,
    summarize_capas_ncrs,
    summarize_kpis,
    summarize_prior_actions,
    summarize_process_perf,
    summarize_scorecard,
)


def _json_safe(d: object) -> None:
    """rfc8785.dumps raises on any non-JSON-safe leaf — proves every leaf is a primitive."""
    assert isinstance(rfc8785.dumps(d), bytes)


def test_summarize_scorecard_counts_by_rag() -> None:
    sc = {
        "total": 5,
        "on_target": 3,
        "by_rag": {"green": 3, "amber": 1, "red": 1, "unmeasured": 0},
    }
    out = summarize_scorecard(sc)
    assert out == {"total": 5, "on_target": 3, "by_rag": sc["by_rag"]}
    _json_safe(out)


def test_summarize_audits_open_vs_closed() -> None:
    # 4-tuples (audit, identifier, title, created_at); audit has a .state with a .value.
    class _A:
        def __init__(self, state: str) -> None:
            self.state = type("S", (), {"value": state})()

    now = datetime.datetime(2026, 6, 12, tzinfo=datetime.UTC)
    rows = [
        (_A("InProgress"), "AUD-001", "Q1 audit", now),
        (_A("Closed"), "AUD-002", "Q2 audit", now),
        (_A("Reported"), "AUD-003", "Q3 audit", now),
    ]
    out = summarize_audits(rows)
    assert out == {"total": 3, "open": 2, "closed": 1}
    _json_safe(out)


def test_summarize_capas_ncrs_counts_open_and_by_close_state() -> None:
    class _Capa:
        def __init__(self, close_state: str) -> None:
            self.close_state = type("CS", (), {"value": close_state})()

    now = datetime.datetime(2026, 6, 12, tzinfo=datetime.UTC)
    capas = [
        (_Capa("Raised"), "CAPA-1", "t", now),
        (_Capa("Closed"), "CAPA-2", "t", now),
        (_Capa("Verify"), "CAPA-3", "t", now),
    ]

    class _Ncr:
        def __init__(self, disposition: object) -> None:
            self.disposition = disposition

    # bare Ncr rows; an undisposed NCR (disposition None) is open.
    ncrs = [_Ncr(None), _Ncr(None), _Ncr(type("D", (), {"value": "UseAsIs"})())]

    class _Complaint:
        pass

    # 2-tuples (complaint, identifier).
    complaints = [(_Complaint(), "CMP-1"), (_Complaint(), "CMP-2")]

    out = summarize_capas_ncrs(capas, ncrs, complaints)
    assert out["open_capas"] == 2  # Raised + Verify open; Closed not
    assert out["open_ncrs"] == 2  # two undisposed
    assert out["complaints"] == 2
    assert out["by_close_state"] == {"Raised": 1, "Closed": 1, "Verify": 1}
    _json_safe(out)


def test_summarize_kpis_readings_and_objectives_measured() -> None:
    out = summarize_kpis(readings=7, objectives_measured=3)
    assert out == {"readings": 7, "objectives_measured": 3}
    _json_safe(out)


def test_summarize_process_perf_from_checklist_and_drift() -> None:
    checklist = {
        "rollup": {"total": 20, "covered": 18, "partial": 1, "gap": 1, "overdue_review": 2},
    }
    drift = {
        "blob_coverage": {"total": 100, "never_verified": 0, "failing": 0},
        "superseded_copies": {"versions": 0, "copies": 0},
    }
    out = summarize_process_perf(checklist, drift)
    assert out["star_coverage"] == {"total": 20, "covered": 18, "partial": 1, "gap": 1}
    assert out["overdue_reviews"] == 2
    assert out["integrity"] == {"blobs": 100, "failing": 0, "superseded_copies": 0}
    _json_safe(out)


def test_summarize_process_perf_tolerates_missing_drift() -> None:
    # drift can be None if the owner lacks drift.read — the checklist part still summarizes.
    checklist = {
        "rollup": {"total": 20, "covered": 18, "partial": 1, "gap": 1, "overdue_review": 0},
    }
    out = summarize_process_perf(checklist, None)
    assert out["star_coverage"]["total"] == 20
    assert out["integrity"] is None
    _json_safe(out)


def test_summarize_prior_actions_counts_outputs_and_done() -> None:
    # The previous released MR's outputs + their spawned task terminal states.
    rows = [
        {"output_type": "ACTION", "task_state": "DONE"},
        {"output_type": "ACTION", "task_state": "PENDING"},
        {"output_type": "DECISION", "task_state": None},
    ]
    out = summarize_prior_actions("MR-2025-001", rows)
    assert out["prior_review"] == "MR-2025-001"
    assert out["action_outputs"] == 2
    assert out["actions_done"] == 1
    _json_safe(out)
