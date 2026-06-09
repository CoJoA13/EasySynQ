# S-drift-1 — Scheduled re-review (D5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship D5 (scheduled re-review / currency) — migration 0045, the recompute rules, the daily
Beat review-sweep, the `PERIODIC_REVIEW` task-decision dispatch, and the thin read surface — per the
approved spec `docs/superpowers/specs/2026-06-09-drift-family-s-drift-1-scheduled-re-review-design.md`.

**Architecture:** Three nullable columns on `documented_information` + a seeded single-stage
`periodic_review` workflow definition. A new pure-domain module `services/vault/review.py` owns the
ONE recompute rule + the `review_state` projection + the sweep + the decision handler. The engine
gains one additive `context_users` assignee-spec key (role path untouched). Everything rides
existing gates: task membership for the decision, `document.manage_metadata` for the period,
`document.read` for the fields.

**Tech Stack:** FastAPI / SQLAlchemy 2 async / Alembic / Celery Beat / pytest (+testcontainers for
`-m integration`).

**Branch:** `feat/s-drift-1-scheduled-re-review` (already created; spec committed).

**⚠ This box (native Windows):** the FULL `-m unit` and `-m integration` suites are Linux-CI-only.
Locally run: targeted unit files (fine — the access violation is only in
`test_ingestion_helpers.py`), `ruff check` + `ruff format --check` + `mypy` (strict), and the
`/check-migrations` + `/check-contracts` skills. Integration tests are WRITTEN here, EXECUTED in CI.
All `uv run …` commands run from `apps/api/`.

**Key verified facts (do not re-derive):**
- `SignatureMeaning.review_confirmed` EXISTS (`db/models/_signature_enums.py:28`) — the seeded
  stage's `signature: {"meaning": "review_confirmed"}` passes `_valid_signature_spec`.
- `WorkflowSubjectType.PERIODIC_REVIEW` (`_workflow_enums.py:29`) and `TaskType.PERIODIC_REVIEW`
  (`_workflow_enums.py:41`) EXIST; the PG enums already carry the values (migration 0008).
- `engine._POSITIVE` contains `complete`; `_NEGATIVE` contains `changes_requested` → quorum-ANY MET
  → `COMPLETED`, FAILED → `REJECTED`. No engine outcome change needed.
- `engine.decide()` accepts only `PENDING` tasks (engine.py:390) — the sweep must NEVER flip task
  state for escalation.
- psycopg3 cannot load a PG `INTERVAL` with month components → the column is
  `review_period_months INTEGER` (spec §2 amendment).
- `AuditEvent.scope_ref` exists (`db/models/audit_event.py:77`); `AuditObjectType.document` is the
  vault's object type.
- The 0043 seed recipe (`migrations/versions/0043_dcr_approval.py`) resolves the org via
  `SELECT id FROM organization WHERE short_code = 'DEFAULT'` — it ran clean on this live install and
  CI; copy it verbatim.

---

### Task 1: Migration 0045 + ORM columns + seed

**Files:**
- Modify: `apps/api/src/easysynq_api/db/models/documented_information.py`
- Modify: `apps/api/src/easysynq_api/db/models/_audit_enums.py`
- Create: `migrations/versions/0045_periodic_review.py`

- [ ] **Step 1: Add the three columns + partial index to the ORM**

In `documented_information.py`: extend the `sqlalchemy` import line with `Date, Integer` (it already
imports `Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text` — match the local
style). Append to `__table_args__` (after the singleton partial-index entry — the partial-index
modeling precedent):

```python
        Index(
            "ix_documented_information_next_review_due",
            "next_review_due",
            postgresql_where=text("next_review_due IS NOT NULL"),
        ),
```

Append the columns after `classification` (before the `created_at` audit-fields block):

```python
    # S-drift-1 (D5, doc 04 §9): periodic re-review. NULL review_period_months = not scheduled
    # (legacy/opt-out — the owner's no-backfill fork). next_review_due is STORED, not derived:
    # review-confirm resets it from the review date, not from effective_from.
    review_period_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_review_due: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    last_reviewed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 2: Add the two audit event types to the ORM enum**

In `_audit_enums.py`, inside `class EventType`, next to the existing lifecycle events (e.g. after
`SUBMITTED_FOR_REVIEW` or grouped with the most recent additions — match the file's grouping
comments):

```python
    REVIEW_CONFIRMED = "REVIEW_CONFIRMED"  # D5: periodic review concluded "no change needed"
    REVIEW_OVERDUE = "REVIEW_OVERDUE"  # D5: open review task past next_review_due (once per cycle)
```

(`EVENT_TYPE_VALUES = tuple(_vals(EventType))` at the file bottom picks them up automatically.)

- [ ] **Step 3: Write migration 0045**

Create `migrations/versions/0045_periodic_review.py`. Model the seed on
`migrations/versions/0043_dcr_approval.py` (read it first — copy its imports/header style):

```python
"""S-drift-1 (D5, doc 04 §9): periodic re-review — review columns + the periodic_review seed.

Adds ``review_period_months`` / ``next_review_due`` / ``last_reviewed_at`` to
``documented_information`` (all nullable — NO backfill, the owner's opt-in fork), the partial
sweep index, the ``REVIEW_CONFIRMED`` / ``REVIEW_OVERDUE`` event types (additive ADD VALUE, no-op
downgrade — the 0011 pattern), and seeds the single-stage ``periodic_review`` workflow definition
(the 0043 recipe; assignee = the document owner via the ``context_users`` spec key).

Revision ID: 0045_periodic_review
Revises: 0044_dcr_implement
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "0045_periodic_review"
down_revision: str | None = "0044_dcr_implement"
branch_labels: str | None = None
depends_on: str | None = None

_DEF_KEY = "periodic_review"
_NEW_EVENT_TYPES = ("REVIEW_CONFIRMED", "REVIEW_OVERDUE")
_STAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "review",
        "mode": "PARALLEL",
        "assignees": {
            "context_users": "owner_user_id",
            "task_type": "PERIODIC_REVIEW",
            "action_expected": "periodic_review",
        },
        "quorum": {"type": "ANY"},
        "transitions": [],
        "signature": {"meaning": "review_confirmed"},
    },
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Event types (IF NOT EXISTS → idempotent; not used by any row in this txn).
    for value in _NEW_EVENT_TYPES:
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{value}'")

    # 2. The review columns (all nullable — no backfill).
    op.add_column(
        "documented_information",
        sa.Column("review_period_months", sa.Integer(), nullable=True),
    )
    op.add_column(
        "documented_information", sa.Column("next_review_due", sa.Date(), nullable=True)
    )
    op.add_column(
        "documented_information",
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_documented_information_next_review_due",
        "documented_information",
        ["next_review_due"],
        postgresql_where=sa.text("next_review_due IS NOT NULL"),
    )

    # 3. The periodic_review definition (the 0043 recipe).
    org_id = bind.execute(
        sa.text("SELECT id FROM organization WHERE short_code = 'DEFAULT'")
    ).scalar_one()
    definition_t = sa.table(
        "workflow_definition",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("version", sa.Integer),
        sa.column("effective", sa.Boolean),
        sa.column("subject_type", postgresql.ENUM(name="workflow_subject_type", create_type=False)),
        sa.column("stages", postgresql.JSONB),
        sa.column("default_sla", postgresql.JSONB),
    )
    bind.execute(
        pg_insert(definition_t)
        .values(
            org_id=org_id,
            key=_DEF_KEY,
            version=1,
            effective=True,
            subject_type="PERIODIC_REVIEW",
            stages={"entry": "review"},
            default_sla=None,  # due_at is set by the sweep (= next_review_due), not an SLA
        )
        .on_conflict_do_nothing(index_elements=["org_id", "key", "version"])
    )
    definition_id = bind.execute(
        sa.text(
            "SELECT id FROM workflow_definition WHERE org_id = :org AND key = :key AND version = 1"
        ),
        {"org": org_id, "key": _DEF_KEY},
    ).scalar_one()
    stage_t = sa.table(
        "workflow_stage",
        sa.column("org_id", postgresql.UUID(as_uuid=True)),
        sa.column("definition_id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.Text),
        sa.column("mode", postgresql.ENUM(name="workflow_stage_mode", create_type=False)),
        sa.column("assignees", postgresql.JSONB),
        sa.column("quorum", postgresql.JSONB),
        sa.column("transitions", postgresql.JSONB),
        sa.column("signature", postgresql.JSONB),
    )
    for st in _STAGES:
        bind.execute(
            pg_insert(stage_t)
            .values(
                org_id=org_id,
                definition_id=definition_id,
                key=st["key"],
                mode=st["mode"],
                assignees=st.get("assignees"),
                quorum=st.get("quorum"),
                transitions=st.get("transitions"),
                signature=st.get("signature"),
            )
            .on_conflict_do_nothing(index_elements=["definition_id", "key"])
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Seed delete guarded by child instances (the 0023/0043 precedent: a populated-DB downgrade
    # with runtime instances leaves the seed intact rather than aborting on RESTRICT).
    has_instances = bind.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM workflow_instance wi "
            "JOIN workflow_definition wd ON wi.definition_id = wd.id WHERE wd.key = :k)"
        ),
        {"k": _DEF_KEY},
    ).scalar()
    if not has_instances:
        bind.execute(
            sa.text(
                "DELETE FROM workflow_stage WHERE definition_id IN "
                "(SELECT id FROM workflow_definition WHERE key = :k)"
            ),
            {"k": _DEF_KEY},
        )
        bind.execute(sa.text("DELETE FROM workflow_definition WHERE key = :k"), {"k": _DEF_KEY})
    op.drop_index(
        "ix_documented_information_next_review_due", table_name="documented_information"
    )
    op.drop_column("documented_information", "last_reviewed_at")
    op.drop_column("documented_information", "next_review_due")
    op.drop_column("documented_information", "review_period_months")
    # Enum values: deliberate no-op (PG cannot remove an enum value; the 0011 precedent).
```

- [ ] **Step 4: Static checks pass**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` (from `apps/api`).
Expected: clean. (If the repo's mypy target differs, run the `/check-api` skill's static portion.)

- [ ] **Step 5: Round-trip the migration**

Run the `/check-migrations` skill (alembic up↔down↔`alembic check` on a throwaway PG16).
Expected: clean — in particular NO phantom-drop on the partial index (it is modeled in
`__table_args__` with a matching name + `postgresql_where`, the singleton-index precedent; it does
NOT go into `env.py`'s `_MIGRATION_MANAGED_INDEXES`, which is only for unmodelable
expression/functional indexes).

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/easysynq_api/db/models/documented_information.py \
        apps/api/src/easysynq_api/db/models/_audit_enums.py \
        migrations/versions/0045_periodic_review.py
git commit -m "feat(s-drift-1): migration 0045 — review columns + periodic_review seed"
```

---

### Task 2: The review domain module (pure functions)

**Files:**
- Create: `apps/api/src/easysynq_api/services/vault/review.py`
- Test: `apps/api/tests/unit/test_review_domain.py`

- [ ] **Step 1: Write the failing unit tests**

```python
"""Unit tests for the D5 review domain rules (services/vault/review.py)."""

from __future__ import annotations

import datetime

from easysynq_api.services.vault.review import (
    REVIEW_LEAD_DAYS,
    REVIEW_PERIOD_DEFAULT_MONTHS,
    add_months,
    compute_next_review_due,
    review_state,
)

UTC = datetime.UTC


def test_default_period_is_24_months() -> None:
    assert REVIEW_PERIOD_DEFAULT_MONTHS == 24


def test_add_months_simple() -> None:
    assert add_months(datetime.date(2026, 1, 15), 12) == datetime.date(2027, 1, 15)


def test_add_months_clamps_day_to_target_month() -> None:
    assert add_months(datetime.date(2026, 1, 31), 1) == datetime.date(2026, 2, 28)
    assert add_months(datetime.date(2024, 1, 31), 1) == datetime.date(2024, 2, 29)  # leap


def test_add_months_year_rollover() -> None:
    assert add_months(datetime.date(2026, 11, 30), 3) == datetime.date(2027, 2, 28)


def test_compute_none_when_period_null() -> None:
    eff = datetime.datetime(2026, 1, 1, tzinfo=UTC)
    assert compute_next_review_due(None, None, eff) is None


def test_compute_none_when_no_anchor() -> None:
    assert compute_next_review_due(24, None, None) is None


def test_compute_anchors_on_effective_from() -> None:
    eff = datetime.datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    assert compute_next_review_due(24, None, eff) == datetime.date(2028, 1, 10)


def test_compute_anchor_is_the_later_timestamp() -> None:
    eff = datetime.datetime(2026, 1, 10, tzinfo=UTC)
    reviewed = datetime.datetime(2026, 6, 1, tzinfo=UTC)
    # confirm after release → anchors on the review date
    assert compute_next_review_due(12, reviewed, eff) == datetime.date(2027, 6, 1)
    # re-release after a confirm → anchors on the newer effective_from
    eff2 = datetime.datetime(2026, 9, 1, tzinfo=UTC)
    assert compute_next_review_due(12, reviewed, eff2) == datetime.date(2027, 9, 1)


def test_review_state_projection_boundaries() -> None:
    due = datetime.date(2026, 7, 1)
    lead = datetime.timedelta(days=REVIEW_LEAD_DAYS)
    assert review_state(None, datetime.date(2026, 6, 9)) is None
    assert review_state(due, due - lead - datetime.timedelta(days=1)) == "current"
    assert review_state(due, due - lead) == "due_soon"  # boundary: lead-window entry day
    assert review_state(due, due - datetime.timedelta(days=1)) == "due_soon"
    assert review_state(due, due) == "overdue"  # boundary: the due day itself
    assert review_state(due, due + datetime.timedelta(days=30)) == "overdue"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_review_domain.py -v` (from `apps/api`)
Expected: FAIL — `ModuleNotFoundError`/`ImportError` (module doesn't exist yet).

- [ ] **Step 3: Implement the module**

Create `apps/api/src/easysynq_api/services/vault/review.py`:

```python
"""Periodic re-review (D5 — doc 04 §9, doc 05 §9.1, spec S-drift-1).

The ONE recompute rule + the ``review_state`` read-time projection live here and nowhere else.
``next_review_due`` is STORED on ``documented_information`` (a confirm resets it from the review
date); ``review_state`` is NEVER stored (always derived — the owner's fork). Periods are integer
MONTHS (psycopg3 cannot load month-bearing PG intervals into timedelta)."""

from __future__ import annotations

import calendar
import datetime
from zoneinfo import ZoneInfo

from ...config import get_settings

REVIEW_PERIOD_DEFAULT_MONTHS = 24  # doc 04's "e.g. 12/24/36 months" middle value (owner fork)
REVIEW_LEAD_DAYS = 30  # doc 04 §9.1's lead window ("e.g. 30 days"); org-config later, additive


def add_months(day: datetime.date, months: int) -> datetime.date:
    """Calendar month-add, day clamped to the target month's length (Jan 31 + 1mo → Feb 28/29)."""
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _org_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().easysynq_org_timezone)


def today_org() -> datetime.date:
    """Today as a DATE in the org timezone (R8: dates display in org tz; UTC stays authoritative)."""
    return datetime.datetime.now(_org_tz()).date()


def compute_next_review_due(
    review_period_months: int | None,
    last_reviewed_at: datetime.datetime | None,
    effective_from: datetime.datetime | None,
) -> datetime.date | None:
    """anchor = the LATER of (last_reviewed_at, effective_from); + period months, org-tz dated.

    One rule, three triggers (release / review-confirm / PATCH): a re-release after a confirm
    anchors on the newer effective_from, a confirm after a release anchors on the newer review
    date. NULL period or no anchor → None (not scheduled)."""
    if review_period_months is None:
        return None
    anchors = [a for a in (last_reviewed_at, effective_from) if a is not None]
    if not anchors:
        return None
    return add_months(max(anchors).astimezone(_org_tz()).date(), review_period_months)


def review_state(next_review_due: datetime.date | None, today: datetime.date) -> str | None:
    """The derived currency projection: current | due_soon | overdue (None = not scheduled)."""
    if next_review_due is None:
        return None
    if today >= next_review_due:
        return "overdue"
    if today >= next_review_due - datetime.timedelta(days=REVIEW_LEAD_DAYS):
        return "due_soon"
    return "current"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_review_domain.py -v`
Expected: all PASS.

- [ ] **Step 5: Static checks + commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`

```bash
git add apps/api/src/easysynq_api/services/vault/review.py \
        apps/api/tests/unit/test_review_domain.py
git commit -m "feat(s-drift-1): review domain — recompute rule + review_state projection"
```

---

### Task 3: Engine `context_users` pool + `actor: AppUser | None`

**Files:**
- Modify: `apps/api/src/easysynq_api/services/workflow/engine.py` (`_resolve_pool` ~line 134,
  `instantiate` ~line 256)
- Test: `apps/api/tests/unit/test_engine_context_users.py`

- [ ] **Step 1: Write the failing unit tests (pure helper)**

```python
"""Unit tests for the additive ``context_users`` assignee resolution (S-drift-1)."""

from __future__ import annotations

import uuid

from easysynq_api.services.workflow.engine import _context_user_ids


def test_resolves_a_single_context_user() -> None:
    uid = uuid.uuid4()
    assert _context_user_ids({"owner_user_id": str(uid)}, "owner_user_id") == [uid]


def test_resolves_a_list_of_context_users() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert _context_user_ids({"reviewers": [str(a), str(b)]}, "reviewers") == [a, b]


def test_missing_key_resolves_empty_fail_closed() -> None:
    assert _context_user_ids({}, "owner_user_id") == []
    assert _context_user_ids(None, "owner_user_id") == []


def test_malformed_values_are_skipped() -> None:
    uid = uuid.uuid4()
    assert _context_user_ids({"x": ["not-a-uuid", str(uid), None]}, "x") == [uid]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_engine_context_users.py -v`
Expected: FAIL — `ImportError: cannot import name '_context_user_ids'`.

- [ ] **Step 3: Implement (additive — role path byte-identical)**

In `engine.py`, add the pure helper above `_resolve_pool`:

```python
def _context_user_ids(context: dict[str, Any] | None, ref: str) -> list[uuid.UUID]:
    """Parse user ids named by a stage's ``context_users`` spec key out of the instance context
    (one id or a list). Malformed/missing values resolve to [] — the caller's empty-pool check
    then fails closed to NEEDS_ATTENTION (the engine's standard posture)."""
    raw = (context or {}).get(ref)
    values = raw if isinstance(raw, list) else [raw]
    out: list[uuid.UUID] = []
    for v in values:
        try:
            out.append(uuid.UUID(str(v)))
        except (ValueError, TypeError, AttributeError):
            continue
    return out
```

Change `_resolve_pool` to take the instance (it needs `context` + `org_id`) and union the two
sources. Current code (engine.py:134-138):

```python
async def _resolve_pool(
    session: AsyncSession, org_id: uuid.UUID, stage: WorkflowStage
) -> list[uuid.UUID]:
    roles = list(_stage_spec(stage).get("roles", []))
    return await wf_repo.users_with_roles(session, org_id, roles)
```

becomes:

```python
async def _resolve_pool(
    session: AsyncSession, instance: WorkflowInstance, stage: WorkflowStage
) -> list[uuid.UUID]:
    spec = _stage_spec(stage)
    pool = await wf_repo.users_with_roles(session, instance.org_id, list(spec.get("roles", [])))
    ref = spec.get("context_users")
    if isinstance(ref, str):
        for uid in _context_user_ids(instance.context, ref):
            if uid not in pool:
                pool.append(uid)
    return pool
```

Update the call site in `_materialize_stage` (it already holds `instance`):
`pool = await _resolve_pool(session, instance.org_id, stage)` →
`pool = await _resolve_pool(session, instance, stage)`. Grep for any other `_resolve_pool(` caller
first (`uv run python -c "1"` not needed — `grep -rn "_resolve_pool(" src/`); update all.

Widen `instantiate`'s actor parameter (the sweep is system-initiated; `_emit` already accepts
`actor: AppUser | None` and writes `ActorType.system` for None):

```python
    actor: AppUser | None,
```

(only the type annotation changes — the body only passes `actor` to `_emit`).

- [ ] **Step 4: Run the new tests + the existing engine/workflow unit tests**

Run: `uv run pytest tests/unit/test_engine_context_users.py -v`
Expected: PASS.
Run any existing engine-related unit files (`uv run pytest tests/unit -k "workflow or engine" -v`
— skip if none exist; the full parity proof is the CI integration suite).

- [ ] **Step 5: Static checks + commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean — mypy will catch any `_resolve_pool` caller missed in Step 3.

```bash
git add apps/api/src/easysynq_api/services/workflow/engine.py \
        apps/api/tests/unit/test_engine_context_users.py
git commit -m "feat(s-drift-1): engine — additive context_users pool + system instantiate"
```

---

### Task 4: Write-path wiring — create default, T2 auto-default, snapshot, release recompute, PATCH

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/service.py` (`create_document` ~line 111,
  `_snapshot` ~line 90)
- Modify: `apps/api/src/easysynq_api/services/vault/lifecycle.py` (`submit_review` ~line 179,
  `_cutover` ~line 430)
- Modify: `apps/api/src/easysynq_api/api/documents.py` (`MetadataUpdate` + PATCH handler ~line 668)
- Test: `apps/api/tests/integration/test_periodic_review.py` (new — first half)

- [ ] **Step 1: Write the failing integration tests (CI-executed)**

Create `apps/api/tests/integration/test_periodic_review.py`. Read
`apps/api/tests/integration/test_lifecycle.py:1-80` first and reuse its helpers/imports verbatim
(`_create`, `_upload`, `_checkin`, `_map_clause`, the `s5` helper module, `_auth`, fixtures
`app_client`, `token_factory`, `subj`). ⚠ Every assertion run-scoped to the doc THIS test creates
(the shared-session-DB rule). First half:

```python
pytestmark = pytest.mark.integration


async def _release_doc(app_client, ha, hb, did: str) -> dict:
    """create→checkout→checkin→map→submit→approve→release helper, mirroring test_lifecycle."""
    # (reuse the exact call sequence from test_lifecycle.py — checkout, upload unique bytes,
    #  checkin MAJOR, map clause, submit-review, approve via s5.task_for_doc + decision,
    #  then POST /documents/{did}/release; return the GET /documents/{did} body)


async def test_create_defaults_review_period_to_24(app_client, token_factory, subj) -> None:
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    body = await _create(app_client, ha, await s5.type_id("SOP"))
    got = (await app_client.get(f"/api/v1/documents/{body['id']}", headers=ha)).json()
    assert got["review_period_months"] == 24
    assert got["next_review_due"] is None  # no effective_from yet
    assert got["review_state"] is None


async def test_release_computes_next_review_due(app_client, token_factory, subj) -> None:
    ...  # _release_doc; then assert next_review_due == (effective_from date + 24 months)
    # and review_state == "current"


async def test_patch_review_period_recomputes_and_null_clears(
    app_client, token_factory, subj
) -> None:
    ...  # on a released doc: PATCH {"review_period_months": 12} → 200,
    # next_review_due == effective_from + 12 months;
    # PATCH {"review_period_months": None} → next_review_due None, review_state None;
    # PATCH {"review_period_months": 0} → 422 (ge=1)


async def test_submit_review_autodefaults_null_period(app_client, token_factory, subj) -> None:
    ...  # create; NULL the column directly (get_sessionmaker()(), UPDATE ... SET
    # review_period_months = NULL WHERE id = did); checkout/checkin/map; submit-review → 200;
    # GET → review_period_months == 24  (the T2 auto-default — never a 422)
```

Write the four tests FULLY (no `...` in the real file) — model every HTTP call on
`test_lifecycle.py`'s existing sequences.

- [ ] **Step 2: Verify the new tests are collected (cannot execute here)**

Run: `uv run pytest tests/integration/test_periodic_review.py --collect-only -q`
Expected: 4 tests collected, no import errors. (Execution is Linux-CI-only on this box.)

- [ ] **Step 3: Implement the write paths**

(a) `services/vault/service.py` — import `from .review import REVIEW_PERIOD_DEFAULT_MONTHS`; in
`create_document`'s `DocumentedInformation(...)` constructor add:

```python
        review_period_months=REVIEW_PERIOD_DEFAULT_MONTHS,
```

(b) `_snapshot` — add to the `snap` dict (review_period is Snapshot-✔, doc 04 §6.1; old snapshots
simply lack the key):

```python
        "review_period_months": doc.review_period_months,
```

(c) `services/vault/lifecycle.py` — import
`from .review import REVIEW_PERIOD_DEFAULT_MONTHS, compute_next_review_due`. In `submit_review`,
after the clause-mapping gate and before `_advance_active_version`:

```python
    if doc.review_period_months is None:
        # T2 auto-default (spec §3 amendment): the create-default applied late, so a legacy doc
        # is never stranded at submit while the SPA lacks the field (pre-S-web-8).
        doc.review_period_months = REVIEW_PERIOD_DEFAULT_MONTHS
```

In `_cutover`, right after `doc.current_state = transition.to_doc_state`:

```python
    doc.next_review_due = compute_next_review_due(
        doc.review_period_months, doc.last_reviewed_at, eff_from
    )
```

(`eff_from` is the local already assigned to `version.effective_from` a few lines above — verify
the exact name when editing.)

(d) `api/documents.py` — extend `MetadataUpdate` (note `Field` import from pydantic):

```python
class MetadataUpdate(BaseModel):
    title: str | None = None
    folder_path: str | None = None
    classification: str | None = None
    review_period_months: int | None = Field(default=None, ge=1, le=120)
```

In `update_metadata_endpoint`, after the classification block (⚠ sent-null vs omitted MUST diverge
— the S-web-7d omitted-field lesson, enforced server-side via `model_fields_set`):

```python
    if "review_period_months" in body.model_fields_set:
        doc.review_period_months = body.review_period_months
        eff_from = None
        if doc.current_effective_version_id is not None:
            ver = await session.get(DocumentVersion, doc.current_effective_version_id)
            eff_from = ver.effective_from if ver is not None else None
        doc.next_review_due = compute_next_review_due(
            doc.review_period_months, doc.last_reviewed_at, eff_from
        )
```

with `from ..services.vault.review import compute_next_review_due` added to the imports (match the
file's existing relative-import style) and `DocumentVersion` imported if not already.

- [ ] **Step 4: Static checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/easysynq_api/services/vault/service.py \
        apps/api/src/easysynq_api/services/vault/lifecycle.py \
        apps/api/src/easysynq_api/api/documents.py \
        apps/api/tests/integration/test_periodic_review.py
git commit -m "feat(s-drift-1): review-period write paths — create default, T2 auto-default, release recompute, PATCH"
```

---

### Task 5: The Beat review-sweep

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/review.py` (add the sweep)
- Create: `apps/api/src/easysynq_api/tasks/review.py`
- Modify: `apps/api/src/easysynq_api/tasks/app.py` (Beat entry),
  `apps/api/src/easysynq_api/tasks/__init__.py` (register)
- Test: `apps/api/tests/unit/test_review_task_registration.py`,
  `apps/api/tests/integration/test_periodic_review.py` (extend)

- [ ] **Step 1: Write the failing registration unit test**

`apps/api/tests/unit/test_review_task_registration.py` (mirror
`test_records_task_registration.py`):

```python
"""The review-sweep Celery task is registered AND Beat-scheduled (the tasks/__init__ rule)."""

from easysynq_api.tasks import app


def test_review_sweep_task_is_registered() -> None:
    assert "easysynq.documents.review_sweep" in app.tasks


def test_review_sweep_is_beat_scheduled_daily() -> None:
    entries = {e["task"]: e["schedule"] for e in app.conf.beat_schedule.values()}
    assert entries.get("easysynq.documents.review_sweep") == 86400.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_review_task_registration.py -v`
Expected: FAIL (task name not in `app.tasks`).

- [ ] **Step 3: Implement the sweep service function**

Append to `services/vault/review.py` (new imports at top: `uuid`, `select`/`update` from
sqlalchemy, the models — `AuditEvent, ActorType, AuditObjectType, DocumentCurrentState,
DocumentKind, DocumentedInformation, EventType, Task, TaskState, WorkflowInstance,
WorkflowSubjectType`, `AsyncSession`, `from ..workflow import engine as wf_engine`,
`from ..workflow import repository as wf_repo`; copy the `_now`/`_rid` helpers exactly from
`services/records/service.py` — `_rid` reads the request-id contextvar and is None-safe in Beat):

```python
_TERMINAL_INSTANCE_STATES = (wf_engine.COMPLETED, wf_engine.REJECTED, wf_engine.NEEDS_ATTENTION)
_DEF_KEY = "periodic_review"


async def sweep_reviews(session: AsyncSession) -> dict[str, int]:
    """The daily D5 sweep (doc 04 §9.1). Pass 1: open ONE periodic_review instance+task per
    Effective doc inside the lead window (idempotent — the open-instance check is the WHERE NOT
    EXISTS guard; NEEDS_ATTENTION counts as terminal so a failed-closed instance may be retried,
    the CAPA precedent). Pass 2: once-per-cycle REVIEW_OVERDUE audit for past-due open tasks —
    NEVER flips task state (decide() accepts only PENDING; engine.py:390). One commit; re-run and
    acks-late safe."""
    today = today_org()
    horizon = today + datetime.timedelta(days=REVIEW_LEAD_DAYS)
    created = escalated = 0

    docs = (
        (
            await session.execute(
                select(DocumentedInformation).where(
                    DocumentedInformation.kind == DocumentKind.DOCUMENT,
                    DocumentedInformation.current_state == DocumentCurrentState.Effective,
                    DocumentedInformation.next_review_due.is_not(None),
                    DocumentedInformation.next_review_due <= horizon,
                )
            )
        )
        .scalars()
        .all()
    )
    for doc in docs:
        if (
            await wf_repo.find_nonterminal_instance(
                session,
                doc.org_id,
                WorkflowSubjectType.PERIODIC_REVIEW,
                doc.id,
                _TERMINAL_INSTANCE_STATES,
            )
            is not None
        ):
            continue
        instance = await wf_engine.instantiate(
            session,
            org_id=doc.org_id,
            definition_key=_DEF_KEY,
            subject_type=WorkflowSubjectType.PERIODIC_REVIEW,
            subject_id=doc.id,
            context={"owner_user_id": str(doc.owner_user_id), "identifier": doc.identifier},
            actor=None,
        )
        await session.flush()
        due_at = datetime.datetime.combine(
            doc.next_review_due, datetime.time(0, 0), tzinfo=datetime.UTC
        )
        await session.execute(
            update(Task).where(Task.instance_id == instance.id).values(due_at=due_at)
        )
        created += 1

    overdue_rows = (
        await session.execute(
            select(Task, WorkflowInstance)
            .join(WorkflowInstance, Task.instance_id == WorkflowInstance.id)
            .where(
                WorkflowInstance.subject_type == WorkflowSubjectType.PERIODIC_REVIEW,
                Task.state == TaskState.PENDING,
                Task.due_at.is_not(None),
                Task.due_at < _now(),
            )
        )
    ).all()
    for task, instance in overdue_rows:
        already = (
            await session.execute(
                select(AuditEvent.id)
                .where(
                    AuditEvent.object_type == AuditObjectType.document,
                    AuditEvent.object_id == instance.subject_id,
                    AuditEvent.event_type == EventType.REVIEW_OVERDUE,
                    AuditEvent.occurred_at >= instance.started_at,
                )
                .limit(1)
            )
        ).first()
        if already is not None:
            continue
        doc_row = await session.get(DocumentedInformation, instance.subject_id)
        session.add(
            AuditEvent(
                org_id=instance.org_id,
                occurred_at=_now(),
                actor_id=None,
                actor_type=ActorType.system,
                event_type=EventType.REVIEW_OVERDUE,
                object_type=AuditObjectType.document,
                object_id=instance.subject_id,
                scope_ref=doc_row.identifier if doc_row is not None else None,
                after={"due_at": task.due_at.isoformat() if task.due_at else None},
                request_id=_rid(),
            )
        )
        escalated += 1

    await session.commit()
    return {"tasks_created": created, "escalated": escalated}
```

- [ ] **Step 4: Implement the Celery module + Beat entry + registration**

Create `apps/api/src/easysynq_api/tasks/review.py` (the `tasks/records.py` shape exactly):

```python
"""Celery/Beat task for the D5 periodic re-review sweep (S-drift-1, doc 04 §9.1)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings
from ..services.vault.review import sweep_reviews
from .app import app

logger = logging.getLogger("easysynq.documents.tasks")


async def _run_review_sweep() -> dict[str, int]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        async with sessionmaker() as session:
            summary = await sweep_reviews(session)
            logger.info("documents.review_sweep", extra={"extra_fields": summary})
            return summary
    finally:
        await engine.dispose()


@app.task(name="easysynq.documents.review_sweep")  # type: ignore[untyped-decorator]
def review_sweep() -> dict[str, int]:
    """Daily D5 sweep; returns ``{tasks_created, escalated}``."""
    return asyncio.run(_run_review_sweep())
```

In `tasks/__init__.py` add `review,` to the import tuple (alphabetical — after `records`).
In `tasks/app.py` `beat_schedule`, next to `records-retention-sweep`:

```python
        # S-drift-1: daily D5 periodic re-review sweep (doc 04 §9.1)
        "documents-review-sweep": {
            "task": "easysynq.documents.review_sweep",
            "schedule": 86400.0,  # daily
        },
```

- [ ] **Step 5: Run the registration test**

Run: `uv run pytest tests/unit/test_review_task_registration.py -v`
Expected: PASS.

- [ ] **Step 6: Extend the integration tests (CI-executed)**

Append to `tests/integration/test_periodic_review.py` (run-scoped — assert ONLY on this test's
doc/instance, never global counts):

```python
async def test_sweep_creates_one_task_idempotently(app_client, token_factory, subj) -> None:
    ...  # release a doc; UPDATE its next_review_due = today (direct session);
    # await sweep_reviews(session) → instance+task exist for THIS doc
    # (find by subject_id == doc id; task.assignee_user_id == the owner's app_user.id;
    #  task.type == PERIODIC_REVIEW; task.due_at.date() == next_review_due);
    # await sweep_reviews(session) AGAIN → still exactly ONE non-terminal instance for this doc


async def test_sweep_skips_non_effective_and_unscheduled(app_client, token_factory, subj) -> None:
    ...  # a Draft doc with next_review_due set directly + an Effective doc with NULL period:
    # sweep → NO instance for either (subject-scoped lookups)


async def test_sweep_escalates_overdue_once(app_client, token_factory, subj) -> None:
    ...  # released doc; next_review_due = today - 40d (direct UPDATE); sweep → task created
    # AND one REVIEW_OVERDUE audit_event (object_id == doc id, scope_ref == identifier);
    # sweep again → STILL exactly one REVIEW_OVERDUE for this doc id
```

Write fully; use the `app_under_test`-backed `get_sessionmaker()` pattern from
`test_lifecycle.py` for direct DB setup, and call `sweep_reviews(session)` directly (the
service fn, not the Celery wrapper — the `release_due()` testing precedent).

- [ ] **Step 7: Collect-check + static checks + commit**

Run: `uv run pytest tests/integration/test_periodic_review.py --collect-only -q` → collected.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` → clean.

```bash
git add apps/api/src/easysynq_api/services/vault/review.py \
        apps/api/src/easysynq_api/tasks/review.py \
        apps/api/src/easysynq_api/tasks/__init__.py \
        apps/api/src/easysynq_api/tasks/app.py \
        apps/api/tests/unit/test_review_task_registration.py \
        apps/api/tests/integration/test_periodic_review.py
git commit -m "feat(s-drift-1): daily review sweep — lead-window task creation + once-per-cycle overdue audit"
```

---

### Task 6: The decision handler + dispatch

**Files:**
- Modify: `apps/api/src/easysynq_api/services/vault/review.py` (add `decide_periodic_review`)
- Modify: `apps/api/src/easysynq_api/api/workflow.py` (dispatch branch ~line 203)
- Test: `apps/api/tests/integration/test_periodic_review.py` (extend)

- [ ] **Step 1: Read the DCR handler first**

Read `apps/api/src/easysynq_api/services/dcr/service.py:565-711` (`decide_dcr_approval`). Mirror
its lock/replay/commit skeleton EXACTLY (instance lock helper, the replay early-return, the single
commit). The periodic-review handler differs only in the membership check, the outcome whitelist,
and the COMPLETED side effects.

- [ ] **Step 2: Write the failing integration tests (CI-executed)**

Append to `tests/integration/test_periodic_review.py`:

```python
async def test_decide_complete_confirms_review(app_client, token_factory, subj) -> None:
    ...  # release as subj.a (the owner); sweep → task; decide as the OWNER:
    # POST /tasks/{tid}/decision {"outcome": "complete"} → 200,
    # body["current_state"] == "COMPLETED", body["next_review_due"] is the reset date.
    # Assert (run-scoped): one signature_event meaning=review_confirmed,
    # signed_object_type=document_version, signed_object_id == effective version id,
    # content_digest == that version's source_blob_sha256;
    # one REVIEW_CONFIRMED audit (object_id == doc id, scope_ref == identifier);
    # doc.last_reviewed_at set; doc.next_review_due == today_org() + period months;
    # re-run sweep → NO new instance (clock reset, doc not due).


async def test_decide_changes_requested_keeps_clock_and_renag(
    app_client, token_factory, subj
) -> None:
    ...  # released+due doc; sweep → task; owner decides changes_requested → 200,
    # instance current_state == "REJECTED"; doc.next_review_due UNCHANGED;
    # NO review_confirmed signature for this doc; sweep again → a NEW open instance
    # (the deliberate re-nag while the doc stays Effective and due).


async def test_decide_rejects_non_owner_and_bad_outcomes(app_client, token_factory, subj) -> None:
    ...  # subj.b (not the owner, no candidate membership) decides → 403;
    # owner decides {"outcome": "approve"} → 422 (whitelist);
    # owner decides complete twice with the same Idempotency-Key header → second is a
    # 200 replay with NO second signature_event (count review_confirmed sigs for this doc == 1).
```

Write fully (model headers/decision calls on the existing DCR/CAPA decision tests — grep
`tests/integration` for `"/decision"`).

- [ ] **Step 3: Implement the handler**

Append to `services/vault/review.py` (new imports: `DocumentVersion`, `SignatureEvent`, `AppUser`,
`ProblemException` — match the paths used in `services/dcr/service.py`; `SignatureEventSink` from
the same module DCR imports it from):

```python
_ALLOWED_REVIEW_OUTCOMES = {"complete", "changes_requested"}


async def decide_periodic_review(
    session: AsyncSession,
    task: Task,
    actor: AppUser,
    *,
    outcome: str,
    comment: str | None,
    idempotency_key: str | None,
    sig_sink: SignatureEventSink,
) -> dict[str, Any]:
    """Decide a PERIODIC_REVIEW task (doc 04 §9.2). ``complete`` = "no change needed" → the
    review_confirmed signature bound to the CURRENT Effective version's source digest + the clock
    reset from the review date. ``changes_requested`` = "change needed" → terminal REJECTED, no
    clock reset (the sweep re-nags while the doc stays Effective and due — deliberate).
    "Obsolete it" is NOT a task outcome (rides the obsolete endpoint). Membership = the task's
    assignee/candidate pool (app_user.id), nothing role-based."""
    instance = ...  # lock instance FOR UPDATE — the exact helper decide_dcr_approval uses
    if instance is None or instance.org_id != actor.org_id:
        raise ProblemException(status=404, code="not_found", title="Task not found")
    pool = [str(u) for u in (task.candidate_pool or [])]
    if task.assignee_user_id != actor.id and str(actor.id) not in pool:
        raise ProblemException(
            status=403, code="forbidden", title="Not a candidate for this review task"
        )
    if outcome not in _ALLOWED_REVIEW_OUTCOMES:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="Periodic review accepts outcome complete | changes_requested",
        )

    result = await wf_engine.decide(
        session,
        task,
        actor,
        outcome=outcome,
        comment=comment,
        idempotency_key=idempotency_key,
        _commit=False,
    )
    if result.get("replayed") or result.get("stage_state") == "ALREADY_SATISFIED":
        await session.commit()
        return result

    if result.get("current_state") == wf_engine.COMPLETED and outcome == "complete":
        doc = (
            await session.execute(
                select(DocumentedInformation)
                .where(DocumentedInformation.id == instance.subject_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if doc is None or doc.current_effective_version_id is None:
            # raising here rolls the whole txn back (engine rows included, _commit=False) —
            # the task stays PENDING and can be re-decided once the doc's state settles.
            raise ProblemException(
                status=409,
                code="conflict",
                title="Document no longer has an Effective version to confirm",
            )
        version = await session.get(DocumentVersion, doc.current_effective_version_id)
        assert version is not None  # FK-guaranteed
        sig = sig_sink.record(
            session,
            SignatureEvent(
                org_id=actor.org_id,
                signed_object_id=version.id,
                meaning="review_confirmed",
                signer_user_id=actor.id,
                signed_object_type="document_version",
                content_digest=version.source_blob_sha256,
                auth_context={"acr": "SESSION"},
            ),
        )
        await session.flush()
        now = _now()
        doc.last_reviewed_at = now
        doc.next_review_due = compute_next_review_due(
            doc.review_period_months, now, version.effective_from
        )
        session.add(
            AuditEvent(
                org_id=actor.org_id,
                occurred_at=now,
                actor_id=actor.id,
                actor_type=ActorType.user,
                event_type=EventType.REVIEW_CONFIRMED,
                object_type=AuditObjectType.document,
                object_id=doc.id,
                scope_ref=doc.identifier,
                after={
                    "revision_label": version.revision_label,
                    "next_review_due": (
                        doc.next_review_due.isoformat() if doc.next_review_due else None
                    ),
                    "signature_event_id": str(sig.id) if sig is not None else None,
                },
                request_id=_rid(),
            )
        )
        result["document_id"] = str(doc.id)
        result["next_review_due"] = (
            doc.next_review_due.isoformat() if doc.next_review_due else None
        )
        result["signature_event_id"] = str(sig.id) if sig is not None else None

    await session.commit()
    return result
```

(The `instance = ...` line: copy the exact lock call `decide_dcr_approval` makes at its top —
same helper, same arguments. Everything else above is complete.)

- [ ] **Step 4: Add the dispatch branch**

In `api/workflow.py`, AFTER the DCR branch and BEFORE the DOCUMENT default
(`_OUTCOME_PERMISSION` lookup):

```python
    if instance is not None and instance.subject_type is WorkflowSubjectType.PERIODIC_REVIEW:
        return await decide_periodic_review(
            session,
            task,
            caller,
            outcome=body.outcome,
            comment=body.comment,
            idempotency_key=idempotency_key,
            sig_sink=sig_sink,
        )
```

with `from ..services.vault.review import decide_periodic_review` in the imports. The DOCUMENT /
DCR / CAPA paths stay byte-identical.

- [ ] **Step 5: Collect-check + static checks + commit**

Run: `uv run pytest tests/integration/test_periodic_review.py --collect-only -q` → collected.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` → clean.

```bash
git add apps/api/src/easysynq_api/services/vault/review.py \
        apps/api/src/easysynq_api/api/workflow.py \
        apps/api/tests/integration/test_periodic_review.py
git commit -m "feat(s-drift-1): PERIODIC_REVIEW decision — review_confirmed signature + clock reset"
```

---

### Task 7: Read surface — document serializer + checklist overdue leg

**Files:**
- Modify: `apps/api/src/easysynq_api/api/documents.py` (`_document` ~line 138)
- Modify: `apps/api/src/easysynq_api/services/reports/checklist.py`
- Test: `apps/api/tests/integration/test_periodic_review.py` (extend),
  plus the existing checklist test file (grep `tests/` for `compliance-checklist` and extend there)

- [ ] **Step 1: Write the failing tests (CI-executed)**

Append to `test_periodic_review.py`:

```python
async def test_document_serializer_carries_review_fields(app_client, token_factory, subj) -> None:
    ...  # released doc → GET detail: review_period_months == 24, next_review_due set,
    # last_reviewed_at None, review_state == "current";
    # UPDATE next_review_due = today - 1d directly → GET → review_state == "overdue";
    # the LIST endpoint row for THIS doc id carries the same four fields.
```

Extend the existing checklist integration test file with one run-scoped test: release a doc mapped
to a known clause, set its `next_review_due` past, call `GET /reports/compliance-checklist`, find
THIS clause's row → `overdue_review is True` and `rollup["overdue_review"] >= 1`; reset the date to
future → row flips back to `False`. (Delta-style: only assert on the clause this test mapped.)

- [ ] **Step 2: Implement the serializer fields**

In `api/documents.py`, import `from ..services.vault.review import review_state, today_org`. In
`_document(...)`, after the `"created_at"` entry:

```python
        "review_period_months": d.review_period_months,
        "next_review_due": d.next_review_due.isoformat() if d.next_review_due else None,
        "last_reviewed_at": d.last_reviewed_at.isoformat() if d.last_reviewed_at else None,
        "review_state": review_state(d.next_review_due, today_org()),
```

(Every list/detail call site uses `_document`, so the fields flow everywhere — verify with
`grep -n "_document(" apps/api/src/easysynq_api/api/documents.py`.)

- [ ] **Step 3: Implement the checklist leg**

In `services/reports/checklist.py` (read the whole file first — ~161 lines): extend the grouped
coverage query with a third conditional aggregate next to `mapped`/`effective`:

```python
            sa.func.count(sa.distinct(DocumentedInformation.id))
            .filter(
                DocumentedInformation.current_state == DocumentCurrentState.Effective,
                DocumentedInformation.next_review_due.is_not(None),
                DocumentedInformation.next_review_due <= sa.bindparam("today"),
            )
            .label("overdue"),
```

binding `today=today_org()` at execute time (import `today_org` from `..vault.review`; adapt the
exact aggregate style to how `mapped`/`effective` are built in that file — match it). Unpack the
7-tuple rows, then:

- each row dict gains `"overdue_review": overdue > 0` (a flag ORTHOGONAL to `status` —
  COVERED/PARTIAL/GAP semantics unchanged);
- the rollup gains `"overdue_review": <count of rows where the flag is True>`;
- if the file computes `projected_status`, leave that path untouched.

Update the module docstring's "Deferred" note (it names the missing `next_review_due` — that leg
now exists; the "linked evidence" leg stays deferred).

- [ ] **Step 4: Collect-check + static checks + commit**

Run: `uv run pytest tests/integration/test_periodic_review.py --collect-only -q` → collected
(and the checklist test file collects).
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` → clean.

```bash
git add apps/api/src/easysynq_api/api/documents.py \
        apps/api/src/easysynq_api/services/reports/checklist.py \
        apps/api/tests/integration/
git commit -m "feat(s-drift-1): read surface — review fields on documents + checklist overdue leg"
```

---

### Task 8: Contract updates

**Files:**
- Modify: `packages/contracts/openapi.yaml`

- [ ] **Step 1: Extend the schemas**

(a) `Document` schema (~line 4686) — add after `created_at`:

```yaml
        review_period_months: { type: [integer, "null"], minimum: 1, maximum: 120 }
        next_review_due: { type: [string, "null"], format: date }
        last_reviewed_at: { type: [string, "null"], format: date-time }
        review_state:
          type: [string, "null"]
          enum: [current, due_soon, overdue, null]
          description: "Derived currency projection (D5, doc 05 §9.3); null = no review scheduled."
```

(b) `MetadataUpdate` (~line 4798):

```yaml
        review_period_months:
          type: [integer, "null"]
          minimum: 1
          maximum: 120
          description: "Months between periodic reviews. Explicit null clears the schedule (opt-out)."
```

(c) `ChecklistRow` — add `overdue_review` to `required` and:

```yaml
        overdue_review:
          type: boolean
          description: "True iff any mapped Effective document is past its next_review_due."
```

(d) `ChecklistRollup` — add `overdue_review` to `required` and:

```yaml
        overdue_review: { type: integer, description: "Count of rows with overdue_review=true." }
```

(e) On the `POST /tasks/{task_id}/decision` operation description, append one sentence: for
`PERIODIC_REVIEW` subjects the accepted outcomes are `complete` ("no change needed" — emits the
`review_confirmed` signature and resets `next_review_due`) and `changes_requested`.

Match the file's exact YAML style (2-space, inline `{ }` where neighbors use it).

- [ ] **Step 2: Lint + commit**

Run the `/check-contracts` skill (redocly lint). Expected: clean.

```bash
git add packages/contracts/openapi.yaml
git commit -m "feat(s-drift-1): contract — review fields, MetadataUpdate, checklist overdue leg"
```

---

### Task 9: Full local gates + docs

**Files:**
- Modify: `docs/slice-history.md`, `CLAUDE.md` (Current status + migration head), spec status line.

- [ ] **Step 1: Full local gate sweep**

- `/check-api` static portion: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
- Targeted unit files: `uv run pytest tests/unit/test_review_domain.py tests/unit/test_engine_context_users.py tests/unit/test_review_task_registration.py -v` → all PASS
- `/check-migrations` → clean round-trip
- `/check-contracts` → clean
- `uv run pytest tests/integration/test_periodic_review.py --collect-only -q` → collects
(Full unit + integration suites run in Linux CI on the PR.)

- [ ] **Step 2: Docs**

- `docs/slice-history.md`: add the S-drift-1 entry (family kickoff; mig 0045; the
  context_users engine seam; the INT-months psycopg amendment; the T2 auto-default; the re-nag
  semantics; checklist overdue leg closed).
- `CLAUDE.md` Current status: drift family started, S-drift-1 ✅ pending merge; **migration head
  `0045` (next `0046`)**.
- Spec header: leave status as approved (it is).

- [ ] **Step 3: Commit**

```bash
git add docs/slice-history.md CLAUDE.md
git commit -m "docs(s-drift-1): slice history + current status (mig head 0045)"
```

---

## After the plan (orchestrator-level, NOT plan tasks)

1. **diff-critic** on the full branch diff (`Agent` tool, `subagent_type: diff-critic`).
2. **Pre-merge live smoke** (it caught real bugs on 7c AND 7d): `just up s`; rebuild
   `api` + worker/beat images (`docker compose --env-file .env -f infra/compose/compose.yml -f
   infra/compose/compose.s.yml up -d --build api`); `curl http://localhost/readyz` → head `0045`.
   Grant `document.*` SYSTEM overrides to the LIVE login's app_user row (org AHT — re-created
   Keycloak users mint new JIT rows). Author+release a doc → PATCH `review_period_months: 1` →
   `UPDATE documented_information SET next_review_due = CURRENT_DATE` (or PATCH a 1-month period on
   an old-effective doc) → force one sweep (`docker compose … exec api python -c` invoking the task,
   or `celery call easysynq.documents.review_sweep`) → task appears in `GET /tasks` → decide
   `complete` → verify the `review_confirmed` signature row, the reset `next_review_due`, the
   checklist `overdue_review` flag flipping.
3. **PR** via the `/pr` skill → green CI (all 5 jobs) → address Codex on EVERY thread (reply +
   resolve via `gh api`, path WITHOUT leading slash) → squash-merge.
