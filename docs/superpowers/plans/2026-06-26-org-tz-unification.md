# Org Timezone Unification (S-orgtz-unify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the three drifting org-timezone sources into one canonical, DB-resolved timezone (`working_calendar.timezone → organization.timezone → env → UTC`) so review dates, the `review_state` badge, snap/timer, and notification rendering all judge dates in one frame.

**Architecture:** A new `services/common/org_clock.py` owns the single resolver (`resolve_org_tz`) + a request/sweep-scoped `ContextVar`. `today_org()` reads the contextvar (set once at the auth boundary, and around the escalation render); the genuine date-*transform* functions (`compute_next_review_due`, `read_cadence`/`_last_released_effective_from`) take an explicit `org_tz` param (pure, unit-testable). `resolve_working_calendar` sources its tz from the same `pick_tz` chain, so the calendar/timer frame and the review/date frame cannot disagree.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, Celery, `zoneinfo`, pytest (unit + testcontainers integration), argparse CLI.

## Global Constraints

- **NO migration** — both DB columns (`organization.timezone`, `working_calendar.timezone`) already exist; head stays **`0067`**. **NO new permission key** (catalog stays 102). **NO web/FE change** (`review_state`/MR badge are server-computed). **env `easysynq_org_timezone` is KEPT** as the bottom fallback.
- **Canonical tz chain (D-1):** `is_default working_calendar.timezone → organization.timezone → env easysynq_org_timezone → UTC`, all fail-safe (never raise on a bad IANA name).
- **R8 cutover stays UTC (D-4):** only date-level *display/derivation* moves to the canonical tz; effective-date cutover is unchanged.
- **Hybrid mechanism (D-2):** `today_org()` is contextvar-backed; compute functions take explicit `org_tz`.
- **Contextvar no-leak rule:** a `ContextVar.set()` in an `async` request dependency is isolated to that request's task (its context copy is discarded at task end) — safe without reset. In a **worker** (Celery), wrap per-org/per-task usage in the `using_org_tz()` context manager (reset on exit) so it never leaks to the next task in the same loop.
- **Verification gates (all must pass before PR):** `/check-api` (ruff + ruff-format + mypy-strict + unit), `/check-migrations` (no migration here, but run to confirm `alembic check` clean — head 0067), `/check-web` (unchanged — confirm still green), `/check-contracts` (unchanged). Then `diff-critic` on the branch diff.
- **Test gotchas (recurring):** (a) the PostToolUse ruff `--fix` hook strips a just-added import before its first use (F401) — add the using code first, or re-add the import. (b) An integration test writing an `audit_event` must use an `occurred_at` inside a seeded monthly partition (2026-06/07/08); the existing review/cadence tests use real `datetime.now(UTC)` (current month is seeded) — keep that. (c) The integration DB is **shared** across files and `working_calendar`/`organization` rows can't be deleted (REVOKE DELETE / RESTRICT FK) — never assume the org's tz; **resolve the actual canonical tz** (`resolve_org_tz`) and compute expected dates in *that* frame. (d) A service-level integration test still needs the `app_under_test` fixture (it repoints `get_sessionmaker()` to the testcontainer DB).
- **On this Linux box `pytest -m unit` has a known 17-failure baseline** (ProactorEventLoop / symlink) — run **targeted** unit files (`cd apps/api && uv run pytest tests/unit/test_<x>.py`) for a clean local signal; treat the full suite as CI-authoritative.

---

## File map

**Create:**
- `apps/api/src/easysynq_api/services/common/org_clock.py` — the resolver + contextvar (Task 1).
- `apps/api/src/easysynq_api/cli/backfill_review_dates.py` — the backfill CLI (Task 8).
- `apps/api/tests/unit/test_org_clock.py` — pick_tz/current_org_tz/using_org_tz unit tests (Task 1).
- `apps/api/tests/integration/test_org_clock.py` — resolve_org_tz chain + parity (Task 2).
- `apps/api/tests/integration/test_backfill_review_dates.py` — backfill correctness (Task 8).

**Modify:**
- `services/notifications/escalation.py` — `resolve_working_calendar` tz via `pick_tz`; `process_task_timers` render wrap (Tasks 2, 6).
- `services/vault/review.py` — `_org_tz`/`today_org` delegate; `compute_next_review_due` gains `org_tz`; `sweep_reviews` explicit `today` (Tasks 2, 4, 5).
- `services/mgmt_review/cadence.py` — `read_cadence`/`_last_released_effective_from` gain `org_tz`; `sweep_mgmt_reviews` explicit `today`/`org_tz` (Task 5).
- `api/mgmt_review.py` — pass `org_tz` to `read_cadence` (Task 5).
- `api/documents.py` — `compute_next_review_due` caller passes `current_org_tz()` (Task 4).
- `services/vault/lifecycle.py` — `compute_next_review_due` caller passes `current_org_tz()` (Task 4).
- `auth/dependencies.py` — `get_current_user` sets the contextvar (Task 3).
- `services/notifications/render.py` — `_fmt_date` re-converts aware datetimes (Task 6).
- `services/notifications/timer.py` — OVERDUE `now_is_working` gate (Task 7).
- `tests/unit/test_review_domain.py`, `tests/unit/test_mgmt_review_cadence.py`, `tests/unit/test_notification_timer.py` — updated for the new signatures/gate (Tasks 4, 5, 7).
- `tests/integration/test_periodic_review.py`, `tests/integration/test_mgmt_review_cadence.py` — divergent-tz hardening (Task 9).
- `docs/decisions-register.md`, `CLAUDE.md`, `docs/slice-history.md` — R56 + amends (Task 10).

---

## Task 1: `org_clock` module — resolver + contextvar

**Files:**
- Create: `apps/api/src/easysynq_api/services/common/org_clock.py`
- Test: `apps/api/tests/unit/test_org_clock.py`

**Interfaces:**
- Produces:
  - `pick_tz(cal_tz: str | None, org_tz: str | None) -> zoneinfo.ZoneInfo` — sync, pure, chain cal→org→env→UTC.
  - `current_org_tz() -> zoneinfo.ZoneInfo` — contextvar value, else env→UTC.
  - `set_request_org_tz(tz: zoneinfo.ZoneInfo) -> None` — set (no reset).
  - `using_org_tz(tz: zoneinfo.ZoneInfo)` — sync context manager (set + reset).
  - `async resolve_org_tz(session: AsyncSession, org_id: uuid.UUID) -> zoneinfo.ZoneInfo`.
  - `async resolve_default_org_tz(session: AsyncSession) -> zoneinfo.ZoneInfo`.

- [ ] **Step 1: Write the failing unit test**

Create `apps/api/tests/unit/test_org_clock.py`:

```python
import zoneinfo

import pytest

from easysynq_api.services.common import org_clock


def test_pick_tz_prefers_calendar_then_org():
    assert org_clock.pick_tz("America/Chicago", "America/Denver") == zoneinfo.ZoneInfo(
        "America/Chicago"
    )
    assert org_clock.pick_tz(None, "America/Denver") == zoneinfo.ZoneInfo("America/Denver")


def test_pick_tz_skips_invalid_falls_through():
    # Invalid calendar tz → org tz; invalid both → env default (UTC in tests) → UTC.
    assert org_clock.pick_tz("Not/AZone", "Europe/Paris") == zoneinfo.ZoneInfo("Europe/Paris")
    assert org_clock.pick_tz("Not/AZone", "Also/Bad") == zoneinfo.ZoneInfo("UTC")
    assert org_clock.pick_tz(None, None) == zoneinfo.ZoneInfo("UTC")


def test_current_org_tz_unset_is_env_fallback():
    # No contextvar set → env easysynq_org_timezone (UTC in tests).
    assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")


def test_using_org_tz_sets_and_resets():
    tokyo = zoneinfo.ZoneInfo("Asia/Tokyo")
    assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")
    with org_clock.using_org_tz(tokyo):
        assert org_clock.current_org_tz() == tokyo
    assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")


def test_set_request_org_tz_no_reset_within_call():
    org_clock.using_org_tz  # keep import used
    tok = zoneinfo.ZoneInfo("Asia/Tokyo")
    # set_request_org_tz mutates the current context (a request task) without a token.
    org_clock.set_request_org_tz(tok)
    assert org_clock.current_org_tz() == tok
    # Reset for test isolation (the next test runs in the same pytest task context).
    with org_clock.using_org_tz(zoneinfo.ZoneInfo("UTC")):
        assert org_clock.current_org_tz() == zoneinfo.ZoneInfo("UTC")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_org_clock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'easysynq_api.services.common.org_clock'`

- [ ] **Step 3: Write the module**

Create `apps/api/src/easysynq_api/services/common/org_clock.py`:

```python
"""The single canonical org-timezone resolver + a request/sweep-scoped contextvar (S-orgtz-unify,
R56).

One source of truth for "the org's timezone": the is_default working_calendar's tz, falling back to
organization.timezone, then env easysynq_org_timezone, then UTC. ``resolve_working_calendar``
(escalation.py) sources its tz from the SAME ``pick_tz`` chain, so the calendar/timer frame and the
review/date frame can never disagree (parity by construction).

``today_org()``/``_org_tz()`` (services/vault/review.py) read ``current_org_tz()``: the contextvar
value when set (the auth boundary sets it per-request; the escalation sweep sets it per-task around
the render), else the env fallback — so an unset context degrades to the pre-unify behaviour.

This module imports ONLY models + config — never workflow/engine/escalation — so it introduces no
import cycle.
"""

from __future__ import annotations

import contextlib
import uuid
import zoneinfo
from collections.abc import Iterator
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models.organization import Organization
from ...db.models.working_calendar import WorkingCalendar

_org_tz_var: ContextVar[zoneinfo.ZoneInfo | None] = ContextVar("org_tz", default=None)


def _valid_tz(name: str | None) -> zoneinfo.ZoneInfo | None:
    if not name:
        return None
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return None


def pick_tz(cal_tz: str | None, org_tz: str | None) -> zoneinfo.ZoneInfo:
    """The canonical chain: calendar tz → organization tz → env → UTC (each fail-safe)."""
    return (
        _valid_tz(cal_tz)
        or _valid_tz(org_tz)
        or _valid_tz(get_settings().easysynq_org_timezone)
        or zoneinfo.ZoneInfo("UTC")
    )


def current_org_tz() -> zoneinfo.ZoneInfo:
    """The org tz for the current request/sweep context; env fallback when unset (safe degrade)."""
    tz = _org_tz_var.get()
    if tz is not None:
        return tz
    return _valid_tz(get_settings().easysynq_org_timezone) or zoneinfo.ZoneInfo("UTC")


def set_request_org_tz(tz: zoneinfo.ZoneInfo) -> None:
    """Set the contextvar for the rest of THIS request task. No reset: a request runs in its own
    asyncio task whose context copy is discarded at task end, so it never leaks to another request.
    Use ``using_org_tz`` (which resets) in a worker that loops over tasks/orgs."""
    _org_tz_var.set(tz)


@contextlib.contextmanager
def using_org_tz(tz: zoneinfo.ZoneInfo) -> Iterator[None]:
    """Scope ``tz`` as the org tz for the block (sweeps/workers that loop over orgs — resets after)."""
    token = _org_tz_var.set(tz)
    try:
        yield
    finally:
        _org_tz_var.reset(token)


async def resolve_org_tz(session: AsyncSession, org_id: uuid.UUID) -> zoneinfo.ZoneInfo:
    """Resolve the canonical org tz from the DB (D-1). Never raises."""
    cal_tz = (
        await session.execute(
            select(WorkingCalendar.timezone)
            .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    org_tz = (
        await session.execute(select(Organization.timezone).where(Organization.id == org_id))
    ).scalar_one_or_none()
    return pick_tz(cal_tz, org_tz)


async def resolve_default_org_tz(session: AsyncSession) -> zoneinfo.ZoneInfo:
    """The canonical tz of the single/default org (D1) — for the global review-sweep horizon."""
    org_id = (
        await session.execute(select(Organization.id).order_by(Organization.created_at).limit(1))
    ).scalar_one_or_none()
    if org_id is None:
        return _valid_tz(get_settings().easysynq_org_timezone) or zoneinfo.ZoneInfo("UTC")
    return await resolve_org_tz(session, org_id)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && uv run pytest tests/unit/test_org_clock.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/common/org_clock.py apps/api/tests/unit/test_org_clock.py
git commit -m "feat(s-orgtz-unify): org_clock — canonical org-tz resolver + request/sweep contextvar"
```

---

## Task 2: Wire `_org_tz`/`today_org` to the contextvar + refactor `resolve_working_calendar` (parity)

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/review.py:50-56`
- Modify: `apps/api/src/easysynq_api/services/notifications/escalation.py:173-220`
- Test: `apps/api/tests/integration/test_org_clock.py`

**Interfaces:**
- Consumes: `org_clock.current_org_tz`, `org_clock.pick_tz`, `org_clock.resolve_org_tz`.
- Produces: `review._org_tz()` now returns `current_org_tz()`; `resolve_working_calendar(...).tz == resolve_org_tz(...)` for any org.

- [ ] **Step 1: Write the failing integration test**

Create `apps/api/tests/integration/test_org_clock.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/integration/test_org_clock.py -v -m integration`
Expected: FAIL — `test_resolve_org_tz_parity_with_calendar` fails because today `resolve_working_calendar` derives tz from `row.timezone` directly while `resolve_org_tz` uses `pick_tz` (they match for a valid row but the test pins the contract); `test_resolve_org_tz_reads_calendar_tz` exercises the new `pick_tz` path. (If both pass pre-change because the row tz is valid, that is acceptable — they are the regression backstop; proceed to make the refactor and confirm they still pass.)

- [ ] **Step 3: Refactor `review._org_tz` to delegate**

In `apps/api/src/easysynq_api/services/vault/review.py`, replace lines 50-51:

```python
def _org_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().easysynq_org_timezone)
```

with (add the import near the other service imports, e.g. after the `from ..common.pg_locks import ...` line):

```python
from ..common.org_clock import current_org_tz


def _org_tz() -> ZoneInfo:
    return current_org_tz()
```

Leave `today_org()` (54-56) unchanged — it calls `_org_tz()`. The now-unused `get_settings` import in review.py may be removed only if nothing else uses it (grep first: `grep -n get_settings apps/api/src/easysynq_api/services/vault/review.py`); if other code uses it, keep it.

- [ ] **Step 4: Refactor `resolve_working_calendar` tz via `pick_tz`**

In `apps/api/src/easysynq_api/services/notifications/escalation.py`, add imports (with the other model/service imports):

```python
from ...db.models.organization import Organization
from ..common.org_clock import pick_tz
```

Replace the body of `resolve_working_calendar` (lines 184-220, from `row = (` through the final `return Calendar(...)`) with:

```python
    row = (
        await session.execute(
            select(WorkingCalendar)
            .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    org_tz = (
        await session.execute(select(Organization.timezone).where(Organization.id == org_id))
    ).scalar_one_or_none()
    # ONE tz decision shared with resolve_org_tz (parity by construction): cal → org → env → UTC.
    tz = pick_tz(row.timezone if row is not None else None, org_tz)
    if row is None:
        # No calendar row (a new org pre-editor): Mon-Fri default, but in the org's resolved tz
        # (NOT UTC) so the is_working_day(now) gate judges weekends in local time.
        return Calendar(
            working_weekdays=frozenset({1, 2, 3, 4, 5}), holidays=frozenset(), tz=tz
        )
    weekdays = parse_working_days(row.working_days)
    if weekdays is None:
        logger.warning("notifications.timer_bad_working_days", extra={"org_id": str(org_id)})
        weekdays = frozenset({1, 2, 3, 4, 5})
    raw_holidays = row.holidays if isinstance(row.holidays, list) else []
    holidays: set[datetime.date] = set()
    for h in raw_holidays:
        parsed = parse_holiday(h)
        if parsed is None:
            logger.warning(
                "notifications.timer_bad_holiday", extra={"org_id": str(org_id), "value": str(h)}
            )
        else:
            holidays.add(parsed)
    return Calendar(working_weekdays=weekdays, holidays=frozenset(holidays), tz=tz)
```

This removes the local `zoneinfo.ZoneInfo(row.timezone or "UTC")` try/except (the tz now comes from `pick_tz`). Keep the `zoneinfo` import (still used by `DEFAULT_CALENDAR` / other code — grep to confirm; if truly unused after the edit, the ruff `--fix` hook will flag it). `DEFAULT_CALENDAR` stays exported (used elsewhere as a constant); it is simply no longer returned by the row-None branch.

- [ ] **Step 5: Run tests + commit**

Run: `cd apps/api && uv run pytest tests/integration/test_org_clock.py tests/unit/test_org_clock.py -v -m "integration or unit"`
Expected: PASS. Also run the escalation regression: `uv run pytest tests/integration/test_notification_timer_sweep.py -v -m integration` → PASS (parity preserved for a valid calendar).

```bash
git add apps/api/src/easysynq_api/services/vault/review.py apps/api/src/easysynq_api/services/notifications/escalation.py apps/api/tests/integration/test_org_clock.py
git commit -m "feat(s-orgtz-unify): today_org reads the contextvar; resolve_working_calendar tz via pick_tz (parity)"
```

---

## Task 3: Set the contextvar at the auth boundary

**Files:**
- Modify: `apps/api/src/easysynq_api/auth/dependencies.py:87-92`
- Test: `apps/api/tests/integration/test_org_clock.py` (append)

**Interfaces:**
- Consumes: `org_clock.resolve_org_tz`, `org_clock.set_request_org_tz`.
- Produces: every authenticated request has `current_org_tz()` == the caller's org canonical tz, visible in handlers + serializers.

- [ ] **Step 1: Write the failing test (propagation confirmation)**

Append to `apps/api/tests/integration/test_org_clock.py`:

```python
async def test_review_state_uses_calendar_tz_end_to_end(
    app_under_test: object, client_factory
) -> None:
    """A non-UTC calendar tz drives review_state via the auth-boundary contextvar (propagation).

    Sets the org calendar to a far-east tz, then reads a document detail and asserts the served
    review_state matches review_state(next_review_due, today-in-cal-tz) — proving the contextvar
    set in get_current_user reaches the _document serializer.
    """
    # NOTE: implement using the repo's existing integration HTTP harness (see test_periodic_review
    # for the client + effective-document fixtures). Resolve the actual calendar tz with
    # resolve_org_tz(session, org_id), compute the expected review_state in that frame, and assert
    # the API payload's review_state equals it. Restore the calendar tz in a finally (UPDATE).
    pytest.skip("Wire to the repo HTTP harness during implementation; see test_periodic_review")
```

(The implementer replaces the `skip` with the concrete HTTP assertion using the existing client/effective-doc fixtures from `test_periodic_review.py`. The behavioural assertion: with the calendar set to a tz where "today" differs from UTC across the lead boundary, the served `review_state` matches the cal-tz computation — and would NOT match the UTC computation, making it mutation-distinguishing.)

- [ ] **Step 2: Run the test (skipped placeholder) — confirm collection**

Run: `cd apps/api && uv run pytest tests/integration/test_org_clock.py -v -m integration`
Expected: the new test is collected and SKIPPED (others PASS).

- [ ] **Step 3: Set the contextvar in `get_current_user`**

In `apps/api/src/easysynq_api/auth/dependencies.py`, add the import (top, with the other `from ..` imports):

```python
from ..services.common.org_clock import resolve_org_tz, set_request_org_tz
```

Replace `get_current_user` (lines 87-92):

```python
async def get_current_user(
    request: Request,
    jwks: JWKSCache = Depends(get_jwks_cache),
    session: AsyncSession = Depends(get_session),
) -> AppUser:
    user = await resolve_current_user(request, jwks, session)
    # S-orgtz-unify: pin the caller's canonical org tz for this request task so today_org() /
    # _document review_state / _fmt_date judge dates in the org's frame. Isolated to this request
    # (its context copy is discarded at task end) — no reset needed.
    set_request_org_tz(await resolve_org_tz(session, user.org_id))
    return user
```

- [ ] **Step 4: Implement the propagation test + run**

Replace the `pytest.skip(...)` in `test_review_state_uses_calendar_tz_end_to_end` with the concrete HTTP flow (mirror `test_periodic_review.py`'s client + effective-document creation; set `working_calendar.timezone` to e.g. `Pacific/Kiritimati` (UTC+14) via UPDATE; pick a `next_review_due` such that the cal-tz "today" vs UTC "today" land on different sides of the `due_soon`/`current` boundary; assert the served `review_state`; restore tz in `finally`).

Run: `cd apps/api && uv run pytest tests/integration/test_org_clock.py -v -m integration`
Expected: PASS — proving the dependency-set contextvar propagates to the serializer.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/auth/dependencies.py apps/api/tests/integration/test_org_clock.py
git commit -m "feat(s-orgtz-unify): set the canonical org-tz contextvar at the auth boundary"
```

---

## Task 4: `compute_next_review_due` takes explicit `org_tz`

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/review.py:59-74,369`
- Modify: `apps/api/src/easysynq_api/api/documents.py:895`
- Modify: `apps/api/src/easysynq_api/services/vault/lifecycle.py:542`
- Test: `apps/api/tests/unit/test_review_domain.py`

**Interfaces:**
- Produces: `compute_next_review_due(review_period_months, last_reviewed_at, effective_from, org_tz)` — new required final param.
- Consumes (callers): `org_clock.current_org_tz()`.

- [ ] **Step 1: Update the failing unit test**

In `apps/api/tests/unit/test_review_domain.py`, find the test that monkeypatches `review_mod._org_tz` (around line 76-81) and the `compute_next_review_due` calls. Replace the monkeypatch pattern with an explicit `org_tz` argument. Example (the existing Auckland test):

```python
def test_compute_next_review_due_dates_in_org_tz():
    from zoneinfo import ZoneInfo

    from easysynq_api.services.vault import review as review_mod

    # An effective_from at 2026-01-31 11:00 UTC is 2026-02-01 00:00 in Auckland (UTC+13) —
    # add_months from Feb 1 (not Jan 31) proves the date is taken in the passed tz.
    import datetime

    eff = datetime.datetime(2026, 1, 31, 11, 0, tzinfo=datetime.UTC)
    out = review_mod.compute_next_review_due(12, None, eff, ZoneInfo("Pacific/Auckland"))
    assert out == datetime.date(2027, 2, 1)
    out_utc = review_mod.compute_next_review_due(12, None, eff, ZoneInfo("UTC"))
    assert out_utc == datetime.date(2027, 1, 31)
```

Update every other `compute_next_review_due(...)` call in this file to pass an explicit `org_tz` (e.g. `ZoneInfo("UTC")` where the date is tz-insensitive).

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && uv run pytest tests/unit/test_review_domain.py -v`
Expected: FAIL — `compute_next_review_due() takes 3 positional arguments but 4 were given`.

- [ ] **Step 3: Add the `org_tz` parameter + update callers**

In `review.py`, change `compute_next_review_due` (59-74):

```python
def compute_next_review_due(
    review_period_months: int | None,
    last_reviewed_at: datetime.datetime | None,
    effective_from: datetime.datetime | None,
    org_tz: ZoneInfo,
) -> datetime.date | None:
    """anchor = the LATER of (last_reviewed_at, effective_from); + period months, dated in
    ``org_tz`` (the resolved canonical org tz — S-orgtz-unify R56). [rest of docstring unchanged]"""
    if review_period_months is None:
        return None
    anchors = [a for a in (last_reviewed_at, effective_from) if a is not None]
    if not anchors:
        return None
    return add_months(max(anchors).astimezone(org_tz).date(), review_period_months)
```

In `review.py:369` (decide path) — add `current_org_tz()`:

```python
        doc.next_review_due = compute_next_review_due(
            doc.review_period_months, now, version.effective_from, current_org_tz()
        )
```

In `api/documents.py:895` — add `current_org_tz()` (add `from ..services.common.org_clock import current_org_tz` to the imports near line 96):

```python
        doc.next_review_due = compute_next_review_due(
            doc.review_period_months,
            doc.last_reviewed_at,
            <existing effective_from arg>,
            current_org_tz(),
        )
```

(Read lines 890-900 first to preserve the exact existing arguments; only append `current_org_tz()`.)

In `services/vault/lifecycle.py:542` — add `current_org_tz()` (add `from ..common.org_clock import current_org_tz` near line 45):

```python
    doc.next_review_due = compute_next_review_due(
        <existing args>, current_org_tz()
    )
```

(Read lines 538-548 first; append `current_org_tz()` as the final argument.)

- [ ] **Step 4: Run the test + targeted callers**

Run: `cd apps/api && uv run pytest tests/unit/test_review_domain.py -v`
Expected: PASS.
Run mypy on the changed modules: `cd apps/api && uv run mypy --strict src/easysynq_api/services/vault/review.py src/easysynq_api/api/documents.py src/easysynq_api/services/vault/lifecycle.py`
Expected: no errors (all callers now pass 4 args).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/review.py apps/api/src/easysynq_api/api/documents.py apps/api/src/easysynq_api/services/vault/lifecycle.py apps/api/tests/unit/test_review_domain.py
git commit -m "feat(s-orgtz-unify): compute_next_review_due takes explicit org_tz; callers pass current_org_tz()"
```

---

## Task 5: Thread `org_tz` through `read_cadence` + explicit `today` in both sweeps

**Files:**
- Modify: `apps/api/src/easysynq_api/services/mgmt_review/cadence.py:88-103,128-157,165-301`
- Modify: `apps/api/src/easysynq_api/api/mgmt_review.py` (the `read_cadence` caller near line 361)
- Modify: `apps/api/src/easysynq_api/services/vault/review.py:125`
- Test: `apps/api/tests/unit/test_mgmt_review_cadence.py`

**Interfaces:**
- Produces: `read_cadence(session, org_id, org_tz)` and `_last_released_effective_from(session, org_id, org_tz)` — new required `org_tz`.
- Consumes: `org_clock.resolve_org_tz`, `org_clock.resolve_default_org_tz`.

- [ ] **Step 1: Update the failing unit test**

`test_mgmt_review_cadence.py` unit-tests `next_mr_due`/`mr_review_state` (pure, tz-free — leave those). If it tests `read_cadence`/`_last_released_effective_from` (DB), those are integration. Add/adjust the pure tests only if signatures referenced. If no unit test references `read_cadence`, this step is a no-op for unit; the real coverage lands in Task 9 integration. Add one explicit-tz pure assertion to lock intent if absent:

```python
def test_next_mr_due_is_pure_month_add():
    import datetime

    from easysynq_api.services.mgmt_review.cadence import next_mr_due

    assert next_mr_due(datetime.date(2026, 1, 31), 12) == datetime.date(2027, 1, 31)
    assert next_mr_due(None, 12) is None
```

- [ ] **Step 2: Run it**

Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_cadence.py -v`
Expected: PASS (pure helpers unchanged) — this confirms the baseline before the signature change.

- [ ] **Step 3: Thread `org_tz` + explicit `today`**

In `cadence.py`, change the imports near line 52 from:

```python
from ..vault.review import _org_tz, add_months, today_org
```

to:

```python
from ..common.org_clock import resolve_org_tz
from ..vault.review import add_months
```

Change `_last_released_effective_from` (128-157) signature + the final line:

```python
async def _last_released_effective_from(
    session: AsyncSession, org_id: uuid.UUID, org_tz: ZoneInfo
) -> datetime.date | None:
    ...  # body unchanged through the query
    if row is None:
        return None
    return row.astimezone(org_tz).date()
```

Add `from zoneinfo import ZoneInfo` to cadence.py imports.

Change `read_cadence` (88-103) signature + the `_last_released_effective_from` call:

```python
async def read_cadence(
    session: AsyncSession, org_id: uuid.UUID, org_tz: ZoneInfo
) -> CadenceStatus | None:
    ...
    anchor = await _last_released_effective_from(session, org_id, org_tz)
    ...
```

In `sweep_mgmt_reviews` (165-301): after `org_id = await _resolve_org_id(session)` and its None-guard (line 174-177), resolve the tz and use it explicitly. Replace the `cad = await read_cadence(session, org_id)` (line 179) with:

```python
        org_tz = await resolve_org_tz(session, org_id)
        cad = await read_cadence(session, org_id, org_tz)
```

Replace `today = today_org()` (line 216) with:

```python
        today = datetime.datetime.now(org_tz).date()
```

In `api/mgmt_review.py` (the `read_cadence` call near 361): add `from ..services.common.org_clock import resolve_org_tz` and resolve+pass the tz, e.g.:

```python
    org_tz = await resolve_org_tz(session, caller.org_id)  # or the endpoint's org_id source
    cad = await read_cadence(session, <org_id>, org_tz)
    ...
    "review_state": mr_review_state(cad.next_review_due, today_org()),
```

(`today_org()` here is fine — this is a request handler, so the auth boundary already set the contextvar. Read the surrounding handler to use its existing `org_id`/`caller` variable.)

In `review.py` `sweep_reviews` (line 125): replace `today = today_org()` with an explicit resolve (add `from ..common.org_clock import resolve_default_org_tz` to review.py imports):

```python
        today = datetime.datetime.now(await resolve_default_org_tz(session)).date()
```

- [ ] **Step 4: Run mypy + targeted integration**

Run: `cd apps/api && uv run mypy --strict src/easysynq_api/services/mgmt_review/cadence.py src/easysynq_api/api/mgmt_review.py src/easysynq_api/services/vault/review.py`
Expected: no errors.
Run: `cd apps/api && uv run pytest tests/unit/test_mgmt_review_cadence.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/mgmt_review/cadence.py apps/api/src/easysynq_api/api/mgmt_review.py apps/api/src/easysynq_api/services/vault/review.py apps/api/tests/unit/test_mgmt_review_cadence.py
git commit -m "feat(s-orgtz-unify): read_cadence/_last_released_effective_from take org_tz; sweeps compute today explicitly"
```

---

## Task 6: `_fmt_date` render hardening + escalation-sweep render wrap

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/render.py:36-41`
- Modify: `apps/api/src/easysynq_api/services/notifications/escalation.py` (`process_task_timers`, around 272-end)
- Test: `apps/api/tests/unit/test_notification_render.py` (or `test_render.py`)

**Interfaces:**
- Consumes: `org_clock.current_org_tz`, `org_clock.using_org_tz`.

- [ ] **Step 1: Write the failing unit test (mutation-distinguishing)**

Add to `apps/api/tests/unit/test_notification_render.py`:

```python
import datetime
import zoneinfo

from easysynq_api.services.common.org_clock import using_org_tz
from easysynq_api.services.notifications.render import _fmt_date


def test_fmt_date_reconverts_aware_datetime_to_org_tz():
    # 2026-06-29 00:00 in Asia/Tokyo (UTC+9) is 2026-06-28 15:00 UTC. Rendering the UTC instant
    # under the org tz must show 2026-06-29 (the local date), NOT 2026-06-28 (the UTC date).
    utc_instant = datetime.datetime(2026, 6, 28, 15, 0, tzinfo=datetime.UTC)
    with using_org_tz(zoneinfo.ZoneInfo("Asia/Tokyo")):
        assert _fmt_date(utc_instant) == "2026-06-29"
    # Unset context (UTC fallback) → the UTC date.
    assert _fmt_date(utc_instant) == "2026-06-28"


def test_fmt_date_passes_naive_and_date_through():
    assert _fmt_date(datetime.datetime(2026, 6, 28, 15, 0)) == "2026-06-28"  # naive: no convert
    assert _fmt_date(datetime.date(2026, 6, 28)) == "2026-06-28"
```

- [ ] **Step 2: Run it**

Run: `cd apps/api && uv run pytest tests/unit/test_notification_render.py -k fmt_date -v`
Expected: FAIL — `_fmt_date` currently returns `2026-06-28` for the Tokyo case.

- [ ] **Step 3: Harden `_fmt_date`**

In `render.py`, add the import (top):

```python
from ..common.org_clock import current_org_tz
```

Replace `_fmt_date` (36-41):

```python
def _fmt_date(value: object) -> str:
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            # Re-convert an aware instant to the org's tz before dating it (S-orgtz-unify): a
            # due_at read back from PG is UTC-aware, so .date() would show the UTC date — off by a
            # day for an east-of-UTC org. current_org_tz() is set at the auth boundary (request
            # renders) and in process_task_timers (sweep renders).
            return value.astimezone(current_org_tz()).date().isoformat()
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return _PLACEHOLDER
```

- [ ] **Step 4: Wrap the escalation render in `using_org_tz`**

In `escalation.py` `process_task_timers`, add `from ..common.org_clock import using_org_tz` to imports. After `calendar = await resolve_working_calendar(session, task.org_id)` (line 272) the function builds `tpolicy`/`stamps` then loops `for step in due_steps(...)`. Wrap the step-firing loop (the `fired = 0` line through the end of the `for step in due_steps(...)` loop, i.e. lines ~286 to the loop's close) in:

```python
    with using_org_tz(calendar.tz):
        fired = 0
        for step in due_steps(tpolicy, task.due_at, stamps, now, calendar):
            ...  # existing loop body, indented one level
```

Indent the existing loop body one level under the `with`. (The render — `emit_task_event` → `render` → `_fmt_date` — runs inside this loop, so `current_org_tz()` returns `calendar.tz` there. `calendar.tz` equals `resolve_org_tz` by construction, so this is the canonical tz.) Anything after the loop (the audit write / `return fired`) can stay outside the `with`.

- [ ] **Step 5: Verify the digest render path + run + commit**

Check whether the digest sweep renders `task.due_at`:
Run: `grep -n "due_at\|_fmt_date\|render(" apps/api/src/easysynq_api/services/notifications/digest.py`
- If `digest.py` calls `render(...)` on templates containing `{{ ... | date }}` for a `due_at`, wrap its per-user/per-org render section in `using_org_tz(<resolved org tz>)` the same way (resolve via `resolve_org_tz(session, <org_id>)`). If it only lists pre-rendered titles (no date filter), no change is needed — note this in the commit body.

Run: `cd apps/api && uv run pytest tests/unit/test_notification_render.py -v` → PASS.
Run: `cd apps/api && uv run pytest tests/integration/test_notification_timer_sweep.py -v -m integration` → PASS (render still works in the sweep).

```bash
git add apps/api/src/easysynq_api/services/notifications/render.py apps/api/src/easysynq_api/services/notifications/escalation.py apps/api/tests/unit/test_notification_render.py
git commit -m "feat(s-orgtz-unify): _fmt_date re-converts aware datetimes to the org tz; wrap the escalation render"
```

---

## Task 7: OVERDUE `now_is_working` gate

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/timer.py:194` (+ docstring 168-175)
- Test: `apps/api/tests/unit/test_notification_timer.py`

**Interfaces:**
- Behaviour change: `due_steps` yields `OVERDUE` only when `now` (in `calendar.tz`) is a working day.

- [ ] **Step 1: Write the failing unit test**

Add to `apps/api/tests/unit/test_notification_timer.py` (mirror the file's existing `Calendar`/`TimerPolicy`/`TimerStamps` construction — read the top of the file for the helpers and a Mon-Fri calendar):

```python
def test_overdue_suppressed_on_non_working_day():
    import datetime
    import zoneinfo

    from easysynq_api.services.notifications.timer import (
        Calendar,
        TimerPolicy,
        TimerStamps,
        TimerStep,
        due_steps,
    )

    monfri = Calendar(
        working_weekdays=frozenset({1, 2, 3, 4, 5}),
        holidays=frozenset(),
        tz=zoneinfo.ZoneInfo("UTC"),
    )
    policy = TimerPolicy(remind_1_before=None, remind_2_before=None, escalate_1_after=None)
    stamps = TimerStamps(
        remind_1_sent_at=None,
        remind_2_sent_at=None,
        overdue_notified_at=None,
        escalated_1_at=None,
    )
    due = datetime.datetime(2026, 6, 26, 9, 0, tzinfo=datetime.UTC)  # Friday
    # Saturday now → past due_at, but a non-working day → OVERDUE suppressed.
    sat = datetime.datetime(2026, 6, 27, 9, 0, tzinfo=datetime.UTC)
    assert TimerStep.OVERDUE not in due_steps(policy, due, stamps, sat, monfri)
    # Monday now → working day → OVERDUE fires.
    mon = datetime.datetime(2026, 6, 29, 9, 0, tzinfo=datetime.UTC)
    assert TimerStep.OVERDUE in due_steps(policy, due, stamps, mon, monfri)
```

- [ ] **Step 2: Run it**

Run: `cd apps/api && uv run pytest tests/unit/test_notification_timer.py::test_overdue_suppressed_on_non_working_day -v`
Expected: FAIL — OVERDUE currently fires on Saturday (no gate).

- [ ] **Step 3: Add the gate**

In `timer.py`, change line 194 from:

```python
    if stamps.overdue_notified_at is None and now >= due_at:
```

to:

```python
    if stamps.overdue_notified_at is None and now_is_working and now >= due_at:
```

Update the docstring (168-175) — replace the sentence "OVERDUE is exempt from that gate (it is always-on at ``due_at`` by design)." with: "OVERDUE is ALSO gated on ``now_is_working`` (S-orgtz-unify, closing R55 D-5's weekend-pierce exemption) — a weekend/holiday overdue notice defers to the next working day (doc 10 §9.5)."

- [ ] **Step 4: Run the new test + the existing timer suite**

Run: `cd apps/api && uv run pytest tests/unit/test_notification_timer.py tests/unit/test_duedate_snap.py -v`
Expected: PASS. (Existing OVERDUE tests that use an all-days calendar still pass — every day is working. If any existing OVERDUE test uses a Mon-Fri calendar with a weekend `now`, it asserted the OLD behaviour and must be updated to the new gate; read the failures and adjust the assertion to match doc 10 §9.5.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/timer.py apps/api/tests/unit/test_notification_timer.py
git commit -m "feat(s-orgtz-unify): gate OVERDUE on now_is_working (closes R55 D-5 weekend-pierce)"
```

---

## Task 8: Backfill CLI for stored `next_review_due`

**Files:**
- Create: `apps/api/src/easysynq_api/cli/backfill_review_dates.py`
- Test: `apps/api/tests/integration/test_backfill_review_dates.py`

**Interfaces:**
- Produces: `async backfill(session, *, dry_run: bool) -> list[tuple[uuid.UUID, datetime.date | None, datetime.date | None]]` (rows that changed: id, old, new); `main(argv)` CLI entry.
- Consumes: `org_clock.resolve_org_tz`, `review.compute_next_review_due`.

- [ ] **Step 1: Write the failing integration test**

Create `apps/api/tests/integration/test_backfill_review_dates.py`:

```python
import datetime
import uuid

import pytest
from sqlalchemy import select, update

from easysynq_api.cli.backfill_review_dates import backfill
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.organization import Organization
from easysynq_api.db.models.working_calendar import WorkingCalendar
from easysynq_api.db.session import get_sessionmaker

pytestmark = pytest.mark.integration


async def test_backfill_recomputes_changed_only_and_is_idempotent(app_under_test: object) -> None:
    async with get_sessionmaker()() as session:
        org_id = (
            await session.execute(
                select(Organization.id).order_by(Organization.created_at).limit(1)
            )
        ).scalar_one()
        # Pick any Effective doc with a review period + effective version; if none exists in the
        # shared DB, create one via the test harness used by test_periodic_review. For the assertion
        # we only need: a documented_information row with review_period_months set and a non-null
        # next_review_due deliberately stored as a WRONG value, then assert backfill fixes it.
        doc = (
            await session.execute(
                select(DocumentedInformation)
                .where(
                    DocumentedInformation.org_id == org_id,
                    DocumentedInformation.review_period_months.is_not(None),
                    DocumentedInformation.next_review_due.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if doc is None:
            pytest.skip("No periodic-review doc in the shared DB; create via the review harness")
        wrong = doc.next_review_due + datetime.timedelta(days=400)
        await session.execute(
            update(DocumentedInformation)
            .where(DocumentedInformation.id == doc.id)
            .values(next_review_due=wrong)
        )
        await session.commit()

        changed = await backfill(session, dry_run=False)
        assert any(c[0] == doc.id for c in changed)
        refreshed = await session.get(DocumentedInformation, doc.id)
        assert refreshed.next_review_due != wrong  # recomputed to the canonical-tz value

        # Idempotent: a second run reports this doc unchanged.
        changed2 = await backfill(session, dry_run=False)
        assert all(c[0] != doc.id for c in changed2)
```

- [ ] **Step 2: Run it**

Run: `cd apps/api && uv run pytest tests/integration/test_backfill_review_dates.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: ...cli.backfill_review_dates`.

- [ ] **Step 3: Write the CLI**

Create `apps/api/src/easysynq_api/cli/backfill_review_dates.py`:

```python
"""Backfill stored ``documented_information.next_review_due`` into the canonical org tz
(S-orgtz-unify, R56). One-time + idempotent; recompute needs Python (add_months + per-org tz), so a
CLI (not raw SQL in a migration). Runs inside the api image (DB reachable):

    python -m easysynq_api.cli.backfill_review_dates --dry-run
    python -m easysynq_api.cli.backfill_review_dates

Only ``next_review_due`` is stored; the MR cadence next-due is derived (no backfill). MGMT-review
documents have no review_period_months, so they are naturally skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models.documented_information import DocumentedInformation
from ..db.models.organization import Organization
from ..services.common.org_clock import resolve_org_tz
from ..services.vault.review import compute_next_review_due


async def backfill(
    session: AsyncSession, *, dry_run: bool
) -> list[tuple[uuid.UUID, datetime.date | None, datetime.date | None]]:
    """Recompute next_review_due per doc in its org's canonical tz; return the rows that change.
    Commits when not dry_run."""
    changed: list[tuple[uuid.UUID, datetime.date | None, datetime.date | None]] = []
    org_ids = (await session.execute(select(Organization.id))).scalars().all()
    for org_id in org_ids:
        org_tz = await resolve_org_tz(session, org_id)
        docs = (
            await session.execute(
                select(DocumentedInformation).where(
                    DocumentedInformation.org_id == org_id,
                    DocumentedInformation.review_period_months.is_not(None),
                    DocumentedInformation.next_review_due.is_not(None),
                )
            )
        ).scalars().all()
        for doc in docs:
            # Anchor identically to compute_next_review_due's release/confirm rule: the LATER of
            # last_reviewed_at / the governing effective version's effective_from. We use
            # last_reviewed_at and the stored anchor inputs already on the row; effective_from is
            # read from the current effective version when present.
            effective_from = None
            if doc.current_effective_version_id is not None:
                from ..db.models.document_version import DocumentVersion

                effective_from = (
                    await session.execute(
                        select(DocumentVersion.effective_from).where(
                            DocumentVersion.id == doc.current_effective_version_id
                        )
                    )
                ).scalar_one_or_none()
            new = compute_next_review_due(
                doc.review_period_months, doc.last_reviewed_at, effective_from, org_tz
            )
            if new != doc.next_review_due:
                changed.append((doc.id, doc.next_review_due, new))
                if not dry_run:
                    doc.next_review_due = new
    if not dry_run:
        await session.commit()
    return changed


async def _run(dry_run: bool) -> list[tuple[uuid.UUID, datetime.date | None, datetime.date | None]]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            return await backfill(session, dry_run=dry_run)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="easysynq-backfill-review-dates",
        description="Recompute stored next_review_due into the canonical org tz (S-orgtz-unify).",
    )
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args(argv)
    changed = asyncio.run(_run(args.dry_run))
    verb = "would change" if args.dry_run else "changed"
    print(f"{verb}: {len(changed)} document(s)")
    for doc_id, old, new in changed:
        print(f"  {doc_id}: {old} -> {new}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

(Verify `DocumentedInformation` has `current_effective_version_id`, `last_reviewed_at`, `review_period_months`, `next_review_due`, `org_id` — `grep -n "Mapped" apps/api/src/easysynq_api/db/models/documented_information.py`. Adjust the effective_from source to match exactly how `compute_next_review_due` is fed at release/confirm.)

- [ ] **Step 4: Run the test + commit**

Run: `cd apps/api && uv run pytest tests/integration/test_backfill_review_dates.py -v -m integration`
Expected: PASS.

```bash
git add apps/api/src/easysynq_api/cli/backfill_review_dates.py apps/api/tests/integration/test_backfill_review_dates.py
git commit -m "feat(s-orgtz-unify): backfill CLI — recompute next_review_due into the canonical org tz"
```

---

## Task 9: Divergent-tz integration hardening

**Files:**
- Modify: `apps/api/tests/integration/test_periodic_review.py` (the `_org_tz()` expectation sites)
- Modify: `apps/api/tests/integration/test_mgmt_review_cadence.py`

**Interfaces:** none (test-only). Goal: expected dates computed in the **resolved canonical tz**, not env `_org_tz()`.

- [ ] **Step 1: Add a divergent-tz resolver helper to the tests**

In each test file, add a module-level async helper (or reuse `org_clock.resolve_org_tz`):

```python
from easysynq_api.services.common.org_clock import resolve_org_tz
```

- [ ] **Step 2: Replace env `_org_tz()` expectations with the canonical tz**

Wherever the test computes an expected date via `datetime.now(_org_tz()).date()` or `dt.astimezone(_org_tz()).date()` (e.g. `test_periodic_review.py` lines ~106-849), replace `_org_tz()` with the resolved org tz:

```python
# OLD: expected = eff_from_dt.astimezone(_org_tz()).date()
org_tz = await resolve_org_tz(session, org_id)
expected = eff_from_dt.astimezone(org_tz).date()
```

Use the `session`/`org_id` already in scope (or resolve `org_id` via the default-org select). Remove the now-unused `_org_tz` import if nothing else uses it.

- [ ] **Step 3: Run both suites**

Run: `cd apps/api && uv run pytest tests/integration/test_periodic_review.py tests/integration/test_mgmt_review_cadence.py -v -m integration`
Expected: PASS. (In CI the org tz is UTC == env UTC, so behaviour is unchanged; the rewrite makes the assertions correct under divergence and mutation-distinguishing.)

- [ ] **Step 4: Mutation-verify the unification**

Temporarily edit `org_clock.resolve_org_tz` to `return zoneinfo.ZoneInfo("UTC")` and add a focused divergent test (set the org calendar to `Pacific/Kiritimati`, assert a `next_review_due` that differs from the UTC computation). Confirm it FAILS with the mutation, then revert the mutation. (Document the result in the commit body; do not commit the mutation.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/integration/test_periodic_review.py apps/api/tests/integration/test_mgmt_review_cadence.py
git commit -m "test(s-orgtz-unify): divergent-tz hardening — expectations in the resolved canonical tz"
```

---

## Task 10: Docs + register + finish-slice

**Files:**
- Modify: `docs/decisions-register.md` (new R56 + amend-notes on R8 and R55)
- Modify: `CLAUDE.md` (Recent learnings) + `docs/slice-history.md` + Current-status pointer

- [ ] **Step 1: Add R56 to the decisions register**

Append to `docs/decisions-register.md` (after R55), the binding decision:

```markdown
### R56 — Org-timezone unification (single canonical tz; slice S-orgtz-unify) — 2026-06-26

The org's display/derivation timezone is resolved from the DB, not the env: the chain is
**is_default `working_calendar.timezone` → `organization.timezone` → env `easysynq_org_timezone`
→ UTC** (`services/common/org_clock.py::resolve_org_tz`/`pick_tz`, fail-safe). `resolve_working_calendar`
sources its tz from the same `pick_tz`, so the timer/calendar frame and the review/date frame cannot
diverge (parity by construction). `today_org()` reads a request/sweep-scoped contextvar (set at the
auth boundary and around the escalation render); compute functions (`compute_next_review_due`,
`read_cadence`) take an explicit `org_tz`. **R8 cutover is unchanged** — only date-level
display/derivation moves to the canonical tz; effective-date cutover stays UTC-clock-authoritative.
The env var is retained as the bottom fallback; `organization.timezone` and `working_calendar.timezone`
remain independently editable (cal-canonical-with-fallback — no S-notify-7 regression).
```

- [ ] **Step 2: Add the amend-notes**

In R8's section, append: "**Amended by R56 (2026-06-26):** the org tz for date display/derivation is the resolved canonical tz (calendar-first); effective-date cutover is unchanged (UTC-clock-authoritative)."

In R55's D-5 paragraph, append: "**Amended by R56 (2026-06-26):** OVERDUE is now `now_is_working`-gated (the weekend-pierce exemption is closed); a weekend/holiday overdue notice defers to the next working day (doc 10 §9.5)."

- [ ] **Step 3: Run the full local gate**

```bash
cd apps/api && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy --strict src
```
Expected: clean. Then run `/check-migrations` (confirm `alembic check` clean, head 0067 — no migration added) and `/check-web` (confirm still green — no FE change) and `/check-contracts`.

- [ ] **Step 4: Run `diff-critic` on the branch diff**

Dispatch the `diff-critic` agent (Agent tool, `subagent_type: diff-critic`) on the `feat/s-orgtz-unify` diff. Fold only confirmed findings.

- [ ] **Step 5: Commit docs + invoke `/finish-slice`**

```bash
git add docs/decisions-register.md
git commit -m "docs(s-orgtz-unify): R56 org-tz unification + R8/R55 amend-notes"
```

Then invoke the `/finish-slice` skill to write the CLAUDE.md learning, the `docs/slice-history.md` narrative, the Current-status pointer, the memory resume note, and the test deltas in one pass. (CLAUDE.md head stays `0067`; record the api unit/integration deltas + web unchanged.)

---

## Self-Review

**Spec coverage:**
- §3.1 resolver + parity → Tasks 1, 2. ✓
- §3.2 contextvar `today_org` + auth boundary → Tasks 2, 3. ✓
- §3.3 explicit compute `org_tz` → Tasks 4, 5. ✓
- §3.4 `_fmt_date` hardening → Task 6. ✓
- §3.5 OVERDUE gate → Task 7. ✓
- §3.6 backfill CLI (next_review_due only; MR derived) → Task 8. ✓
- §5 testing (unit + divergent-tz integration + mutation-verify) → Tasks 1-9 (esp. 9). ✓
- §6 docs/register (R56 + amends) → Task 10. ✓
- §4 out-of-scope (no migration / no key / no FE / env kept / cols separate) → Global Constraints + enforced by not touching those files. ✓

**Placeholder scan:** The only deliberate placeholder is Task 3 Step 1's `pytest.skip` (the propagation test wired to the repo HTTP harness in Step 4) — flagged, not silent. No "TBD"/"add validation"/"similar to Task N".

**Type consistency:**
- `compute_next_review_due(period, last_reviewed_at, effective_from, org_tz)` — Task 4 defines, Tasks 4/8 call with 4 args. ✓
- `read_cadence(session, org_id, org_tz)` / `_last_released_effective_from(session, org_id, org_tz)` — Task 5 defines + all callers updated (sweep, api). ✓
- `current_org_tz()/resolve_org_tz()/resolve_default_org_tz()/using_org_tz()/set_request_org_tz()/pick_tz()` — Task 1 signatures match every consumer (Tasks 2-9). ✓
- OVERDUE gate uses the existing `now_is_working` local in `due_steps` — Task 7. ✓
