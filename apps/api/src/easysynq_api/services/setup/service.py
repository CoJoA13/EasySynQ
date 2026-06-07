"""Setup use-cases — bootstrap-of-trust, org profile, finalize + the gate registry (S8a, doc 08).

The flow: an operator mints a bootstrap secret (``cli/setup.py``), the first user POSTs it to the
public ``/setup/bootstrap`` (outside the PEP) and is granted ``System Administrator`` — breaking the
deny-by-default chicken-and-egg; then that admin sets the org profile and finalizes, flipping the
``setup_state`` latch ``UNINITIALIZED → IN_SETUP → OPERATIONAL``. Finalize re-checks the registered
:data:`GATES` live (doc 08 §14.2). S8a registers **G-A** (admin) + **G-E** (org); **G-B** (WORM),
**G-C/AC#5** (restore-drill), **G-D** (auth) append to ``GATES`` in S8b/S8c.

Audit rows are appended directly (object types ``config``/``user``) and commit atomically with the
state change — the app role holds INSERT on ``audit_event`` (S6); the chain-linker picks them up.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import re
import uuid
import zoneinfo
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.backup_policy import BackupPolicy
from ...db.models.organization import Organization
from ...db.models.role import Role, RoleAssignment
from ...db.models.storage_config import StorageConfig
from ...db.models.system_config import SetupState, SystemConfig
from ...logging import request_id_var
from ...problems import ProblemException
from ..audit.checkpoint import tamper_evidence_attested
from ..backup import configure_backup_destination_check
from ..vault import storage
from . import auth_check
from .bootstrap import verify_secret

_OBJECT_LOCK_MODES = frozenset({"GOVERNANCE", "COMPLIANCE"})
_AUTH_METHODS = frozenset({"LOCAL", "FEDERATED"})

logger = logging.getLogger("easysynq.setup")

SYSTEM_ADMIN_ROLE = "System Administrator"
_DEFAULT_SHORT_CODE = "DEFAULT"
_SHORT_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")

_RL_KEY = "setup:bootstrap:fails"
_RL_MAX = 5
_RL_WINDOW_SECONDS = 900  # 5 attempts / 15 min (doc 08 §4)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _maybe_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


# --- audit ------------------------------------------------------------------------------


def _emit(
    session: AsyncSession,
    *,
    event_type: str,
    actor: AppUser,
    object_type: AuditObjectType,
    object_id: uuid.UUID,
    after: dict[str, Any] | None = None,
) -> None:
    """Append a setup ``audit_event`` row to the session (no commit) — commits atomically with the
    state change it records. Hashes stay NULL until the chain-linker fills them (R12)."""
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=EventType(event_type),
            object_type=object_type,
            object_id=object_id,
            after=after,
            request_id=_maybe_uuid(request_id_var.get()),
        )
    )


# --- rate limit (best-effort; never blocks the happy path if Redis is down) -------------


def _redis() -> Any:
    return aioredis.from_url(get_settings().redis_url, decode_responses=True)  # type: ignore[no-untyped-call]


async def _check_rate_limit() -> None:
    try:
        async with _redis() as client:
            fails = await client.get(_RL_KEY)
        if fails is not None and int(fails) >= _RL_MAX:
            raise ProblemException(
                status=429,
                code="rate_limited",
                title="Too many bootstrap attempts; try again later",
            )
    except ProblemException:
        raise
    except Exception:  # noqa: BLE001 — best-effort: a Redis outage must not brick bootstrap
        logger.warning("setup.bootstrap: rate-limit check skipped (redis unavailable)")


async def _record_failure() -> None:
    try:
        async with _redis() as client:
            count = await client.incr(_RL_KEY)
            if count == 1:
                await client.expire(_RL_KEY, _RL_WINDOW_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("setup.bootstrap: failed to record a bootstrap failure (redis unavailable)")


async def _reset_failures() -> None:
    try:
        async with _redis() as client:
            await client.delete(_RL_KEY)
    except Exception:  # noqa: BLE001
        logger.debug("setup.bootstrap: failed to reset the failure counter (redis unavailable)")


# --- gate registry ----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Gate:
    key: str
    description: str
    check: Callable[[AsyncSession, uuid.UUID], Awaitable[bool]]


async def _gate_admin_exists(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """G-A: at least one user holds the System Administrator role in this org."""
    row = await session.scalar(
        select(RoleAssignment.id)
        .join(Role, RoleAssignment.role_id == Role.id)
        .where(RoleAssignment.org_id == org_id, Role.name == SYSTEM_ADMIN_ROLE)
        .limit(1)
    )
    return row is not None


async def _gate_org_profile_set(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """G-E: the org profile has been completed (short_code is no longer the seeded placeholder)."""
    code = await session.scalar(select(Organization.short_code).where(Organization.id == org_id))
    return code is not None and code != _DEFAULT_SHORT_CODE


async def _gate_worm_verified(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """G-B (S8b): the vault bucket's object-lock WORM enforcement was verified (doc 08 §7.2)."""
    verified = await session.scalar(
        select(StorageConfig.worm_verified_at).where(StorageConfig.org_id == org_id)
    )
    return verified is not None


async def _gate_restore_test_passed(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """G-C / AC#5 (S8b2): a backup→restore-into-scratch drill PASSED (doc 08 §8). Keys on the
    persisted result == 'PASS' — NOT merely ``last_restore_test_at`` non-null, since a FAILED drill
    also stamps the timestamp. "Configured but unverified" (null result) does not satisfy."""
    result = await session.scalar(
        select(BackupPolicy.last_restore_test_result).where(BackupPolicy.org_id == org_id)
    )
    return result == "PASS"


async def _gate_auth_configured(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """G-D (S8c): an auth method was configured and a non-bootstrap login proven (doc 08 §9). Keys
    on the persisted ``auth_test_login_ok is True`` — NOT just ``auth_test_login_at`` non-null (a
    FAILED probe stamps the timestamp too). Null/False reads as G-D-unsatisfied (no false-PASS)."""
    ok = await session.scalar(
        select(SystemConfig.auth_test_login_ok).where(SystemConfig.org_id == org_id)
    )
    return ok is True


# Gates land incrementally. S8a: G-A (admin) + G-E (org); S8b: G-B (WORM); S8b2: G-C (restore);
# S8c: G-D (auth). finalize re-checks GATES live (doc 08 §14.2) — appending here is the only change
# a gate needs. (The off-host audit-sink anchor is a SOFT gate — see ``get_setup_detail`` — and is
# deliberately NOT in this list: it warns but never blocks finalize, R13 / doc 08 §8.3.)
GATES: list[Gate] = [
    Gate("G-A", "A System Administrator has been assigned", _gate_admin_exists),
    Gate("G-E", "The organization profile has been set", _gate_org_profile_set),
    Gate("G-B", "Vault object-lock (WORM) was verified", _gate_worm_verified),
    Gate("G-C", "A backup/restore drill has passed", _gate_restore_test_passed),
    Gate(
        "G-D",
        "An auth method was configured and a non-bootstrap login proven",
        _gate_auth_configured,
    ),
]


async def _gate_status(session: AsyncSession, org_id: uuid.UUID) -> dict[str, bool]:
    return {gate.key: await gate.check(session, org_id) for gate in GATES}


# --- repository-ish helpers -------------------------------------------------------------


async def _load_config(
    session: AsyncSession, org_id: uuid.UUID, *, lock: bool = False
) -> SystemConfig:
    stmt = select(SystemConfig).where(SystemConfig.org_id == org_id)
    if lock:
        stmt = stmt.with_for_update()
    cfg = (await session.execute(stmt)).scalar_one_or_none()
    if cfg is None:  # pragma: no cover - 0012 seeds the singleton row
        raise ProblemException(
            status=409, code="setup_not_initialized", title="System is not initialized for setup"
        )
    return cfg


# --- use-cases --------------------------------------------------------------------------


async def get_setup_state(session: AsyncSession) -> SetupState:
    """The singleton latch state (the latch middleware + the public /setup/state read this). A
    missing row reads as UNINITIALIZED (locked) defensively."""
    state = await session.scalar(select(SystemConfig.setup_state).limit(1))
    return state or SetupState.UNINITIALIZED


async def get_setup_detail(session: AsyncSession, actor: AppUser) -> dict[str, Any]:
    cfg = await _load_config(session, actor.org_id)
    org = await session.get(Organization, actor.org_id)
    policy = await session.scalar(select(BackupPolicy).where(BackupPolicy.org_id == actor.org_id))
    return {
        "setup_state": cfg.setup_state.value,
        "gates": await _gate_status(session, actor.org_id),
        "org_profile": {
            "legal_name": org.legal_name if org else None,
            "short_code": org.short_code if org else None,
            "timezone": org.timezone if org else None,
        },
        "backup": {
            "configured": policy is not None,
            "destination": policy.destination if policy else None,
            "last_restore_test_at": (
                policy.last_restore_test_at.isoformat()
                if policy and policy.last_restore_test_at
                else None
            ),
            "last_restore_test_result": policy.last_restore_test_result if policy else None,
        },
        "auth": {
            "configured": cfg.auth_test_login_ok is True,
            "method": cfg.auth_method,
            "last_test_at": (
                cfg.auth_test_login_at.isoformat() if cfg.auth_test_login_at else None
            ),
        },
        # Soft gate (R13 / doc 08 §8.3): finalize is NEVER blocked on this, but an install with no
        # fresh off-host audit anchor is loudly flagged NOT tamper-evident until one is configured.
        "tamper_evident": await tamper_evidence_attested(session, actor.org_id),
    }


async def bootstrap_admin(session: AsyncSession, actor: AppUser, secret: str) -> dict[str, Any]:
    """Consume the one-time bootstrap secret and grant the caller System Administrator (the
    bootstrap-of-trust). Single-use + TTL'd; transitions UNINITIALIZED → IN_SETUP."""
    await _check_rate_limit()
    cfg = await _load_config(session, actor.org_id, lock=True)

    if cfg.setup_state == SetupState.OPERATIONAL:
        raise ProblemException(
            status=409, code="setup_already_complete", title="Setup is already complete"
        )
    if cfg.bootstrap_secret_hash is None:
        raise ProblemException(
            status=409,
            code="no_bootstrap_secret",
            title="No bootstrap secret has been minted (run: easysynq setup mint-bootstrap)",
        )
    if cfg.bootstrap_consumed_at is not None:
        raise ProblemException(
            status=409, code="bootstrap_already_consumed", title="The bootstrap secret was used"
        )
    if cfg.bootstrap_expires_at is not None and _now() > cfg.bootstrap_expires_at:
        raise ProblemException(
            status=403, code="bootstrap_expired", title="The bootstrap secret has expired"
        )
    if not verify_secret(secret, cfg.bootstrap_secret_hash):
        await _record_failure()
        raise ProblemException(
            status=403, code="bootstrap_invalid", title="Invalid bootstrap secret"
        )

    role = await session.scalar(
        select(Role).where(Role.org_id == actor.org_id, Role.name == SYSTEM_ADMIN_ROLE)
    )
    if role is None:  # pragma: no cover - the role is seeded in 0004
        raise ProblemException(
            status=500, code="role_missing", title="System Administrator role is not seeded"
        )
    already = await session.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.user_id == actor.id, RoleAssignment.role_id == role.id
        )
    )
    if already is None:
        session.add(
            RoleAssignment(org_id=actor.org_id, user_id=actor.id, role_id=role.id, bound_scope=None)
        )

    cfg.bootstrap_consumed_at = _now()
    if cfg.setup_state == SetupState.UNINITIALIZED:
        cfg.setup_state = SetupState.IN_SETUP

    _emit(
        session,
        event_type="BOOTSTRAP_CONSUMED",
        actor=actor,
        object_type=AuditObjectType.config,
        object_id=actor.org_id,
    )
    _emit(
        session,
        event_type="ADMIN_BOOTSTRAPPED",
        actor=actor,
        object_type=AuditObjectType.user,
        object_id=actor.id,
        after={"role": SYSTEM_ADMIN_ROLE},
    )
    await session.commit()
    await _reset_failures()
    return {"setup_state": cfg.setup_state.value, "admin_user_id": str(actor.id)}


async def set_org_profile(
    session: AsyncSession, actor: AppUser, *, legal_name: str, short_code: str, timezone: str
) -> dict[str, Any]:
    """Set the org profile (G-E); timezone is authoritative for effective dates (R8)."""
    legal_name = legal_name.strip()
    short_code = short_code.strip().upper()
    if not legal_name:
        raise ProblemException(
            status=422, code="validation_error", title="legal_name must not be empty"
        )
    if not _SHORT_CODE_RE.match(short_code) or short_code == _DEFAULT_SHORT_CODE:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="short_code must be 2-32 chars [A-Z0-9-] and not 'DEFAULT'",
        )
    try:
        zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise ProblemException(
            status=422, code="validation_error", title=f"Unknown IANA timezone: {timezone!r}"
        ) from exc

    org = await session.get(Organization, actor.org_id)
    if org is None:  # pragma: no cover - the org is seeded in 0002
        raise ProblemException(status=404, code="not_found", title="Organization not found")
    org.legal_name = legal_name
    org.short_code = short_code
    org.timezone = timezone
    _emit(
        session,
        event_type="ORG_PROFILE_SET",
        actor=actor,
        object_type=AuditObjectType.config,
        object_id=actor.org_id,
        after={"short_code": short_code, "timezone": timezone},
    )
    await session.commit()
    return {"legal_name": legal_name, "short_code": short_code, "timezone": timezone}


async def verify_storage(
    session: AsyncSession, actor: AppUser, *, object_lock_mode: str
) -> dict[str, Any]:
    """Verify the vault bucket enforces WORM (gate G-B, doc 08 §7) and record the result + the
    operator's object-lock-mode choice (D-7). PASS sets ``storage_config.worm_verified_at`` + emits
    WORM_VERIFIED; a bucket that does NOT enforce WORM is a 422 (the gate signal stays null)."""
    mode = object_lock_mode.strip().upper()
    if mode not in _OBJECT_LOCK_MODES:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="object_lock_mode must be GOVERNANCE or COMPLIANCE",
        )
    probe = await storage.worm_probe()
    if not probe.verified:
        raise ProblemException(
            status=422,
            code="worm_not_enforced",
            title="The vault bucket does not enforce WORM object-lock",
            detail=probe.detail,
        )
    # Serialize per-org setup mutations on the system_config singleton (matches bootstrap_admin /
    # finalize_setup) so a concurrent same-org verify-storage can't lose the check-then-insert race
    # on storage_config's UNIQUE(org_id) and surface an unhandled IntegrityError.
    await _load_config(session, actor.org_id, lock=True)
    cfg = await session.scalar(select(StorageConfig).where(StorageConfig.org_id == actor.org_id))
    if cfg is None:
        cfg = StorageConfig(org_id=actor.org_id)
        session.add(cfg)
    cfg.worm_verified_at = _now()
    cfg.object_lock_mode = mode
    _emit(
        session,
        event_type="WORM_VERIFIED",
        actor=actor,
        object_type=AuditObjectType.config,
        object_id=actor.org_id,
        after={"object_lock_mode": mode, "detail": probe.detail},
    )
    await session.commit()
    return {
        "worm_verified": True,
        "object_lock_mode": mode,
        "retain_until": probe.retain_until.isoformat() if probe.retain_until else None,
    }


_CRON_RE = re.compile(r"^\S+(\s+\S+){4}$")  # 5 whitespace-separated fields (light MVP check)


async def configure_backup(
    session: AsyncSession,
    actor: AppUser,
    *,
    destination: str,
    cron: str,
    retention_daily: int,
    retention_weekly: int,
    retention_monthly: int,
    encryption_key_ref: str | None = None,
    alert_sink: str | None = None,
    wal_pitr_enabled: bool = False,
) -> dict[str, Any]:
    """Record the admin-controlled backup policy (doc 08 §8.1) + a live destination writability
    check. Does NOT satisfy G-C on its own — the restore-test drill must PASS (configured ≠
    verified)."""
    destination = destination.strip()
    if not destination:
        raise ProblemException(
            status=422, code="validation_error", title="backup destination must not be empty"
        )
    if not _CRON_RE.match(cron.strip()):
        raise ProblemException(
            status=422, code="validation_error", title="cron must be a 5-field schedule"
        )
    if retention_daily < 1 or retention_weekly < 0 or retention_monthly < 0:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="retention must keep ≥1 daily and be non-negative",
        )
    if wal_pitr_enabled:
        # The column is a recorded forward-seam; continuous WAL/PITR is S11/v1.x (D-6). Reject true
        # so the scope boundary is enforced, not silently accepted (the scheduler ignores it today).
        raise ProblemException(
            status=422,
            code="wal_pitr_unavailable",
            title="WAL/PITR is reserved for a later release (D-6); leave wal_pitr_enabled false",
        )
    ok, detail = await asyncio.to_thread(configure_backup_destination_check, destination)
    if not ok:
        raise ProblemException(
            status=422,
            code="backup_destination_unreachable",
            title="The backup destination is not reachable/writable",
            detail=detail,
        )

    # Serialize per-org setup mutations on the system_config singleton (matches verify_storage) so a
    # concurrent same-org configure-backup cannot lose the check-then-insert race on UNIQUE(org_id).
    await _load_config(session, actor.org_id, lock=True)
    policy = await session.scalar(select(BackupPolicy).where(BackupPolicy.org_id == actor.org_id))
    if policy is None:
        policy = BackupPolicy(org_id=actor.org_id)
        session.add(policy)
    policy.destination = destination
    policy.cron = cron.strip()
    policy.retention_daily = retention_daily
    policy.retention_weekly = retention_weekly
    policy.retention_monthly = retention_monthly
    policy.encryption_key_ref = encryption_key_ref
    policy.alert_sink = alert_sink
    policy.wal_pitr_enabled = wal_pitr_enabled
    _emit(
        session,
        event_type="BACKUP_CONFIGURED",
        actor=actor,
        object_type=AuditObjectType.config,
        object_id=actor.org_id,
        after={"destination": destination, "cron": cron.strip()},
    )
    await session.commit()
    return {
        "configured": True,
        "destination": destination,
        "cron": cron.strip(),
        "detail": detail,
    }


async def trigger_restore_test(session: AsyncSession, actor: AppUser) -> dict[str, Any]:
    """Enqueue the backup→restore-into-scratch drill (gate G-C). The drill is an async worker task
    (it may take minutes — RTO target ≤2h, doc 08 §8.2); finalize reads the PERSISTED result, never
    runs it inline. Requires a configured backup policy first.

    If a drill is already running, the worker task takes the ``LOCK_RESTORE_DRILL`` advisory lock,
    finds it held, and SKIPS without persisting — so ``last_restore_test_result`` is unchanged and a
    poller sees no new result (the operator simply re-runs). The endpoint still returns 202; the
    enqueue succeeded, the drill just deduplicated against the in-flight one."""
    policy = await session.scalar(
        select(BackupPolicy.id).where(BackupPolicy.org_id == actor.org_id)
    )
    if policy is None:
        raise ProblemException(
            status=409,
            code="backup_not_configured",
            title="Configure a backup destination before running the restore-test",
        )
    # Lazy import — tasks import services, so importing at module top would risk a cycle.
    from ...tasks.backup import backup_restore_test

    backup_restore_test.delay(str(actor.org_id), str(actor.id))
    return {"status": "enqueued"}


async def configure_auth(
    session: AsyncSession,
    actor: AppUser,
    *,
    method: str,
    mfa_acknowledged: bool = False,
) -> dict[str, Any]:
    """Record the primary auth method + prove a non-bootstrap login works (gate G-D, doc 08 §9).

    The proof has two legs, both required: (1) the **caller's valid non-bootstrap JWT** — this
    endpoint runs inside the PEP (``config.update`` + ``get_current_user``), whereas the bootstrap
    path authorizes via the install *secret* outside the PEP, so reaching here at all proves a real
    Keycloak login works; (2) a **live OIDC-issuer reachability probe** (the realm the app validates
    tokens against is reachable + self-consistent) so a misconfigured/unreachable IdP can't strand
    the org. A failed probe → 422 ``auth_unavailable`` + AUTH_TEST_LOGIN_FAILED (signal stays null →
    no false-PASS). MFA is a logged acknowledgement only (enforcement is the reserved Part-11 seam,
    D3); local break-glass login is never disabled here, so the org cannot be locked out."""
    method = method.strip().upper()
    if method not in _AUTH_METHODS:
        raise ProblemException(
            status=422, code="validation_error", title="method must be LOCAL or FEDERATED"
        )

    verified, detail = await auth_check.probe_oidc_discovery(
        get_settings().oidc_issuer, get_settings().oidc_discovery_url or None
    )
    if not verified:
        cfg = await _load_config(session, actor.org_id, lock=True)
        cfg.auth_test_login_at = _now()
        cfg.auth_test_login_ok = False
        _emit(
            session,
            event_type="AUTH_TEST_LOGIN_FAILED",
            actor=actor,
            object_type=AuditObjectType.config,
            object_id=actor.org_id,
            after={"method": method, "detail": detail},
        )
        await session.commit()
        raise ProblemException(
            status=422,
            code="auth_unavailable",
            title="The configured identity provider is not reachable/well-formed",
            detail=detail,
        )

    # Serialize per-org setup mutations on the system_config singleton (matches verify_storage /
    # configure_backup) so a concurrent same-org configure-auth can't race the read-then-write.
    cfg = await _load_config(session, actor.org_id, lock=True)
    cfg.auth_method = method
    cfg.auth_test_login_ok = True
    cfg.auth_test_login_at = _now()
    _emit(
        session,
        event_type="AUTH_CONFIGURED",
        actor=actor,
        object_type=AuditObjectType.config,
        object_id=actor.org_id,
        after={
            "method": method,
            "mfa_acknowledged": mfa_acknowledged,
            "break_glass_local_login": True,
        },
    )
    _emit(
        session,
        event_type="AUTH_TEST_LOGIN_OK",
        actor=actor,
        object_type=AuditObjectType.config,
        object_id=actor.org_id,
        after={"method": method},
    )
    await session.commit()
    return {"auth_test_login_ok": True, "method": method, "detail": detail}


async def finalize_setup(session: AsyncSession, actor: AppUser) -> dict[str, Any]:
    """Re-check the registered gates live and flip the latch to OPERATIONAL (doc 08 §14)."""
    cfg = await _load_config(session, actor.org_id, lock=True)
    if cfg.setup_state == SetupState.OPERATIONAL:
        raise ProblemException(
            status=409, code="setup_already_complete", title="Setup is already complete"
        )

    status = await _gate_status(session, actor.org_id)
    failed = [
        {"key": gate.key, "description": gate.description}
        for gate in GATES
        if not status.get(gate.key, False)
    ]
    if failed:
        raise ProblemException(
            status=409,
            code="setup_gates_unsatisfied",
            title="Cannot finalize: required setup gates are not satisfied",
            members={"failed_gates": failed},
        )

    cfg.setup_state = SetupState.OPERATIONAL
    cfg.finalized_at = _now()
    _emit(
        session,
        event_type="SETUP_FINALIZED",
        actor=actor,
        object_type=AuditObjectType.config,
        object_id=actor.org_id,
        after={
            "gates": status
        },  # the full {gate: bool} snapshot (sorted(dict) would drop the bools)
    )
    await session.commit()
    return {"setup_state": cfg.setup_state.value, "finalized_at": cfg.finalized_at.isoformat()}
