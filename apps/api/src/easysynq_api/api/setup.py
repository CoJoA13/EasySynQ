"""The first-run setup wizard surface (slice S8a, doc 08).

All routes are latch-exempt (``main.py`` lets ``/api/v1/setup*`` through while ``setup_state !=
OPERATIONAL``). ``/setup/state`` and the two first-administrator operations are public: the
bootstrap secret is their complete pre-authentication authority. ``/setup`` requires
``config.read`` because it exposes configuration and gate diagnostics; later configuration steps
use the permissions held by the newly provisioned System Administrator.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.app_user import AppUser
from ..db.session import get_session
from ..services.authz import require
from ..services.setup import (
    FirstAdministratorProfile,
    acknowledge_first_administrator,
    configure_auth,
    configure_backup,
    finalize_setup,
    get_setup_detail,
    get_setup_state,
    provision_first_administrator,
    set_org_profile,
    trigger_restore_test,
    verify_storage,
)

router = APIRouter(prefix="/api/v1", tags=["setup"])

# Both config permissions are SYSTEM-scoped in the System Administrator bundle (doc 07 §3.9).
_config_read = require("config.read")
_config_update = require("config.update")
# storage.manage gates the WORM-verify step (doc 07 §3.9 / doc 15 §8.17); also in that bundle.
_storage_manage = require("storage.manage")
# S8b2: backup.configure records the policy; restore.run runs the gating drill (both in the bundle).
_backup_configure = require("backup.configure")
_restore_run = require("restore.run")


class FirstAdministratorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1, max_length=512)
    username: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    email: str | None = Field(default=None, max_length=320)
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)


class BootstrapAcknowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1, max_length=512)
    credential_receipt: str = Field(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
    )


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


class ConfigureAuthRequest(BaseModel):
    method: str = "LOCAL"  # LOCAL | FEDERATED (the app always authenticates via Keycloak/OIDC)
    mfa_acknowledged: bool = False


@router.get("/setup/state")
async def setup_state_endpoint(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """The latch state — PUBLIC (no auth) so the SPA can choose wizard-vs-shell before sign-in.
    Minimal disclosure (just the enum)."""
    return {"setup_state": (await get_setup_state(session)).value}


@router.get("/setup")
async def setup_detail_endpoint(
    caller: AppUser = Depends(_config_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The wizard's sensitive config/gate view. Requires ``config.read``."""
    return await get_setup_detail(session, caller)


@router.post("/setup/administrator")
async def setup_administrator_endpoint(
    body: FirstAdministratorRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Provision or recover the one administrator bound by the bootstrap secret."""
    result = await provision_first_administrator(
        session,
        secret=body.secret,
        profile=FirstAdministratorProfile(
            username=body.username,
            display_name=body.display_name,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
        ),
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return {
        "administrator": {
            "id": str(result.admin_user_id),
            "username": result.username,
            "display_name": result.display_name,
            "email": result.email,
            "status": "INVITED",
        },
        "temporary_password": result.temporary_password,
        "credential_receipt": result.credential_receipt,
        "password_delivery": "shown_once",
    }


@router.post("/setup/administrator/acknowledge")
async def setup_administrator_acknowledge_endpoint(
    body: BootstrapAcknowledgeRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Acknowledge receipt of the volatile password and advance the setup latch."""
    return await acknowledge_first_administrator(
        session,
        secret=body.secret,
        credential_receipt=body.credential_receipt,
    )


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
    """Record an absolute non-root POSIX backup path after a preliminary API-context filesystem
    probe. Needs ``backup.configure``. This does not certify the worker mount or persistent backing
    and does NOT satisfy G-C — the worker restore-test drill must PASS."""
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


@router.post("/setup/configure-auth")
async def setup_configure_auth_endpoint(
    body: ConfigureAuthRequest,
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Record the auth method + prove a non-bootstrap login (gate G-D, doc 08 §9). Needs
    ``config.update``. The caller's valid non-bootstrap JWT + a live OIDC-issuer reachability probe
    are the proof; an unreachable IdP → 422 ``auth_unavailable`` (G-D stays unsatisfied)."""
    return await configure_auth(
        session, caller, method=body.method, mfa_acknowledged=body.mfa_acknowledged
    )


@router.post("/setup/finalize")
async def setup_finalize_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Re-check the gates live and flip the latch to OPERATIONAL. Needs ``config.update``."""
    return await finalize_setup(session, caller)
