"""Retention-policy management API (slice S-rec-4, doc 06 §5.1, doc 15 §8.16).

The authenticated CRUD + soft-archive surface for retention policies (policy-as-data). A SEPARATE
router from ``api/records.py`` — records are immutable (one PATCH = /disposition), whereas policies
are freely editable governance assets, so keeping them apart keeps the records immutability proof
tight.

Authz: reads → ``retention.read`` (QMS Owner + Internal Auditor), writes → ``retention.manage``
(QMS Owner) — the two CONTENT-domain keys opened additively in 0028 (R38). Both gate at SYSTEM scope
(``require``'s default ``_system_scope`` → ``ResourceContext.system()``) because retention policies
are org-level, not artifact/folder-scoped — the ``config.update`` mechanic. No PUT/DELETE: a hard
delete is blocked by 3 RESTRICT FKs, so retirement is the soft ``/archive`` action."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._retention_enums import DispositionAction, RetentionBasis
from ..db.models.app_user import AppUser
from ..db.models.retention_policy import RetentionPolicy
from ..db.session import get_session
from ..problems import ProblemException
from ..services.authz import require
from ..services.records import repository as repo
from ..services.records import retention_policies as svc

router = APIRouter(prefix="/api/v1", tags=["retention-policies"])

_retention_read = require("retention.read")
_retention_manage = require("retention.manage")


class RetentionPolicyCreate(BaseModel):
    name: str
    applies_to: dict[str, Any] | None = None
    basis: RetentionBasis = RetentionBasis.CAPTURED_AT
    duration: str
    disposition_action: DispositionAction
    review_required: bool = False
    worm_lock_period: str | None = None


class RetentionPolicyUpdate(BaseModel):
    # All optional → a partial update; only supplied fields change (model_dump(exclude_unset=True)).
    name: str | None = None
    applies_to: dict[str, Any] | None = None
    basis: RetentionBasis | None = None
    duration: str | None = None
    disposition_action: DispositionAction | None = None
    review_required: bool | None = None
    worm_lock_period: str | None = None


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _view(policy: RetentionPolicy) -> dict[str, Any]:
    return {
        "id": str(policy.id),
        "org_id": str(policy.org_id),
        "name": policy.name,
        "applies_to": policy.applies_to,
        "basis": policy.basis.value,
        "duration": policy.duration,
        "disposition_action": policy.disposition_action.value,
        "review_required": policy.review_required,
        "worm_lock_period": policy.worm_lock_period,
        "active": policy.active,
        "archived_at": _iso(policy.archived_at),
        "archived_by": str(policy.archived_by) if policy.archived_by else None,
        "created_at": _iso(policy.created_at),
        "updated_at": _iso(policy.updated_at),
    }


@router.get("/retention-policies")
async def list_retention_policies_endpoint(
    include_archived: bool = False,
    caller: AppUser = Depends(_retention_read),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """List the org's retention policies (active only by default). Needs ``retention.read``."""
    policies = await repo.list_retention_policies(
        session, caller.org_id, include_archived=include_archived
    )
    return [_view(p) for p in policies]


@router.get("/retention-policies/{policy_id}")
async def get_retention_policy_endpoint(
    policy_id: uuid.UUID,
    caller: AppUser = Depends(_retention_read),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A single retention policy. Needs ``retention.read``."""
    policy = await repo.get_policy(session, policy_id, caller.org_id)
    if policy is None:
        raise ProblemException(status=404, code="not_found", title="Retention policy not found")
    return _view(policy)


@router.post("/retention-policies", status_code=status.HTTP_201_CREATED)
async def create_retention_policy_endpoint(
    body: RetentionPolicyCreate,
    caller: AppUser = Depends(_retention_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a retention policy. Needs ``retention.manage``."""
    policy = await svc.create_policy(
        session,
        caller,
        name=body.name,
        applies_to=body.applies_to,
        basis=body.basis,
        duration=body.duration,
        disposition_action=body.disposition_action,
        review_required=body.review_required,
        worm_lock_period=body.worm_lock_period,
    )
    return _view(policy)


@router.patch("/retention-policies/{policy_id}")
async def update_retention_policy_endpoint(
    policy_id: uuid.UUID,
    body: RetentionPolicyUpdate,
    caller: AppUser = Depends(_retention_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Edit a retention policy (partial; extend-forward only when records are pinned). Needs
    ``retention.manage``."""
    policy = await svc.update_policy(
        session, caller, policy_id, changes=body.model_dump(exclude_unset=True)
    )
    return _view(policy)


@router.post("/retention-policies/{policy_id}/archive")
async def archive_retention_policy_endpoint(
    policy_id: uuid.UUID,
    caller: AppUser = Depends(_retention_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Soft-archive a policy (hide from new-capture resolution; pinned records keep it). Needs
    ``retention.manage``."""
    policy = await svc.archive_policy(session, caller, policy_id)
    return _view(policy)


@router.post("/retention-policies/{policy_id}/unarchive")
async def unarchive_retention_policy_endpoint(
    policy_id: uuid.UUID,
    caller: AppUser = Depends(_retention_manage),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Reactivate an archived policy. Needs ``retention.manage``."""
    policy = await svc.unarchive_policy(session, caller, policy_id)
    return _view(policy)
