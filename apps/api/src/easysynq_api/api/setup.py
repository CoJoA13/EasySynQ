"""The first-run setup wizard surface (slice S8a, doc 08).

All routes are latch-exempt (``main.py`` lets ``/api/v1/setup*`` through while ``setup_state !=
OPERATIONAL``). ``/setup/state`` is **public** so the SPA can route before sign-in; ``/setup`` +
``/setup/bootstrap`` are authenticated but run **outside the PEP** (the bootstrap secret — not a
grant — authorizes becoming the first admin, breaking the deny-by-default chicken-and-egg); the
config-mutating steps require ``config.update`` (held by the just-granted System Administrator).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..services.authz import require
from ..services.setup import (
    bootstrap_admin,
    finalize_setup,
    get_setup_detail,
    get_setup_state,
    set_org_profile,
    verify_storage,
)

router = APIRouter(prefix="/api/v1", tags=["setup"])

# config.update is a SYSTEM-scope permission in the System Administrator bundle (doc 07 §3.9).
_config_update = require("config.update")
# storage.manage gates the WORM-verify step (doc 07 §3.9 / doc 15 §8.17); also in that bundle.
_storage_manage = require("storage.manage")


class BootstrapRequest(BaseModel):
    secret: str


class OrgProfileUpdate(BaseModel):
    legal_name: str
    short_code: str
    timezone: str


class VerifyStorageRequest(BaseModel):
    object_lock_mode: str = "GOVERNANCE"


@router.get("/setup/state")
async def setup_state_endpoint(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The latch state — PUBLIC (no auth) so the SPA can choose wizard-vs-shell before sign-in.
    Minimal disclosure (just the enum)."""
    return {"setup_state": (await get_setup_state(session)).value}


@router.get("/setup")
async def setup_detail_endpoint(
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The wizard's view: state + live gate status + the current org profile. Authenticated (any
    signed-in user — needed before the caller is the admin), not PEP-gated."""
    return await get_setup_detail(session, caller)


@router.post("/setup/bootstrap")
async def setup_bootstrap_endpoint(
    body: BootstrapRequest,
    caller: AppUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Consume the one-time install secret and grant the caller System Administrator (doc 08 §4-5).
    Authenticated but OUTSIDE the PEP — the secret authorizes the first-admin grant."""
    return await bootstrap_admin(session, caller, body.secret)


@router.patch("/setup/org-profile")
async def setup_org_profile_endpoint(
    body: OrgProfileUpdate,
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Set the organization profile (Step 2 / G-E). Needs ``config.update``."""
    return await set_org_profile(
        session,
        caller,
        legal_name=body.legal_name,
        short_code=body.short_code,
        timezone=body.timezone,
    )


@router.post("/setup/verify-storage")
async def setup_verify_storage_endpoint(
    body: VerifyStorageRequest,
    caller: AppUser = Depends(_storage_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Verify the vault bucket enforces WORM object-lock (gate G-B) + record the object-lock mode
    (D-7). 422 ``worm_not_enforced`` if the bucket does not enforce it. Needs ``storage.manage``."""
    return await verify_storage(session, caller, object_lock_mode=body.object_lock_mode)


@router.post("/setup/finalize")
async def setup_finalize_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Re-check the gates live and flip the latch to OPERATIONAL. Needs ``config.update``."""
    return await finalize_setup(session, caller)
