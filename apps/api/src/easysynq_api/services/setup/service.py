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
from ...db.models.organization import Organization
from ...db.models.role import Role, RoleAssignment
from ...db.models.system_config import SetupState, SystemConfig
from ...logging import request_id_var
from ...problems import ProblemException
from .bootstrap import verify_secret

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


# S8a registers the gates whose configuration this slice builds. S8b/S8c append G-B/G-C/G-D.
GATES: list[Gate] = [
    Gate("G-A", "A System Administrator has been assigned", _gate_admin_exists),
    Gate("G-E", "The organization profile has been set", _gate_org_profile_set),
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
    return {
        "setup_state": cfg.setup_state.value,
        "gates": await _gate_status(session, actor.org_id),
        "org_profile": {
            "legal_name": org.legal_name if org else None,
            "short_code": org.short_code if org else None,
            "timezone": org.timezone if org else None,
        },
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
