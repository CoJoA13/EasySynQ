# Working-calendar Admin Editor (S-notify-7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `config.update`-gated admin editor for the org's default `working_calendar` (week mask + holidays + name + timezone), so business-day SLAs become configurable in-app — closing the R29-named editor residual.

**Architecture:** Extract the strict calendar parsers into a shared pure module both the fail-safe resolver and the fail-loud editor import (validation parity). A thin service (`get`/`update`) does an atomic `ON CONFLICT` upsert of the single `is_default` row; **the route commits** (the `api/config.py` precedent) so an INSERT-branch test can roll back leak-free. The FE adds a self-contained editor section to the existing `/admin/config` Config tab.

**Tech Stack:** FastAPI / Python 3.12 / SQLAlchemy 2 (async) / Pydantic 2.13.4 / PostgreSQL 16 (JSONB, partial-unique index) · React/TS + Mantine + Tailwind / vitest + MSW + jest-axe.

## Global Constraints

- **NO migration** (table + INSERT/SELECT/UPDATE grants exist from 0067; alembic head stays `0067`). DELETE is REVOKEd on `working_calendar` — never delete a calendar row.
- **NO new permission key** (reuse `config.update`; catalog stays **102** — do NOT touch the `==102` assertions). Audit reuses `CONFIG_UPDATED` (no new `event_type` → no `ALTER TYPE`).
- **Validation parity:** the PUT must 422 exactly what `resolve_working_calendar` degrades (working_days→Mon–Fri, tz→UTC) AND reject a holiday the resolver would silently drop — via the SHARED strict parsers. Body fields are `list[Any]` (NOT `list[int]`) so every value reaches the strict parser (pydantic 2.13.4 lax-coerces `[true]`/`["1"]`/`[1.0]`).
- **Duplicates in `working_days` are deduped + ACCEPTED** (not 422) — parity with the resolver (`[1,1]→{1}`).
- **The service does NOT commit; the route commits** (config.py precedent; leak-free INSERT test).
- **Timezone is EDITABLE** here (the operational way to set the business-day tz). Storage: `working_days` sorted-unique int list; `holidays` sorted-unique `YYYY-MM-DD` strings.
- Bounds: `working_days` raw request length ≤ 31; `holidays` length ≤ 1000 → 422 on oversize. `name` non-empty (after strip), ≤ 255.
- **N9 / R53 / R32 unchanged:** the editor only edits config; it never fires/decides/delivers.
- Commit identity is already set repo-local to the noreply. Branch: `feat/s-notify-7-working-calendar-editor`.
- Web tests: import `expect`/`it` from `vitest`; MSW fixtures pinned via `satisfies <Type>`; every endpoint a mounted component fetches needs a base handler (`onUnhandledRequest:"error"`).
- Integration tests use the **app role** (REVOKE DELETE on `working_calendar`/`notification`); operate on AHT's existing default row with **UPDATE-restore** in a `finally`; never leak an org (`test_restore` does `scalar_one()` on `Organization`).

---

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/src/easysynq_api/services/notifications/calendar_spec.py` *(new)* | Pure strict parsers: `parse_working_days`, `parse_holiday`, `is_valid_timezone`. Imported by both the resolver and the editor. |
| `apps/api/src/easysynq_api/services/notifications/escalation.py` *(edit)* | Import the shared parsers; delete the private `_parse_working_days`; use `parse_holiday`. Resolver stays byte-identical. |
| `apps/api/tests/unit/test_calendar_spec.py` *(new)* | Table-driven parser unit tests (incl. the int-holiday byte-identical pin). |
| `apps/api/tests/unit/test_working_calendar_resolve.py` *(edit)* | Repoint the `_parse_working_days` import to `calendar_spec.parse_working_days`. |
| `apps/api/src/easysynq_api/services/notifications/calendar_admin.py` *(new)* | `get_working_calendar` + `update_working_calendar` (validate → atomic upsert → audit-on-diff; no commit). |
| `apps/api/tests/integration/test_working_calendar_admin.py` *(new)* | Service- + HTTP-level integration tests. |
| `apps/api/src/easysynq_api/api/config.py` *(edit)* | The 2 endpoints + `WorkingCalendarUpdate` body; route commits. |
| `packages/contracts/openapi.yaml` *(edit)* | 2 paths + 2 schemas. |
| `apps/web/src/lib/types.ts` *(edit)* | `WorkingCalendar`, `WorkingCalendarUpdate`. |
| `apps/web/src/admin/hooks.ts` *(edit)* | `useWorkingCalendar`, `useUpdateWorkingCalendar`. |
| `apps/web/src/test/msw/handlers.ts` *(edit)* | `workingCalendarFixture` + base GET/PUT handlers. |
| `apps/web/src/admin/WorkingCalendarEditor.tsx` *(new)* + `.test.tsx` *(new)* | The editor section + its tests. |
| `apps/web/src/admin/ConfigAdmin.tsx` *(edit)* | Mount the editor section. |

---

## Task 1: Shared pure parser module (`calendar_spec.py`) + resolver refactor

**Files:**
- Create: `apps/api/src/easysynq_api/services/notifications/calendar_spec.py`
- Create: `apps/api/tests/unit/test_calendar_spec.py`
- Modify: `apps/api/src/easysynq_api/services/notifications/escalation.py` (delete `_parse_working_days` lines ~172-185; replace inline holiday parse lines ~223-231)
- Modify: `apps/api/tests/unit/test_working_calendar_resolve.py:5` (+ rename call-sites)

**Interfaces:**
- Produces: `parse_working_days(value: object) -> frozenset[int] | None`, `parse_holiday(value: object) -> datetime.date | None`, `is_valid_timezone(value: str) -> bool` in `easysynq_api.services.notifications.calendar_spec`.

- [ ] **Step 1: Write the failing unit test** — `apps/api/tests/unit/test_calendar_spec.py`

```python
"""Unit tests for the shared pure calendar parsers (S-notify-7). The editor (fail-loud → 422) and
the resolver (fail-safe → degrade) BOTH use these, so parity can't drift."""

import datetime

from easysynq_api.services.notifications.calendar_spec import (
    is_valid_timezone,
    parse_holiday,
    parse_working_days,
)


def test_working_days_valid_and_dedup():
    assert parse_working_days([1, 2, 3, 4, 5]) == frozenset({1, 2, 3, 4, 5})
    assert parse_working_days([1, 1, 2, 7]) == frozenset({1, 2, 7})  # dedup + ACCEPT


def test_working_days_broken_returns_none():
    assert parse_working_days([]) is None
    assert parse_working_days([0]) is None
    assert parse_working_days([8]) is None
    assert parse_working_days([True]) is None  # bool is an int subclass — rejected
    assert parse_working_days([1.0]) is None  # float rejected
    assert parse_working_days(["1"]) is None  # JSON-string rejected
    assert parse_working_days("67") is None
    assert parse_working_days(5) is None
    assert parse_working_days(None) is None


def test_parse_holiday_str_coercion_keeps_resolver_byte_identical():
    assert parse_holiday("2026-12-25") == datetime.date(2026, 12, 25)
    # The resolver coerced with str() — an int entry like 20260101 must still parse (no isinstance-str guard).
    assert parse_holiday(20260101) == datetime.date(2026, 1, 1)


def test_parse_holiday_broken_returns_none():
    assert parse_holiday("2026-13-01") is None
    assert parse_holiday("nope") is None
    assert parse_holiday("") is None
    assert parse_holiday(None) is None
    assert parse_holiday([1]) is None


def test_is_valid_timezone():
    assert is_valid_timezone("America/Chicago") is True
    assert is_valid_timezone("UTC") is True
    assert is_valid_timezone("Mars/Phobos") is False
    assert is_valid_timezone("") is False
```

- [ ] **Step 2: Run it — verify it fails (module missing)**

Run: `cd apps/api && uv run pytest tests/unit/test_calendar_spec.py -q`
Expected: FAIL — `ModuleNotFoundError: ...calendar_spec`.

- [ ] **Step 3: Create `calendar_spec.py`**

```python
"""Pure, stdlib-only strict calendar parsers (S-notify-7). Shared by the FAIL-SAFE resolver
(``resolve_working_calendar`` degrades on None) and the FAIL-LOUD editor (422s on None), so the two
can never drift. No DB, no I/O (the ``timer.py`` precedent)."""

from __future__ import annotations

import datetime
import zoneinfo


def parse_working_days(value: object) -> frozenset[int] | None:
    """A JSONB ``working_days`` value → a frozenset of ISO weekdays, else None if broken.

    A NON-EMPTY JSON array whose every element is a real int 1..7 — NOT a bool (``True``/``False``
    are ``int`` subclasses → ``int(True)==1``) and NOT a float (``int(1.9)==1``) and NOT a JSON
    string (``"67"`` is iterable → would wrongly become ``{6,7}``). Duplicates are deduped + ACCEPTED
    (``[1,1,2,7] → {1,2,7}``). None ⇒ broken (the resolver falls back to Mon-Fri; the editor 422s)."""
    if not isinstance(value, list) or not value:
        return None
    out: set[int] = set()
    for x in value:
        if isinstance(x, bool) or not isinstance(x, int) or not (1 <= x <= 7):
            return None
        out.add(x)
    return frozenset(out)


def parse_holiday(value: object) -> datetime.date | None:
    """A single holiday entry → a date, else None. Preserves the resolver's ``str()`` coercion so an
    int entry like ``20260101`` still parses (keeps ``resolve_working_calendar`` byte-identical)."""
    try:
        return datetime.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def is_valid_timezone(value: str) -> bool:
    """True iff ``zoneinfo.ZoneInfo(value)`` succeeds (the resolver's tz check)."""
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True
```

- [ ] **Step 4: Run it — verify it passes**

Run: `cd apps/api && uv run pytest tests/unit/test_calendar_spec.py -q`
Expected: PASS (all parser tests).

- [ ] **Step 5: Refactor `escalation.py` to import the shared parsers**

In `apps/api/src/easysynq_api/services/notifications/escalation.py`:
1. Add import near the other `.` imports: `from .calendar_spec import parse_holiday, parse_working_days`.
2. **Delete** the entire private `def _parse_working_days(...)` function (lines ~172-185).
3. In `resolve_working_calendar`, replace `weekdays = _parse_working_days(row.working_days)` with `weekdays = parse_working_days(row.working_days)`.
4. Replace the inline holiday loop body — change:

```python
    for h in raw_holidays:
        try:
            holidays.add(datetime.date.fromisoformat(str(h)))
        except (TypeError, ValueError):
            logger.warning(
                "notifications.timer_bad_holiday", extra={"org_id": str(org_id), "value": str(h)}
            )
```
to:
```python
    for h in raw_holidays:
        parsed = parse_holiday(h)
        if parsed is None:
            logger.warning(
                "notifications.timer_bad_holiday", extra={"org_id": str(org_id), "value": str(h)}
            )
        else:
            holidays.add(parsed)
```

> ⚠ The PostToolUse ruff `--fix` hook strips a just-added unused import (F401). The import is used immediately by the edits above, so add the import in the SAME edit pass as the usage.

- [ ] **Step 6: Repoint the resolver's parser unit test**

In `apps/api/tests/unit/test_working_calendar_resolve.py`: change line 5
`from easysynq_api.services.notifications.escalation import _parse_working_days`
→ `from easysynq_api.services.notifications.calendar_spec import parse_working_days`
and rename every `_parse_working_days(` call to `parse_working_days(` in that file (≈11–15 sites — use a find/replace within the file).

- [ ] **Step 7: Run the affected API unit + resolver suites — verify byte-identical**

Run: `cd apps/api && uv run pytest tests/unit/test_calendar_spec.py tests/unit/test_working_calendar_resolve.py -q`
Expected: PASS (the resolver's behaviour is unchanged; the parser tests are green).

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/calendar_spec.py apps/api/tests/unit/test_calendar_spec.py apps/api/src/easysynq_api/services/notifications/escalation.py apps/api/tests/unit/test_working_calendar_resolve.py
git commit -m "feat(s-notify-7): extract shared strict calendar parsers (calendar_spec)"
```

---

## Task 2: Editor service (`calendar_admin.py`) + service-level integration tests

**Files:**
- Create: `apps/api/src/easysynq_api/services/notifications/calendar_admin.py`
- Create: `apps/api/tests/integration/test_working_calendar_admin.py`

**Interfaces:**
- Consumes: `parse_working_days`, `parse_holiday`, `is_valid_timezone` (Task 1).
- Produces:
  - `get_working_calendar(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]` → `{name, working_days: list[int], holidays: list[str], timezone: str, exists: bool}`.
  - `update_working_calendar(session, *, actor: AppUser, name: str, working_days: list[Any], holidays: list[Any], timezone: str) -> dict[str, Any]` → the saved view. Validates fail-loud (raises `ProblemException(status=422)`); does the atomic `ON CONFLICT` upsert; adds the `CONFIG_UPDATED` audit only on a real diff; **does NOT commit** (the route commits).

- [ ] **Step 1: Write the failing service-level integration test** — `apps/api/tests/integration/test_working_calendar_admin.py`

```python
"""S-notify-7: the working-calendar admin editor — service + HTTP integration proofs."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.working_calendar import WorkingCalendar
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.problems import ProblemException
from easysynq_api.services.notifications.calendar_admin import (
    get_working_calendar,
    update_working_calendar,
)
from easysynq_api.services.notifications.escalation import resolve_working_calendar
from easysynq_api.services.notifications.timer import DEFAULT_CALENDAR

from .test_notification_config import _grant
from .test_vault import _auth

pytestmark = pytest.mark.integration


async def _default_org_id() -> uuid.UUID:
    """The seeded org that owns the is_default working_calendar (AHT in dev; the 0002 org in CI)."""
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(
                select(WorkingCalendar).where(WorkingCalendar.is_default.is_(True))
            )
        ).scalars().first()
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
        actor = AppUser(org_id=org_id, display_name="WCal Admin", email=None)
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
    A broken holiday the resolver DROPS → the service 422s (editor is stricter). A duplicate is
    deduped + ACCEPTED (not 422). Runs against a calendar-less org, never commits."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"WCal VAL {salt}", short_code=f"WV{salt[:6].upper()}")
        s.add(org)
        await s.commit()
        org_id = org.id
        actor = AppUser(org_id=org_id, display_name="WCal V", email=None)
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
```

> Replace the `parse_dedup_resolver_ok()` placeholder line with a direct resolver-parity assertion inline (no extra row needed — the resolver parity for working_days is already proven in `test_calendar_spec.py`); delete that line. (Keep the dup-accepted assert above it.)

- [ ] **Step 2: Run it — verify it fails (service missing)**

Run: `cd apps/api && uv run pytest tests/integration/test_working_calendar_admin.py -q -m integration` *(needs Docker; on this box run sharded/sequential — see windows-dev/dev-workflow notes)*
Expected: FAIL — `ImportError: ...calendar_admin`.

- [ ] **Step 3: Implement `calendar_admin.py`**

```python
"""S-notify-7: the working-calendar admin editor service. Reads/writes the org's single is_default
working_calendar row. Validation is FAIL-LOUD (422 via ProblemException) using the SAME shared
strict parsers the fail-safe resolver trusts, so a saved calendar never silently degrades.

The service does NOT commit — the route commits (the api/config.py precedent). This keeps the
INSERT-branch test leak-free (the app role can't DELETE a working_calendar row)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models.app_user import AppUser
from ...db.models.audit_event import AuditEvent
from ...db.models.organization import Organization
from ...db.models.working_calendar import WorkingCalendar
from ...logging import request_id_var
from ...problems import ProblemException
from .calendar_spec import is_valid_timezone, parse_holiday, parse_working_days

_MAX_WORKING_DAYS_LEN = 31
_MAX_HOLIDAYS_LEN = 1000
_DEFAULT_WORKING_DAYS = [1, 2, 3, 4, 5]


def _rid() -> uuid.UUID | None:
    raw = request_id_var.get()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _422(title: str) -> ProblemException:
    return ProblemException(status=422, code="validation_error", title=title)


async def _load_default(session: AsyncSession, org_id: uuid.UUID) -> WorkingCalendar | None:
    return (
        await session.execute(
            select(WorkingCalendar)
            .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
            .limit(1)  # defensive (mirror resolve_working_calendar) — the partial index guarantees <=1
        )
    ).scalar_one_or_none()


def _view(name: str, working_days: list[int], holidays: list[str], timezone: str, exists: bool) -> dict[str, Any]:
    return {
        "name": name,
        "working_days": working_days,
        "holidays": holidays,
        "timezone": timezone,
        "exists": exists,
    }


async def get_working_calendar(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """The org's is_default working_calendar as a view dict, or the synthesized Mon-Fri default
    (tz = organization.timezone, exists=False) when none. Stored holidays/working_days are SANITIZED
    through the shared parsers (kept-good) so a malformed legacy entry can never wedge a later Save."""
    row = await _load_default(session, org_id)
    if row is None:
        org = await session.get(Organization, org_id)
        org_tz = org.timezone if org is not None else "UTC"
        return _view("Default", list(_DEFAULT_WORKING_DAYS), [], org_tz, exists=False)
    wd = parse_working_days(row.working_days)
    working_days = sorted(wd) if wd is not None else list(_DEFAULT_WORKING_DAYS)
    raw_holidays = row.holidays if isinstance(row.holidays, list) else []
    parsed = [h for h in (parse_holiday(x) for x in raw_holidays) if h is not None]
    holidays = sorted(d.isoformat() for d in parsed)
    tz = row.timezone if is_valid_timezone(row.timezone or "") else "UTC"
    return _view(row.name, working_days, holidays, tz, exists=True)


def _validate(
    name: str, working_days: list[Any], holidays: list[Any], timezone: str
) -> tuple[str, list[int], list[str], str]:
    name = name.strip()
    if not name:
        raise _422("name must not be empty")
    if len(name) > 255:
        raise _422("name must be at most 255 characters")
    if isinstance(working_days, list) and len(working_days) > _MAX_WORKING_DAYS_LEN:
        raise _422("working_days is too long")
    wd = parse_working_days(working_days)
    if wd is None:
        raise _422("working_days must be a non-empty list of ISO weekdays 1..7")
    if not isinstance(holidays, list):
        raise _422("holidays must be a list")
    if len(holidays) > _MAX_HOLIDAYS_LEN:
        raise _422("holidays is too long")
    dates: set[datetime.date] = set()
    for h in holidays:
        parsed = parse_holiday(h)
        if parsed is None:
            raise _422(f"holiday is not a valid YYYY-MM-DD date: {h!r}")
        dates.add(parsed)
    if not is_valid_timezone(timezone):
        raise _422(f"unknown IANA timezone: {timezone!r}")
    return name, sorted(wd), sorted(d.isoformat() for d in dates), timezone


async def update_working_calendar(
    session: AsyncSession,
    *,
    actor: AppUser,
    name: str,
    working_days: list[Any],
    holidays: list[Any],
    timezone: str,
) -> dict[str, Any]:
    """Validate (fail-loud → 422) → atomic ON CONFLICT upsert of the is_default row → audit
    CONFIG_UPDATED on a real diff. Does NOT commit (the route commits)."""
    org_id = actor.org_id
    name, wd, hol, tz = _validate(name, working_days, holidays, timezone)

    before = await get_working_calendar(session, org_id)  # existing row or synthesized default
    before_fields = {k: before[k] for k in ("name", "working_days", "holidays", "timezone")}
    after_fields = {"name": name, "working_days": wd, "holidays": hol, "timezone": tz}

    stmt = (
        pg_insert(WorkingCalendar)
        .values(
            id=uuid.uuid4(),
            org_id=org_id,
            name=name,
            working_days=wd,
            holidays=hol,
            timezone=tz,
            is_default=True,
        )
        .on_conflict_do_update(
            index_elements=["org_id"],
            index_where=sa.text("is_default"),
            set_={
                "name": name,
                "working_days": wd,
                "holidays": hol,
                "timezone": tz,
                "updated_at": sa.func.now(),
            },
        )
    )
    await session.execute(stmt)

    if before_fields != after_fields:
        session.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=datetime.datetime.now(datetime.UTC),
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.CONFIG_UPDATED,
                object_type=AuditObjectType.config,
                object_id=org_id,
                before={"working_calendar": before_fields},
                after={"working_calendar": after_fields},
                request_id=_rid(),
            )
        )
    return _view(name, wd, hol, tz, exists=True)
```

- [ ] **Step 4: Run the service-level tests — verify they pass**

Run: `cd apps/api && uv run pytest tests/integration/test_working_calendar_admin.py -q -m integration`
Expected: PASS (GET-synth, INSERT-no-commit, validation-parity).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/calendar_admin.py apps/api/tests/integration/test_working_calendar_admin.py
git commit -m "feat(s-notify-7): working-calendar editor service (validate + atomic upsert, no commit)"
```

---

## Task 3: Endpoints (`api/config.py`) + openapi + HTTP integration tests

**Files:**
- Modify: `apps/api/src/easysynq_api/api/config.py` (add the body model + 2 routes)
- Modify: `packages/contracts/openapi.yaml` (2 paths + 2 schemas)
- Modify: `apps/api/tests/integration/test_working_calendar_admin.py` (add HTTP-level tests)

**Interfaces:**
- Consumes: `get_working_calendar`, `update_working_calendar` (Task 2).
- Produces: `GET /api/v1/admin/notifications/working-calendar` + `PUT /api/v1/admin/notifications/working-calendar` (both `config.update`-gated; PUT body `WorkingCalendarUpdate`; route commits).

- [ ] **Step 1: Write the failing HTTP integration tests** (append to `test_working_calendar_admin.py`)

```python
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
        rg = await app_client.get(
            "/api/v1/admin/notifications/working-calendar", headers=h
        )
        assert rg.status_code == 200 and rg.json()["holidays"] == ["2026-01-01", "2026-12-25"]
        # A no-op PUT (same values) writes NO new audit.
        from .test_notification_config import _config_updated_count_for_key

        c1 = await _config_updated_count_for_key(org_id, "working_calendar")
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
```

- [ ] **Step 2: Run — verify they fail (routes missing → 404)**

Run: `cd apps/api && uv run pytest tests/integration/test_working_calendar_admin.py -q -m integration -k http`
Expected: FAIL (404 — routes not mounted).

- [ ] **Step 3: Add the body model + 2 routes to `api/config.py`**

Add imports at the top:
```python
from typing import Any  # (already present)
from .services.notifications.calendar_admin import get_working_calendar, update_working_calendar
```
Add the body model near `OrgConfigUpdate`:
```python
class WorkingCalendarUpdate(BaseModel):
    # list[Any] (NOT list[int]/list[str]) so EVERY value reaches the strict service parser —
    # pydantic 2.13.4 lax-coerces [true]/["1"]/[1.0] under list[int], which would make the
    # parity guarantee false (the strict bool/float/string guards would be dead code).
    name: str
    working_days: list[Any]
    holidays: list[Any]
    timezone: str
```
Add the two routes (after the health endpoint):
```python
@router.get("/admin/notifications/working-calendar")
async def get_working_calendar_endpoint(
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The org's default working calendar (or a synthesized Mon-Fri default). Needs config.update."""
    return await get_working_calendar(session, caller.org_id)


@router.put("/admin/notifications/working-calendar")
async def put_working_calendar_endpoint(
    body: WorkingCalendarUpdate,
    caller: AppUser = Depends(_config_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Replace the org's default working calendar (validate → atomic upsert → audit). config.update."""
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
```

- [ ] **Step 4: Run the HTTP tests — verify they pass**

Run: `cd apps/api && uv run pytest tests/integration/test_working_calendar_admin.py -q -m integration`
Expected: PASS (all service + HTTP tests).

- [ ] **Step 5: Add the openapi paths + schemas** — `packages/contracts/openapi.yaml`

Under `paths:`, after the `/admin/notifications/health` block:
```yaml
  /admin/notifications/working-calendar:
    get:
      tags: [admin]
      operationId: getWorkingCalendar
      summary: The org's default working calendar (week mask + holidays + timezone). Needs config.update (SYSTEM, admin-only).
      responses:
        "200":
          description: The default working calendar (synthesized Mon-Fri default when none exists).
          content:
            application/json:
              schema: { $ref: "#/components/schemas/WorkingCalendar" }
        "403": { $ref: "#/components/responses/ProblemResponse" }
    put:
      tags: [admin]
      operationId: putWorkingCalendar
      summary: >-
        Replace the org's default working calendar — week mask (ISO 1..7), holidays (YYYY-MM-DD),
        name, timezone. Business-day SLA reminders/escalations resolve against this (R29). Audited
        CONFIG_UPDATED. Needs config.update (SYSTEM, admin-only).
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/WorkingCalendarUpdate" }
      responses:
        "200":
          description: The saved working calendar.
          content:
            application/json:
              schema: { $ref: "#/components/schemas/WorkingCalendar" }
        "403": { $ref: "#/components/responses/ProblemResponse" }
        "422": { $ref: "#/components/responses/ProblemResponse" }
```
Under `components.schemas:`:
```yaml
    WorkingCalendar:
      type: object
      required: [name, working_days, holidays, timezone, exists]
      properties:
        name: { type: string }
        working_days:
          type: array
          items: { type: integer, minimum: 1, maximum: 7 }
          description: ISO weekday ints (1=Mon..7=Sun) that are working days, sorted unique.
        holidays:
          type: array
          items: { type: string, format: date }
          description: Holiday YYYY-MM-DD dates, sorted ascending.
        timezone: { type: string, description: IANA timezone the business-day calendar is evaluated in. }
        exists: { type: boolean, description: False when synthesized (no stored default row yet). }
    WorkingCalendarUpdate:
      type: object
      required: [name, working_days, holidays, timezone]
      properties:
        name: { type: string, maxLength: 255 }
        working_days:
          type: array
          items: { type: integer, minimum: 1, maximum: 7 }
        holidays:
          type: array
          items: { type: string, format: date }
        timezone: { type: string }
```

- [ ] **Step 6: Lint the contract**

Run: `npx @redocly/cli lint packages/contracts/openapi.yaml` (or `/check-contracts`).
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/api/config.py packages/contracts/openapi.yaml apps/api/tests/integration/test_working_calendar_admin.py
git commit -m "feat(s-notify-7): working-calendar GET/PUT endpoints + openapi"
```

---

## Task 4: FE types, hooks, MSW base handlers

**Files:**
- Modify: `apps/web/src/lib/types.ts` (add after `OrgConfigUpdate`, ~line 1964)
- Modify: `apps/web/src/admin/hooks.ts`
- Modify: `apps/web/src/test/msw/handlers.ts` (fixture near `orgConfigFixture` ~line 2669; base handlers near the config handlers ~line 3558)

**Interfaces:**
- Produces: `WorkingCalendar`, `WorkingCalendarUpdate` (types); `useWorkingCalendar()`, `useUpdateWorkingCalendar()` (hooks); `workingCalendarFixture` (MSW).

- [ ] **Step 1: Add the types** — `apps/web/src/lib/types.ts`

```typescript
export interface WorkingCalendar {
  name: string;
  working_days: number[];
  holidays: string[];
  timezone: string;
  exists: boolean;
}

export interface WorkingCalendarUpdate {
  name: string;
  working_days: number[];
  holidays: string[];
  timezone: string;
}
```

- [ ] **Step 2: Add the hooks** — `apps/web/src/admin/hooks.ts`

```typescript
import type {
  NotificationDeliveryHealth,
  OrgConfig,
  OrgConfigUpdate,
  WorkingCalendar,
  WorkingCalendarUpdate,
} from "../lib/types";

// ... existing hooks ...

export function useWorkingCalendar() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["working-calendar"],
    queryFn: () => api.get<WorkingCalendar>("/api/v1/admin/notifications/working-calendar"),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useUpdateWorkingCalendar() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WorkingCalendarUpdate) =>
      api.send<WorkingCalendar>("PUT", "/api/v1/admin/notifications/working-calendar", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["working-calendar"] });
    },
  });
}
```

- [ ] **Step 3: Add the MSW fixture + base handlers** — `apps/web/src/test/msw/handlers.ts`

Near `orgConfigFixture` (~2669):
```typescript
export const workingCalendarFixture = {
  name: "Default",
  working_days: [1, 2, 3, 4, 5],
  holidays: ["2026-12-25"],
  timezone: "America/Chicago",
  exists: true,
} satisfies WorkingCalendar;
```
(Add `WorkingCalendar` to the `../../lib/types` import at the top of handlers.ts.)

Near the config handlers (~3558), add:
```typescript
  http.get("/api/v1/admin/notifications/working-calendar", () =>
    HttpResponse.json(workingCalendarFixture as unknown as Record<string, unknown>),
  ),
  http.put("/api/v1/admin/notifications/working-calendar", async ({ request }) =>
    HttpResponse.json({
      ...workingCalendarFixture,
      ...((await request.json()) as Record<string, unknown>),
      exists: true,
    } as Record<string, unknown>),
  ),
```

- [ ] **Step 4: Typecheck — verify the new types compile**

Run: `cd apps/web && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/admin/hooks.ts apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-notify-7): FE working-calendar types, hooks, MSW base handlers"
```

---

## Task 5: `WorkingCalendarEditor.tsx` + mount + tests

**Files:**
- Create: `apps/web/src/admin/WorkingCalendarEditor.tsx`
- Create: `apps/web/src/admin/WorkingCalendarEditor.test.tsx`
- Modify: `apps/web/src/admin/ConfigAdmin.tsx` (mount the section before `<NotificationHealthPanel/>`)

**Interfaces:**
- Consumes: `useWorkingCalendar`, `useUpdateWorkingCalendar` (Task 4); `timezones.ts` (`allTimeZones`, `detectTimeZone`, `restingZones`).
- Produces: `<WorkingCalendarEditor/>`.

- [ ] **Step 1: Write the failing component test** — `apps/web/src/admin/WorkingCalendarEditor.test.tsx`

```typescript
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { WorkingCalendar } from "../lib/types";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { workingCalendarFixture } from "../test/msw/handlers";
import { WorkingCalendarEditor } from "./WorkingCalendarEditor";

function statefulCal(initial: WorkingCalendar = workingCalendarFixture) {
  let current: WorkingCalendar = { ...initial };
  return [
    http.get("/api/v1/admin/notifications/working-calendar", () =>
      HttpResponse.json(current as unknown as Record<string, unknown>),
    ),
    http.put("/api/v1/admin/notifications/working-calendar", async ({ request }) => {
      const b = (await request.json()) as Partial<WorkingCalendar>;
      current = { ...current, ...b, exists: true };
      return HttpResponse.json(current as unknown as Record<string, unknown>);
    }),
  ];
}

describe("WorkingCalendarEditor", () => {
  it("renders the loaded calendar and is accessible", async () => {
    server.use(...statefulCal());
    const { container } = renderWithProviders(<WorkingCalendarEditor />);
    expect(await screen.findByRole("checkbox", { name: "Monday" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Saturday" })).not.toBeChecked();
    expect(screen.getByText("2026-12-25")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("Save is gated by dirty and returns to disabled + Saved after a round-trip", async () => {
    server.use(...statefulCal());
    const user = userEvent.setup();
    renderWithProviders(<WorkingCalendarEditor />);
    const sat = await screen.findByRole("checkbox", { name: "Saturday" });
    const save = screen.getByRole("button", { name: "Save calendar" });
    expect(save).toBeDisabled();
    await user.click(sat); // dirty
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(save).toBeDisabled()); // value-equality dirty reset
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("enforces at least one working day (mutation-verify, not tautology)", async () => {
    server.use(...statefulCal());
    const user = userEvent.setup();
    renderWithProviders(<WorkingCalendarEditor />);
    for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]) {
      await user.click(await screen.findByRole("checkbox", { name: day }));
    }
    const save = screen.getByRole("button", { name: "Save calendar" });
    expect(save).toBeDisabled(); // dirty + invalid
    expect(screen.getByText(/at least one working day/i)).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Monday" }));
    expect(save).toBeEnabled(); // re-valid
  });

  it("adds a holiday via the date input and removes it; blank Add is a no-op", async () => {
    server.use(...statefulCal({ ...workingCalendarFixture, holidays: [] }));
    const user = userEvent.setup();
    renderWithProviders(<WorkingCalendarEditor />);
    const addBtn = await screen.findByRole("button", { name: "Add holiday" });
    await user.click(addBtn); // blank input → no-op
    expect(screen.queryByText(/^2026-/)).not.toBeInTheDocument();
    const input = screen.getByLabelText("Holiday date");
    fireEvent.change(input, { target: { value: "2026-12-25" } });
    await user.click(addBtn);
    expect(await screen.findByText("2026-12-25")).toBeInTheDocument();
    const remove = screen.getByRole("button", { name: "Remove holiday 2026-12-25" });
    await user.click(remove);
    expect(screen.queryByText("2026-12-25")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — verify it fails (component missing)**

Run: `cd apps/web && npx vitest run src/admin/WorkingCalendarEditor.test.tsx`
Expected: FAIL — cannot find `./WorkingCalendarEditor`.

- [ ] **Step 3: Implement `WorkingCalendarEditor.tsx`**

```typescript
import {
  Badge,
  Button,
  Checkbox,
  Group,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { ErrorState, LoadingState, MutationErrorState } from "../lib/states";
import type { WorkingCalendar, WorkingCalendarUpdate } from "../lib/types";
import { allTimeZones, detectTimeZone, restingZones } from "../features/notifications/timezones";
import { useUpdateWorkingCalendar, useWorkingCalendar } from "./hooks";

const WEEKDAYS: { iso: number; label: string }[] = [
  { iso: 1, label: "Monday" },
  { iso: 2, label: "Tuesday" },
  { iso: 3, label: "Wednesday" },
  { iso: 4, label: "Thursday" },
  { iso: 5, label: "Friday" },
  { iso: 6, label: "Saturday" },
  { iso: 7, label: "Sunday" },
];

interface Working {
  name: string;
  days: number[]; // sorted-unique ISO ints
  holidays: string[]; // sorted-unique YYYY-MM-DD
  timezone: string;
}

const uniqSortNums = (xs: number[]) => [...new Set(xs)].sort((a, b) => a - b);
const uniqSortStrs = (xs: string[]) => [...new Set(xs)].sort();

function toWorking(c: WorkingCalendar): Working {
  return {
    name: c.name,
    days: uniqSortNums(c.working_days),
    holidays: uniqSortStrs(c.holidays),
    timezone: c.timezone,
  };
}

// Value-equality over canonical (already sorted-unique) forms — reference !== would read permanently
// dirty after the post-save reseed (the S-notify-3b post-save-reset class).
function isDirty(w: Working, b: WorkingCalendar): boolean {
  return (
    w.name !== b.name ||
    w.timezone !== b.timezone ||
    JSON.stringify(w.days) !== JSON.stringify(uniqSortNums(b.working_days)) ||
    JSON.stringify(w.holidays) !== JSON.stringify(uniqSortStrs(b.holidays))
  );
}

export function WorkingCalendarEditor() {
  const cal = useWorkingCalendar();
  const update = useUpdateWorkingCalendar();
  const [working, setWorking] = useState<Working | null>(null);
  const [holidayInput, setHolidayInput] = useState("");

  useEffect(() => {
    if (cal.data) setWorking(toWorking(cal.data));
  }, [cal.data]);

  const detected = useMemo(detectTimeZone, []);
  const [tzSearch, setTzSearch] = useState("");
  const tzData = useMemo(() => {
    const current = working?.timezone ?? "UTC";
    const q = tzSearch.trim().toLowerCase();
    if (!q) return restingZones(detected, current);
    const matches = allTimeZones()
      .filter((z) => z.toLowerCase().includes(q))
      .slice(0, 50);
    return matches.includes(current) ? matches : [current, ...matches];
  }, [tzSearch, detected, working?.timezone]);

  if (cal.isError) {
    return <ErrorState title="Couldn't load the working calendar" onRetry={() => void cal.refetch()} />;
  }
  if (cal.isLoading || !working || !cal.data) {
    return <LoadingState label="Loading working calendar" />;
  }

  const validName = working.name.trim().length > 0 && working.name.length <= 255;
  const hasDay = working.days.length > 0;
  const dirty = isDirty(working, cal.data);
  const canSave = dirty && validName && hasDay;

  const toggleDay = (iso: number, on: boolean) =>
    setWorking({
      ...working,
      days: uniqSortNums(on ? [...working.days, iso] : working.days.filter((d) => d !== iso)),
    });

  const addHoliday = () => {
    const v = holidayInput; // <input type=date> emits "" when empty/invalid
    if (!v || working.holidays.includes(v)) return;
    setWorking({ ...working, holidays: uniqSortStrs([...working.holidays, v]) });
    setHolidayInput("");
  };

  const removeHoliday = (d: string) =>
    setWorking({ ...working, holidays: working.holidays.filter((h) => h !== d) });

  const save = () => {
    if (!canSave) return;
    const body: WorkingCalendarUpdate = {
      name: working.name.trim(),
      working_days: working.days,
      holidays: working.holidays,
      timezone: working.timezone,
    };
    update.mutate(body);
  };

  return (
    <Stack gap="md">
      <Title order={3}>Working calendar</Title>
      <Text size="sm" c="dimmed">
        Business-day reminders and escalations skip non-working days and the holidays below.
      </Text>

      <TextInput
        label="Calendar name"
        value={working.name}
        error={!validName ? "A name is required" : undefined}
        onChange={(e) => setWorking({ ...working, name: e.currentTarget.value })}
      />

      <Checkbox.Group
        label="Working days"
        description="The weekdays SLAs count as business days."
        value={working.days.map(String)}
        onChange={(vals) =>
          setWorking({ ...working, days: uniqSortNums(vals.map(Number)) })
        }
        error={!hasDay ? "Select at least one working day" : undefined}
      >
        <Group gap="md" mt="xs">
          {WEEKDAYS.map((d) => (
            <Checkbox key={d.iso} value={String(d.iso)} label={d.label} aria-label={d.label} />
          ))}
        </Group>
      </Checkbox.Group>

      <Stack gap="xs">
        <Text fw={600} size="sm">
          Holidays
        </Text>
        <Group align="flex-end">
          <TextInput
            type="date"
            label="Holiday date"
            aria-label="Holiday date"
            value={holidayInput}
            onChange={(e) => setHolidayInput(e.currentTarget.value)}
          />
          <Button variant="default" onClick={addHoliday} disabled={!holidayInput} aria-label="Add holiday">
            Add holiday
          </Button>
        </Group>
        <Group gap="xs">
          {working.holidays.length === 0 ? (
            <Text size="sm" c="dimmed">
              No holidays.
            </Text>
          ) : (
            working.holidays.map((d) => (
              <Badge
                key={d}
                variant="light"
                rightSection={
                  <Button
                    variant="transparent"
                    size="compact-xs"
                    p={0}
                    aria-label={`Remove holiday ${d}`}
                    onClick={() => removeHoliday(d)}
                  >
                    ×
                  </Button>
                }
              >
                {d}
              </Badge>
            ))
          )}
        </Group>
      </Stack>

      <Select
        label="Timezone"
        description="Business days are evaluated in this timezone. Type to search all time zones."
        searchable
        searchValue={tzSearch}
        onSearchChange={setTzSearch}
        onDropdownOpen={() => setTzSearch("")}
        data={tzData}
        value={working.timezone}
        onChange={(v) => v && setWorking({ ...working, timezone: v })}
        nothingFoundMessage="No matching zone"
        limit={50}
        allowDeselect={false}
        comboboxProps={{ keepMounted: false }}
      />

      <div>
        <Button onClick={save} disabled={!canSave} loading={update.isPending}>
          Save calendar
        </Button>
        {update.isSuccess && !dirty && (
          <Text size="sm" c="dimmed" mt="xs">
            Saved.
          </Text>
        )}
      </div>
      {update.isError && (
        <MutationErrorState title="Couldn't save the working calendar" error={update.error} />
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run the component test — verify it passes**

Run: `cd apps/web && npx vitest run src/admin/WorkingCalendarEditor.test.tsx`
Expected: PASS (render/a11y, dirty round-trip reset, ≥1-weekday mutation-verify, holiday add/remove/blank-guard).

- [ ] **Step 5: Mount the editor in `ConfigAdmin.tsx`**

Add the import: `import { WorkingCalendarEditor } from "./WorkingCalendarEditor";`
In the returned JSX, insert `<WorkingCalendarEditor />` between the toggles `</Stack>` and `<NotificationHealthPanel />`:
```tsx
      </Stack>

      <WorkingCalendarEditor />

      <NotificationHealthPanel />
    </Stack>
```

- [ ] **Step 6: Run the ConfigAdmin suite — verify the base handlers keep it green**

Run: `cd apps/web && npx vitest run src/admin/ConfigAdmin.test.tsx src/admin/WorkingCalendarEditor.test.tsx`
Expected: PASS (the base GET/PUT handlers from Task 4 prevent the unhandled-request error in ConfigAdmin's success-path suites).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/admin/WorkingCalendarEditor.tsx apps/web/src/admin/WorkingCalendarEditor.test.tsx apps/web/src/admin/ConfigAdmin.tsx
git commit -m "feat(s-notify-7): working-calendar editor section in the admin Config tab"
```

---

## Task 6: Full gates + adversarial review + live-smoke

**Files:** none (verification only).

- [ ] **Step 1: API gate** — `/check-api` (ruff + format-check + mypy-strict + pytest unit). Expected: green. (Integration: run `test_working_calendar_admin.py` + `test_working_calendar_resolve.py` + the timer-sweep suite under Docker, sharded/sequential.)
- [ ] **Step 2: Web gate** — `/check-web` (eslint + strict tsc + build + the full vitest suite). Expected: green (`noUncheckedIndexedAccess` + cross-file drift).
- [ ] **Step 3: Contracts gate** — `/check-contracts` (redocly). Expected: green.
- [ ] **Step 4: Adversarial review** — run the `diff-critic` agent + the `web-test-trap-reviewer` agent on the branch diff; triage + fix any confirmed finding. (No `migration-reviewer` — no migration.)
- [ ] **Step 5: Live-smoke** — `/live-smoke`: rebuild web; grant `demo` a `config.update` SYSTEM override (org AHT); open `/admin/config`; edit the week mask + add a holiday + change the timezone + Save; verify the DB row; then run a `timer_sweep` against a task whose business-day reminder/escalation threshold straddles the new holiday and confirm the threshold **SHIFTS** (the end-to-end proof). (The UPDATE path is covered; the INSERT path is integration-test-only — the dev org has the seed.)
- [ ] **Step 6: Open the PR** — `/pr` (full local gate, then a PR against protected `main`). Then the Codex review loop (triage → fix → reply → resolve → re-request until 👍).

---

## Self-Review

**Spec coverage:** §4.1 shared parsers → Task 1. §4.2 service (get/update/upsert/audit/sanitize) → Task 2. §4.3 endpoints + list[Any] body + route-commits → Task 3. §4.4 openapi → Task 3 Step 5. §5 FE (Checkbox.Group, holiday input + blank guard + distinct remove labels, editable tz Select, value-equality dirty, independent Save) → Task 5. §6 testing (parser unit + parity matrix + GET split + INSERT-no-commit + dup-accept + holiday-reject + tz-reject + bounds + audit-on-diff + 403 + MSW base handlers + mutation-verify gating + fireEvent date input) → Tasks 1–5. §7 constraints → Global Constraints. §8 residuals → unchanged (finish-slice). §9 inventory → File Structure.

**Concurrency/INSERT-race:** the `ON CONFLICT (org_id) WHERE is_default DO UPDATE` upsert (Task 2 Step 3) makes the statement atomic — a concurrent no-row INSERT resolves to an UPDATE, no IntegrityError/500. The INSERT correctness is covered leak-free (service test, rolled back). The operational UPDATE-side concurrency is inherently safe (single statement); a dedicated 2-session race test is OPTIONAL (would require committing on AHT + UPDATE-restore) — add it if the reviewer asks.

**Placeholder scan:** the only intentional placeholder is the `parse_dedup_resolver_ok()` line in Task 2 Step 1, explicitly flagged to delete/replace with an inline dup-accept assertion. No other TBD/TODO.

**Type consistency:** `get_working_calendar`/`update_working_calendar` signatures + the `{name, working_days, holidays, timezone, exists}` view are identical across Tasks 2–4; the FE `WorkingCalendar`/`WorkingCalendarUpdate` mirror it; `useWorkingCalendar`/`useUpdateWorkingCalendar` names match Tasks 4–5.
