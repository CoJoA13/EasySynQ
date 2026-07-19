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
import logging
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
from ..services.notifications.calendar_admin import get_working_calendar, update_working_calendar
from ..services.notifications.health import get_delivery_health
from ..services.notifications.requeue import requeue_failed

router = APIRouter(prefix="/api/v1", tags=["admin"])

# config.update is SYSTEM-domain / admin-only (doc 07 §3.9, R35) — NOT a content key, NOT no-gate.
_config_update = require("config.update")

logger = logging.getLogger("easysynq.notifications.requeue")


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
    # S-notify-1 (doc 10 §9, R53): the per-org email-delivery opt-in. Default OFF; an admin enables
    # it after configuring SMTP env. Audited via CONFIG_UPDATED.
    notifications_email_enabled: bool | None = None
    # S-notify-3a: when True (the default), urgent/critical notifications bypass quiet hours and are
    # delivered immediately regardless of user preference.
    notifications_escalation_pierce_quiet_hours: bool | None = None


class WorkingCalendarUpdate(BaseModel):
    # list[Any] (NOT list[int]/list[str]) so EVERY value reaches the strict service parser —
    # pydantic 2.13.4 lax-coerces [true]/["1"]/[1.0] under list[int], which would make the
    # parity guarantee false (the strict bool/float/string guards would be dead code).
    name: str
    working_days: list[Any]
    holidays: list[Any]
    timezone: str


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
        "notifications_email_enabled": cfg.notifications_email_enabled,
        "notifications_escalation_pierce_quiet_hours": (
            cfg.notifications_escalation_pierce_quiet_hours
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


@router.get("/admin/notifications/health")
async def get_notification_health_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Notification email delivery-health snapshot for the caller's org (S-notify-5b, doc 10 §9).

    Failure/backlog/suppressed counts + the recent-failure list (operational-only) + the awareness
    fan-out backlog. Pure read. Needs ``config.update`` (admin-only)."""
    return await get_delivery_health(session, caller.org_id)


@router.post("/admin/notifications/requeue-failed")
async def requeue_failed_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Requeue this org's FAILED notification emails → PENDING so the outbox drain retries them.

    Ops-recovery action (structured-log only; email is advisory). Needs ``config.update``."""
    cfg = await session.get(SystemConfig, caller.org_id)
    if cfg is None or not cfg.notifications_email_enabled:
        # Email delivery is off → leave FAILED rows untouched. Requeuing them would only let the
        # next drain terminally SUPPRESS them (drain._still_eligible), making them unrecoverable
        # once email is re-enabled. The FE also disables the action while email is off.
        return {"requeued": 0}
    count = await requeue_failed(session, caller.org_id)
    await session.commit()
    # Emit the record AFTER the commit — this structured log is the sole trace of the action (no
    # audit_event by design), so a rolled-back requeue must not leave a false "requeued N".
    logger.info(
        "notifications.requeued",
        extra={
            "extra_fields": {
                "count": count,
                "org_id": str(caller.org_id),
                "actor_id": str(caller.id),
            }
        },
    )
    return {"requeued": count}


@router.get("/admin/notifications/working-calendar")
async def get_working_calendar_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The org's default working calendar (synthesized Mon-Fri when none exists).

    Needs config.update (admin-only)."""
    return await get_working_calendar(session, caller.org_id)


@router.put("/admin/notifications/working-calendar")
async def put_working_calendar_endpoint(
    body: WorkingCalendarUpdate,
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Replace the org's default working calendar (validate → atomic upsert → audit).

    Needs config.update (admin-only)."""
    view = await update_working_calendar(
        session,
        actor=caller,
        name=body.name,
        working_days=body.working_days,
        holidays=body.holidays,
        timezone=body.timezone,
    )
    await session.commit()
    return view


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
    if body.notifications_email_enabled is not None:
        before["notifications_email_enabled"] = cfg.notifications_email_enabled
        cfg.notifications_email_enabled = body.notifications_email_enabled
        after["notifications_email_enabled"] = body.notifications_email_enabled
    if body.notifications_escalation_pierce_quiet_hours is not None:
        before["notifications_escalation_pierce_quiet_hours"] = (
            cfg.notifications_escalation_pierce_quiet_hours
        )
        cfg.notifications_escalation_pierce_quiet_hours = (
            body.notifications_escalation_pierce_quiet_hours
        )
        after["notifications_escalation_pierce_quiet_hours"] = (
            body.notifications_escalation_pierce_quiet_hours
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
