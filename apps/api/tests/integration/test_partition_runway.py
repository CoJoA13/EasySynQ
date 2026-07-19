import datetime
from typing import Any

from sqlalchemy import text

from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.audit.partitions import upcoming_month_starts


async def test_month_plus_two_partition_exists(app_under_test: Any) -> None:
    # The month+2 partition is NOT in migration 0010's fixed seed — only the conftest ensure-call
    # (and, in prod, the lifespan/Beat) creates it. Its existence proves the runway wiring ran.
    plus_two = upcoming_month_starts(datetime.datetime.now(datetime.UTC).date())[-1]
    name = f"audit_event_{plus_two.strftime('%Y_%m')}"
    async with get_sessionmaker()() as s:
        count = (
            await s.execute(text("SELECT count(*) FROM pg_class WHERE relname = :n"), {"n": name})
        ).scalar_one()
    assert count == 1, f"expected partition {name} to exist"
