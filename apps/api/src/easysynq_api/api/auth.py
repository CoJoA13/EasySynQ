"""Auth & session surface (slice S1). The SPA runs OIDC Authorization-Code + PKCE
directly against Keycloak; the API validates the resulting access JWT.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..auth.dependencies import get_current_user
from ..config import get_settings
from ..db.models.app_user import AppUser

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


@router.post("/auth/step-up")
async def step_up(user: AppUser = Depends(get_current_user)) -> dict[str, Any]:
    """Reserved Part-11 seam: no step-up is enforced in v1, but the endpoint exists
    so signing actions can later demand re-auth/MFA without new routes."""
    return {"user_id": str(user.id), "acr_satisfied": True, "enforced": False}
