"""S-duedate-snap (R55): integration proofs that materialize sites snap due_at to a working day.

The pure snap math is exhaustively unit-tested (tests/unit/test_duedate_snap.py); here we prove the
DB→snap WIRING: the seam resolves the org's real working_calendar, and an INSTANT materialize site
(the workflow engine) stores a snapped due_at.

⚠ The session DB is SHARED + the default org's working_calendar state is unpredictable across files
(a throwaway org with a committed calendar can't be cleaned up — REVOKE DELETE / the S-notify-4/7
leaked-org trap that breaks test_restore's scalar_one). So every assertion RESOLVES the org's ACTUAL
calendar and checks against ``snap_to_working_day(raw, cal)`` + ``is_working_day(...)`` — robust to
the calendar present, yet mutation-distinguishing (pre-slice the raw weekend value is stored, so
``is_working_day`` fails).
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import select

from easysynq_api.db.models.workflow import Task
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.services.notifications.duedate import (
    resolve_calendar,
    snap_due_at,
    snap_to_working_day,
)
from easysynq_api.services.notifications.timer import is_working_day
from easysynq_api.services.workflow import engine

from .test_workflow_engine import (
    _approve_stage,
    _instantiate,
    _org_id,
    _role_with,
    _seed_definition,
    _user,
)

pytestmark = pytest.mark.integration

UTC = datetime.UTC
# A Saturday inside a seeded audit_event partition (migration 0010 seeds 2026-06/07/08). Noon UTC is
# still a Saturday in any realistic calendar tz, so the raw lands on a non-working day for Mon-Fri.
_SAT = datetime.datetime(2026, 6, 27, 12, 0, tzinfo=UTC)


async def test_seam_snap_due_at_resolves_real_calendar_and_snaps(app_under_test: object) -> None:
    org_id = await _org_id()
    async with get_sessionmaker()() as s:
        cal = await resolve_calendar(s, org_id)
        out = await snap_due_at(s, org_id, _SAT)
        assert await snap_due_at(s, org_id, None) is None  # passthrough
    assert out is not None
    assert out == snap_to_working_day(_SAT, cal)  # exact, against the resolved calendar
    assert is_working_day(out.astimezone(cal.tz).date(), cal)  # §9.5: lands on a working day


async def test_engine_materialize_stores_snapped_due_at(
    app_under_test: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pin the engine clock to a Saturday (seeded partition); sla=1h → raw due_at is Saturday noon.
    monkeypatch.setattr(engine, "_now", lambda: datetime.datetime(2026, 6, 27, 11, 0, tzinfo=UTC))
    raw = datetime.datetime(2026, 6, 27, 12, 0, tzinfo=UTC)  # _now() + 1h

    org = await _org_id()
    actor = await _user("ddsnap")
    role = await _role_with([actor])
    stage = _approve_stage("gate", role, quorum={"type": "ANY"})
    stage["sla"] = {"hours": 1}
    key = await _seed_definition(org, [stage], entry="gate")
    iid = await _instantiate(key, uuid.uuid4(), None, actor)

    async with get_sessionmaker()() as s:
        task = (await s.execute(select(Task).where(Task.instance_id == iid))).scalars().first()
        assert task is not None and task.due_at is not None
        cal = await resolve_calendar(s, org)
    # The stored due_at is the snapped value (NOT the raw Saturday) — mutation-distinguishing.
    assert task.due_at == snap_to_working_day(raw, cal)
    assert is_working_day(task.due_at.astimezone(cal.tz).date(), cal)
