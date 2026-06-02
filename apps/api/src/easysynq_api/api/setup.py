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
    configure_backup,
    finalize_setup,
    get_setup_detail,
    get_setup_state,
    set_org_profile,
    trigger_restore_test,
    verify_storage,
)

router = APIRouter(prefix="/api/v1", tags=["setup"])

# config.update is a SYSTEM-scope permission in the System Administrator bundle (doc 07 §3.9).
_config_update = require("config.update")
# storage.manage gates the WORM-verify step (doc 07 §3.9 / doc 15 §8.17); also in that bundle.
_storage_manage = require("storage.manage")
# S8b2: backup.configure records the policy; restore.run runs the gating drill (both in the bundle).
_backup_configure = require("backup.configure")
_restore_run = require("restore.run")


class BootstrapRequest(BaseModel):
    secret: str


class OrgProfileUpdate(BaseModel):
    legal_name: str
    short_code: str
    timezone: str


class VerifyStorageRequest(BaseModel):
    object_lock_mode: str = "GOVERNANCE"


class ConfigureBackupRequest(BaseModel):
    destination: str
    cron: str = "0 2 * * *"  # nightly 02:00 (doc 08 §8.1 default; org tz)
    retention_daily: int = 7
    retention_weekly: int = 4
    retention_monthly: int = 6
    encryption_key_ref: str | None = None
    alert_sink: str | None = None
    wal_pitr_enabled: bool = False


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


@router.post("/setup/configure-backup")
async def setup_configure_backup_endpoint(
    body: ConfigureBackupRequest,
    caller: AppUser = Depends(_backup_configure),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Record the backup policy (Step 4 config; doc 08 §8.1) + a live destination check. Needs
    ``backup.configure``. Does NOT satisfy G-C — the restore-test drill must PASS."""
    return await configure_backup(
        session,
        caller,
        destination=body.destination,
        cron=body.cron,
        retention_daily=body.retention_daily,
        retention_weekly=body.retention_weekly,
        retention_monthly=body.retention_monthly,
        encryption_key_ref=body.encryption_key_ref,
        alert_sink=body.alert_sink,
        wal_pitr_enabled=body.wal_pitr_enabled,
    )


@router.post("/setup/run-restore-test", status_code=202)
async def setup_run_restore_test_endpoint(
    caller: AppUser = Depends(_restore_run),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enqueue the backup→restore-into-scratch drill (gate G-C / AC#5). Async (it may take minutes);
    poll ``GET /setup`` for the persisted result. Needs ``restore.run``; 409 if no backup yet."""
    return await trigger_restore_test(session, caller)


@router.post("/setup/finalize")
async def setup_finalize_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Re-check the gates live and flip the latch to OPERATIONAL. Needs ``config.update``."""
    return await finalize_setup(session, caller)
