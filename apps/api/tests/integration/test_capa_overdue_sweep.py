"""S-capa-overdue: integration proof for sweep_capa_overdue.

The sweep notifies the QMS Owner role when an open CAPA passes its target_completion_date.
CAPAs have no Task row; the sweep is a new state-scan pattern (services/capa/overdue.py)
modeled on fan_out_awareness — fresh session per CAPA + FOR UPDATE SKIP LOCKED + the
overdue_notified_at stamp.

Tests:
1. test_sweep_fires_for_open_overdue_capa: an open CAPA past its target → exactly 1
   capa.overdue notification (for the seeded QMS Owner recipient) + overdue_notified_at stamped
   + 1 CAPA_OVERDUE audit_event. A terminal (Closed) CAPA and a not-yet-due CAPA are NOT
   notified (run-scoped delta assertions, specific recipient).

2. test_rearm_fires_distinct_notification: clear overdue_notified_at + set a NEW earlier
   target_completion_date, re-run → a SECOND distinct notification row for the same recipient.
   This is the mutation-verify for subject_version_id: with a constant version_id the second
   row would be de-duped (ON CONFLICT DO NOTHING) and the assertion would fail.

Clock: _BASE = 2026-06-24 10:00 UTC
  - Wednesday → working day in the seeded Mon-Fri calendar (gate fires).
  - In the 2026-06 audit_event partition (migration 0010 creates 2026-06/07/08).
  - Seeded DEFAULT org calendar has timezone=UTC (organization.timezone server_default='UTC').
  - _OVERDUE_DATE = 2026-06-23 < _BASE.date() → triggers the overdue claim.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models._capa_enums import CapaCloseState
from easysynq_api.db.models.app_user import AppUser, UserStatus
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.capa import Capa
from easysynq_api.db.models.notification import Notification
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.role import Role, RoleAssignment
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.capa.overdue import sweep_capa_overdue
from easysynq_api.services.notifications.constants import EVENT_CAPA_OVERDUE

from .test_capa import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixed reference time
# ---------------------------------------------------------------------------
# Wednesday 2026-06-24 10:00 UTC:
#   - Working day in Mon-Fri calendar → the now_is_working gate fires.
#   - In the 2026-06 audit_event partition → CAPA_OVERDUE audit writes succeed.
# ---------------------------------------------------------------------------
_BASE = datetime.datetime(2026, 6, 24, 10, 0, 0, tzinfo=datetime.UTC)
_OVERDUE_DATE = datetime.date(2026, 6, 23)  # day before _BASE → past-due
_FUTURE_DATE = datetime.date(2026, 9, 24)  # far future → not yet due

# Minimal CAPA-create permission set (SYSTEM-scoped ALLOW overrides, as in test_capa.py).
_CAPA_CREATE_KEYS = ("capa.create", "capa.read")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _default_org_id() -> uuid.UUID:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
        ).scalar_one()


async def _seed_user(
    org_id: uuid.UUID,
    *,
    display_name: str | None = None,
    email: str | None = "capa-overdue-sweep@example.com",
) -> uuid.UUID:
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        user = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-capa-overdue-{salt}",
            display_name=display_name or f"CAPA Overdue Sweep User {salt}",
            email=email,
            status=UserStatus.ACTIVE,
        )
        s.add(user)
        await s.commit()
        return user.id


async def _assign_role(org_id: uuid.UUID, user_id: uuid.UUID, role_name: str) -> None:
    async with get_sessionmaker()() as s:
        role = (
            await s.execute(select(Role).where(Role.org_id == org_id, Role.name == role_name))
        ).scalar_one_or_none()
        if role is None:
            role = Role(org_id=org_id, name=role_name, is_reserved=False)
            s.add(role)
            await s.flush()
        s.add(RoleAssignment(org_id=org_id, role_id=role.id, user_id=user_id, bound_scope=None))
        await s.commit()


async def _count_notifications_for(
    capa_id: uuid.UUID,
    event_key: str,
    recipient_id: uuid.UUID,
) -> int:
    """Count task-less capa.overdue notifications for a SPECIFIC recipient + CAPA pair."""
    async with get_sessionmaker()() as s:
        return (
            await s.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.subject_id == capa_id,
                    Notification.event_key == event_key,
                    Notification.task_id.is_(None),
                    Notification.recipient_user_id == recipient_id,
                )
            )
        ) or 0


async def _count_capa_overdue_audits(org_id: uuid.UUID, capa_id: uuid.UUID) -> int:
    """Count CAPA_OVERDUE audit_events for this capa_id."""
    async with get_sessionmaker()() as s:
        return (
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.org_id == org_id,
                    AuditEvent.object_id == capa_id,
                    AuditEvent.event_type == EventType.CAPA_OVERDUE,
                )
            )
        ) or 0


async def _get_overdue_notified_at(capa_id: uuid.UUID) -> datetime.datetime | None:
    async with get_sessionmaker()() as s:
        return await s.scalar(select(Capa.overdue_notified_at).where(Capa.id == capa_id))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_sweep_fires_for_open_overdue_capa(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """An open, past-target CAPA → 1 capa.overdue notification + stamp + audit.
    Terminal (Closed) and not-yet-due CAPAs are NOT notified (run-scoped delta).
    """
    org_id = await _default_org_id()

    # Self-provide a QMS Owner holder: a fresh user so the count is 0 before the sweep.
    qm_id = await _seed_user(org_id, display_name="CAPA Overdue QM Holder")
    await _assign_role(org_id, qm_id, "QMS Owner")

    # Create 3 CAPAs via HTTP (each gets a default future target date from the service).
    subject = f"kc-capa-os-{uuid.uuid4().hex[:8]}"
    await _grant(subject, _CAPA_CREATE_KEYS)
    h = _auth(token_factory, subject)

    r1 = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "Overdue CAPA Sweep Test", "severity": "Major"}
    )
    assert r1.status_code == 201, r1.text
    overdue_capa_id = uuid.UUID(r1.json()["id"])

    r2 = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "Future CAPA Sweep Test", "severity": "Minor"}
    )
    assert r2.status_code == 201, r2.text
    future_capa_id = uuid.UUID(r2.json()["id"])

    r3 = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "Closed CAPA Sweep Test", "severity": "Major"}
    )
    assert r3.status_code == 201, r3.text
    closed_capa_id = uuid.UUID(r3.json()["id"])

    # Manipulate target dates and close_state directly in the DB.
    async with get_sessionmaker()() as s:
        # Overdue CAPA: set target to the past, clear stamp (should already be NULL).
        await s.execute(
            update(Capa)
            .where(Capa.id == overdue_capa_id)
            .values(target_completion_date=_OVERDUE_DATE, overdue_notified_at=None)
        )
        # Future CAPA: explicitly set a future date (may already be future, but be precise).
        await s.execute(
            update(Capa)
            .where(Capa.id == future_capa_id)
            .values(target_completion_date=_FUTURE_DATE)
        )
        # Closed CAPA: set to overdue date but put it in terminal state.
        await s.execute(
            update(Capa)
            .where(Capa.id == closed_capa_id)
            .values(target_completion_date=_OVERDUE_DATE, close_state=CapaCloseState.Closed)
        )
        await s.commit()

    # Capture before-counts (run-scoped, specific to our new QM recipient).
    before_overdue = await _count_notifications_for(overdue_capa_id, EVENT_CAPA_OVERDUE, qm_id)
    before_future = await _count_notifications_for(future_capa_id, EVENT_CAPA_OVERDUE, qm_id)
    before_closed = await _count_notifications_for(closed_capa_id, EVENT_CAPA_OVERDUE, qm_id)
    before_audits = await _count_capa_overdue_audits(org_id, overdue_capa_id)

    # Run the sweep.
    sm = get_sessionmaker()
    result = await sweep_capa_overdue(sm, _BASE)
    assert result["capas"] >= 1, f"Expected ≥1 CAPA processed, got {result}"

    # Overdue open CAPA: exactly 1 new notification for our QM recipient.
    after_overdue = await _count_notifications_for(overdue_capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert after_overdue == before_overdue + 1, (
        f"Expected 1 new capa.overdue notification for overdue CAPA, "
        f"got {after_overdue - before_overdue} new (total {after_overdue})"
    )

    # overdue_notified_at must be set.
    stamp = await _get_overdue_notified_at(overdue_capa_id)
    assert stamp is not None, "overdue_notified_at must be set after the sweep"

    # Exactly 1 new CAPA_OVERDUE audit_event.
    after_audits = await _count_capa_overdue_audits(org_id, overdue_capa_id)
    assert after_audits == before_audits + 1, (
        f"Expected 1 new CAPA_OVERDUE audit event, "
        f"got {after_audits - before_audits} new (total {after_audits})"
    )

    # Future and closed CAPAs: NOT notified (run-scoped delta).
    after_future = await _count_notifications_for(future_capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert after_future == before_future, "Not-yet-due CAPA must NOT be notified by the sweep"
    after_closed = await _count_notifications_for(closed_capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert after_closed == before_closed, "Terminal (Closed) CAPA must NOT be notified by the sweep"

    # Re-sweep is a no-op (overdue_notified_at is stamped → not in the claim).
    await sweep_capa_overdue(sm, _BASE)
    after_overdue_resweep = await _count_notifications_for(
        overdue_capa_id, EVENT_CAPA_OVERDUE, qm_id
    )
    assert after_overdue_resweep == after_overdue, (
        "Re-sweep must be a no-op (stamp prevents re-claim)"
    )
    after_audits_resweep = await _count_capa_overdue_audits(org_id, overdue_capa_id)
    assert after_audits_resweep == after_audits, (
        "Re-sweep must NOT write a second CAPA_OVERDUE audit event"
    )


async def test_rearm_fires_distinct_notification(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """RE-ARM: clear overdue_notified_at + new target_completion_date → SECOND distinct row.

    Mutation-verify: if _version_id returned a constant (same uuid regardless of target date),
    the second enqueue_awareness_one call would collide on the awareness dedup index
    (recipient_user_id, event_key, subject_type, subject_id, subject_version_id WHERE task_id IS
    NULL) → ON CONFLICT DO NOTHING → no new row → count stays at 1 → assertion fails.

    This proves the subject_version_id discriminator is load-bearing.
    """
    org_id = await _default_org_id()

    # Self-provide a fresh QMS Owner holder so counts start at 0.
    qm_id = await _seed_user(org_id, display_name="CAPA Rearm QM Holder")
    await _assign_role(org_id, qm_id, "QMS Owner")

    subject = f"kc-capa-ra-{uuid.uuid4().hex[:8]}"
    await _grant(subject, _CAPA_CREATE_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "Rearm CAPA Test", "severity": "Critical"}
    )
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    # Set the initial overdue target date.
    _DATE_1 = datetime.date(2026, 6, 20)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Capa)
            .where(Capa.id == capa_id)
            .values(target_completion_date=_DATE_1, overdue_notified_at=None)
        )
        await s.commit()

    sm = get_sessionmaker()

    # First sweep → 1 notification for our QM holder.
    await sweep_capa_overdue(sm, _BASE)
    count_1 = await _count_notifications_for(capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert count_1 == 1, f"First sweep should create exactly 1 notification, got {count_1}"

    stamp_1 = await _get_overdue_notified_at(capa_id)
    assert stamp_1 is not None, "overdue_notified_at must be set after first sweep"

    # RE-ARM: clear the stamp + set a DIFFERENT (earlier) target date.
    # A different date → different _version_id → distinct dedup key → NEW row allowed.
    _DATE_2 = datetime.date(2026, 6, 10)  # different from _DATE_1 → different subject_version_id
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Capa)
            .where(Capa.id == capa_id)
            .values(overdue_notified_at=None, target_completion_date=_DATE_2)
        )
        await s.commit()

    # Second sweep → 1 more notification (distinct subject_version_id allows a new row).
    await sweep_capa_overdue(sm, _BASE)
    count_2 = await _count_notifications_for(capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert count_2 == count_1 + 1, (
        f"Re-arm should produce a SECOND distinct notification row "
        f"(got {count_2} total, expected {count_1 + 1}). "
        "If this fails, subject_version_id is constant — the dedup index collapses both sweeps."
    )
