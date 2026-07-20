"""Reporting surface (slice S10, doc 13 §3.1/§6, doc 15 §8.15).

The MVP ships the org-wide **Compliance Checklist** (★ mandatory-item coverage) only; dashboards,
the canonical document-control reports, async export, and evidence packs are deferred (v1). Gated on
the dedicated SYSTEM key ``report.compliance_checklist.read`` (doc 07 §3.8) — the default-SYSTEM
``require(...)`` shape (the ``GET /clauses`` precedent). Held by QMS Owner and (per S10's 0021
backfill) Internal Auditor.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..config import get_settings
from ..db.models.app_user import AppUser
from ..db.models.organization import Organization
from ..db.session import get_session
from ..domain.authz import Effect, ScopeLevel
from ..problems import ProblemException
from ..services.authz import gather_grants, require
from ..services.common.org_clock import current_org_tz
from ..services.reports import compute_checklist
from ..services.reports.document_control import (
    build_provenance,
    compute_document_control_register,
)
from .documents import _parse_document_filters

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


# report.read is seeded at SYSTEM scope (QMS Owner/Internal Auditor) AND at PROCESS scope (the
# built-in Process Owner — migrations/versions/0004_seed_authz.py _PROCESS_OWNER_KEYS). The
# register is an org-level surface, so the SURFACE gate here admits any report.read ALLOW at
# SYSTEM or PROCESS scope (an ARTIFACT-scoped guest grant stays excluded — the spec keeps guests
# on Evidence Packs; a plain Employee with no report.read grant is refused here) UNLESS a
# report.read DENY also exists at one of those levels — deny-always-wins (R3 / AZ-INV-2) must hold
# at the surface gate too, not just the per-row filter below (a narrow lower-scope DENY outside
# _SURFACE_LEVELS is out of scope for this check; the per-row filter remains the data boundary for
# it). Rows are then filtered per-row by document.read inside the service (doc 13 §6.1 "all
# Documents the requester may see") — a Process Owner admitted here still only sees their
# linked-process docs.
_SURFACE_LEVELS = frozenset({ScopeLevel.SYSTEM, ScopeLevel.PROCESS})


async def _org_short_code(session: AsyncSession, org_id: uuid.UUID) -> str:
    org = await session.get(Organization, org_id)
    return org.short_code if org else str(org_id)


@router.get("/reports/document-control")
async def document_control_register_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The Controlled Document Register (ISO 9001 §7.5.3 master list) — a provenance-stamped,
    content-hashed master list of every controlled Document the caller may read. Full set (no
    pagination); facet filters via the shared ``filter[field][op]`` grammar. Read-only (no
    audit_event)."""
    report_grants = await gather_grants(session, caller.id, caller.org_id, "report.read")
    surface_grants = [g for g in report_grants if g.level in _SURFACE_LEVELS]
    if not any(g.effect == Effect.ALLOW for g in surface_grants) or any(
        g.effect == Effect.DENY for g in surface_grants
    ):
        raise ProblemException(status=403, code="forbidden", title="report.read required")
    filters = _parse_document_filters(request)
    source_ip = request.client.host if request.client else None
    result = await compute_document_control_register(caller, filters=filters, source_ip=source_ip)
    # echo the applied filter[...] params (for hash reproducibility) — only the filter[...] keys,
    # grouping repeated values per key (a repeated filter[clause_refs][has] is treated as AND by
    # the parser, so collapsing to the last value via plain .items() would misrepresent the applied
    # query and break hash reproducibility).
    applied: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        if k.startswith("filter["):
            applied.setdefault(k, []).append(v)
    generated_at = datetime.datetime.now(current_org_tz())
    provenance = build_provenance(
        generated_by=(caller.display_name or caller.email or str(caller.id)),
        generated_at=generated_at,
        scope=f"org:{await _org_short_code(session, caller.org_id)}",
        app_version=get_settings().version,
        filters=applied,
        row_count=result.row_count,
        content_hash=result.content_hash,
    )
    return {"provenance": provenance, "rows": result.rows}
