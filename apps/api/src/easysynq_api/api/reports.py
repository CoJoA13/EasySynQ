"""Reporting surface (slice S10, doc 13 §3.1/§6, doc 15 §8.15).

The MVP ships the org-wide **Compliance Checklist** (★ mandatory-item coverage) only; dashboards,
the canonical document-control reports, async export, and evidence packs are deferred (v1). Gated on
the dedicated SYSTEM key ``report.compliance_checklist.read`` (doc 07 §3.8) — the default-SYSTEM
``require(...)`` shape (the ``GET /clauses`` precedent). Held by QMS Owner and (per S10's 0021
backfill) Internal Auditor.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..services.authz import require
from ..services.reports import compute_checklist

router = APIRouter(prefix="/api/v1", tags=["reports"])

# report.compliance_checklist.read is SYSTEM-scoped (the org-wide coverage view) → default SYSTEM
# scope, no resolver (the GET /clauses shape).
_checklist_read = require("report.compliance_checklist.read")


@router.get("/reports/compliance-checklist")
async def compliance_checklist_endpoint(
    caller: AppUser = Depends(_checklist_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The ★ mandatory-clause coverage view: per-clause COVERED/PARTIAL/GAP + a rollup RAG."""
    return await compute_checklist(session, caller.org_id)
