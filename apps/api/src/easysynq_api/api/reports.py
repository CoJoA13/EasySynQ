"""Reporting surface (slice S10, doc 13 §3.1/§6, doc 15 §8.15).

The MVP ships the org-wide **Compliance Checklist** (★ mandatory-item coverage) only; dashboards,
the canonical document-control reports, async export, and evidence packs are deferred (v1). Gated on
the dedicated SYSTEM key ``report.compliance_checklist.read`` (doc 07 §3.8) — the default-SYSTEM
``require(...)`` shape (the ``GET /clauses`` precedent). Held by QMS Owner and (per S10's 0021
backfill) Internal Auditor.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..config import get_settings
from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..domain.authz import Effect, RequestContext, ResourceContext, ScopeLevel
from ..domain.authz.pdp import _predicates_pass
from ..problems import ProblemException
from ..services.authz import gather_grants, require
from ..services.common.org_clock import current_org_tz
from ..services.reports import compute_checklist
from ..services.reports.document_control import (
    build_provenance,
    compute_document_control_register,
)
from .documents import parse_document_filters_with_applied

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
#
# FIX A (Codex round 2, P1): the surface gate must also evaluate each candidate grant's ABAC
# predicates (valid_from/valid_until/ip_allow) BEFORE the effect+level check — otherwise an
# expired/not-yet-valid/wrong-IP report.read ALLOW still admits (and an expired/future DENY blocks
# forever). Reuses the PDP's own predicate evaluator (``_predicates_pass``) so this gate matches
# the exact semantics ``authorize()`` would apply, rather than re-implementing a parallel (and
# potentially divergent) check. ``ResourceContext.system()`` is the right resource to evaluate
# against: report.read is an org-level surface permission with no artifact/folder/process-bound
# target, and ``_predicates_pass`` only reads context-only predicates (valid_from/until, ip_allow,
# read_only) plus resource-bound ones (lifecycle_state, requirement_source) that are nonsensical on
# a report.read grant — those simply never match ``ResourceContext.system()``'s all-None fields, so
# such a predicate fails safe (drops the grant) rather than raising, which is acceptable here.
_SURFACE_LEVELS = frozenset({ScopeLevel.SYSTEM, ScopeLevel.PROCESS})


@router.get("/reports/document-control")
async def document_control_register_endpoint(
    request: Request,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The Controlled Document Register (ISO 9001 §7.5.3 master list) — a provenance-stamped,
    content-hashed master list of every controlled Document the caller may read. Full set (no
    pagination); facet filters via the shared ``filter[field][op]`` grammar. Read-only (no
    audit_event).

    FIX C (Codex round 2, P2, two-connection overlap): ``get_current_user``/``get_session`` already
    hold one DB connection (connection #1) for the whole request. The register materialization
    (``compute_document_control_register``) opens its OWN REPEATABLE READ session (connection #2)
    for the whole-org scan + batched enrichment — a genuinely long-running read. Concurrent reports
    each holding #1 while waiting on #2 risk a pool-timeout deadlock under load. Fix: capture the
    caller identity into locals (no I/O — already-loaded columns), run the surface gate (FIX A) on
    the still-open request session, THEN ``await session.rollback()`` to release connection #1
    *before* calling the service — so at most ONE connection is checked out during materialization.
    The ORM ``caller`` object is expired by ``rollback()``; every subsequent read in this handler
    uses ONLY the captured locals, never ``caller`` again."""
    uid, org_id, display = (
        caller.id,
        caller.org_id,
        caller.display_name or caller.email or str(caller.id),
    )
    source_ip = request.client.host if request.client else None

    # --- surface gate (FIX A) — still on connection #1, before it's released ---
    report_grants = await gather_grants(session, uid, org_id, "report.read")
    gate_ctx = RequestContext(now=datetime.datetime.now(datetime.UTC), source_ip=source_ip)
    active = [
        g
        for g in report_grants
        if g.level in _SURFACE_LEVELS
        and _predicates_pass(g, ResourceContext.system(), gate_ctx, "report.read")
    ]
    if not any(g.effect == Effect.ALLOW for g in active) or any(
        g.effect == Effect.DENY for g in active
    ):
        raise ProblemException(status=403, code="forbidden", title="report.read required")

    # FIX D: echo only the filter[...] keys the parser actually accepted (matched the bracket
    # grammar AND allow-listed) — a malformed/unknown key the parser silently ignores must never
    # appear in provenance.filters as if it had narrowed the row set.
    filters, applied = parse_document_filters_with_applied(request)

    # FIX C: release connection #1 before the long materialization opens connection #2. After this
    # point ``caller``/``session`` are never touched again — only the captured locals above and the
    # service's own snapshot session.
    await session.rollback()

    result = await compute_document_control_register(
        user_id=uid, org_id=org_id, source_ip=source_ip, filters=filters
    )
    # FIX B: generated_at/as_of are the snapshot instant CAPTURED INSIDE the service's REPEATABLE
    # READ transaction (a ``SELECT now()`` there == the txn/snapshot start), not a later wall-clock
    # read taken after that transaction (and its connection) already closed.
    generated_at = result.snapshot_at.astimezone(current_org_tz())
    provenance = build_provenance(
        generated_by=display,
        generated_at=generated_at,
        scope=f"org:{result.org_short_code}",
        app_version=get_settings().version,
        filters=applied,
        row_count=result.row_count,
        content_hash=result.content_hash,
    )
    return {"provenance": provenance, "rows": result.rows}
