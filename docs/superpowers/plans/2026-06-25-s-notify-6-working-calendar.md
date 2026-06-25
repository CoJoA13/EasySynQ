# S-notify-6 — Business-day escalation SLAs (working_calendar) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire business-day reminder/escalation SLAs into the notification `timer_sweep` by adding a per-org `working_calendar` entity and computing the reminder (before-due) and escalation (after-due) offsets against it (skip weekends + holidays), closing R29's business-day half. OVERDUE stays raw at `due_at` (D-5).

**Architecture:** A pure, session-free `Calendar` value object + business-day helpers live in `services/notifications/timer.py` (keeping `due_steps` unit-testable). `escalation.py` resolves the task org's `is_default` `working_calendar` row into a `Calendar` (fail-safe to a Mon–Fri/UTC default) once per locked per-task txn and threads it into `due_steps`. Migration `0067` adds the table + a per-org default seed. No endpoint / no `apps/web` (BE-only; the admin editor is deferred).

**Tech Stack:** Python 3.12, SQLAlchemy 2.x (async) + Alembic, PostgreSQL 16 (JSONB), `zoneinfo` (stdlib), pytest (unit + testcontainers integration). Spec: `docs/superpowers/specs/2026-06-25-s-notify-6-working-calendar-design.md`.

## Global Constraints

- **N9 (locked):** the sweep only reminds/notifies/escalates — NO auto-decide, auto-reassign, or task-state flip. This slice only changes *when* existing notifications fire.
- **R38 additive-only:** NO new permission key. Catalog stays **102** — do NOT touch the `== 102` assertions in `test_authz.py` / `test_quality_objectives.py`.
- **R53 / R32:** unchanged — a failed email never gates a transition; the SAVEPOINT enqueue + drain are untouched.
- **OVERDUE stays raw** (`now >= due_at`, no business-day shift) — D-5; the weekend-overdue root cause is the upstream R39 `due_at`-snap, a named residual.
- **Migration head:** `0066` → `0067` (revises `0066_awareness_events`). `alembic check` must be clean; up↔down round-trips on a throwaway PG16 (CI) + a manual 0066↔0067 on the populated dev DB (live-smoke).
- **App-role grants:** `working_calendar` keeps INSERT/SELECT/UPDATE, REVOKE DELETE (operational config). `sla_policy` is unchanged (SELECT-only).
- **`working_days`** = JSON array of ISO weekday ints (1=Mon..7=Sun); seed `[1,2,3,4,5]`. **`holidays`** = JSON array of `"YYYY-MM-DD"`; seed `[]`. **`timezone`** = IANA TEXT, seed = that org's `organization.timezone`.
- Run `cd ~/dev/EasySynQ` first (NOT the `~/Desktop/...` path with spaces). API checks: `cd apps/api && uv run pytest ...`. In-session Docker needs `sg docker -c '…'`.

---

### Task 1: Pure `Calendar` value object + business-day helpers

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/timer.py`
- Test: `apps/api/tests/unit/test_notification_timer.py`

**Interfaces:**
- Produces: `Calendar(working_weekdays: frozenset[int], holidays: frozenset[datetime.date], tz: zoneinfo.ZoneInfo)`; `ThresholdDirection.BEFORE|AFTER`; `DEFAULT_CALENDAR`; `is_working_day(d, cal) -> bool`; `shift_business_days(anchor, n, direction, cal) -> date`; `business_threshold(due_at, offset, direction, cal) -> datetime` (UTC-aware).

- [ ] **Step 1: Write the failing unit tests** — append to `apps/api/tests/unit/test_notification_timer.py` (add imports at the top: `import zoneinfo` and extend the existing `from …timer import (...)` to also import `Calendar, DEFAULT_CALENDAR, ThresholdDirection, business_threshold, is_working_day, shift_business_days`):

```python
import zoneinfo  # add near the top imports

MON_FRI = Calendar(
    working_weekdays=frozenset({1, 2, 3, 4, 5}), holidays=frozenset(), tz=zoneinfo.ZoneInfo("UTC")
)
ALL_DAYS = Calendar(
    working_weekdays=frozenset({1, 2, 3, 4, 5, 6, 7}), holidays=frozenset(), tz=zoneinfo.ZoneInfo("UTC")
)
_D = datetime.date  # alias for brevity in this block


def test_is_working_day_weekday_weekend_holiday():
    cal = Calendar(frozenset({1, 2, 3, 4, 5}), frozenset({_D(2026, 6, 24)}), zoneinfo.ZoneInfo("UTC"))
    assert is_working_day(_D(2026, 6, 23), cal) is True  # Tuesday
    assert is_working_day(_D(2026, 6, 27), cal) is False  # Saturday
    assert is_working_day(_D(2026, 6, 24), cal) is False  # holiday (a Wednesday)


def test_shift_business_days_before_skips_weekend():
    # 3 business days BEFORE Monday 2026-06-29 -> Wednesday 2026-06-24 (skip Sat/Sun).
    assert shift_business_days(_D(2026, 6, 29), 3, ThresholdDirection.BEFORE, MON_FRI) == _D(2026, 6, 24)


def test_shift_business_days_after_skips_weekend():
    # 1 business day AFTER Friday 2026-06-26 -> Monday 2026-06-29.
    assert shift_business_days(_D(2026, 6, 26), 1, ThresholdDirection.AFTER, MON_FRI) == _D(2026, 6, 29)


def test_shift_business_days_skips_holiday():
    cal = Calendar(frozenset({1, 2, 3, 4, 5}), frozenset({_D(2026, 6, 24)}), zoneinfo.ZoneInfo("UTC"))
    # 3 biz days before Mon 06-29 with Wed 06-24 a holiday -> Tue 06-23 (skip Sat,Sun,Wed-holiday).
    assert shift_business_days(_D(2026, 6, 29), 3, ThresholdDirection.BEFORE, cal) == _D(2026, 6, 23)


def test_shift_business_days_zero_is_identity():
    assert shift_business_days(_D(2026, 6, 29), 0, ThresholdDirection.BEFORE, MON_FRI) == _D(2026, 6, 29)


def test_business_threshold_before_preserves_time_of_day():
    due = datetime.datetime(2026, 6, 29, 14, 37, tzinfo=UTC)  # Monday
    got = business_threshold(due, timedelta(days=3), ThresholdDirection.BEFORE, MON_FRI)
    assert got == datetime.datetime(2026, 6, 24, 14, 37, tzinfo=UTC)  # prior Wednesday, same time


def test_business_threshold_after_skips_weekend():
    due = datetime.datetime(2026, 6, 26, 12, 0, tzinfo=UTC)  # Friday
    got = business_threshold(due, timedelta(days=1), ThresholdDirection.AFTER, MON_FRI)
    assert got == datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)  # Monday


def test_business_threshold_tz_changes_local_date():
    # 02:00 UTC on Mon 06-29 is 22:00 EDT on SUNDAY 06-28 -> 3 biz days before Sunday -> Wed 06-24.
    cal = Calendar(frozenset({1, 2, 3, 4, 5}), frozenset(), zoneinfo.ZoneInfo("America/New_York"))
    due = datetime.datetime(2026, 6, 29, 2, 0, tzinfo=UTC)
    got = business_threshold(due, timedelta(days=3), ThresholdDirection.BEFORE, cal)
    # 06-24 22:00 EDT == 06-25 02:00 UTC.
    assert got == datetime.datetime(2026, 6, 25, 2, 0, tzinfo=UTC)


def test_business_threshold_dst_transition_does_not_crash():
    # US spring-forward 2026-03-08; just assert it returns a sane UTC instant within a day.
    cal = Calendar(frozenset({1, 2, 3, 4, 5}), frozenset(), zoneinfo.ZoneInfo("America/New_York"))
    due = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=UTC)  # Tuesday after the transition
    got = business_threshold(due, timedelta(days=1), ThresholdDirection.AFTER, cal)
    assert got.tzinfo == UTC and abs((got - due).total_seconds()) < 36 * 3600


def test_all_days_calendar_degenerates_to_raw():
    due = datetime.datetime(2026, 6, 23, 12, 0, tzinfo=UTC)  # Tuesday
    assert business_threshold(due, timedelta(days=3), ThresholdDirection.BEFORE, ALL_DAYS) == due - timedelta(days=3)
    assert business_threshold(due, timedelta(days=1), ThresholdDirection.AFTER, ALL_DAYS) == due + timedelta(days=1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/dev/EasySynQ/apps/api && uv run pytest tests/unit/test_notification_timer.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'Calendar'` (helpers not defined yet).

- [ ] **Step 3: Implement the pure helpers** — add to `apps/api/src/easysynq_api/services/notifications/timer.py`. Add `import zoneinfo` to the imports (the file already imports `datetime`, `enum`, `dataclass`). Insert AFTER the existing `TimerStep` enum / before `TimerPolicy` (or at the end — order is free since it's pure):

```python
import zoneinfo  # add with the other imports at the top


class ThresholdDirection(enum.Enum):
    BEFORE = "before"  # reminders: N business days BEFORE due_at
    AFTER = "after"    # escalation: N business days AFTER due_at


@dataclass(frozen=True)
class Calendar:
    """A pure business-day calendar. ``working_weekdays`` uses ISO weekday ints (1=Mon..7=Sun)."""

    working_weekdays: frozenset[int]
    holidays: frozenset[datetime.date]
    tz: zoneinfo.ZoneInfo


# Fail-safe default the resolver falls back to: Mon-Fri, no holidays, UTC.
DEFAULT_CALENDAR = Calendar(
    working_weekdays=frozenset({1, 2, 3, 4, 5}),
    holidays=frozenset(),
    tz=zoneinfo.ZoneInfo("UTC"),
)


def is_working_day(d: datetime.date, cal: Calendar) -> bool:
    return d.isoweekday() in cal.working_weekdays and d not in cal.holidays


def shift_business_days(
    anchor: datetime.date, n: int, direction: ThresholdDirection, cal: Calendar
) -> datetime.date:
    """The date that is ``n`` working days before/after ``anchor`` (the anchor day is NOT counted).

    ``n <= 0`` returns ``anchor`` unchanged. The loop is bounded so a pathological all-non-working
    calendar can never spin forever (the resolver rejects an empty working set anyway)."""
    if n <= 0:
        return anchor
    step = datetime.timedelta(days=1 if direction is ThresholdDirection.AFTER else -1)
    d = anchor
    counted = 0
    for _ in range(n * 7 + 366):
        d = d + step
        if is_working_day(d, cal):
            counted += 1
            if counted == n:
                return d
    return d  # pragma: no cover — only an all-non-working calendar reaches here


def business_threshold(
    due_at: datetime.datetime,
    offset: datetime.timedelta,
    direction: ThresholdDirection,
    cal: Calendar,
) -> datetime.datetime:
    """The UTC instant ``offset`` BUSINESS days before/after ``due_at``, evaluated against ``cal``.

    The whole-day component walks working days; any sub-day remainder is applied as wall-clock.
    Preserves ``due_at``'s local (``cal.tz``) time-of-day on the shifted date. (DST-ambiguous wall
    times default to ``fold=0`` — within tolerance for a 5-minute-granularity sweep.)"""
    local = due_at.astimezone(cal.tz)
    whole = offset.days  # timedelta normalizes a positive offset: days >= 0, remainder >= 0
    remainder = offset - datetime.timedelta(days=whole)
    target_date = shift_business_days(local.date(), whole, direction, cal)
    threshold = datetime.datetime.combine(target_date, local.time(), tzinfo=cal.tz)
    threshold = threshold - remainder if direction is ThresholdDirection.BEFORE else threshold + remainder
    return threshold.astimezone(datetime.UTC)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/dev/EasySynQ/apps/api && uv run pytest tests/unit/test_notification_timer.py -q`
Expected: PASS (the new helper tests; the existing `due_steps` tests still pass — they call the 4-arg form, unchanged until Task 2).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/EasySynQ && git add apps/api/src/easysynq_api/services/notifications/timer.py apps/api/tests/unit/test_notification_timer.py
git commit -m "feat(s-notify-6): pure Calendar value object + business-day helpers (timer.py)"
```

---

### Task 2: Thread the calendar into `due_steps`

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/timer.py` (the `due_steps` function)
- Test: `apps/api/tests/unit/test_notification_timer.py`

**Interfaces:**
- Consumes: `Calendar`, `business_threshold`, `ThresholdDirection` (Task 1).
- Produces: `due_steps(policy, due_at, stamps, now, calendar) -> list[TimerStep]` (the new 5-arg signature; REMIND_1/2 use `business_threshold(..., BEFORE)`, ESCALATE_1 uses `AFTER`, OVERDUE stays `now >= due_at`).

- [ ] **Step 1: Update the EXISTING `due_steps` tests to the 5-arg form with `ALL_DAYS` (raw-equivalence regression) and add business-day cases.** In `apps/api/tests/unit/test_notification_timer.py`, append `, ALL_DAYS` to every existing `due_steps(...)` call (the 6 existing tests), then add:

```python
def test_due_steps_reminder_uses_business_days():
    due = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)  # Monday
    pol = TimerPolicy(remind_1_before=timedelta(days=3), remind_2_before=None, escalate_1_after=None)
    # business remind_1 threshold = prior Wednesday 06-24 12:00 (skip weekend).
    assert due_steps(pol, due, NONE, datetime.datetime(2026, 6, 24, 11, 59, tzinfo=UTC), MON_FRI) == []
    assert due_steps(pol, due, NONE, datetime.datetime(2026, 6, 24, 12, 0, tzinfo=UTC), MON_FRI) == [TimerStep.REMIND_1]


def test_due_steps_escalate_waits_for_business_day_overdue_does_not():
    due = datetime.datetime(2026, 6, 26, 12, 0, tzinfo=UTC)  # Friday
    pol = TimerPolicy(remind_1_before=None, remind_2_before=None, escalate_1_after=timedelta(days=1))
    sat = datetime.datetime(2026, 6, 27, 18, 0, tzinfo=UTC)  # Saturday: past raw due+1d, before biz Mon
    # OVERDUE fires (unshifted, D-5); ESCALATE_1 does NOT (business threshold is Monday 06-29).
    assert due_steps(pol, due, NONE, sat, MON_FRI) == [TimerStep.OVERDUE]
    mon = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert due_steps(pol, due, NONE, mon, MON_FRI) == [TimerStep.OVERDUE, TimerStep.ESCALATE_1]


def test_due_steps_stamp_gating_unchanged_with_calendar():
    due = datetime.datetime(2026, 6, 26, 12, 0, tzinfo=UTC)  # Friday
    pol = TimerPolicy(remind_1_before=None, remind_2_before=None, escalate_1_after=timedelta(days=1))
    stamps = TimerStamps(None, None, None, escalated_1_at=due)  # already escalated
    mon = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=UTC)
    assert due_steps(pol, due, stamps, mon, MON_FRI) == [TimerStep.OVERDUE]  # ESCALATE_1 does not re-fire
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `cd ~/dev/EasySynQ/apps/api && uv run pytest tests/unit/test_notification_timer.py -x -q`
Expected: FAIL — `due_steps() takes 4 positional arguments but 5 were given`.

- [ ] **Step 3: Update `due_steps`** in `timer.py` — add the `calendar` param and swap the raw arithmetic for `business_threshold` (OVERDUE unchanged):

```python
def due_steps(
    policy: TimerPolicy,
    due_at: datetime.datetime,
    stamps: TimerStamps,
    now: datetime.datetime,
    calendar: Calendar,
) -> list[TimerStep]:
    """Steps whose threshold has passed AND whose stamp is null, chronological. Reminder/escalate
    thresholds are BUSINESS-DAY offsets against ``calendar`` (skip weekends + holidays); OVERDUE is
    always-on at ``due_at`` with NO business-day shift (D-5 — ``due_at`` itself is raw wall-clock,
    snapping it is the upstream R39 residual)."""
    out: list[TimerStep] = []
    if (
        policy.remind_1_before is not None
        and stamps.remind_1_sent_at is None
        and now >= business_threshold(due_at, policy.remind_1_before, ThresholdDirection.BEFORE, calendar)
    ):
        out.append(TimerStep.REMIND_1)
    if (
        policy.remind_2_before is not None
        and stamps.remind_2_sent_at is None
        and now >= business_threshold(due_at, policy.remind_2_before, ThresholdDirection.BEFORE, calendar)
    ):
        out.append(TimerStep.REMIND_2)
    if stamps.overdue_notified_at is None and now >= due_at:
        out.append(TimerStep.OVERDUE)
    if (
        policy.escalate_1_after is not None
        and stamps.escalated_1_at is None
        and now >= business_threshold(due_at, policy.escalate_1_after, ThresholdDirection.AFTER, calendar)
    ):
        out.append(TimerStep.ESCALATE_1)
    return out
```

- [ ] **Step 4: Run to verify all pass**

Run: `cd ~/dev/EasySynQ/apps/api && uv run pytest tests/unit/test_notification_timer.py -q`
Expected: PASS (existing tests via ALL_DAYS == raw; new business-day tests). NOTE: `escalation.py` still calls the 4-arg form — `mypy` will flag it; that is fixed in Task 5. Do not run `mypy` yet.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/EasySynQ && git add apps/api/src/easysynq_api/services/notifications/timer.py apps/api/tests/unit/test_notification_timer.py
git commit -m "feat(s-notify-6): due_steps computes business-day reminder/escalation thresholds"
```

---

### Task 3: `working_calendar` ORM model + registration

**Files:**
- Create: `apps/api/src/easysynq_api/db/models/working_calendar.py`
- Modify: `apps/api/src/easysynq_api/db/models/__init__.py` (import + `__all__`)
- Test: `apps/api/tests/unit/test_models_registered.py` (if it exists; else add a small assertion to an existing model-registration test — search `grep -rln "Base.metadata.tables" apps/api/tests/unit`)

**Interfaces:**
- Produces: `WorkingCalendar` ORM (`__tablename__ = "working_calendar"`; columns `id, org_id, name, working_days, holidays, timezone, is_default, created_at, updated_at`). The partial-unique-default index is migration-managed → intentionally absent from the ORM.

- [ ] **Step 1: Write the model file** `apps/api/src/easysynq_api/db/models/working_calendar.py`:

```python
"""Per-org working calendar (S-notify-6, doc 14 §line 131, R29). A working-day week mask + holiday
list the ``timer_sweep`` resolves to compute business-day reminder/escalation thresholds.

Operational config: the app role keeps INSERT/SELECT/UPDATE; migration 0067 REVOKEs DELETE.
The at-most-one-default-per-org partial unique index ``uq_working_calendar_one_default`` is
migration-managed (``migrations/env.py``) and intentionally NOT modelled here (the
``ix_task_timer_pending`` precedent — Alembic reflects predicate indexes, so an ORM copy would
phantom-DROP/CREATE)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, false, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WorkingCalendar(Base):
    __tablename__ = "working_calendar"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "organization.id", ondelete="RESTRICT", name="fk_working_calendar_org_id_organization"
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # ISO weekday ints (1=Mon..7=Sun) that are working days. JSONB; NO server_default (the seed +
    # the future editor always supply it — avoids the JSONB server_default alembic-check trap).
    working_days: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    # Array of "YYYY-MM-DD" holiday dates. JSONB; NO server_default (seed supplies []).
    holidays: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

- [ ] **Step 2: Register the model** in `apps/api/src/easysynq_api/db/models/__init__.py` — add the import (alphabetical, after `.working_draft` / near it) and the `__all__` entry:

```python
# add with the other model imports (keep the file's existing ordering — place after .visual_diff
# / before .workflow, matching the alphabetical-ish grouping):
from .working_calendar import WorkingCalendar
```

```python
# add to __all__ (alphabetical — after "VisualDiffStatus" / before "WorkflowDefinition"):
    "WorkingCalendar",
```

- [ ] **Step 3: Verify the model imports + registers**

Run: `cd ~/dev/EasySynQ/apps/api && uv run python -c "from easysynq_api.db.models import WorkingCalendar; from easysynq_api.db.base import Base; assert 'working_calendar' in Base.metadata.tables; print('OK', WorkingCalendar.__tablename__)"`
Expected: `OK working_calendar`

- [ ] **Step 4: Run the model-registration unit test (if present) + ruff**

Run: `cd ~/dev/EasySynQ/apps/api && uv run ruff check src/easysynq_api/db/models/working_calendar.py src/easysynq_api/db/models/__init__.py && uv run ruff format --check src/easysynq_api/db/models/working_calendar.py`
Expected: PASS / "All checks passed".

- [ ] **Step 5: Commit**

```bash
cd ~/dev/EasySynQ && git add apps/api/src/easysynq_api/db/models/working_calendar.py apps/api/src/easysynq_api/db/models/__init__.py
git commit -m "feat(s-notify-6): WorkingCalendar ORM model + registration"
```

---

### Task 4: Migration `0067` + env.py index registration

**Files:**
- Create: `migrations/versions/0067_working_calendar.py`
- Modify: `migrations/env.py` (add `"uq_working_calendar_one_default"` to `_MIGRATION_MANAGED_INDEXES`)

**Interfaces:**
- Consumes: the `WorkingCalendar` model (Task 3) — `alembic check` compares the migration against `Base.metadata`.
- Produces: the `working_calendar` table + seed, head `0067`.

- [ ] **Step 1: Register the migration-managed index** in `migrations/env.py` — add to the `_MIGRATION_MANAGED_INDEXES` frozenset (after `"uq_notification_dedup_awareness",`):

```python
        "uq_working_calendar_one_default",     # S-notify-6 at-most-one-default-per-org
```

- [ ] **Step 2: Write the migration** `migrations/versions/0067_working_calendar.py`:

```python
"""S-notify-6: working_calendar table + per-org default seed (business-day escalation SLAs, R29).

Creates the working_calendar entity (doc 14 §line 131 + an additive ``timezone`` column per the
S-notify-6 design D-2). The timer_sweep resolves the org's is_default calendar to compute
business-day reminder/escalation thresholds (skip weekends + holidays).

* NEW TABLE working_calendar — operational config. The 0010 ALTER DEFAULT PRIVILEGES auto-grants
  full DML to easysynq_app; REVOKE DELETE only (keep INSERT/SELECT/UPDATE for the deferred editor) —
  the notification-ledger posture, NOT sla_policy's SELECT-only.
* Partial unique index uq_working_calendar_one_default (org_id) WHERE is_default — at most one
  default per org. Migration-managed (env.py _MIGRATION_MANAGED_INDEXES; absent from the ORM).
* Seed: one is_default Mon-Fri calendar per org, timezone = that org's organization.timezone.
  (NOTE: a fresh CI/throwaway DB has zero organization rows, so the seed loop is a no-op there —
  the seed insert path is exercised only on the populated dev DB during live-smoke.)

Revision ID: 0067_working_calendar
Revises: 0066_awareness_events
Create Date: 2026-06-25
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_working_calendar"
down_revision: str | None = "0066_awareness_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "easysynq_app"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "working_calendar",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("working_days", postgresql.JSONB(), nullable=False),
        sa.Column("holidays", postgresql.JSONB(), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_working_calendar"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organization.id"],
            name="fk_working_calendar_org_id_organization",
            ondelete="RESTRICT",
        ),
    )

    # At most one default calendar per org (migration-managed partial unique index; env.py-excluded).
    op.create_index(
        "uq_working_calendar_one_default",
        "working_calendar",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    # working_calendar is operational config → keep INSERT/SELECT/UPDATE, REVOKE DELETE.
    # 0010's ALTER DEFAULT PRIVILEGES already GRANTed full DML to easysynq_app, so an explicit
    # GRANT is a no-op; only the REVOKE DELETE enforces the intended posture.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                EXECUTE 'REVOKE DELETE ON working_calendar FROM {_APP_ROLE}';
            END IF;
        END $$;
        """
    )

    # Seed one is_default Mon-Fri calendar per org (resilient multi-org loop; D1 single-org makes
    # this v1-moot but a from-scratch replay must be multi-org-safe). Explicit JSONB CAST + json.dumps
    # so the array serialization is correct without depending on op.bulk_insert's JSONB adaptation.
    org_rows = bind.execute(sa.text("SELECT id, timezone FROM organization")).all()
    for row in org_rows:
        bind.execute(
            sa.text(
                "INSERT INTO working_calendar"
                " (id, org_id, name, working_days, holidays, timezone, is_default)"
                " VALUES (:id, :org_id, :name, CAST(:working_days AS JSONB),"
                "         CAST(:holidays AS JSONB), :timezone, TRUE)"
            ),
            {
                "id": uuid.uuid4(),
                "org_id": row.id,
                "name": "Default",
                "working_days": json.dumps([1, 2, 3, 4, 5]),
                "holidays": json.dumps([]),
                "timezone": row.timezone or "UTC",
            },
        )


def downgrade() -> None:
    # No inbound FK references working_calendar.id → drop_table is clean on a populated DB (no
    # NOT-EXISTS seed-delete guard needed). Drop the migration-managed index first for parity with
    # 0066 (the cascade makes it optional).
    op.drop_index("uq_working_calendar_one_default", table_name="working_calendar")
    op.drop_table("working_calendar")
```

- [ ] **Step 3: Run the migrations gate (alembic check + up↔down round-trip on a throwaway PG16)**

Run: `cd ~/dev/EasySynQ && /check-migrations` (or the underlying script). 
Expected: PASS — `alembic check` clean (no phantom DROP/CREATE for the table, the FK, or the partial index); upgrade 0066→0067 + downgrade 0067→0066 both succeed.

If `alembic check` reports a phantom index DROP for `uq_working_calendar_one_default`, the env.py registration (Step 1) is missing/misspelled — fix and re-run.

- [ ] **Step 4: Smoke the seed locally on a one-org DB (optional but recommended)** — only if a dev DB is handy; otherwise this is covered in the live-smoke step. Verifies the JSONB seed + tz.

Run (in-session Docker needs `sg docker -c '…'`): apply 0067 to the populated dev DB and check one row exists with `working_days=[1,2,3,4,5]`, `holidays=[]`, `timezone` = the org's tz, `is_default=true`. (Exact command in the live-smoke step, Task 7.)

- [ ] **Step 5: Commit**

```bash
cd ~/dev/EasySynQ && git add migrations/versions/0067_working_calendar.py migrations/env.py
git commit -m "feat(s-notify-6): migration 0067 — working_calendar table + per-org default seed"
```

---

### Task 5: Resolve the calendar + thread it into the sweep

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/escalation.py`
- Test: `apps/api/tests/integration/test_notification_timer_sweep.py` (add resolver tests)

**Interfaces:**
- Consumes: `Calendar`, `DEFAULT_CALENDAR` (Task 1); `WorkingCalendar` model (Task 3); `due_steps(..., calendar)` (Task 2).
- Produces: `resolve_working_calendar(session, org_id) -> Calendar`; `process_task_timers` now resolves the calendar once and passes it to `due_steps`.

- [ ] **Step 1: Write the failing resolver integration tests** — append to `apps/api/tests/integration/test_notification_timer_sweep.py` (add imports: `import zoneinfo`, `from sqlalchemy import update`, `from easysynq_api.db.models.working_calendar import WorkingCalendar`, `from easysynq_api.services.notifications.timer import DEFAULT_CALENDAR`, `from easysynq_api.services.notifications.escalation import resolve_working_calendar`):

```python
async def test_resolve_working_calendar_reads_default_row(app_under_test: Any) -> None:
    """resolve_working_calendar reflects the org's is_default row (holidays + tz round-trip).

    UPDATE AHT's seeded default in place, assert, then RESTORE in finally (working_calendar keeps
    UPDATE for the app role; a 2nd is_default would violate uq_working_calendar_one_default, and the
    row is DELETE-revoked, so update-and-restore is the only safe path)."""
    org_id = await _default_org_id()
    async with get_sessionmaker()() as s:
        before = (
            await s.execute(
                select(WorkingCalendar).where(
                    WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True)
                )
            )
        ).scalar_one_or_none()
    assert before is not None, "0067 must seed an is_default calendar for the default org"
    orig = {
        "working_days": list(before.working_days),
        "holidays": list(before.holidays),
        "timezone": before.timezone,
    }
    try:
        async with get_sessionmaker()() as s:
            await s.execute(
                update(WorkingCalendar)
                .where(WorkingCalendar.id == before.id)
                .values(working_days=[1, 2, 3, 4, 5], holidays=["2026-12-25"], timezone="America/New_York")
            )
            await s.commit()
        async with get_sessionmaker()() as s:
            cal = await resolve_working_calendar(s, org_id)
        assert cal.working_weekdays == frozenset({1, 2, 3, 4, 5})
        assert datetime.date(2026, 12, 25) in cal.holidays
        assert cal.tz == zoneinfo.ZoneInfo("America/New_York")
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(update(WorkingCalendar).where(WorkingCalendar.id == before.id).values(**orig))
            await s.commit()


async def test_resolve_working_calendar_missing_row_falls_back_to_default(app_under_test: Any) -> None:
    """An org with no working_calendar row resolves to DEFAULT_CALENDAR (no crash)."""
    salt = uuid.uuid4().hex[:8]
    async with get_sessionmaker()() as s:
        org = Organization(legal_name=f"NoCal Org {salt}", short_code=f"NC{salt[:6].upper()}")
        s.add(org)
        await s.commit()
        no_cal_org_id = org.id
    try:
        async with get_sessionmaker()() as s:
            cal = await resolve_working_calendar(s, no_cal_org_id)
        assert cal == DEFAULT_CALENDAR
    finally:
        async with get_sessionmaker()() as s:
            await s.execute(delete(Organization).where(Organization.id == no_cal_org_id))
            await s.commit()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/dev/EasySynQ/apps/api && sg docker -c "uv run pytest tests/integration/test_notification_timer_sweep.py -k resolve_working_calendar -q"`
Expected: FAIL — `ImportError: cannot import name 'resolve_working_calendar'`.

- [ ] **Step 3: Implement `resolve_working_calendar` + wire it in** — in `escalation.py`. Add imports near the top: `import datetime`, `import zoneinfo` (datetime already imported); extend the `from .timer import ...` line to include `Calendar, DEFAULT_CALENDAR`; add `from ...db.models.working_calendar import WorkingCalendar`. Then add the function (above `process_task_timers`):

```python
async def resolve_working_calendar(session: AsyncSession, org_id: uuid.UUID) -> Calendar:
    """Build the org's business-day ``Calendar`` from its is_default ``working_calendar`` row.

    Fail-safe (the sweep must NEVER crash on calendar data): a missing row, a structurally-broken
    ``working_days`` (empty / not ISO ints 1..7), or an unknown ``timezone`` falls back to
    ``DEFAULT_CALENDAR`` (Mon-Fri/UTC) + a warning. Individual unparseable holiday dates are skipped
    (kept-good) so one bad entry never discards the whole list. (DEFAULT_CALENDAR is itself Mon-Fri,
    so a fallback never re-enables weekend firing.)"""
    row = (
        await session.execute(
            select(WorkingCalendar)
            .where(WorkingCalendar.org_id == org_id, WorkingCalendar.is_default.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return DEFAULT_CALENDAR
    try:
        weekdays = frozenset(int(x) for x in row.working_days)
    except (TypeError, ValueError):
        weekdays = frozenset()
    if not weekdays or not weekdays <= {1, 2, 3, 4, 5, 6, 7}:
        logger.warning("notifications.timer_bad_working_days", extra={"org_id": str(org_id)})
        return DEFAULT_CALENDAR
    try:
        tz = zoneinfo.ZoneInfo(row.timezone or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "notifications.timer_bad_timezone", extra={"org_id": str(org_id), "tz": row.timezone}
        )
        return DEFAULT_CALENDAR
    holidays: set[datetime.date] = set()
    for h in row.holidays or []:
        try:
            holidays.add(datetime.date.fromisoformat(str(h)))
        except (TypeError, ValueError):
            logger.warning(
                "notifications.timer_bad_holiday", extra={"org_id": str(org_id), "value": str(h)}
            )
    return Calendar(working_weekdays=weekdays, holidays=frozenset(holidays), tz=tz)
```

Then in `process_task_timers`, resolve the calendar once (after the `instance is None` guard, before the `for step in due_steps(...)` loop) and pass it in:

```python
    instance = await session.get(WorkflowInstance, task.instance_id)
    if instance is None:
        return 0

    # Resolve the org's business-day calendar ONCE per task (one snapshot for all steps). This is a
    # non-locking read of a row not in the identity map — no S-drift-1 stale-attr risk, no lock-order
    # deadlock, no MissingGreenlet (validated by the §9/L3 spec-validation lens).
    calendar = await resolve_working_calendar(session, task.org_id)

    tpolicy = TimerPolicy(
        ...  # unchanged
    )
    stamps = TimerStamps(
        ...  # unchanged
    )

    fired = 0
    for step in due_steps(tpolicy, task.due_at, stamps, now, calendar):  # was: (tpolicy, task.due_at, stamps, now)
        ...  # body unchanged
```

- [ ] **Step 4: Run to verify the resolver tests pass**

Run: `cd ~/dev/EasySynQ/apps/api && sg docker -c "uv run pytest tests/integration/test_notification_timer_sweep.py -k resolve_working_calendar -q"`
Expected: PASS (both resolver tests).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/EasySynQ && git add apps/api/src/easysynq_api/services/notifications/escalation.py apps/api/tests/integration/test_notification_timer_sweep.py
git commit -m "feat(s-notify-6): resolve_working_calendar + thread into process_task_timers"
```

---

### Task 6: Anti-tautology weekend wiring test + harden existing sweep tests

**Files:**
- Modify: `apps/api/tests/integration/test_notification_timer_sweep.py`

**Interfaces:**
- Consumes: the wired sweep (Task 5). `_BASE` (= `2032-03-10 10:00 UTC`, **already a Wednesday**) is the fixed reference.

- [ ] **Step 1: Add the anti-tautology weekend wiring test** — the load-bearing proof that the sweep applies business-day skipping end-to-end (and FAILS against the old raw code). Append to `apps/api/tests/integration/test_notification_timer_sweep.py`. It anchors `due_at`/`now` in the resolved calendar's own tz so it is correct for any seeded tz, and uses AHT's seeded `escalate_1_after=1d`:

```python
async def test_escalation_skips_weekend_business_day(app_under_test: Any) -> None:
    """Wiring proof: escalation fires one BUSINESS day after a Friday due_at — Monday, not Saturday.

    Uses AHT's seeded Mon-Fri calendar (or the DEFAULT_CALENDAR fallback — both Mon-Fri) AS-IS, no
    mutation, no holiday. Anti-tautology: Test A FAILS against the old raw-wall-clock timer.py (which
    escalates on Saturday). Pre-stamp remind+overdue to isolate ESCALATE_1."""
    org_id = await _default_org_id()
    manager_id = await _seed_user(org_id, display_name="Weekend Escalation Manager")
    assignee_id = await _seed_user(
        org_id, display_name="Weekend Escalation Assignee", manager_id=manager_id
    )
    async with get_sessionmaker()() as s:
        cal = await resolve_working_calendar(s, org_id)
    tz = cal.tz
    # due_at = Friday 2026-06-26 10:00 local; raw escalate = Sat 06-27, business escalate = Mon 06-29.
    due_at = datetime.datetime(2026, 6, 26, 10, 0, tzinfo=tz)
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=due_at,
        remind_1_sent_at=_STAMPED,
        remind_2_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )
    sm = get_sessionmaker()

    # Test A: now = Saturday 06-27 18:00 local — past raw due+1d, BEFORE the business Monday threshold.
    now_sat = datetime.datetime(2026, 6, 27, 18, 0, tzinfo=tz)
    await sweep_task_timers(sm, now_sat)
    async with get_sessionmaker()() as s:
        t = await s.get(Task, task.id)
        assert t is not None and t.escalated_1_at is None, "must NOT escalate on a Saturday (business-day)"
        esc = (
            await s.execute(
                select(Notification).where(
                    Notification.task_id == task.id, Notification.event_key == EVENT_TASK_ESCALATED
                )
            )
        ).scalars().all()
        assert esc == [], "no task.escalated notification before the business threshold"

    # Test B: now = Monday 06-29 12:00 local — the business escalate threshold has passed.
    now_mon = datetime.datetime(2026, 6, 29, 12, 0, tzinfo=tz)
    await sweep_task_timers(sm, now_mon)
    async with get_sessionmaker()() as s:
        t = await s.get(Task, task.id)
        assert t is not None and t.escalated_1_at is not None, "must escalate on Monday (business-day)"
        esc = (
            await s.execute(
                select(Notification).where(
                    Notification.recipient_user_id == manager_id,
                    Notification.task_id == task.id,
                    Notification.event_key == EVENT_TASK_ESCALATED,
                )
            )
        ).scalars().all()
        assert len(esc) == 1, "exactly one escalation notification to the manager on Monday"
```

- [ ] **Step 2: Run the new test — verify it PASSES against the business code**

Run: `cd ~/dev/EasySynQ/apps/api && sg docker -c "uv run pytest tests/integration/test_notification_timer_sweep.py -k escalation_skips_weekend -q"`
Expected: PASS.

- [ ] **Step 3: Mutation-verify the anti-tautology test FAILS against the old raw math.** Temporarily revert `due_steps`'s ESCALATE_1 branch to raw (`now >= due_at + policy.escalate_1_after`) in `timer.py`, re-run ONLY Test A, confirm it FAILS (old code escalates on Saturday → `escalated_1_at` set), then restore the business code.

Run (manual): edit `timer.py` ESCALATE_1 → raw; `sg docker -c "uv run pytest tests/integration/test_notification_timer_sweep.py::test_escalation_skips_weekend_business_day -q"` → expect FAIL on the "must NOT escalate on a Saturday" assertion; then `git checkout apps/api/src/easysynq_api/services/notifications/timer.py` to restore.
Expected: FAIL under raw, PASS under business — confirms the test is not a tautology.

- [ ] **Step 4: Harden the existing sweep tests against the now-active Mon-Fri calendar.** The sweep now applies AHT's Mon-Fri calendar, so any sweep test using a real `now` with `due_at = now - 2d` becomes weekday-flaky (breaks when CI runs on a Sunday: `due_at`=Friday → business escalate threshold=Monday > Sunday `now`). FIX: in `apps/api/tests/integration/test_notification_timer_sweep.py`, replace every `now = datetime.datetime.now(datetime.UTC)` with `now = _BASE` (`_BASE` = `2032-03-10 10:00 UTC`, **already a Wednesday**; `due_at = _BASE - 2d` = Monday → business escalate threshold = Tuesday ≤ Wednesday → all escalation-fires assertions hold; the assertions and pre-stamps are otherwise unchanged). The `_BASE`-anchored tests (`test_remind_fires_once`, `test_overdue_is_critical`, `test_done_task_skipped`) already pass under Mon-Fri (verified: remind→prior business day, overdue unshifted).

```bash
cd ~/dev/EasySynQ && sed -i 's/now = datetime\.datetime\.now(datetime\.UTC)/now = _BASE/g' apps/api/tests/integration/test_notification_timer_sweep.py
```

- [ ] **Step 5: Run the WHOLE timer + escalation integration suites — verify green**

Run: `cd ~/dev/EasySynQ/apps/api && sg docker -c "uv run pytest tests/integration/test_notification_timer_sweep.py tests/integration/test_notification_escalation.py -q"`
Expected: PASS. If any single test still shifts (e.g. a non-`due_at=now-2d` margin), widen ITS `due_at` to `now - 5d` (guarantees the business escalate threshold ≤ `now` regardless of weekday) — NEVER weaken an assertion. `test_notification_escalation.py` resolve-recipient/emit tests don't invoke the sweep and need no change; confirm they're untouched/green.

- [ ] **Step 6: Commit**

```bash
cd ~/dev/EasySynQ && git add apps/api/tests/integration/test_notification_timer_sweep.py
git commit -m "test(s-notify-6): anti-tautology weekend wiring test + pin existing sweep tests to a fixed Wednesday"
```

---

### Task 7: Full local gates + convergence review + live-smoke

**Files:** none new — runs the gates + the working-agreement review wave.

- [ ] **Step 1: `/check-api`** (ruff + format-check + mypy-strict + unit) — catches the mypy on the new `due_steps` signature + the resolver typing.

Run: `cd ~/dev/EasySynQ && /check-api`
Expected: PASS. (If mypy flags `working_days`/`holidays` JSONB `Mapped[list[...]]`, fall back to `Mapped[Any]` with `from typing import Any` in the model.)

- [ ] **Step 2: `/check-migrations`** (alembic check + up↔down round-trip).

Run: `cd ~/dev/EasySynQ && /check-migrations`
Expected: PASS.

- [ ] **Step 3: Full touched integration suites** (sharded/sequential per the dev-box note).

Run: `cd ~/dev/EasySynQ/apps/api && sg docker -c "uv run pytest tests/integration/test_notification_timer_sweep.py tests/integration/test_notification_escalation.py -q"`
Expected: PASS.

- [ ] **Step 4: Convergence review (working agreement — a migration+worker slice won't converge in one round; triage per round).** Run, in parallel: `diff-critic`, `migration-reviewer`, and a worker-safety / whole-branch opus review on the branch diff. Fold confirmed findings; re-review. Pay special attention to: the JSONB seed serialization, the partial-index env.py registration, the OVERDUE-stays-raw decision (D-5) being intentional, the fail-safe fallback, and idempotency invariance.

- [ ] **Step 5: Live-smoke (`/live-smoke` — BE-driven, no UI this slice).** On the populated dev DB: apply 0066→0067, verify the seed produced a Mon-Fri/`[]`/org-tz `is_default` calendar; UPDATE it to add a holiday + a Friday-due overdue task; run a real `timer_sweep()` and confirm escalation fires on the next business day (not the weekend/holiday); confirm OVERDUE still fires at `due_at`; downgrade 0067→0066 cleanly. Capture the result.

- [ ] **Step 6: Optionally run Codex** per the working agreement, triage to convergence, then proceed to the slice-end doc pass (`/finish-slice`).

---

## Self-Review

**Spec coverage:**
- §2 schema/migration/seed/grants/index/downgrade → Tasks 3 + 4. ✓
- §3 Calendar + helpers → Task 1. ✓
- §4 business-day semantics + OVERDUE-raw (D-5) → Task 2. ✓
- §5 resolver + wiring (resolve-once, granular fail-safe) → Task 5. ✓
- §6 constraints (N9/R38==102/R53/R32) → Global Constraints + Task 7 review. ✓
- §7 tests (unit math incl. DST; reuse-AHT + pre-stamp; weekend anti-tautology + mutation-verify; UPDATE-AHT-restore resolver; missing-cal→DEFAULT; idempotency/concurrency already covered by the now-pinned existing tests) → Tasks 1/2/5/6. ✓
- §8 residuals (R39, editor, register entry) → recorded; the register entry + doc updates land in the `/finish-slice` doc pass (Task 7 Step 6). ✓
- §9 validation folded → reflected throughout. ✓

**Placeholder scan:** no TBD/TODO; every code step has complete code; commands have expected output. ✓

**Type consistency:** `Calendar(working_weekdays, holidays, tz)`, `ThresholdDirection.BEFORE/AFTER`, `business_threshold(due_at, offset, direction, cal)`, `shift_business_days(anchor, n, direction, cal)`, `due_steps(policy, due_at, stamps, now, calendar)`, `resolve_working_calendar(session, org_id) -> Calendar` — used consistently across Tasks 1/2/5/6. The model column names (`working_days`/`holidays`/`timezone`/`is_default`) match the migration + the resolver + the tests. ✓
