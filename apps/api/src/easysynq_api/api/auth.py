"""Auth & session surface (slice S1). The SPA runs OIDC Authorization-Code + PKCE
directly against Keycloak; the API validates the resulting access JWT.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..config import get_settings
from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..services.authz.effective import compute_effective_permissions

router = APIRouter(prefix="/api/v1", tags=["auth"])


@router.get("/auth/config")
async def auth_config() -> dict[str, str]:
    """Public: the realm/client/authority the SPA needs to start PKCE."""
    s = get_settings()
    return {
        "issuer": s.oidc_issuer,
        "client_id": s.oidc_client_id,
        "audience": s.oidc_audience,
    }


def _represent(user: AppUser) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "keycloak_subject": user.keycloak_subject,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status.value,
        "is_guest": user.is_guest,
    }


@router.get("/me")
async def me(user: AppUser = Depends(get_current_user)) -> dict[str, Any]:
    return _represent(user)


@router.get("/me/permissions")
async def me_permissions(
    scope_level: str | None = None,
    scope_id: str | None = None,
    user: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The caller's OWN effective permission set at a scope (default SYSTEM) — the data source for
    DP-6 affordance gating in the SPA (S-web-3). Authentication-only (it reports the caller's own
    grants, so it gates nothing — the ``/me`` precedent; not ``user.read``-gated, which would lock
    out an ordinary author from discovering their own affordances). The optional
    ``scope_level``/``scope_id`` let the UI ask a scoped question (e.g. ``document.create`` at a
    DOC_CLASS). A global SYSTEM answer is coarse — per-document write buttons gate on the document's
    ``capabilities`` block instead (``GET /documents/{id}``)."""
    return await compute_effective_permissions(
        session,
        user_id=user.id,
        org_id=user.org_id,
        scope_level=scope_level,
        scope_id=scope_id,
    )


@router.post("/auth/step-up")
async def step_up(user: AppUser = Depends(get_current_user)) -> dict[str, Any]:
    """Reserved Part-11 seam: no step-up is enforced in v1, but the endpoint exists
    so signing actions can later demand re-auth/MFA without new routes."""
    return {"user_id": str(user.id), "acr_satisfied": True, "enforced": False}
