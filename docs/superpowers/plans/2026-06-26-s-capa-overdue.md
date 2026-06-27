# S-capa-overdue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `capa.overdue` end-to-end — a severity-defaulted, editable CAPA target-completion date plus a daily Beat sweep that notifies the **QMS Owner** role when an open CAPA passes that date.

**Architecture:** Two nullable columns on the mutable `capa` row (`target_completion_date DATE`, `overdue_notified_at TIMESTAMPTZ`); the date is defaulted from severity at raise and editable via a `capa.update`-gated `PATCH`. A new state-scan Beat sweep (`services/capa/overdue.py`) claims breached CAPAs and emits the task-less `capa.overdue` notification by **reusing the existing awareness machinery** (`enqueue_awareness_one`, `resolve_subject("CAPA", …)`), deduped via `subject_version_id = uuid5(capa_id, target_date)` so a re-armed breach fires distinctly. The mutable deadline + the breach each leave a WORM audit (`CAPA_TARGET_DATE_SET` / `CAPA_OVERDUE`).

**Tech Stack:** FastAPI / Python 3.12 · async SQLAlchemy 2.x · Alembic · Celery Beat · React/TS + Mantine (SPA).

## Global Constraints

- **Migration head moves `0069 → 0070`.** New revision `down_revision = "0069_escalate2_final_tier"`; head moves by exactly one. Run `/check-migrations` (round-trip up↔down↔`alembic check` on a throwaway PG16) before the PR.
- **No new permission key** (R38 additive-only is not exercised) — the edit endpoint reuses the existing `capa.update` key.
- **Two additive `event_type` enum values** (`CAPA_OVERDUE`, `CAPA_TARGET_DATE_SET`) added via `ALTER TYPE … ADD VALUE IF NOT EXISTS` (autocommit), sourced from the ORM `EVENT_TYPE_VALUES`; no-op enum downgrade.
- **`capa.overdue` is already `classes.py → CRITICAL`** — do not re-map; CRITICAL = immediate + pierces quiet hours (correct for a compliance-deadline breach).
- **Distinct-dedup invariant:** the sweep's `subject_version_id` MUST vary by the target date (a re-armed breach must NOT collapse onto the first notification's `uq_notification_dedup_awareness` row — the S-remind2/S-escalate2 lesson, task-less form).
- **Template seed is GLOBAL** (no `org_id`) → CI-covered by `alembic upgrade head`; its downgrade `DELETE` is guarded `AND NOT EXISTS (SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)` (RESTRICT FK).
- **Severity offsets are a code constant** (`Critical 30 / Major 60 / Minor 90` calendar days); an admin editor is a deferral.
- **Integration tests:** weekday-pinned (`_BASE` a Wednesday in 2026-06), `audit_event.occurred_at` in a seeded monthly partition (2026-06), run-scoped / delta-based, self-provide preconditions, reuse the default org (a 2nd `Organization` trips `scalar_one`), keep the `app_under_test` fixture. The full local `-m integration` run is NOT a gate (~54 env/pollution false-fails) — run the scoped file; CI's 4 shards are authoritative.
- **The PostToolUse ruff `--fix` hook strips a just-added import before its first use (F401→F821)** — add the using code first, or re-add the import after.
- **Live-smoke is DB-only** — never run the live sweep (it emails real QMs).

---

### Task 1: Migration `0070` + ORM columns + `EventType` members

**Files:**
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py` (add 2 `EventType` members near the CAPA cluster ~line 314)
- Modify: `apps/api/src/easysynq_api/db/models/capa.py` (add 2 mapped columns)
- Create: `migrations/versions/0070_capa_overdue.py`
- Test: `apps/api/tests/unit/test_capa_overdue_model.py`

**Interfaces:**
- Produces: `Capa.target_completion_date: Mapped[datetime.date | None]`, `Capa.overdue_notified_at: Mapped[datetime.datetime | None]`; `EventType.CAPA_OVERDUE`, `EventType.CAPA_TARGET_DATE_SET`; migration revision `"0070_capa_overdue"`.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_capa_overdue_model.py
from easysynq_api.db.models._audit_enums import EVENT_TYPE_VALUES, EventType
from easysynq_api.db.models.capa import Capa


def test_event_type_has_capa_overdue_members():
    assert EventType.CAPA_OVERDUE.value == "CAPA_OVERDUE"
    assert EventType.CAPA_TARGET_DATE_SET.value == "CAPA_TARGET_DATE_SET"
    assert "CAPA_OVERDUE" in EVENT_TYPE_VALUES
    assert "CAPA_TARGET_DATE_SET" in EVENT_TYPE_VALUES


def test_capa_model_has_overdue_columns():
    cols = Capa.__table__.columns
    assert "target_completion_date" in cols
    assert "overdue_notified_at" in cols
    assert cols["target_completion_date"].nullable is True
    assert cols["overdue_notified_at"].nullable is True
```

- [ ] **Step 2: Run it (fails — members/columns missing)**

Run: `cd apps/api && uv run pytest tests/unit/test_capa_overdue_model.py -v`
Expected: FAIL (`AttributeError: CAPA_OVERDUE` / `KeyError: target_completion_date`).

- [ ] **Step 3: Add the `EventType` members**

In `_audit_enums.py`, in the CAPA cluster right after `CAPA_TRANSITIONED = "CAPA_TRANSITIONED"` (~line 314):

```python
    # S-capa-overdue: the Beat-sweep breach audit (a CAPA past its target_completion_date) and the
    # deadline-edit audit (who moved a mutable compliance deadline). Both additive via ALTER TYPE
    # ADD VALUE in 0070 (a from-scratch upgrade head rebuilds the type from EVENT_TYPE_VALUES).
    CAPA_OVERDUE = "CAPA_OVERDUE"
    CAPA_TARGET_DATE_SET = "CAPA_TARGET_DATE_SET"
```

- [ ] **Step 4: Add the ORM columns**

In `capa.py`, add the imports `import datetime` (already present? it imports `uuid`; add `datetime`) and `from sqlalchemy import Date, DateTime`. Append to the `Capa` class after `cycle_marker`:

```python
    # S-capa-overdue: the auditor-checked target-completion deadline (severity-defaulted at raise,
    # editable via capa.update). Overdue ⟺ today(org_tz) > this AND close_state not terminal.
    target_completion_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    # The capa-overdue Beat sweep's claim-filter + once-per-breach stamp (the task.overdue_notified_at
    # mirror); cleared on a date edit to re-arm. No server_default.
    overdue_notified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 5: Write the migration**

```python
# migrations/versions/0070_capa_overdue.py
"""S-capa-overdue: CAPA target-completion date + overdue notification.

Adds capa.target_completion_date (Date) + capa.overdue_notified_at (timestamptz), two additive
event_type values (CAPA_OVERDUE, CAPA_TARGET_DATE_SET), and the global capa.overdue notification
template. Both columns are nullable (existing CAPAs keep NULL → never overdue until the backfill CLI
or an edit sets a date) and ORM-mirrored (capa.py) so alembic check stays clean. The template table
is global (no org_id) so the seed is exercised by a fresh-DB upgrade head.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "0070_capa_overdue"
down_revision: str | None = "0069_escalate2_final_tier"
branch_labels: str | None = None
depends_on: str | None = None

_IN_APP_TITLE = "CAPA overdue: {{subject.identifier}}"
_IN_APP_BODY = (
    '{{subject.identifier}} — "{{subject.title}}" passed its target completion date'
    " ({{target_completion_date}}) and is still open"
)
_EMAIL_SUBJECT = "[EasySynQ] CAPA overdue: {{subject.identifier}} {{subject.title}}"
_EMAIL_BODY = (
    "Hi {{recipient.first_name}},\n\n"
    "A corrective action in EasySynQ has passed its target completion date and remains open: "
    '{{subject.identifier}} — "{{subject.title}}".\n\n'
    "  Target completion date: {{target_completion_date}}\n\n"
    "Open in EasySynQ: {{deep_link}}\n\n"
    "Manage notifications: {{prefs_link}}\n"
)


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("capa", sa.Column("target_completion_date", sa.Date(), nullable=True))
    op.add_column(
        "capa", sa.Column("overdue_notified_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Additive enum values — each in its own autocommit block (ADD VALUE cannot run in a txn block);
    # IF NOT EXISTS so a from-scratch upgrade head (which already has them via the ORM) is a no-op.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'CAPA_OVERDUE'")
        op.execute("ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'CAPA_TARGET_DATE_SET'")
    bind.execute(
        sa.text(
            "INSERT INTO notification_template"
            " (id, event_key, locale, version, is_effective,"
            "  in_app_title, in_app_body, email_subject, email_body)"
            " VALUES (:id, 'capa.overdue', 'en', 1, TRUE,"
            "         :in_app_title, :in_app_body, :email_subject, :email_body)"
            " ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "in_app_title": _IN_APP_TITLE,
            "in_app_body": _IN_APP_BODY,
            "email_subject": _EMAIL_SUBJECT,
            "email_body": _EMAIL_BODY,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    # Guard the template delete: notification.template_id is a RESTRICT FK, so an unguarded delete
    # aborts a populated downgrade (fresh-DB CI is blind to this — the S-notify-4 lesson).
    bind.execute(
        sa.text(
            "DELETE FROM notification_template"
            " WHERE event_key = 'capa.overdue'"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)"
        )
    )
    op.drop_column("capa", "overdue_notified_at")
    op.drop_column("capa", "target_completion_date")
    # The two event_type values are left in place — ALTER TYPE has no DROP VALUE; a re-upgrade's
    # IF NOT EXISTS makes them idempotent (the additive-enum no-op-downgrade convention).
```

- [ ] **Step 6: Run the unit test (passes) + the migration round-trip**

Run: `cd apps/api && uv run pytest tests/unit/test_capa_overdue_model.py -v` → PASS.
Run: `/check-migrations` (round-trip up↔down↔`alembic check` on a throwaway PG16) → clean, no phantom-DROP. (Also dispatch the `migration-reviewer` agent in the review.)

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/_audit_enums.py apps/api/src/easysynq_api/db/models/capa.py migrations/versions/0070_capa_overdue.py apps/api/tests/unit/test_capa_overdue_model.py
git commit -m "feat(s-capa-overdue): migration 0070 — capa target/overdue columns + event_type values + template"
```

---

### Task 2: Severity-default target date at raise

**Files:**
- Create: `apps/api/src/easysynq_api/domain/capa/targets.py`
- Modify: `apps/api/src/easysynq_api/domain/capa/__init__.py` (export the new helpers)
- Modify: `apps/api/src/easysynq_api/services/capa/service.py` (`build_capa` ~L322 and `spawn_capa_from_complaint` ~L254)
- Test: `apps/api/tests/unit/test_capa_targets.py`

**Interfaces:**
- Consumes: `Capa.target_completion_date` (Task 1); `NcSeverity` (`db/models/_capa_enums.py`).
- Produces: `CAPA_TARGET_DAYS: dict[NcSeverity, int]`, `default_target_date(severity: NcSeverity, raised_on: datetime.date) -> datetime.date`.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_capa_targets.py
import datetime

from easysynq_api.db.models._capa_enums import NcSeverity
from easysynq_api.domain.capa.targets import CAPA_TARGET_DAYS, default_target_date


def test_offsets_by_severity():
    assert CAPA_TARGET_DAYS == {NcSeverity.Critical: 30, NcSeverity.Major: 60, NcSeverity.Minor: 90}


def test_default_target_date_adds_calendar_days():
    raised = datetime.date(2026, 6, 24)
    assert default_target_date(NcSeverity.Critical, raised) == datetime.date(2026, 7, 24)
    assert default_target_date(NcSeverity.Major, raised) == datetime.date(2026, 8, 23)
    assert default_target_date(NcSeverity.Minor, raised) == datetime.date(2026, 9, 22)
```

- [ ] **Step 2: Run it (fails — module missing)**

Run: `cd apps/api && uv run pytest tests/unit/test_capa_targets.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement the pure helper**

```python
# apps/api/src/easysynq_api/domain/capa/targets.py
"""Pure CAPA target-completion-date defaults (S-capa-overdue). Severity → calendar-day offset; a
code constant for v1 (an admin-editable offset table is a deferred residual)."""

from __future__ import annotations

import datetime

from ...db.models._capa_enums import NcSeverity

CAPA_TARGET_DAYS: dict[NcSeverity, int] = {
    NcSeverity.Critical: 30,
    NcSeverity.Major: 60,
    NcSeverity.Minor: 90,
}


def default_target_date(severity: NcSeverity, raised_on: datetime.date) -> datetime.date:
    """The default target-completion date = raise date + the severity's calendar-day offset."""
    return raised_on + datetime.timedelta(days=CAPA_TARGET_DAYS[severity])
```

Add to `domain/capa/__init__.py`: `from .targets import CAPA_TARGET_DAYS, default_target_date` and append both to `__all__`.

- [ ] **Step 4: Set the date at the two construction sites**

In `services/capa/service.py`, import at top: `from ...domain.capa import default_target_date` (add to the existing `from ...domain.capa import (...)` block) and `from ..common.org_clock import resolve_org_tz`.

In `build_capa`, replace the `Capa(...)` constructor (lines ~322-331) — add the target date:

```python
    target_tz = await resolve_org_tz(session, actor.org_id)
    capa = Capa(
        id=record.id,
        org_id=actor.org_id,
        origin_finding_id=origin_finding_id,
        source=source,
        severity=severity,
        process_id=process_id,
        close_state=CapaCloseState.Raised,
        cycle_marker=0,
        target_completion_date=default_target_date(
            severity, datetime.datetime.now(target_tz).date()
        ),
    )
```

In `spawn_capa_from_complaint`, do the same to its inline `Capa(...)` (lines ~254-263): add
`target_completion_date=default_target_date(resolved_severity, datetime.datetime.now(await resolve_org_tz(session, actor.org_id)).date())`.
(`datetime` is already imported in service.py.)

- [ ] **Step 5: Add an integration test for the default + run both**

```python
# add to apps/api/tests/integration/test_capa.py (or a new test_capa_overdue.py)
# After raising a CAPA via the service/HTTP, assert capa.target_completion_date is not None and
# equals raise-date + offset[severity] (resolve the org tz the same way the service does).
```

Run: `cd apps/api && uv run pytest tests/unit/test_capa_targets.py -v` → PASS, and the scoped integration test file → PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/domain/capa/ apps/api/src/easysynq_api/services/capa/service.py apps/api/tests/unit/test_capa_targets.py apps/api/tests/integration/
git commit -m "feat(s-capa-overdue): default target_completion_date at CAPA raise (30/60/90 by severity)"
```

---

### Task 3: Notification event wiring — `EVENT_CAPA_OVERDUE` + whitelist

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/constants.py`
- Test: `apps/api/tests/unit/test_notification_capa_overdue.py`

**Interfaces:**
- Produces: `EVENT_CAPA_OVERDUE = "capa.overdue"`; a `VARIABLE_WHITELIST["capa.overdue"]` entry.
- Consumes: `classes.py::class_of` (already maps `capa.overdue → CRITICAL`).

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_notification_capa_overdue.py
from easysynq_api.services.notifications.classes import NotificationClass, class_of
from easysynq_api.services.notifications.constants import EVENT_CAPA_OVERDUE, VARIABLE_WHITELIST


def test_capa_overdue_event_key():
    assert EVENT_CAPA_OVERDUE == "capa.overdue"


def test_capa_overdue_is_whitelisted_and_critical():
    wl = VARIABLE_WHITELIST[EVENT_CAPA_OVERDUE]
    assert {"subject.identifier", "subject.title", "target_completion_date", "deep_link"} <= wl
    assert class_of(EVENT_CAPA_OVERDUE) is NotificationClass.CRITICAL
```

- [ ] **Step 2: Run it (fails — constant/whitelist missing)**

Run: `cd apps/api && uv run pytest tests/unit/test_notification_capa_overdue.py -v` → FAIL.

- [ ] **Step 3: Add the constant + whitelist entry**

In `constants.py`, after `EVENT_DOC_RELEASED` (~line 14): `EVENT_CAPA_OVERDUE = "capa.overdue"`. Then add to the `VARIABLE_WHITELIST` dict:

```python
    EVENT_CAPA_OVERDUE: frozenset(
        {
            "recipient.first_name",
            "subject.identifier",
            "subject.title",
            "subject.kind",
            "deep_link",
            "prefs_link",
            "target_completion_date",
        }
    ),
```

(`classes.py` already maps `capa.overdue → CRITICAL` — no change there.)

- [ ] **Step 4: Run the test (passes)**

Run: `cd apps/api && uv run pytest tests/unit/test_notification_capa_overdue.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/constants.py apps/api/tests/unit/test_notification_capa_overdue.py
git commit -m "feat(s-capa-overdue): wire EVENT_CAPA_OVERDUE + its variable whitelist"
```

---

### Task 4: The overdue Beat sweep (service)

**Files:**
- Create: `apps/api/src/easysynq_api/services/capa/overdue.py`
- Modify: `apps/api/src/easysynq_api/services/capa/repository.py` (add `list_overdue_capa_ids`)
- Test: `apps/api/tests/integration/test_capa_overdue_sweep.py`

**Interfaces:**
- Consumes: `Capa.target_completion_date`/`overdue_notified_at` (T1); `EVENT_CAPA_OVERDUE` (T3); `enqueue_awareness_one(session, *, org_id, subject: SubjectInfo, subject_id, subject_version_id, recipient: Recipient, event_key, context_vars, now, org_enabled, org_pierce) -> Literal["created","deduped","no_template"]` (`notifications/dispatch.py`); `resolve_subject(session, "CAPA", capa_id) -> SubjectInfo` (`notifications/subjects.py`); `_recipient_for_user(session, uid, *, org_id) -> Recipient | None` (`notifications/escalation.py`); `users_with_roles(session, org_id, roles) -> set[uuid.UUID]` (`workflow/repository.py`); `resolve_default_org_tz(session)`/`resolve_org_tz` + `using_org_tz` (`common/org_clock.py`); `resolve_calendar(session, org_id)` (`notifications/duedate.py`) + `is_working_day(date, cal)` (`notifications/timer.py`).
- Produces: `async def sweep_capa_overdue(sessionmaker, now: datetime.datetime) -> dict[str, int]`; `async def list_overdue_capa_ids(session, today: datetime.date) -> list[uuid.UUID]`.

**Architecture note (deviation from the spec's "advisory lock"):** this sweep uses the **`fan_out_awareness` per-unit pattern** — a fresh session per CAPA with `FOR UPDATE SKIP LOCKED` + the `overdue_notified_at` stamp — NOT a sweep-wide advisory lock. That combination is idempotent and safe under concurrent ticks and `acks_late` redelivery (two ticks split the work; a committed stamp excludes the row next time), and it gives per-CAPA exception isolation. Observable behaviour matches the spec; the per-unit lock is the better fit for a task-less notification sweep, so `LOCK_CAPA_OVERDUE_SWEEP` is not added.

- [ ] **Step 1: Write the repo query + the failing integration test**

Add to `services/capa/repository.py`:

```python
async def list_overdue_capa_ids(
    session: AsyncSession, today: datetime.date
) -> list[uuid.UUID]:
    """CAPA ids past their target date, still open, not yet notified — the sweep's claim candidates.
    (datetime is imported as ``from datetime import datetime`` in this module; ``datetime.date`` here
    refers to the stdlib date type — adjust the import if the module aliases differ.)"""
    from ...db.models._capa_enums import CapaCloseState

    rows = await session.execute(
        select(Capa.id).where(
            Capa.target_completion_date.is_not(None),
            Capa.target_completion_date < today,
            Capa.close_state.notin_([CapaCloseState.Closed, CapaCloseState.Rejected]),
            Capa.overdue_notified_at.is_(None),
        )
    )
    return list(rows.scalars().all())
```

Then the integration test (mutation-distinguishing; weekday-pinned; partition-safe):

```python
# apps/api/tests/integration/test_capa_overdue_sweep.py
# _BASE = a Wednesday in a seeded audit partition month so an audit write lands + no weekday flake.
# Build a CAPA whose target_completion_date < _BASE.date(), close_state open, overdue_notified_at NULL,
# with at least one active QMS-Owner holder in the default org (self-provide: ensure a holder exists).
# Run sweep_capa_overdue(sessionmaker, now=_BASE) and assert:
#   - exactly one capa.overdue notification row for the QMS-Owner recipient (subject_id == capa.id);
#   - capa.overdue_notified_at is now set;
#   - one CAPA_OVERDUE audit_event (object_type=record, object_id=capa.id);
#   - a terminal (Closed) CAPA and a not-yet-due CAPA are NOT notified (run-scoped membership);
#   - RE-ARM: clear overdue_notified_at + move target_completion_date to a NEW earlier date,
#     re-run the sweep, assert a SECOND distinct notification row exists (proves the
#     subject_version_id discriminator — it would COLLAPSE under a constant subject_version_id).
# Pin the clock in the resolved calendar's tz; assertions delta/run-scoped; reuse the default org;
# keep the app_under_test fixture.
```

- [ ] **Step 2: Run it (fails — module missing)**

Run: `sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_capa_overdue_sweep.py -v"` → FAIL (ImportError on `sweep_capa_overdue`).

- [ ] **Step 3: Implement the sweep**

```python
# apps/api/src/easysynq_api/services/capa/overdue.py
"""The capa.overdue Beat sweep (S-capa-overdue). Scans CAPAs past their target_completion_date and
notifies the QMS Owner role, reusing the awareness machinery (task-less, version-discriminated
dedup). Fresh session per CAPA + FOR UPDATE SKIP LOCKED + the overdue_notified_at stamp make it
idempotent and concurrency-safe (the fan_out_awareness precedent) — no sweep-wide advisory lock.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...db.models._audit_enums import ActorType, AuditObjectType, EventType
from ...db.models._capa_enums import CapaCloseState
from ...db.models.audit_event import AuditEvent
from ...db.models.capa import Capa
from ...db.models.organization import Organization
from ...db.models.system_config import SystemConfig
from ..common.org_clock import resolve_default_org_tz
from ..notifications.constants import EVENT_CAPA_OVERDUE
from ..notifications.dispatch import enqueue_awareness_one
from ..notifications.duedate import resolve_calendar
from ..notifications.escalation import _recipient_for_user
from ..notifications.subjects import resolve_subject
from ..notifications.timer import is_working_day
from ..workflow.repository import users_with_roles
from . import repository as repo

logger = logging.getLogger("easysynq.capa.overdue")

_QMS_OWNER_ROLE = "QMS Owner"
# Stable namespace for the version-discriminated dedup key (capa_id + target date → distinct row).
_NS = uuid.UUID("0c5a9e8e-7b1f-4c2a-9d3b-2f9c0a1b6e44")


async def _org_flags(session: AsyncSession, org_id: uuid.UUID) -> tuple[bool, bool]:
    """(email_enabled, pierce_quiet_hours) — mirrors fanout._org_flags; (False, False) when absent."""
    cfg = (
        await session.execute(select(SystemConfig).where(SystemConfig.org_id == org_id))
    ).scalar_one_or_none()
    if cfg is None:
        return (False, False)
    return (cfg.notifications_email_enabled, cfg.notifications_escalation_pierce_quiet_hours)


def _version_id(capa_id: uuid.UUID, target: datetime.date) -> uuid.UUID:
    """A per-(capa, target-date) dedup discriminator — a NEW target date re-arms the notification
    (mirrors the awareness subject_version_id; a constant value would collapse a re-armed breach)."""
    return uuid.uuid5(_NS, f"{capa_id}:{target.isoformat()}")


async def _process_one(
    sessionmaker: async_sessionmaker[AsyncSession], capa_id: uuid.UUID, now: datetime.datetime
) -> int:
    async with sessionmaker() as session:
        capa = (
            await session.execute(
                select(Capa)
                .where(
                    Capa.id == capa_id,
                    Capa.overdue_notified_at.is_(None),
                    Capa.target_completion_date.is_not(None),
                    Capa.close_state.notin_([CapaCloseState.Closed, CapaCloseState.Rejected]),
                )
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if capa is None:
            return 0  # claimed/stamped by a concurrent tick, or no longer eligible
        org_id = capa.org_id
        target = capa.target_completion_date
        assert target is not None  # the WHERE guarantees it
        subject = await resolve_subject(session, "CAPA", capa.id)
        org_enabled, org_pierce = await _org_flags(session, org_id)
        recipient_ids = await users_with_roles(session, org_id, [_QMS_OWNER_ROLE])
        version_id = _version_id(capa.id, target)
        context_vars: dict[str, object] = {"target_completion_date": target.isoformat()}
        created = 0
        for uid in recipient_ids:
            recipient = await _recipient_for_user(session, uid, org_id=org_id)
            if recipient is None:
                continue
            outcome = await enqueue_awareness_one(
                session,
                org_id=org_id,
                subject=subject,
                subject_id=capa.id,
                subject_version_id=version_id,
                recipient=recipient,
                event_key=EVENT_CAPA_OVERDUE,
                context_vars=context_vars,
                now=now,
                org_enabled=org_enabled,
                org_pierce=org_pierce,
            )
            if outcome == "no_template":
                # Template vanished (TOCTOU) — do NOT stamp; roll back so the CAPA is re-claimed.
                logger.warning("capa.overdue_template_missing", extra={"capa_id": str(capa.id)})
                return 0
            if outcome == "created":
                created += 1
        capa.overdue_notified_at = now
        session.add(
            AuditEvent(
                org_id=org_id,
                occurred_at=now,
                actor_id=None,
                actor_type=ActorType.system,
                event_type=EventType.CAPA_OVERDUE,
                object_type=AuditObjectType.record,  # capa.id IS a record id (the CAPA audit precedent)
                object_id=capa.id,
                scope_ref=str(capa.id),
                after={
                    "capa_id": str(capa.id),
                    "target_completion_date": target.isoformat(),
                    "severity": capa.severity.value,
                },
            )
        )
        await session.commit()
        return created


async def sweep_capa_overdue(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime.datetime
) -> dict[str, int]:
    """Notify the QMS Owner role about every open, past-target CAPA. now_is_working-gated (don't
    email an overdue CAPA on a weekend — the OVERDUE/R56 parity). Fresh session per CAPA."""
    counts: dict[str, int] = {"capas": 0, "notifications": 0, "skipped_non_working": 0}
    async with sessionmaker() as session:
        tz = await resolve_default_org_tz(session)
        today = now.astimezone(tz).date()
        # Working-day gate: resolve the default org's calendar (the resolve_default_org_tz org probe);
        # if today isn't a working day, skip the tick (don't email an overdue CAPA on a weekend).
        org_id = (
            await session.execute(
                select(Organization.id).order_by(Organization.created_at).limit(1)
            )
        ).scalar_one_or_none()
        if org_id is not None:
            cal = await resolve_calendar(session, org_id)
            if not is_working_day(today, cal):
                counts["skipped_non_working"] = 1
                return counts
        ids = await repo.list_overdue_capa_ids(session, today)
    for capa_id in ids:
        try:
            n = await _process_one(sessionmaker, capa_id, now)
        except Exception:  # noqa: BLE001 — one CAPA's failure must not wedge the sweep
            logger.warning(
                "capa.overdue_failed", exc_info=True, extra={"capa_id": str(capa_id)}
            )
            continue
        counts["capas"] += 1
        counts["notifications"] += n
    return counts
```

(If the working-day-gate's `org_id` probe reads better via `resolve_default_org_tz`'s org lookup, keep it simple as written — a single-org install resolves one calendar.)

- [ ] **Step 4: Run the integration test (passes) + mutation-check**

Run: `sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_capa_overdue_sweep.py -v"` → PASS. Confirm the re-arm assertion fails if `subject_version_id` is hard-coded to a constant (mutation-verify the discriminator).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/capa/overdue.py apps/api/src/easysynq_api/services/capa/repository.py apps/api/tests/integration/test_capa_overdue_sweep.py
git commit -m "feat(s-capa-overdue): the capa.overdue Beat sweep (task-less, version-discriminated dedup)"
```

---

### Task 5: Beat task wrapper + registration

**Files:**
- Create: `apps/api/src/easysynq_api/tasks/capa.py`
- Modify: `apps/api/src/easysynq_api/tasks/__init__.py` (import the module so it registers)
- Modify: `apps/api/src/easysynq_api/tasks/app.py` (add the `beat_schedule` entry)
- Test: `apps/api/tests/unit/test_capa_overdue_task.py`

**Interfaces:**
- Consumes: `sweep_capa_overdue(sessionmaker, now)` (T4).
- Produces: a Celery task named `easysynq.capa.overdue_sweep`.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/unit/test_capa_overdue_task.py
from easysynq_api.tasks.app import app


def test_capa_overdue_sweep_task_registered():
    assert "easysynq.capa.overdue_sweep" in app.tasks
```

- [ ] **Step 2: Run it (fails — task unregistered)**

Run: `cd apps/api && uv run pytest tests/unit/test_capa_overdue_task.py -v` → FAIL.

- [ ] **Step 3: Implement the wrapper (mirror `tasks/review.py`)**

```python
# apps/api/src/easysynq_api/tasks/capa.py
"""Celery/Beat task for the capa.overdue sweep (S-capa-overdue)."""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.capa.overdue import sweep_capa_overdue
from .app import task

logger = logging.getLogger("easysynq.capa.tasks")


async def _run() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        summary = await sweep_capa_overdue(sessionmaker, datetime.datetime.now(datetime.UTC))
        logger.info("capa.overdue_sweep", extra={"extra_fields": summary})
        return summary
    finally:
        await engine.dispose()


@task(name="easysynq.capa.overdue_sweep")
def capa_overdue_sweep() -> dict[str, int]:
    """Daily sweep; returns {capas, notifications, skipped_non_working}."""
    return asyncio.run(_run())
```

In `tasks/__init__.py`, add `from . import capa as capa  # noqa: F401` alongside the other task-module imports (so `.delay`/Beat can resolve the name). In `tasks/app.py::beat_schedule`, add:

```python
        # S-capa-overdue: daily scan for CAPAs past their target_completion_date → notify QMS Owner.
        "capa-overdue-sweep": {
            "task": "easysynq.capa.overdue_sweep",
            "schedule": 86400.0,  # daily
        },
```

- [ ] **Step 4: Run the test (passes)**

Run: `cd apps/api && uv run pytest tests/unit/test_capa_overdue_task.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/tasks/capa.py apps/api/src/easysynq_api/tasks/__init__.py apps/api/src/easysynq_api/tasks/app.py apps/api/tests/unit/test_capa_overdue_task.py
git commit -m "feat(s-capa-overdue): register the daily capa.overdue Beat sweep"
```

---

### Task 6: Edit endpoint + serializer fields

**Files:**
- Modify: `apps/api/src/easysynq_api/services/capa/service.py` (add `set_capa_target_date`)
- Modify: `apps/api/src/easysynq_api/api/capa.py` (`CapaTargetDate` schema; `PATCH /capas/{capa_id}`; extend `_capa()`)
- Modify: `packages/contracts/openapi.yaml` (document the PATCH + the two new response fields)
- Test: `apps/api/tests/integration/test_capa_target_date_api.py`

**Interfaces:**
- Consumes: `Capa.target_completion_date`/`overdue_notified_at` (T1); the existing `_capa_update = require("capa.update", async_scope_resolver=_capa_scope)` dependency; `emit_record_event`; `current_org_tz()` (`common/org_clock.py`).
- Produces: `async def set_capa_target_date(session, actor, capa_id, *, target_completion_date: datetime.date | None) -> Capa`; the `_capa()` serializer gains `target_completion_date: str | None` + `overdue: bool`.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/integration/test_capa_target_date_api.py
# Using the HTTP client (a caller with SYSTEM capa.update — the demo/QM override):
#   - GET /capas/{id} returns target_completion_date (the severity default) + overdue: bool.
#   - PATCH /capas/{id} {"target_completion_date": "<past date>"} → 200; GET shows the new date
#     and overdue: true; the capa row's overdue_notified_at is cleared (re-armed).
#   - PATCH on a Closed/Rejected CAPA → 409 capa_terminal-style.
#   - A caller WITHOUT capa.update → 403 (deny path).
```

- [ ] **Step 2: Run it (fails — endpoint missing)**

Run: `sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_capa_target_date_api.py -v"` → FAIL (404 on PATCH).

- [ ] **Step 3: Implement the service + endpoint + serializer**

In `services/capa/service.py` add (reuse `_TERMINAL_CAPA_STATES`, `_not_found`, `_conflict`, `emit_record_event`):

```python
async def set_capa_target_date(
    session: AsyncSession,
    actor: AppUser,
    capa_id: uuid.UUID,
    *,
    target_completion_date: datetime.date | None,
) -> Capa:
    """Set/clear a CAPA's target-completion date (gate capa.update). 409 on a terminal CAPA. Clears
    overdue_notified_at to re-arm the sweep, and writes a CAPA_TARGET_DATE_SET audit (the WORM record
    of who moved a mutable compliance deadline)."""
    capa = await repo.get_capa(session, capa_id, for_update=True)
    if capa is None or capa.org_id != actor.org_id:
        raise _not_found("CAPA")
    if capa.close_state in _TERMINAL_CAPA_STATES:
        raise _conflict(
            "capa_terminal", f"a {capa.close_state.value} CAPA has no live target date"
        )
    before = capa.target_completion_date
    capa.target_completion_date = target_completion_date
    capa.overdue_notified_at = None  # re-arm: a new/cleared date re-opens the breach claim
    emit_record_event(
        session,
        actor,
        EventType.CAPA_TARGET_DATE_SET,
        capa.id,
        before={"target_completion_date": before.isoformat() if before else None},
        after={
            "target_completion_date": (
                target_completion_date.isoformat() if target_completion_date else None
            )
        },
    )
    await session.commit()
    await session.refresh(capa)
    return capa
```

In `api/capa.py`: add the schema (near the other `BaseModel`s, ~line 118):

```python
class CapaTargetDate(BaseModel):
    target_completion_date: datetime.date | None = None
```

Extend `_capa()` (the shared serializer, ~line 156) — add to the `out` dict, computing `overdue` server-side via `current_org_tz()`. **Ensure both `from ...services.common.org_clock import current_org_tz` and `CapaCloseState` (add to the existing `from ...db.models._capa_enums import …` block) are imported at the top of `api/capa.py`** (the serializer compares against `CapaCloseState.Closed/Rejected`):

```python
        "target_completion_date": (
            c.target_completion_date.isoformat() if c.target_completion_date else None
        ),
        "overdue": (
            c.target_completion_date is not None
            and c.close_state not in (CapaCloseState.Closed, CapaCloseState.Rejected)
            and datetime.datetime.now(current_org_tz()).date() > c.target_completion_date
        ),
```

Add the endpoint (after `get_capa_endpoint`, ~line 392), importing `set_capa_target_date` from the service:

```python
@router.patch("/capas/{capa_id}")
async def set_capa_target_date_endpoint(
    capa_id: uuid.UUID,
    body: CapaTargetDate,
    caller: AppUser = Depends(_capa_update),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Set/clear the CAPA's target-completion date (gate ``capa.update``). 409 on a terminal CAPA;
    clears the overdue stamp to re-arm the sweep."""
    capa = await set_capa_target_date(
        session, caller, capa_id, target_completion_date=body.target_completion_date
    )
    return await _capa_full(session, capa)
```

- [ ] **Step 4: Document the contract**

In `packages/contracts/openapi.yaml`: add the `PATCH /capas/{capa_id}` operation (body `{target_completion_date: string<date>|null}`; 200 → the CAPA object; 409; 403) and add `target_completion_date` (string, format date, nullable) + `overdue` (boolean) to the CAPA response schema.

- [ ] **Step 5: Run the tests + contract lint**

Run: `sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_capa_target_date_api.py -v"` → PASS.
Run: `/check-contracts` (redocly lint) → clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/services/capa/service.py apps/api/src/easysynq_api/api/capa.py packages/contracts/openapi.yaml apps/api/tests/integration/test_capa_target_date_api.py
git commit -m "feat(s-capa-overdue): PATCH /capas/{id} target date (re-arm) + serializer overdue field"
```

---

### Task 7: Backfill CLI

**Files:**
- Create: `apps/api/src/easysynq_api/cli/backfill_capa_target_dates.py`
- Test: `apps/api/tests/integration/test_backfill_capa_target_dates.py`

**Interfaces:**
- Consumes: `default_target_date` (T2); `Capa` columns (T1).
- Produces: an idempotent CLI entrypoint `main(argv)` with `--dry-run`.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/integration/test_backfill_capa_target_dates.py
# Build a non-terminal CAPA, force target_completion_date = NULL (UPDATE), then:
#   - run the backfill in --dry-run: target stays NULL, the report says it WOULD set 1.
#   - run it for real: target_completion_date == created_at::date + offset[severity]; report sets 1.
#   - run it again: idempotent (0 changed — already set).
# Mirror the backfill_review_dates test shape; reuse the default org; run-scoped.
```

- [ ] **Step 2: Run it (fails — module missing)**

Run: `sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_backfill_capa_target_dates.py -v"` → FAIL.

- [ ] **Step 3: Implement (mirror `cli/backfill_review_dates.py`)**

Read `apps/api/src/easysynq_api/cli/backfill_review_dates.py` for the exact engine/sessionmaker + argparse + `--dry-run` shape, then implement the CAPA variant: for each non-terminal CAPA with `target_completion_date IS NULL`, compute `default_target_date(capa.severity, created_at_in_org_tz)` (the record's `created_at::date` via `get_capa_header`), set it (unless `--dry-run`), commit, and print a count summary. Never touch a terminal CAPA. Idempotent (the `IS NULL` filter).

- [ ] **Step 4: Run the test (passes)**

Run: `sg docker -c "cd apps/api && uv run pytest -m integration tests/integration/test_backfill_capa_target_dates.py -v"` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/cli/backfill_capa_target_dates.py apps/api/tests/integration/test_backfill_capa_target_dates.py
git commit -m "feat(s-capa-overdue): backfill CLI for existing CAPA target dates (idempotent, --dry-run)"
```

---

### Task 8: FE — CAPA drawer target date + overdue badge + inline edit

**Files:**
- Modify: `apps/web/src/lib/types.ts` (the `Capa` interface, ~L824)
- Modify: `apps/web/src/features/capa/mutations.ts` (add `useCapaSetTargetDate`)
- Modify: `apps/web/src/features/capa/CapaDrawer.tsx` (target-date row + Overdue badge + inline edit)
- Modify: `apps/web/src/test/msw/handlers.ts` (fixture fields + a PATCH handler)
- Test: `apps/web/src/features/capa/CapaDrawer.test.tsx`, `apps/web/src/features/capa/mutations.test.tsx`

**Interfaces:**
- Consumes: the serializer fields `target_completion_date: string | null` + `overdue: boolean` (T6); `PATCH /api/v1/capas/{id}` (T6); `StatusBadge` (`lib/StatusBadge.tsx`); `usePermissions().can("capa.update")`; the `useApi()` + `useCapaInvalidator` patterns in `mutations.ts`.
- Produces: `useCapaSetTargetDate(id)` mutation; the drawer UI additions.

- [ ] **Step 1: Add the type fields + write the failing component test**

In `lib/types.ts`, extend `Capa`:

```typescript
  target_completion_date: string | null;
  overdue: boolean;
```

Write a `CapaDrawer.test.tsx` case: with a fixture where `overdue: true`, the drawer shows an "Overdue" badge (`getByLabelText("CAPA: Overdue")` or `getByText("Overdue")` scoped) and the target-completion-date value; with `overdue: false`, no Overdue badge. A `mutations.test.tsx` case: `useCapaSetTargetDate` fires `PATCH /api/v1/capas/{id}` with the date and invalidates. **Import `expect`/`it`/`describe`/`vi` from `"vitest"`** (the jest-dom×vitest trap). Pin MSW fixtures to the real serializer shape (`satisfies Capa`).

- [ ] **Step 2: Run it (fails)**

Run: `cd apps/web && npx vitest run src/features/capa/CapaDrawer.test.tsx src/features/capa/mutations.test.tsx` → FAIL.

- [ ] **Step 3: Implement the mutation + the drawer UI + the MSW handler**

In `mutations.ts` (mirror `useStageMutation`):

```typescript
export function useCapaSetTargetDate(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: (target_completion_date: string | null) =>
      api.send<Capa>("PATCH", `/api/v1/capas/${capaId}`, { target_completion_date }),
    onSuccess: invalidate,
  });
}
```

In `CapaDrawer.tsx`, near the close_state badge (~L54-59): render a "Target completion" row showing `capa.target_completion_date ?? "—"`; when `capa.overdue` render `<StatusBadge tone="danger" label="Overdue" kind="CAPA" />` (distinct `aria-label` from other badges); and, gated on `usePermissions().can("capa.update")`, an inline `<TextInput type="date" value={…} onChange={…} />` + a Save button calling `useCapaSetTargetDate`. Follow the `audits/PlanForm.tsx` date-input pattern; do NOT render the edit control to a caller who can't `capa.update`.

In `test/msw/handlers.ts`: add `target_completion_date` + `overdue` to the CAPA fixtures (list + detail), and a `http.patch("/api/v1/capas/:id", …)` base handler returning the updated CAPA (so the success path works under `onUnhandledRequest: "error"`).

- [ ] **Step 4: Run the web tests + the full web gate**

Run: `cd apps/web && npx vitest run src/features/capa/` → PASS.
Run: `/check-web` (eslint + strict `tsc --noEmit` + build + the whole vitest suite) → green. Dispatch the `web-test-trap-reviewer` agent on the diff.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/types.ts apps/web/src/features/capa/ apps/web/src/test/msw/handlers.ts
git commit -m "feat(s-capa-overdue): CAPA drawer target-date row + overdue badge + inline edit"
```

---

## Final review & wrap (after Task 8)

- Run the full local gate: `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -m unit`; `cd apps/web && npm run lint && npm run typecheck && npm run build && npm test`; `/check-contracts`.
- Run the **`notification-wiring-reviewer`** agent (the new event/sweep/template), the **`migration-reviewer`** agent (0070), the **`web-test-trap-reviewer`** agent (Task 8), and the whole-branch **`diff-critic`**.
- Live-smoke DB-only: `0069→0070` round-trip on a throwaway PG16 + `SELECT` the new columns / template / enum values. **Never run the live sweep.**
- `/pr`, then triage any Codex round.
- The three `.claude/` artifacts (already committed at `574c4f5`) and the spec (`dfb4374`) ride in this PR.
