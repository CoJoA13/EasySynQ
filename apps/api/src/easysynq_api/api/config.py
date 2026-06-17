"""Post-OPERATIONAL organization configuration (slice S-rec-3, doc 15 §8.17).

``PATCH /admin/config`` is the authenticated, in-PEP surface for the few org-level toggles that live
on ``system_config`` after setup finalizes. It is gated on the SYSTEM-domain ``config.update`` key
(held only by System Administrator; the R35 two-tier guard blocks a content-tier QMS Owner from
being granted it), because relaxing such a flag changes an org-wide integrity rule.

Today it carries the Mode-B ``capture_pre_release_templates`` toggle (doc 06 §4.2): default OFF, so
a record can be captured only against an **Effective** form template; turning it ON lets an operator
fill a Draft/InReview template for a controlled migration. The flip is audited (``CONFIG_UPDATED``
on the closed ``config`` object type)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models._audit_enums import ActorType, AuditObjectType, EventType
from ..db.models.app_user import AppUser
from ..db.models.audit_event import AuditEvent
from ..db.models.system_config import SystemConfig
from ..db.session import get_session
from ..logging import request_id_var
from ..problems import ProblemException
from ..services.authz import require

router = APIRouter(prefix="/api/v1", tags=["admin"])

# config.update is SYSTEM-domain / admin-only (doc 07 §3.9, R35) — NOT a content key, NOT no-gate.
_config_update = require("config.update")


class OrgConfigUpdate(BaseModel):
    # All optional → a partial update; only supplied fields change. Extend additively as more
    # org-level toggles land (the toggle surface, not a per-field endpoint sprawl).
    capture_pre_release_templates: bool | None = None
    # S-rec-4 (doc 07 §7): SoD-6 relaxation — when true, a record's capturer may also dispose it.
    allow_self_disposition: bool | None = None
    # S-capa-1 (R39): severity-aware SoD-4 relaxation — when true, a Minor CAPA's implementer may
    # also
    # verify it (Critical/Major always hard-enforce). Forward seam; enforced in S-capa-3.
    allow_capa_self_verify: bool | None = None
    # S-leadership-1 (doc 10 §2.5, R45): require a signed Top-Management release authorization
    # before a leadership artifact (POL/OBJ/MR) may be released. Default OFF — opt-in.
    leadership_release_requires_top_management_authorization: bool | None = None


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _config_view(cfg: SystemConfig) -> dict[str, Any]:
    return {
        "org_id": str(cfg.org_id),
        "capture_pre_release_templates": cfg.capture_pre_release_templates,
        "allow_self_disposition": cfg.allow_self_disposition,
        "allow_capa_self_verify": cfg.allow_capa_self_verify,
        "leadership_release_requires_top_management_authorization": (
            cfg.leadership_release_requires_top_management_authorization
        ),
    }


@router.get("/admin/config")
async def get_config_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The org's mutable config toggles. Needs ``config.update`` (admin-only)."""
    cfg = await session.get(SystemConfig, caller.org_id)
    if cfg is None:  # pragma: no cover - OPERATIONAL implies a system_config row
        raise ProblemException(status=404, code="not_found", title="No system config")
    return _config_view(cfg)


@router.patch("/admin/config")
async def update_config_endpoint(
    body: OrgConfigUpdate,
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Update org-level config toggles (partial). Audits ``CONFIG_UPDATED``; needs the SYSTEM-domain
    ``config.update`` (admin-only)."""
    cfg = await session.get(SystemConfig, caller.org_id)
    if cfg is None:  # pragma: no cover - OPERATIONAL implies a system_config row
        raise ProblemException(status=404, code="not_found", title="No system config")
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if body.capture_pre_release_templates is not None:
        before["capture_pre_release_templates"] = cfg.capture_pre_release_templates
        cfg.capture_pre_release_templates = body.capture_pre_release_templates
        after["capture_pre_release_templates"] = body.capture_pre_release_templates
    if body.allow_self_disposition is not None:
        before["allow_self_disposition"] = cfg.allow_self_disposition
        cfg.allow_self_disposition = body.allow_self_disposition
        after["allow_self_disposition"] = body.allow_self_disposition
    if body.allow_capa_self_verify is not None:
        before["allow_capa_self_verify"] = cfg.allow_capa_self_verify
        cfg.allow_capa_self_verify = body.allow_capa_self_verify
        after["allow_capa_self_verify"] = body.allow_capa_self_verify
    if body.leadership_release_requires_top_management_authorization is not None:
        before["leadership_release_requires_top_management_authorization"] = (
            cfg.leadership_release_requires_top_management_authorization
        )
        cfg.leadership_release_requires_top_management_authorization = (
            body.leadership_release_requires_top_management_authorization
        )
        after["leadership_release_requires_top_management_authorization"] = (
            body.leadership_release_requires_top_management_authorization
        )
    if after:
        session.add(
            AuditEvent(
                org_id=caller.org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=caller.id,
                actor_type=ActorType.user,
                event_type=EventType.CONFIG_UPDATED,
                object_type=AuditObjectType.config,
                object_id=caller.org_id,
                before=before,
                after=after,
                request_id=_rid(),
            )
        )
        await session.commit()
        await session.refresh(cfg)
    return _config_view(cfg)
