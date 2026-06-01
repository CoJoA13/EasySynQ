"""Health surface: liveness (/healthz) and readiness (/readyz).

Mounted at the app root (not behind /api/v1) so orchestrator/Compose healthchecks
and Caddy gating reach them without auth. The readiness payload mirrors the
``Readiness`` schema in packages/contracts/openapi.yaml.
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
    dependencies = await check_all()
    ready = all(d["ready"] for d in dependencies)
    if not ready:
        response.status_code = 503
    return {"ready": ready, "dependencies": dependencies}
