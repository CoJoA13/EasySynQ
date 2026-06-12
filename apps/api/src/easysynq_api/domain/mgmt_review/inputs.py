"""Pure 9.3.2 input projections (S-mr-1, clause 9.3) — read-shape → JSON-safe summary dict.

``compile.py`` orchestrates the six owner-grant-gated reads; this module owns the deterministic
shape→summary projection so the orchestration stays thin. Each projection returns a plain
JSON-safe ``dict`` (the ``source_ref.summary`` content) — every leaf a JSON primitive (int/str/
bool/None/nested dict-of-those), because the compiled minutes freeze through ``rfc8785.dumps`` at
submit (a Decimal/UUID/datetime leaf would TypeError there).

The input row shapes are pinned to the LIVE service signatures (verified against source):
- ``summarize_audits`` — ``list_audits`` 4-tuples ``(Audit, identifier, title, created_at)``.
- ``summarize_capas_ncrs`` — ``list_capas`` 4-tuples, ``list_ncrs`` BARE ``Ncr`` rows, and
  ``list_complaints`` 2-tuples ``(Complaint, identifier)``.
- ``summarize_scorecard`` — the inline scorecard dict ``compile.py`` builds from
  ``list_objectives`` + the ``resolve_commitment``/``rag_status`` RAG loop.
- ``summarize_process_perf`` — ``compute_checklist`` + ``drift_status`` (no org_id) outputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def summarize_scorecard(scorecard: dict[str, Any]) -> dict[str, Any]:
    """Project the objectives scorecard dict (the ``api/objectives.py`` shape ``compile.py``
    reproduces) → ``{total, on_target, by_rag}``. The ``by_rag`` keys are EXACTLY
    ``{green, amber, red, unmeasured}``; ``on_target`` is the green count."""
    by_rag = scorecard.get("by_rag", {})
    return {
        "total": int(scorecard.get("total", 0)),
        "on_target": int(scorecard.get("on_target", 0)),
        "by_rag": {
            "green": int(by_rag.get("green", 0)),
            "amber": int(by_rag.get("amber", 0)),
            "red": int(by_rag.get("red", 0)),
            "unmeasured": int(by_rag.get("unmeasured", 0)),
        },
    }


def summarize_audits(rows: Sequence[tuple[Any, Any, Any, Any]]) -> dict[str, Any]:
    """``list_audits`` 4-tuples → ``{total, open, closed}``. Open = state is not Closed (the
    ``AuditState.Closed`` terminal). The audit is the FIRST tuple element."""
    total = 0
    closed = 0
    for row in rows:
        audit = row[0]
        total += 1
        state = getattr(audit.state, "value", audit.state)
        if state == "Closed":
            closed += 1
    return {"total": total, "open": total - closed, "closed": closed}


def summarize_capas_ncrs(
    capas: Sequence[tuple[Any, Any, Any, Any]],
    ncrs: Sequence[Any],
    complaints: Sequence[tuple[Any, Any]],
) -> dict[str, Any]:
    """``{open_ncrs, open_capas, complaints, by_close_state}``.

    - ``capas`` are ``list_capas`` 4-tuples — open = ``close_state`` is not ``Closed``.
    - ``ncrs`` are BARE ``Ncr`` rows — open = ``disposition`` is None (undisposed 8.7).
    - ``complaints`` are ``list_complaints`` 2-tuples — informational count only.
    - ``by_close_state`` is the per-state CAPA tally (close-state value → count)."""
    by_close_state: dict[str, int] = {}
    open_capas = 0
    for row in capas:
        capa = row[0]
        cs = getattr(capa.close_state, "value", capa.close_state)
        by_close_state[cs] = by_close_state.get(cs, 0) + 1
        if cs != "Closed":
            open_capas += 1
    open_ncrs = sum(1 for n in ncrs if getattr(n, "disposition", None) is None)
    return {
        "open_ncrs": open_ncrs,
        "open_capas": open_capas,
        "complaints": len(complaints),
        "by_close_state": by_close_state,
    }


def summarize_kpis(*, readings: int, objectives_measured: int) -> dict[str, Any]:
    """``{readings, objectives_measured}`` — the org-wide KPI-measurement count + the distinct
    measured-objective count (``compile.py`` runs the two COUNT queries)."""
    return {"readings": int(readings), "objectives_measured": int(objectives_measured)}


def summarize_process_perf(
    checklist: dict[str, Any], drift: dict[str, Any] | None
) -> dict[str, Any]:
    """``compute_checklist`` rollup + ``drift_status`` → ``{star_coverage, overdue_reviews,
    integrity}``. ``drift`` is None when the owner lacks ``drift.read`` (the checklist part still
    projects; integrity → None)."""
    rollup = checklist.get("rollup", {})
    integrity: dict[str, Any] | None = None
    if drift is not None:
        coverage = drift.get("blob_coverage", {})
        superseded = drift.get("superseded_copies", {})
        integrity = {
            "blobs": int(coverage.get("total", 0)),
            "failing": int(coverage.get("failing", 0)),
            "superseded_copies": int(superseded.get("copies", 0)),
        }
    return {
        "star_coverage": {
            "total": int(rollup.get("total", 0)),
            "covered": int(rollup.get("covered", 0)),
            "partial": int(rollup.get("partial", 0)),
            "gap": int(rollup.get("gap", 0)),
        },
        "overdue_reviews": int(rollup.get("overdue_review", 0)),
        "integrity": integrity,
    }


def summarize_prior_actions(
    prior_identifier: str, outputs: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """The previous released MR's outputs + their spawned task terminal states (each ``outputs``
    row is ``{output_type, task_state}``). ``{prior_review, action_outputs, actions_done}``."""
    action_outputs = 0
    actions_done = 0
    for row in outputs:
        if row.get("output_type") == "ACTION":
            action_outputs += 1
            if row.get("task_state") == "DONE":
                actions_done += 1
    return {
        "prior_review": prior_identifier,
        "action_outputs": action_outputs,
        "actions_done": actions_done,
    }
