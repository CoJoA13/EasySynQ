"""The capa.overdue Beat sweep (S-capa-overdue).

Scans CAPAs past their target_completion_date and notifies the QMS Owner role, reusing the
awareness machinery (task-less, version-discriminated dedup). Fresh session per CAPA +
FOR UPDATE SKIP LOCKED + the overdue_notified_at stamp make it idempotent and concurrency-safe
(the fan_out_awareness precedent) — no sweep-wide advisory lock needed.

Architecture deviation from the spec's "advisory lock":
    This sweep uses the ``fan_out_awareness`` per-unit pattern — a fresh session per CAPA with
    ``FOR UPDATE SKIP LOCKED`` + the ``overdue_notified_at`` stamp — NOT a sweep-wide advisory
    lock. That combination is idempotent and safe under concurrent ticks and ``acks_late``
    redelivery (two ticks split the work; a committed stamp excludes the row next time), and
    gives per-CAPA exception isolation.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import CapaCloseState
from ...db.models.audit_event import AuditEvent
from ...db.models.capa import Capa
from ...db.models.organization import Organization
from ...db.models.system_config import SystemConfig
from ..common.org_clock import resolve_default_org_tz
from ..notifications.constants import EVENT_CAPA_OVERDUE
from ..notifications.dispatch import enqueue_awareness_one
from ..notifications.duedate import resolve_calendar
from ..notifications.escalation import _recipient_for_user
from ..notifications.subjects import resolve_subject
from ..notifications.timer import is_working_day
from ..workflow.repository import users_with_roles
from . import repository as repo

logger = logging.getLogger("easysynq.capa.overdue")

_QMS_OWNER_ROLE = "QMS Owner"

# Stable namespace for the version-discriminated dedup key.
# subject_version_id = uuid5(_NS, f"{capa_id}:{target_date}") — varies by target date so a
# re-armed breach (after a date extension + stamp clear) fires a DISTINCT notification instead
# of colliding with the first on the dedup index (the load-bearing correctness point).
_NS = uuid.UUID("0c5a9e8e-7b1f-4c2a-9d3b-2f9c0a1b6e44")


async def _org_flags(session: AsyncSession, org_id: uuid.UUID) -> tuple[bool, bool]:
    """(email_enabled, pierce_quiet_hours) — mirrors fanout._org_flags; (False, False) on miss."""
    cfg = (
        await session.execute(select(SystemConfig).where(SystemConfig.org_id == org_id))
    ).scalar_one_or_none()
    if cfg is None:
        return (False, False)
    return (cfg.notifications_email_enabled, cfg.notifications_escalation_pierce_quiet_hours)


def _version_id(capa_id: uuid.UUID, target: datetime.date) -> uuid.UUID:
    """A per-(capa, target-date) dedup discriminator.

    A NEW target date re-arms the notification: uuid5 varies with the date, so a clear +
    new date produces a DISTINCT subject_version_id that passes the dedup index. A constant
    value would collapse a re-armed breach (ON CONFLICT DO NOTHING → second row never created).
    """
    return uuid.uuid5(_NS, f"{capa_id}:{target.isoformat()}")


async def _process_one(
    sessionmaker: async_sessionmaker[AsyncSession],
    capa_id: uuid.UUID,
    now: datetime.datetime,
) -> int:
    """Notify QMS Owner for ONE overdue CAPA. Returns the count of newly-created notifications.

    FOR UPDATE SKIP LOCKED claim: a concurrent tick races past this CAPA (claimed/locked);
    populate_existing ensures we see the latest stamped state after waiting for the lock.
    Stamps overdue_notified_at + writes one CAPA_OVERDUE audit_event in the SAME commit.
    A template miss does NOT stamp (retry on next sweep — the fan_out_awareness rule).
    """
    async with sessionmaker() as session:
        capa = (
            await session.execute(
                select(Capa)
                .where(
                    Capa.id == capa_id,
                    Capa.overdue_notified_at.is_(None),
                    Capa.target_completion_date.is_not(None),
                    Capa.close_state.notin_([CapaCloseState.Closed, CapaCloseState.Rejected]),
                )
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if capa is None:
            return 0  # claimed/stamped by a concurrent tick or no longer eligible

        org_id = capa.org_id
        target = capa.target_completion_date
        if target is None:  # guaranteed non-null by the WHERE; guard for type-checker
            return 0

        subject = await resolve_subject(session, "CAPA", capa.id)
        org_enabled, org_pierce = await _org_flags(session, org_id)
        recipient_ids = await users_with_roles(session, org_id, [_QMS_OWNER_ROLE])
        version_id = _version_id(capa.id, target)
        context_vars: dict[str, object] = {"target_completion_date": target.isoformat()}

        created = 0
        for uid in recipient_ids:
            recipient = await _recipient_for_user(session, uid, org_id=org_id)
            if recipient is None:
                continue
            outcome = await enqueue_awareness_one(
                session,
                org_id=org_id,
                subject=subject,
                subject_id=capa.id,
                subject_version_id=version_id,
                recipient=recipient,
                event_key=EVENT_CAPA_OVERDUE,
                context_vars=context_vars,
                now=now,
                org_enabled=org_enabled,
                org_pierce=org_pierce,
            )
            if outcome == "no_template":
                # Template vanished (TOCTOU) — do NOT stamp so the CAPA is re-claimed next sweep.
                logger.warning("capa.overdue_template_missing", extra={"capa_id": str(capa.id)})
                return 0
            if outcome == "created":
                created += 1

        capa.overdue_notified_at = now
        session.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=now,
                actor_id=None,
                actor_type=ActorType.system,
                event_type=EventType.CAPA_OVERDUE,
                object_type=AuditObjectType.record,  # capa.id IS a record id (per capa.py)
                object_id=capa.id,
                scope_ref=str(capa.id),
                after={
                    "capa_id": str(capa.id),
                    "target_completion_date": target.isoformat(),
                    "severity": capa.severity.value,
                },
            )
        )
        await session.commit()
        return created


async def sweep_capa_overdue(
    sessionmaker: async_sessionmaker[AsyncSession],
    now: datetime.datetime,
) -> dict[str, int]:
    """Notify the QMS Owner role about every open, past-target CAPA.

    now_is_working-gated: skip the tick on a non-working day (same posture as OVERDUE/R56 —
    don't email a CAPA overdue notice on a weekend). Fresh session per CAPA for isolation.
    """
    counts: dict[str, int] = {"capas": 0, "notifications": 0, "skipped_non_working": 0}

    async with sessionmaker() as session:
        tz = await resolve_default_org_tz(session)
        today = now.astimezone(tz).date()

        # Working-day gate: resolve the default org's calendar.
        # If today is not a working day, skip the entire tick (no emails on weekends/holidays).
        org_id = (
            await session.execute(
                select(Organization.id).order_by(Organization.created_at).limit(1)
            )
        ).scalar_one_or_none()
        if org_id is not None:
            cal = await resolve_calendar(session, org_id)
            if not is_working_day(today, cal):
                counts["skipped_non_working"] = 1
                return counts

        ids = await repo.list_overdue_capa_ids(session, today)

    for capa_id in ids:
        try:
            n = await _process_one(sessionmaker, capa_id, now)
        except Exception:  # noqa: BLE001 — one CAPA's failure must not wedge the sweep
            logger.warning(
                "capa.overdue_failed",
                exc_info=True,
                extra={"capa_id": str(capa_id)},
            )
            continue
        counts["capas"] += 1
        counts["notifications"] += n

    return counts
