"""The org-wide Compliance Checklist (slice S10, doc 13 §3.1/§5.1, doc 02 §2.1, Register R30).

Computes ★ mandatory-clause coverage from the authoritative PostgreSQL ``clause_mapping`` join —
never from the search index (doc 13 §1.2: "reports and KPIs compute from PostgreSQL only"). Each
``is_mandatory_star`` clause (the doc 02 §2.1 20-item set, incl. 8.5.6 / R30) is scored:

  * ``COVERED``  — ≥1 mapped document has an **Effective** version (current_effective_version_id).
  * ``PARTIAL``  — mapped, but no mapped document is Effective yet (Draft/InReview/Approved).
  * ``GAP``      — no document maps to the clause.

This is **status against a rule, never an auto-compliance verdict** (doc 13 N9). Coverage counts
**any** clause mapping (``is_requirement_level`` ignored — a finer, rarely-set qualifier) targeting
the **exact** ★ clause (no subtree rollup — the discrete-item intuition of doc 02 §2.1). The view is
org-wide (gated on the SYSTEM key ``report.compliance_checklist.read``), so counts are not per-doc
permission-filtered. Each row also carries ``overdue_review`` (True when ≥1 Effective mapped doc has
``next_review_due`` ≤ today) — orthogonal to the COVERED/PARTIAL/GAP status; the rollup carries
``overdue_review`` as the COUNT of flagged rows. Deferred (need unbuilt
schema): the "linked evidence" leg (records), and R31 scope-conditional coverage (Scope authoring
unbuilt) — all ★ rows are shown unconditionally.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._vault_enums import DocumentCurrentState
from ...db.models.clause import Clause
from ...db.models.clause_mapping import ClauseMapping
from ...db.models.documented_information import DocumentedInformation
from ..vault.repository import get_framework
from ..vault.review import today_org


def coverage_status(mapped: int, effective: int) -> str:
    """Pure coverage verdict for one ★ clause (Mapped+Effective semantics, owner-decided)."""
    if effective > 0:
        return "COVERED"
    if mapped > 0:
        return "PARTIAL"
    return "GAP"


def _number_key(number: str) -> list[int]:
    """Numeric clause-number sort key ('8.5' < '8.10', '9' < '10') — 20 rows, sorted in Python."""
    return [int(part) for part in number.split(".")]


async def compute_checklist(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    projected_clause_numbers: set[str] | None = None,
) -> dict[str, Any]:
    """The org's ★ mandatory-item coverage: per-clause status rows + a rollup RAG. One grouped query
    over ``clause`` LEFT JOIN ``clause_mapping`` (org-scoped) LEFT JOIN documented_information.

    ``projected_clause_numbers`` (S-ing-4 pre-commit projection, doc 09 §9.3): when ``None`` (the
    live ``GET /reports/compliance-checklist`` call) the output is byte-identical to the org-wide
    read. When a set of clause CODES is given (the import's confirmed-DOCUMENT keep-items' effective
    ★ clauses), each row gains a ``projected_status`` (= ``COVERED`` if already live-COVERED **or**
    the number is in the set, else the live status — an import only ever IMPROVES coverage) and the
    result gains a ``projected`` rollup. The non-blocking "which ★ items the import *appears*
    to satisfy" advisory (N9 — it NEVER asserts compliance; the projected docs aren't live)."""
    framework = await get_framework(session, org_id)
    if (
        framework is None
    ):  # pragma: no cover - a finalized org always has its iso9001:2015 framework
        empty: dict[str, Any] = {
            "framework": "iso9001:2015",
            "rollup": {"total": 0, "covered": 0, "partial": 0, "gap": 0},
            "rows": [],
        }
        if projected_clause_numbers is not None:
            empty["projected_rollup"] = {"total": 0, "covered": 0, "partial": 0, "gap": 0}
        return empty

    today = today_org()
    mapped_count = func.count(func.distinct(DocumentedInformation.id))
    effective_count = func.count(func.distinct(DocumentedInformation.id)).filter(
        DocumentedInformation.current_effective_version_id.isnot(None)
    )
    overdue_count = (
        sa.func.count(sa.distinct(DocumentedInformation.id))
        .filter(
            DocumentedInformation.current_state == DocumentCurrentState.Effective,
            DocumentedInformation.next_review_due.is_not(None),
            DocumentedInformation.next_review_due <= today,
        )
        .label("overdue")
    )
    rows = (
        await session.execute(
            select(
                Clause.id,
                Clause.number,
                Clause.title,
                Clause.pdca_phase,
                mapped_count.label("mapped"),
                effective_count.label("effective"),
                overdue_count,
            )
            .select_from(Clause)
            .outerjoin(
                ClauseMapping,
                sa.and_(
                    ClauseMapping.clause_id == Clause.id,
                    ClauseMapping.org_id == org_id,
                ),
            )
            .outerjoin(
                DocumentedInformation,
                DocumentedInformation.id == ClauseMapping.documented_information_id,
            )
            .where(Clause.framework_id == framework.id, Clause.is_mandatory_star.is_(True))
            .group_by(Clause.id, Clause.number, Clause.title, Clause.pdca_phase)
        )
    ).all()

    projecting = projected_clause_numbers is not None
    projected = projected_clause_numbers or set()
    out_rows: list[dict[str, Any]] = []
    covered = partial = gap = 0
    proj_covered = proj_partial = proj_gap = 0
    overdue_review_count = 0
    for clause_id, number, title, pdca_phase, mapped, effective, overdue in rows:
        status = coverage_status(mapped, effective)
        if status == "COVERED":
            covered += 1
        elif status == "PARTIAL":
            partial += 1
        else:
            gap += 1
        overdue_flag = overdue > 0
        if overdue_flag:
            overdue_review_count += 1
        row: dict[str, Any] = {
            "clause_id": str(clause_id),
            "number": number,
            "title": title,
            "pdca_phase": pdca_phase.value,
            "mapped_count": mapped,
            "effective_count": effective,
            "status": status,
            "overdue_review": overdue_flag,
        }
        if projecting:
            # An import only ever IMPROVES coverage: a confirmed-DOCUMENT mapping to a GAP/PARTIAL
            # ★ clause → COVERED (it commits as a Rev A Effective baseline); never demotes.
            proj_status = "COVERED" if status == "COVERED" or number in projected else status
            row["projected_status"] = proj_status
            if proj_status == "COVERED":
                proj_covered += 1
            elif proj_status == "PARTIAL":
                proj_partial += 1
            else:
                proj_gap += 1
        out_rows.append(row)
    out_rows.sort(key=lambda r: _number_key(r["number"]))
    result: dict[str, Any] = {
        "framework": framework.code,
        "rollup": {
            "total": len(out_rows),
            "covered": covered,
            "partial": partial,
            "gap": gap,
            "overdue_review": overdue_review_count,
        },
        "rows": out_rows,
    }
    if projecting:
        result["projected_rollup"] = {
            "total": len(out_rows),
            "covered": proj_covered,
            "partial": proj_partial,
            "gap": proj_gap,
        }
    return result
