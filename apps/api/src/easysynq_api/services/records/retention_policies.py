"""Retention-policy management use-cases (slice S-rec-4, doc 06 §5.1, doc 15 §8.16).

The CRUD + soft-archive surface behind ``/api/v1/retention-policies``. Policies are *policy-as-data*
(reusable schedules); editing one propagates to already-captured records via live-deref off the
pinned ``retention_policy_id`` (the sweep reads ``policy.duration`` / ``disposition_action`` /
``review_required`` at sweep time). doc 06 §5.2 wants that ONLY for extensions ("an extension can be
applied forward; a reduction never applies to already-captured records"), so:

* **PATCH is extend-forward** — when a policy has >=1 non-DISPOSED pinned record, a duration
  reduction, a weaker ``disposition_action``, or a ``review_required`` true->false is refused 422.
  Shortening for FUTURE captures is done by archiving the policy + creating a shorter one.
* **Archive is soft** — a hard DELETE is blocked by 3 RESTRICT FKs; ``active=false`` hides the
  policy from new-capture resolution but records already pinned keep being swept under it.
* **The seeded System Default is protected** — it may not be archived, renamed, or have its
  ``applies_to`` changed (it must stay the always-present, no-auto-attach fallback).

Each mutation owns its transaction and writes an ``audit_event`` (object_type=retention_policy)
before commit (the AC#6 atomicity rule)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._retention_enums import DispositionAction, RetentionBasis
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.retention_policy import RetentionPolicy
from ...domain.records.retention import action_preservation_rank, duration_ge, retention_until
from ...problems import ProblemException
from . import repository as repo
from .repository import SYSTEM_DEFAULT_POLICY_NAME
from .service import _now, _rid

_APPLIES_TO_KEYS = frozenset({"record_type", "clause_id", "process_id"})
# The retention_policy fields a PATCH may set (everything but the system/audit columns).
_PATCHABLE = frozenset(
    {
        "name",
        "applies_to",
        "basis",
        "duration",
        "disposition_action",
        "review_required",
        "worm_lock_period",
    }
)


# --- errors ------------------------------------------------------------------------------


def _invalid(field: str, code: str, message: str) -> ProblemException:
    return ProblemException(
        status=422,
        code="validation_error",
        title=message,
        errors=[{"field": field, "code": code, "message": message}],
    )


def _conflict(code: str, title: str) -> ProblemException:
    return ProblemException(status=409, code=code, title=title)


# --- validation --------------------------------------------------------------------------


def _validate_duration(field: str, duration: str) -> None:
    """Reject a malformed duration (non-PERMANENT, non-ISO-8601-date). ``retention_until`` parses
    the same grammar the sweep uses, so a value that passes here can never crash the sweep."""
    if duration.strip().upper() == "PERMANENT":
        return
    try:
        retention_until(datetime.date(2000, 1, 1), duration)
    except ValueError:
        raise _invalid(
            field, "invalid_duration", f"Not an ISO-8601 duration: {duration!r}"
        ) from None


def _validate_applies_to(applies_to: dict[str, Any] | None) -> None:
    if applies_to is None:
        return
    keys = set(applies_to)
    if len(keys) != 1 or not keys <= _APPLIES_TO_KEYS:
        raise _invalid(
            "applies_to",
            "invalid_applies_to",
            "applies_to must be exactly one of {record_type|clause_id|process_id}",
        )
    (value,) = applies_to.values()
    if not isinstance(value, str) or not value.strip():
        raise _invalid(
            "applies_to", "invalid_applies_to", "applies_to value must be a non-empty string"
        )


def _validate_worm_lock(worm_lock_period: str | None, duration: str) -> None:
    """``worm_lock_period`` (if set) must be a valid duration and ≥ ``duration`` (doc 14 §10)."""
    if worm_lock_period is None:
        return
    _validate_duration("worm_lock_period", worm_lock_period)
    # A PERMANENT duration outlasts any finite lock → a finite lock would be < duration.
    if duration.strip().upper() == "PERMANENT" and worm_lock_period.strip().upper() != "PERMANENT":
        raise _invalid(
            "worm_lock_period", "worm_lock_too_short", "worm_lock_period must be ≥ duration"
        )
    if not duration_ge(worm_lock_period, duration):
        raise _invalid(
            "worm_lock_period", "worm_lock_too_short", "worm_lock_period must be ≥ duration"
        )


# --- audit -------------------------------------------------------------------------------


def _emit(
    session: AsyncSession,
    actor: AppUser,
    event_type: EventType,
    policy_id: uuid.UUID,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            org_id=actor.org_id,
            occurred_at=_now(),
            actor_id=actor.id,
            actor_type=ActorType.user,
            event_type=event_type,
            object_type=AuditObjectType.retention_policy,
            object_id=policy_id,
            before=before,
            after=after,
            request_id=_rid(),
        )
    )


# --- use-cases ---------------------------------------------------------------------------


async def create_policy(
    session: AsyncSession,
    actor: AppUser,
    *,
    name: str,
    applies_to: dict[str, Any] | None,
    basis: RetentionBasis,
    duration: str,
    disposition_action: DispositionAction,
    review_required: bool,
    worm_lock_period: str | None,
) -> RetentionPolicy:
    name = name.strip()
    if not name:
        raise _invalid("name", "required", "name is required")
    if name == SYSTEM_DEFAULT_POLICY_NAME:
        raise _invalid(
            "name", "reserved_name", "That name is reserved for the System Default policy"
        )
    _validate_applies_to(applies_to)
    _validate_duration("duration", duration)
    _validate_worm_lock(worm_lock_period, duration)
    if await repo.policy_by_name(session, actor.org_id, name) is not None:
        raise _conflict("name_taken", "A retention policy with that name already exists")

    policy = RetentionPolicy(
        org_id=actor.org_id,
        name=name,
        applies_to=applies_to,
        basis=basis,
        duration=duration,
        disposition_action=disposition_action,
        review_required=review_required,
        worm_lock_period=worm_lock_period,
    )
    session.add(policy)
    await session.flush()
    _emit(
        session,
        actor,
        EventType.RETENTION_POLICY_CREATED,
        policy.id,
        after={
            "name": name,
            "duration": duration,
            "disposition_action": disposition_action.value,
            "review_required": review_required,
        },
    )
    await session.commit()
    await session.refresh(policy)
    return policy


async def update_policy(
    session: AsyncSession,
    actor: AppUser,
    policy_id: uuid.UUID,
    *,
    changes: dict[str, Any],
) -> RetentionPolicy:
    """Apply a partial update (only supplied fields). Enforces System-Default protection + the
    extend-forward ratchet when the policy has pinned records (doc 06 §5.2)."""
    policy = await repo.get_policy(session, policy_id, actor.org_id)
    if policy is None:
        raise ProblemException(status=404, code="not_found", title="Retention policy not found")

    changes = {k: v for k, v in changes.items() if k in _PATCHABLE}
    is_system_default = policy.name == SYSTEM_DEFAULT_POLICY_NAME

    if is_system_default:
        if "name" in changes and changes["name"].strip() != SYSTEM_DEFAULT_POLICY_NAME:
            raise _conflict(
                "system_default_protected", "The System Default policy cannot be renamed"
            )
        if "applies_to" in changes:
            raise _conflict(
                "system_default_protected",
                "The System Default policy's applies_to is fixed (the no-auto-attach fallback)",
            )

    if "name" in changes:
        new_name = changes["name"].strip()
        if not new_name:
            raise _invalid("name", "required", "name cannot be empty")
        if new_name == SYSTEM_DEFAULT_POLICY_NAME and not is_system_default:
            raise _invalid("name", "reserved_name", "That name is reserved for the System Default")
        existing = await repo.policy_by_name(session, actor.org_id, new_name)
        if existing is not None and existing.id != policy.id:
            raise _conflict("name_taken", "A retention policy with that name already exists")
        changes["name"] = new_name
    if "applies_to" in changes:
        _validate_applies_to(changes["applies_to"])
    new_duration = changes.get("duration", policy.duration)
    if "duration" in changes:
        _validate_duration("duration", new_duration)
    new_worm = changes.get("worm_lock_period", policy.worm_lock_period)
    if "worm_lock_period" in changes or "duration" in changes:
        _validate_worm_lock(new_worm, new_duration)

    # Extend-forward ratchet: only bites when records are already pinned (doc 06 §5.2).
    if await repo.count_active_pinned_records(session, policy.id) > 0:
        if "duration" in changes and not duration_ge(new_duration, policy.duration):
            raise _invalid(
                "duration",
                "retention_reduction_blocked",
                "Cannot shorten a policy with active records (extend-forward only); archive it and "
                "create a shorter policy for future captures",
            )
        if "disposition_action" in changes and action_preservation_rank(
            changes["disposition_action"]
        ) < action_preservation_rank(policy.disposition_action):
            raise _invalid(
                "disposition_action",
                "retention_reduction_blocked",
                "Cannot weaken disposition for a policy with active records (extend-forward only)",
            )
        if (
            "review_required" in changes
            and policy.review_required
            and not changes["review_required"]
        ):
            raise _invalid(
                "review_required",
                "retention_reduction_blocked",
                "Cannot drop the review requirement for a policy with active records",
            )

    before = {k: _jsonable(getattr(policy, k)) for k in changes}
    for key, value in changes.items():
        setattr(policy, key, value)
    after = {k: _jsonable(getattr(policy, k)) for k in changes}
    _emit(session, actor, EventType.RETENTION_POLICY_UPDATED, policy.id, before=before, after=after)
    await session.commit()
    await session.refresh(policy)
    return policy


async def archive_policy(
    session: AsyncSession, actor: AppUser, policy_id: uuid.UUID
) -> RetentionPolicy:
    policy = await repo.get_policy(session, policy_id, actor.org_id)
    if policy is None:
        raise ProblemException(status=404, code="not_found", title="Retention policy not found")
    if policy.name == SYSTEM_DEFAULT_POLICY_NAME:
        raise _conflict("system_default_protected", "The System Default policy cannot be archived")
    if not policy.active:
        raise _conflict("already_archived", "Policy is already archived")
    policy.active = False
    policy.archived_at = _now()
    policy.archived_by = actor.id
    _emit(
        session,
        actor,
        EventType.RETENTION_POLICY_ARCHIVED,
        policy.id,
        before={"active": True},
        after={"active": False},
    )
    await session.commit()
    await session.refresh(policy)
    return policy


async def unarchive_policy(
    session: AsyncSession, actor: AppUser, policy_id: uuid.UUID
) -> RetentionPolicy:
    policy = await repo.get_policy(session, policy_id, actor.org_id)
    if policy is None:
        raise ProblemException(status=404, code="not_found", title="Retention policy not found")
    if policy.active:
        raise _conflict("not_archived", "Policy is not archived")
    policy.active = True
    policy.archived_at = None
    policy.archived_by = None
    _emit(
        session,
        actor,
        EventType.RETENTION_POLICY_UPDATED,
        policy.id,
        before={"active": False},
        after={"active": True},
    )
    await session.commit()
    await session.refresh(policy)
    return policy


def _jsonable(value: Any) -> Any:
    """Render a policy field for the audit before/after JSON (enums → their value)."""
    if isinstance(value, (RetentionBasis, DispositionAction)):
        return value.value
    return value
