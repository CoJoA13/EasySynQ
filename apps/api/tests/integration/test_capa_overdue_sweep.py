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
from easysynq_api.services.capa.overdue import _process_one, sweep_capa_overdue
from easysynq_api.services.capa.service import set_capa_target_date
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

# Inactive status that causes _recipient_for_user to return None (mirrors escalation._INACTIVE).
_INACTIVE_STATUS = UserStatus.LOCKED

# Saturday 2026-06-27 10:00 UTC — not a working day in the default Mon-Fri calendar.
_SATURDAY = datetime.datetime(2026, 6, 27, 10, 0, 0, tzinfo=datetime.UTC)
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


async def test_no_stamp_when_no_active_qm_owner(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Finding 2+3 regression: no valid QMS-Owner recipient → do NOT stamp overdue_notified_at.

    Self-provides the precondition: temporarily LOCKS all currently-active QMS Owner holders so
    every _recipient_for_user call returns None (attempted == 0, created == 0, deduped == 0).
    Restores them in a finally block.

    Mutation-verify: against the pre-fix code (unconditional stamp), the first sweep would set
    overdue_notified_at even though no notification was created → the first assert FAILS.
    Against the fix (stamp only when created > 0 or deduped > 0), it PASSES.
    """
    org_id = await _default_org_id()

    # --- Seed overdue CAPA ---
    subject = f"kc-capa-nr-{uuid.uuid4().hex[:8]}"
    await _grant(subject, _CAPA_CREATE_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "No Recipient CAPA", "severity": "Major"}
    )
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    async with get_sessionmaker()() as s:
        await s.execute(
            update(Capa)
            .where(Capa.id == capa_id)
            .values(target_completion_date=_OVERDUE_DATE, overdue_notified_at=None)
        )
        await s.commit()

    # --- Self-provide: find and temporarily LOCK all currently-active QMS Owner holders ---
    # This prevents them from being valid recipients so the sweep has no one to notify.
    locked_ids: list[uuid.UUID] = []
    async with get_sessionmaker()() as s:
        # Get all users assigned the QMS Owner role for this org.
        role_holder_rows = (
            (
                await s.execute(
                    select(RoleAssignment.user_id)
                    .join(Role, Role.id == RoleAssignment.role_id)
                    .where(RoleAssignment.org_id == org_id, Role.name == "QMS Owner")
                )
            )
            .scalars()
            .all()
        )
        # Only lock the ACTIVE ones (don't touch already-inactive users).
        if role_holder_rows:
            active_rows = (
                (
                    await s.execute(
                        select(AppUser.id).where(
                            AppUser.id.in_(role_holder_rows),
                            AppUser.status == UserStatus.ACTIVE,
                        )
                    )
                )
                .scalars()
                .all()
            )
            locked_ids = list(active_rows)
            if locked_ids:
                await s.execute(
                    update(AppUser)
                    .where(AppUser.id.in_(locked_ids))
                    .values(status=_INACTIVE_STATUS)
                )
        await s.commit()

    # Create a LOCKED QMS Owner (inactive → _recipient_for_user returns None).
    locked_qm_id = await _seed_user(org_id, display_name="Inactive QMS Owner No-Stamp")
    async with get_sessionmaker()() as s:
        await s.execute(
            update(AppUser).where(AppUser.id == locked_qm_id).values(status=_INACTIVE_STATUS)
        )
        await s.commit()
    await _assign_role(org_id, locked_qm_id, "QMS Owner")

    sm = get_sessionmaker()

    try:
        before_audits = await _count_capa_overdue_audits(org_id, capa_id)

        # Sweep with ALL QMS Owners inactive → must NOT stamp and must NOT write audit.
        await sweep_capa_overdue(sm, _BASE)

        stamp_no_recipient = await _get_overdue_notified_at(capa_id)
        audits_no_recipient = await _count_capa_overdue_audits(org_id, capa_id)

        assert stamp_no_recipient is None, (
            "overdue_notified_at must NOT be stamped when no valid QMS Owner recipient exists "
            "(Finding 2: must-not-silently-drop; pre-fix code stamps unconditionally → RED)"
        )
        assert audits_no_recipient == before_audits, (
            "No CAPA_OVERDUE audit must be written when no valid recipient "
            "(Finding 1/R4-1: pre-fix code writes audit unconditionally → RED)"
        )

        # Re-activate the locked QMS Owner → a re-sweep must NOW stamp and write 1 audit.
        async with get_sessionmaker()() as s:
            await s.execute(
                update(AppUser).where(AppUser.id == locked_qm_id).values(status=UserStatus.ACTIVE)
            )
            await s.commit()

        await sweep_capa_overdue(sm, _BASE)

        stamp_after_active = await _get_overdue_notified_at(capa_id)
        audits_after_active = await _count_capa_overdue_audits(org_id, capa_id)

        assert stamp_after_active is not None, (
            "overdue_notified_at MUST be stamped after re-activating the QMS Owner"
        )
        assert audits_after_active == before_audits + 1, (
            f"Exactly 1 new CAPA_OVERDUE audit must be written after delivery, "
            f"got {audits_after_active - before_audits} new"
        )

    finally:
        # Restore all temporarily-locked active holders back to ACTIVE.
        if locked_ids:
            async with get_sessionmaker()() as s:
                await s.execute(
                    update(AppUser)
                    .where(AppUser.id.in_(locked_ids))
                    .values(status=UserStatus.ACTIVE)
                )
                await s.commit()
        # Remove the RoleAssignment for the user seeded by this test (cross-file pollution guard
        # — the S-escalate2 leak class: a live RoleAssignment leaks a QMS Owner into the shared
        # DB). We do NOT delete the AppUser: the second sweep creates a notification row that
        # references locked_qm_id via a RESTRICT FK, so deleting the user would raise
        # ForeignKeyViolation. Removing the RoleAssignment is sufficient to stop the pollution
        # (no QMS Owner grant → never re-claimed as a recipient in other tests).
        async with get_sessionmaker()() as s:
            await s.execute(
                RoleAssignment.__table__.delete().where(RoleAssignment.user_id == locked_qm_id)
            )
            await s.commit()


async def test_sweep_skips_on_nonworking_day(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Finding 4: the now_is_working gate — a non-working day (Saturday) returns capas==0.

    Passes _SATURDAY (2026-06-27) to sweep_capa_overdue. The sweep must short-circuit
    (skipped_non_working=1, capas=0) and must NOT stamp an overdue CAPA.
    """
    org_id = await _default_org_id()

    # Self-provide a QMS Owner holder (so the sweep would process the CAPA on a working day).
    qm_id = await _seed_user(org_id, display_name="Weekend Gate QM Holder")
    await _assign_role(org_id, qm_id, "QMS Owner")

    # Seed an overdue CAPA.
    subject = f"kc-capa-wg-{uuid.uuid4().hex[:8]}"
    await _grant(subject, _CAPA_CREATE_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/capas", headers=h, json={"title": "Weekend Gate CAPA", "severity": "Minor"}
    )
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    async with get_sessionmaker()() as s:
        await s.execute(
            update(Capa)
            .where(Capa.id == capa_id)
            .values(target_completion_date=_OVERDUE_DATE, overdue_notified_at=None)
        )
        await s.commit()

    sm = get_sessionmaker()
    result = await sweep_capa_overdue(sm, _SATURDAY)

    # The sweep must short-circuit on a non-working day.
    assert result["capas"] == 0, f"Weekend sweep must process 0 CAPAs, got {result['capas']}"
    assert result["skipped_non_working"] == 1, (
        f"Weekend sweep must set skipped_non_working=1, got {result['skipped_non_working']}"
    )

    # The CAPA must NOT be stamped.
    stamp = await _get_overdue_notified_at(capa_id)
    assert stamp is None, (
        "overdue_notified_at must NOT be stamped when the sweep is skipped on a non-working day"
    )


async def test_future_dated_capa_not_fired_by_process_one(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Fix 1 mutation-verify: _process_one must NOT fire when target_completion_date is FUTURE.

    Simulates the race: a CAPA is claimed as overdue, but by the time _process_one locks it the
    admin extended target_completion_date to a future date (and cleared the stamp). The re-check
    MUST see the future date and return 0 without stamping or writing a notification/audit.

    Mutation-verify: against the pre-fix _process_one (no ``target < today`` predicate), this
    call returns 1 and stamps overdue_notified_at → the assertions FAIL (RED). With the fix the
    WHERE clause skips the future-dated CAPA → returns 0, stamp stays None → GREEN.
    """
    org_id = await _default_org_id()

    # Self-provide a QMS Owner so _process_one would have a valid recipient if it did fire.
    qm_id = await _seed_user(org_id, display_name="Future Date Race QM Holder")
    await _assign_role(org_id, qm_id, "QMS Owner")

    subject = f"kc-capa-fd-{uuid.uuid4().hex[:8]}"
    await _grant(subject, _CAPA_CREATE_KEYS)
    h = _auth(token_factory, subject)
    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "Future Date Race CAPA", "severity": "Major"},
    )
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    # Set target_completion_date to a FUTURE date (well after _BASE.date()) with a clear stamp.
    _FUTURE_TARGET = datetime.date(2026, 9, 30)  # future relative to _BASE (2026-06-24)
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Capa)
            .where(Capa.id == capa_id)
            .values(target_completion_date=_FUTURE_TARGET, overdue_notified_at=None)
        )
        await s.commit()

    before_notifs = await _count_notifications_for(capa_id, EVENT_CAPA_OVERDUE, qm_id)
    before_audits = await _count_capa_overdue_audits(org_id, capa_id)

    # Call _process_one directly (bypassing the coarse claim scan) with today = _BASE.date().
    # The future target must cause the locked WHERE to return no row → result == 0.
    sm = get_sessionmaker()
    result = await _process_one(sm, capa_id, _BASE, _BASE.date())

    assert result == 0, (
        f"_process_one must return 0 for a future-dated CAPA, got {result}. "
        "Pre-fix code (missing target < today) would return 1 here (mutation-verify FAIL→PASS)."
    )
    stamp = await _get_overdue_notified_at(capa_id)
    assert stamp is None, (
        "overdue_notified_at must NOT be stamped for a future-dated CAPA "
        "(pre-fix code stamps unconditionally when a recipient exists → RED)"
    )
    after_notifs = await _count_notifications_for(capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert after_notifs == before_notifs, (
        "No capa.overdue notification must be created for a future-dated CAPA"
    )
    after_audits = await _count_capa_overdue_audits(org_id, capa_id)
    assert after_audits == before_audits, (
        "No CAPA_OVERDUE audit must be written for a future-dated CAPA"
    )


async def test_same_date_rearm_fires_distinct_notification(
    app_client: AsyncClient, token_factory: Callable[..., str]
) -> None:
    """Fix D mutation-verify: same-date re-arm → SECOND distinct capa.overdue notification.

    Scenario: CAPA overdue+notified for date D (stamp set, rearm_seq=0). QM clears the target
    (set_capa_target_date→None; emits audit #1), then re-sets to the SAME date D
    (set_capa_target_date→D; emits audit #2). Now overdue_notified_at=NULL + target=D + seq=2.

    With the OLD _version_id (no rearm_seq):
        version_id = uuid5(NS, f"{capa_id}:{D}") — SAME for both sweeps
        → dedup index collapses the resend → no new row → count stays 1 → assert count==2 FAILS.

    With the FIX (rearm_seq = count of CAPA_TARGET_DATE_SET audits):
        sweep 1: version_id = uuid5(NS, f"{capa_id}:{D}:0")
        sweep 2: version_id = uuid5(NS, f"{capa_id}:{D}:2") — DISTINCT
        → dedup allows new row → count==2 PASSES.
    """
    org_id = await _default_org_id()

    # Self-provide a fresh QMS Owner holder so counts start at 0 for this test.
    qm_id = await _seed_user(org_id, display_name="Same-date Rearm QM Holder")
    await _assign_role(org_id, qm_id, "QMS Owner")

    # Seed a plain org-member actor for the set_capa_target_date service calls.
    # No elevated permissions needed — the service layer doesn't check authz (the route does).
    actor_id = await _seed_user(org_id, display_name="Same-date Rearm Actor", email=None)

    subject = f"kc-capa-sr-{uuid.uuid4().hex[:8]}"
    await _grant(subject, _CAPA_CREATE_KEYS)
    h = _auth(token_factory, subject)

    r = await app_client.post(
        "/api/v1/capas",
        headers=h,
        json={"title": "Same-date Rearm CAPA", "severity": "Major"},
    )
    assert r.status_code == 201, r.text
    capa_id = uuid.UUID(r.json()["id"])

    # Set target via direct UPDATE (no audit emitted → rearm_seq=0 at first sweep).
    _TARGET = _OVERDUE_DATE  # 2026-06-23 < _BASE.date() → triggers the overdue claim
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Capa)
            .where(Capa.id == capa_id)
            .values(target_completion_date=_TARGET, overdue_notified_at=None)
        )
        await s.commit()

    sm = get_sessionmaker()

    # First sweep: rearm_seq=0 → version_id = uuid5(NS, f"{capa_id}:{_TARGET}:0") → 1 notification.
    await sweep_capa_overdue(sm, _BASE)
    count_1 = await _count_notifications_for(capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert count_1 == 1, f"First sweep must create exactly 1 notification, got {count_1}"
    stamp_1 = await _get_overdue_notified_at(capa_id)
    assert stamp_1 is not None, "overdue_notified_at must be stamped after first sweep"

    # RE-ARM via set_capa_target_date: clear (→ None) then re-set to the SAME date D.
    # Each call emits a CAPA_TARGET_DATE_SET audit and clears overdue_notified_at.
    # After both calls: rearm_seq=2, target=_TARGET, overdue_notified_at=None.
    async with get_sessionmaker()() as s:
        actor = (await s.execute(select(AppUser).where(AppUser.id == actor_id))).scalar_one()
        await set_capa_target_date(s, actor, capa_id, target_completion_date=None)

    async with get_sessionmaker()() as s:
        actor = (await s.execute(select(AppUser).where(AppUser.id == actor_id))).scalar_one()
        await set_capa_target_date(s, actor, capa_id, target_completion_date=_TARGET)

    # Second sweep: rearm_seq=2 → version_id = uuid5(NS, f"{capa_id}:{_TARGET}:2") ≠ sweep-1 id
    # → dedup index allows a NEW notification row → count=2.
    await sweep_capa_overdue(sm, _BASE)
    count_2 = await _count_notifications_for(capa_id, EVENT_CAPA_OVERDUE, qm_id)
    assert count_2 == count_1 + 1, (
        f"Same-date re-arm must produce a SECOND distinct notification row "
        f"(got {count_2} total, expected {count_1 + 1}). "
        "Mutation-verify: with old _version_id (no rearm_seq), same date → same uuid5 key "
        "→ dedup index collapses the second send → count stays 1 → this assert FAILS."
    )
