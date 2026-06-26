import uuid
import zoneinfo

import pytest
from sqlalchemy import select, update

from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.working_calendar import WorkingCalendar
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.common.org_clock import resolve_org_tz
from easysynq_api.services.notifications.escalation import resolve_working_calendar

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
