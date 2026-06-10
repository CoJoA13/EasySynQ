"""The thin admin drift-status surface (S-drift-3, doc 05 §9.1, doc 15) — gated drift.read (R41).

Two cheap, pure reads (no scan trigger, no side effect — pure GETs): the latest drift_scan per
kind (the S-drift-2 ``(kind, started_at DESC)`` index read) + D1 blob coverage + the D4
superseded-copies report. ``drift.read`` is the R38-additive SYSTEM-domain key seeded in 0047 and
granted to System Administrator — as-built the SYSTEM-domain key IS the admin gate (the
``config.update`` precedent). The S-web-8 UI consumes exactly this surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..services.authz import require
from ..services.vault import drift_report

router = APIRouter(prefix="/api/v1", tags=["admin"])

# drift.read is SYSTEM-domain / admin-side (doc 07 §3.9, R41) — operational integrity status.
_drift_read = require("drift.read")


@router.get("/admin/drift/status")
async def drift_status_endpoint(
    caller: AppUser = Depends(_drift_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Latest scan per kind + the D1 rolling-cursor coverage + the D4 headline. Needs drift.read."""
    return await drift_report.drift_status(session)


@router.get("/admin/drift/superseded-copies")
async def superseded_copies_endpoint(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    caller: AppUser = Depends(_drift_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The D4 report: outstanding EXPORTED/PRINTED copies of now-superseded versions (doc 05
    §9.1 D4 / R11 — the only detection leg that reaches copies outside the mirror). Needs
    drift.read."""
    return await drift_report.superseded_copies(session, limit=limit, offset=offset)
