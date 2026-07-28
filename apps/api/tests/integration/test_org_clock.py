import datetime
import uuid
import zoneinfo
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update

from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.working_calendar import WorkingCalendar
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.common.org_clock import resolve_org_tz, using_org_tz
from easysynq_api.services.notifications.escalation import resolve_working_calendar
from easysynq_api.services.vault import review as review_module
from easysynq_api.services.vault.review import review_state

from . import s5_helpers as s5
from .test_periodic_review import _release_doc
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def _default_org_id(session) -> uuid.UUID:
    return (
        await session.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
    ).scalar_one()


async def test_resolve_org_tz_parity_with_calendar(app_under_test: object) -> None:
    """resolve_org_tz and resolve_working_calendar.tz agree for the real org calendar (parity)."""
    async with get_sessionmaker()() as session:
        org_id = await _default_org_id(session)
        tz = await resolve_org_tz(session, org_id)
        cal = await resolve_working_calendar(session, org_id)
        assert cal.tz == tz  # parity by construction


async def test_resolve_org_tz_reads_calendar_tz(app_under_test: object) -> None:
    """A non-UTC is_default calendar tz is what resolve_org_tz returns (cal wins over org)."""
    async with get_sessionmaker()() as session:
        org_id = await _default_org_id(session)
        before = (
            await session.execute(
                select(WorkingCalendar.timezone).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one()
        try:
            await session.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
                .values(timezone="Asia/Tokyo")
            )
            await session.commit()
            assert await resolve_org_tz(session, org_id) == zoneinfo.ZoneInfo("Asia/Tokyo")
            assert (await resolve_working_calendar(session, org_id)).tz == zoneinfo.ZoneInfo(
                "Asia/Tokyo"
            )
        finally:
            # Restore (working_calendar has REVOKE DELETE — UPDATE-restore, never delete).
            await session.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
                .values(timezone=before)
            )
            await session.commit()


async def test_review_state_uses_calendar_tz_end_to_end(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    app_under_test: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-UTC calendar tz drives review_state via the auth-boundary contextvar (propagation).

    The serializer clock is fixed at an instant where Pacific/Kiritimati is one date ahead of UTC.
    GET /documents/{id} must therefore return review_state="overdue" only when get_current_user
    replaces the deliberately seeded UTC context with the calendar timezone.

    Mutation-distinguishing at every wall-clock hour: without set_request_org_tz the fixed UTC
    date remains before next_review_due, so the serializer returns "due_soon" instead.
    """
    _CAL_TZ = "Pacific/Kiritimati"
    fixed_utc = datetime.datetime(2026, 7, 28, 12, 0, tzinfo=datetime.UTC)

    salt = uuid.uuid4().hex[:10]
    subj = SimpleNamespace(a=f"kc-orgtz-author-{salt}", b=f"kc-orgtz-approver-{salt}")

    await s5.grant_lifecycle(subj.a)
    await s5.grant_lifecycle(subj.b)
    await s5.set_approver_release(await s5.default_org_id(), True)

    ha = _auth(token_factory, subj.a)
    hb = _auth(token_factory, subj.b)
    type_id = await s5.type_id("SOP")
    content = f"orgtz-propagation-{salt}".encode()

    # Capture the org's calendar tz BEFORE mutating, so we can restore it in finally.
    async with get_sessionmaker()() as session:
        org_id = await _default_org_id(session)
        tz_before = (
            await session.execute(
                select(WorkingCalendar.timezone).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one()

    try:
        # Set the calendar tz to a far-east timezone (UTC+14).
        async with get_sessionmaker()() as session:
            await session.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
                .values(timezone=_CAL_TZ)
            )
            await session.commit()

        # Release a doc so we have an Effective document to GET.
        did, _ = await _release_doc(app_client, ha, hb, type_id, content)
        doc_uuid = uuid.UUID(did)

        # Resolve the org tz from the DB (should now be Pacific/Kiritimati).
        async with get_sessionmaker()() as session:
            tz = await resolve_org_tz(session, org_id)
        assert tz == zoneinfo.ZoneInfo(_CAL_TZ), f"expected {_CAL_TZ}, got {tz}"

        # Pin next_review_due to the fixed instant's calendar date. The same instant is still the
        # previous date in UTC, so the two contextvar outcomes are deterministically distinct.
        today_cal = fixed_utc.astimezone(tz).date()
        assert today_cal > fixed_utc.date()
        async with get_sessionmaker()() as session:
            await session.execute(
                text("UPDATE documented_information SET next_review_due = :d WHERE id = :id"),
                {"d": today_cal, "id": doc_uuid},
            )
            await session.commit()

        class _FixedDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz: datetime.tzinfo | None = None) -> datetime.datetime:
                if tz is None:
                    return fixed_utc.replace(tzinfo=None)
                return fixed_utc.astimezone(tz)

        # Patch the review module's datetime binding, not the shared stdlib module. documents.py
        # holds the today_org function object, whose globals still resolve through review_module.
        monkeypatch.setattr(
            review_module,
            "datetime",
            SimpleNamespace(
                UTC=datetime.UTC,
                date=datetime.date,
                datetime=_FixedDateTime,
                time=datetime.time,
                timedelta=datetime.timedelta,
            ),
        )

        # Seed a known-wrong request context so this proof cannot inherit a correct timezone from a
        # prior ASGI call. The auth dependency must replace it before the serializer runs.
        with using_org_tz(zoneinfo.ZoneInfo("UTC")):
            body = (await app_client.get(f"/api/v1/documents/{did}", headers=ha)).json()

        served_state = body.get("review_state")
        expected_state = review_state(today_cal, today_cal)  # today_cal >= today_cal → "overdue"
        assert expected_state == "overdue", "test invariant: today_cal==next_review_due → overdue"
        assert served_state == expected_state, (
            f"review_state={served_state!r} but expected {expected_state!r} "
            f"(cal_tz={_CAL_TZ}, next_review_due={today_cal}, "
            f"fixed_today_utc={fixed_utc.date()}). "
            "The auth-boundary contextvar (set_request_org_tz) must propagate to the serializer."
        )
    finally:
        # Restore — working_calendar has REVOKE DELETE: UPDATE-restore, never delete.
        async with get_sessionmaker()() as session:
            await session.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
                .values(timezone=tz_before)
            )
            await session.commit()
