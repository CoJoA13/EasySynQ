"""Health surface: liveness (/healthz) and readiness (/readyz).

Mounted at the app root (not behind /api/v1) so orchestrator/Compose healthchecks
and Caddy gating reach them without auth. The public readiness payload reports
dependency names and booleans only; internal diagnostics remain available to
trusted callers of ``check_all``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from ..config import get_settings
from ..readiness import check_all

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "api", "version": get_settings().version}


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    checks = await check_all()
    ready = all(d["ready"] for d in checks)
    if not ready:
        response.status_code = 503
    dependencies = [{"name": check["name"], "ready": check["ready"]} for check in checks]
    return {"ready": ready, "dependencies": dependencies}
