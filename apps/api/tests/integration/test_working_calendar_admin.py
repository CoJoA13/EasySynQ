"""S-notify-7: the working-calendar admin editor — service + HTTP integration proofs."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.db.models._audit_enums import EventType
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.audit_event import AuditEvent
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.working_calendar import WorkingCalendar
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.problems import ProblemException
from easysynq_api.services.notifications.calendar_admin import (
    get_working_calendar,
    update_working_calendar,
)

from .test_notification_config import _config_updated_count_for_key, _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def _default_org_id() -> uuid.UUID:
    """The seeded org that owns the is_default working_calendar (AHT in dev; the 0002 org in CI)."""
    async with get_sessionmaker()() as s:
        row = (
            (await s.execute(select(WorkingCalendar).where(WorkingCalendar.is_default.is_(True))))
            .scalars()
            .first()
        )
        assert row is not None, "expected a seeded default working_calendar"
        return row.org_id


async def _read_default(org_id: uuid.UUID) -> WorkingCalendar | None:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                select(WorkingCalendar).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one_or_none()


async def test_get_synthesizes_default_for_calendar_less_org(app_under_test: Any) -> None:
    """An org with no default row → the synthesized Mon-Fri default with exists=False, tz=org tz."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"WCal GET {salt}", short_code=f"WG{salt[:6].upper()}")
        s.add(org)
        await s.commit()
        org_id, org_tz = org.id, org.timezone
    try:
        async with get_sessionmaker()() as s:
            view = await get_working_calendar(s, org_id)
        assert view == {
            "name": "Default",
            "working_days": [1, 2, 3, 4, 5],
            "holidays": [],
            "timezone": org_tz,
            "exists": False,
        }
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Organization).where(Organization.id == org_id))
            await s.commit()


async def test_update_inserts_for_calendar_less_org_without_commit(app_under_test: Any) -> None:
    """The INSERT branch: a calendar-less org → update_working_calendar stages an is_default row
    (tz = body tz). The service does NOT commit; the test rolls back (never commits) so no
    working_calendar row is left behind (the app role can't DELETE it / the org FK is RESTRICT)."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"WCal INS {salt}", short_code=f"WI{salt[:6].upper()}")
        s.add(org)
        await s.commit()
        org_id = org.id
        actor = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-wcal-ins-{salt}",
            display_name="WCal Admin",
            email=None,
        )
        s.add(actor)
        await s.commit()
        actor_id = actor.id
    try:
        async with get_sessionmaker()() as s:
            actor = await s.get(AppUser, actor_id)
            view = await update_working_calendar(
                s,
                actor=actor,
                name="Plant calendar",
                working_days=[1, 2, 3, 4],
                holidays=["2026-12-25"],
                timezone="America/Chicago",
            )
            # The pending row exists in this txn before commit.
            staged = (
                await s.execute(
                    select(WorkingCalendar).where(
                        WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                    )
                )
            ).scalar_one()
            assert staged.is_default is True
            assert staged.timezone == "America/Chicago"
            assert staged.working_days == [1, 2, 3, 4]
            await s.rollback()  # never commit — leak-free
        assert view["exists"] is True
        assert view["working_days"] == [1, 2, 3, 4]
        # Confirm nothing was committed.
        assert await _read_default(org_id) is None
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(AppUser).where(AppUser.id == actor_id))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            await s.commit()


async def test_update_validation_parity_rejects_what_resolver_degrades(app_under_test: Any) -> None:
    """Each broken working_days / unknown tz the resolver DEGRADES → the service 422s (parity).
    A broken holiday the resolver drops → the service 422s (editor is stricter). A duplicate is
    deduped + ACCEPTED (not 422). Runs against a calendar-less org, never commits."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"WCal VAL {salt}", short_code=f"WV{salt[:6].upper()}")
        s.add(org)
        await s.commit()
        org_id = org.id
        actor = AppUser(
            org_id=org_id,
            keycloak_subject=f"kc-wcal-val-{salt}",
            display_name="WCal V",
            email=None,
        )
        s.add(actor)
        await s.commit()
        actor_id = actor.id

    async def _put(**kw: Any) -> int | None:
        async with get_sessionmaker()() as s:
            actor = await s.get(AppUser, actor_id)
            base = {
                "name": "C",
                "working_days": [1, 2, 3, 4, 5],
                "holidays": [],
                "timezone": "UTC",
            }
            base.update(kw)
            try:
                await update_working_calendar(s, actor=actor, **base)  # type: ignore[arg-type]
                return None
            except ProblemException as exc:
                return exc.status
            finally:
                await s.rollback()

    try:
        # working_days the resolver degrades → 422
        for bad in ([], [0], [8], [True], [1.0], ["1"], "67", [1, 8]):
            assert await _put(working_days=bad) == 422, bad
        # unknown tz the resolver degrades → 422
        assert await _put(timezone="Mars/Phobos") == 422
        # broken holiday the resolver drops → 422
        for badh in (["2026-13-01"], ["nope"], [""]):
            assert await _put(holidays=badh) == 422, badh
        # bounds
        assert await _put(working_days=[1] * 32) == 422
        assert await _put(holidays=[f"2026-01-{(i % 28) + 1:02d}" for i in range(1001)]) == 422
        # empty / too-long name
        assert await _put(name="  ") == 422
        assert await _put(name="x" * 256) == 422
        # duplicate working_days is ACCEPTED (no 422) — parity with the resolver (proven in
        # test_calendar_spec.py: parse_working_days([1,1,2,7]) == {1,2,7}).
        assert await _put(working_days=[1, 1, 5, 5]) is None
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(AppUser).where(AppUser.id == actor_id))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            await s.commit()


async def test_update_insert_audits_even_with_default_values(app_under_test: Any) -> None:
    """INSERT with the synthesized default values still writes a CONFIG_UPDATED audit (before={}).

    Mutation-verify: the OLD code built before_fields from the synthesized default (exists=False),
    so before_fields == after_fields → no audit staged → len==1 assertion would fail. The fix
    sets before_fields={} for the no-row case so the INSERT always fires the audit."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"WCal AUD {salt}", short_code=f"WA{salt[:6].upper()}")
        s.add(org)
        await s.commit()
        org_id, org_tz = org.id, org.timezone
        actor = AppUser(
            org_id=org_id,
            display_name="WCal Aud",
            email=None,
            keycloak_subject=f"kc-wcal-aud-{salt}",
        )
        s.add(actor)
        await s.commit()
        actor_id = actor.id
    try:
        async with get_sessionmaker()() as s:
            actor = await s.get(AppUser, actor_id)
            # Save exactly the synthesized-default values — the OLD code would skip the audit.
            await update_working_calendar(
                s,
                actor=actor,
                name="Default",
                working_days=[1, 2, 3, 4, 5],
                holidays=[],
                timezone=org_tz,
            )
            staged_audits = [
                o
                for o in s.new
                if isinstance(o, AuditEvent) and o.event_type == EventType.CONFIG_UPDATED
            ]
            assert len(staged_audits) == 1, (
                "INSERT with default values must still stage a CONFIG_UPDATED audit"
            )
            assert staged_audits[0].before == {"working_calendar": {}}
            await s.rollback()
        # Confirm nothing was committed (leak-free).
        assert await _read_default(org_id) is None
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(AppUser).where(AppUser.id == actor_id))
            await s.execute(delete(Organization).where(Organization.id == org_id))
            await s.commit()


async def test_http_put_updates_existing_default_and_audits(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """PUT updates AHT's existing default row (UPDATE branch) + writes one CONFIG_UPDATED; GET
    round-trips. Restores the original calendar in finally (app role can't DELETE the row)."""
    subject = f"wc-admin-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("config.update",))
    h = _auth(token_factory, subject)
    org_id = await _default_org_id()
    before = await _read_default(org_id)
    assert before is not None
    orig = (before.name, list(before.working_days), list(before.holidays), before.timezone)
    c0 = await _config_updated_count_for_key(org_id, "working_calendar")
    try:
        body = {
            "name": "Edited calendar",
            "working_days": [1, 2, 3, 4],
            "holidays": ["2026-12-25", "2026-01-01"],
            "timezone": "America/Chicago",
        }
        r = await app_client.put(
            "/api/v1/admin/notifications/working-calendar", headers=h, json=body
        )
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["working_days"] == [1, 2, 3, 4]
        assert v["holidays"] == ["2026-01-01", "2026-12-25"]  # sorted
        assert v["timezone"] == "America/Chicago"
        assert v["exists"] is True
        # GET round-trips the persisted row.
        rg = await app_client.get("/api/v1/admin/notifications/working-calendar", headers=h)
        assert rg.status_code == 200 and rg.json()["holidays"] == ["2026-01-01", "2026-12-25"]
        # A no-op PUT (same values) writes NO new audit.
        c1 = await _config_updated_count_for_key(org_id, "working_calendar")
        assert c1 == c0 + 1, "the real-diff PUT must write exactly one CONFIG_UPDATED audit"
        r2 = await app_client.put(
            "/api/v1/admin/notifications/working-calendar", headers=h, json=body
        )
        assert r2.status_code == 200
        c2 = await _config_updated_count_for_key(org_id, "working_calendar")
        assert c2 == c1, "no-op PUT must not append a CONFIG_UPDATED row"
    finally:
        async with get_sessionmaker()() as s:
            row = (
                await s.execute(
                    select(WorkingCalendar).where(
                        WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                    )
                )
            ).scalar_one()
            row.name, row.working_days, row.holidays, row.timezone = orig
            await s.commit()


async def test_http_put_forbidden_without_config_update(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    subject = f"wc-noperm-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("document.read",))
    h = _auth(token_factory, subject)
    r = await app_client.put(
        "/api/v1/admin/notifications/working-calendar",
        headers=h,
        json={"name": "x", "working_days": [1], "holidays": [], "timezone": "UTC"},
    )
    assert r.status_code == 403, r.text


async def test_http_put_422_on_broken_working_days(
    app_client: AsyncClient, token_factory: Callable[..., str], app_under_test: Any
) -> None:
    """list[Any] body → the bad values reach the SERVICE parser (not Pydantic coercion) → 422."""
    subject = f"wc-bad-{uuid.uuid4().hex[:8]}"
    await _grant(subject, ("config.update",))
    h = _auth(token_factory, subject)
    for bad in ([], [8], [True], [1.0], ["1"]):
        r = await app_client.put(
            "/api/v1/admin/notifications/working-calendar",
            headers=h,
            json={"name": "x", "working_days": bad, "holidays": [], "timezone": "UTC"},
        )
        assert r.status_code == 422, f"{bad} -> {r.status_code} {r.text}"
