# S-claim-filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the escalation timer-sweep claim (`_due_task_ids`) policy-aware so a task is claimed only when it has a *configured*, unstamped timer step — closing the recurring Codex P2 where the `remind_2_sent_at IS NULL` (and DOC_ACK/PERIODIC_REVIEW `escalated_1_at IS NULL`) tautology re-claimed every open task on every sweep forever.

**Architecture:** A single SQLAlchemy `.where(...)` predicate change in `services/notifications/escalation.py::_due_task_ids`, plus three mutation-distinguishing integration tests. No migration, no new permission key, no WORM/authz/web/openapi change. `due_steps` / `process_task_timers` / stamping / audit / recipients are byte-identical — only the coarse pre-filter tightens.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x async, FastAPI, PostgreSQL 16, pytest (integration via testcontainers).

## Global Constraints

- **Migration head stays `0067`** — this slice adds NO migration.
- **No new permission key** — catalog stays at 102.
- **No WORM / authz / web / openapi change.** Only `Task.due_at`-less read-path query logic.
- **`_due_task_ids` signature is UNCHANGED**: `async def _due_task_ids(session: AsyncSession, now: datetime.datetime) -> list[uuid.UUID]`. `now` remains unused (it is the seam for the deferred Option-C threshold-gate); do **not** remove it.
- **The claim is a coarse pre-filter** — it must only ever get *tighter*, never looser. The precise business-day thresholds stay `timer.due_steps`' job inside `process_task_timers`. Mirror `due_steps`' gating: a step is claimable iff its policy offset is non-NULL **and** its stamp is NULL (OVERDUE has no offset → stamp-only).
- **Docker-less dev box:** the `integration` leg cannot run locally. Local signal = `ruff check` + `ruff format --check` + `mypy src` + `pytest --collect-only` on the touched test file. The integration tests are **CI-authoritative** (they run only in CI's `integration` job; the full `-m unit` at `/pr` does **not** run them). Mutation-distinguishing red is established by the documented argument below and confirmed by CI green on the final state.
- **Shared-DB test discipline:** the `-m integration` suite shares ONE DB across files. `_due_task_ids` returns a GLOBAL list of all due task ids. Every assertion MUST be run-scoped membership (`task.id in / not in ids`) on this test's own created task — **never** a count or an absolute.
- **Open state is load-bearing for the mutation tests:** a "fully-fired" task MUST be created `PENDING`/`CLAIMED` with a non-NULL `due_at`. A `DONE` task is excluded by `Task.state.in_(_OPEN)` regardless, so it would pass trivially and NOT distinguish the old code.

---

### Task 1: Add the three claim tests (mutation-distinguishing, CI-authoritative)

**Files:**
- Modify: `apps/api/tests/integration/test_notification_timer_sweep.py` (add `_due_task_ids` to the `escalation` import; append three test functions at the end of the Tests section).

**Interfaces:**
- Consumes (all pre-existing in this file): `_default_org_id()`, `_seed_user(org_id, *, display_name=...)`, `_seed_workflow_objects(org_id, assignee_user_id, *, due_at, task_state=TaskState.PENDING, task_type=TaskType.APPROVE, remind_1_sent_at=None, remind_2_sent_at=None, overdue_notified_at=None, escalated_1_at=None) -> tuple[WorkflowInstance, Task]`, constants `_BASE` (a Wednesday, 2026-06-24 10:00 UTC) and `_STAMPED` (a sentinel non-null datetime), the `app_under_test` fixture, `get_sessionmaker()`.
- Consumes from `escalation`: `_due_task_ids(session, now) -> list[uuid.UUID]`.
- Seeded SLA facts (migration 0065, default org): `remind_1_before=3d` (all types), `remind_2_before=None` (all types), `escalate_1_after=1d` for every type EXCEPT `DOC_ACK`/`PERIODIC_REVIEW` (=`None`). `APPROVE` and `DOC_ACK` are both seeded.
- Produces: nothing consumed by later tasks (pure test additions).

- [ ] **Step 1: Add `_due_task_ids` to the escalation import**

In the existing import block (around line 58), add `_due_task_ids` to the imported names:

```python
from easysynq_api.services.notifications.escalation import (
    _due_task_ids,
    emit_task_event,
    process_task_timers,
    resolve_working_calendar,
    sweep_task_timers,
)
```

- [ ] **Step 2: Write the three test functions**

Append at the end of the file (after the last test):

```python
async def test_due_task_ids_excludes_fully_fired_escalate_enabled(app_under_test: Any) -> None:
    """A fully-fired OPEN APPROVE task is NOT re-claimed (the headline remind_2 tautology).

    APPROVE is escalate-enabled (seed escalate_1_after=1d). With remind_1 + overdue + escalate_1 all
    stamped, the ONLY NULL stamp is remind_2_sent_at — and remind_2_before is NULL in the seed, so
    remind_2 is not a claimable step. The OLD 4-way claim (`remind_2_sent_at IS NULL`) re-selected this
    task on every sweep forever; the policy-aware claim must drop it. (This case = the spec's
    "fully-fired escalate-enabled" AND "remind_2-only-NULL" scenarios — they are the same task shape:
    remind_2 is the sole tautology that would have claimed it.)
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Claim Fully Fired APPROVE")
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=_BASE - datetime.timedelta(days=2),  # well past due; OPEN (PENDING)
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        escalated_1_at=_STAMPED,
        # remind_2_sent_at stays NULL — but remind_2_before is NULL → not a claimable step.
    )

    async with get_sessionmaker()() as s:
        ids = await _due_task_ids(s, _BASE)

    assert task.id not in ids, (
        "fully-fired APPROVE task must NOT be re-claimed (remind_2 tautology closed)"
    )


async def test_due_task_ids_excludes_fully_fired_doc_ack(app_under_test: Any) -> None:
    """A fully-fired OPEN DOC_ACK task is NOT re-claimed (remind_2 + escalate tautologies).

    DOC_ACK is escalate-exempt (seed escalate_1_after=None) → ESCALATE_1 never fires, so escalated_1_at
    stays NULL forever. Its only fireable steps are remind_1 + overdue; with both stamped the task is
    fully fired. The OLD claim re-selected it via BOTH `remind_2_sent_at IS NULL` AND `escalated_1_at
    IS NULL`; the policy-aware claim gates the escalate clause on escalate_1_after IS NOT NULL (false
    for DOC_ACK) and drops the remind_2 clause unless remind_2_before is configured → not claimed.
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Claim Fully Fired DOC_ACK")
    _, task = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=_BASE - datetime.timedelta(days=2),  # well past due; OPEN (PENDING)
        task_type=TaskType.DOC_ACK,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
        # escalated_1_at stays NULL (escalate never fires for DOC_ACK); remind_2_sent_at stays NULL.
    )

    async with get_sessionmaker()() as s:
        ids = await _due_task_ids(s, _BASE)

    assert task.id not in ids, (
        "fully-fired DOC_ACK task must NOT be re-claimed (remind_2 + escalate tautologies closed)"
    )


async def test_due_task_ids_still_claims_pending_steps(app_under_test: Any) -> None:
    """Over-tightening guard: a task with ANY configured, unstamped step IS still claimed.

    Three positive controls on escalate-enabled APPROVE tasks (each isolates one claim reason). These
    pass on BOTH the old and new query — they prove the policy-aware predicate did not drop a task
    that genuinely has a fireable step.
    """
    org_id = await _default_org_id()
    assignee_id = await _seed_user(org_id, display_name="Claim Pending Steps")
    past_due = _BASE - datetime.timedelta(days=2)

    # (a) pending remind_1: only remind_1_sent_at is NULL (remind_1_before=3d → claimable).
    _, task_remind = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=past_due,
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        overdue_notified_at=_STAMPED,
        escalated_1_at=_STAMPED,
    )
    # (b) pending overdue: only overdue_notified_at is NULL (always-on step → claimable).
    _, task_overdue = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=past_due,
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        escalated_1_at=_STAMPED,
    )
    # (c) pending escalate: only escalated_1_at is NULL (APPROVE escalate_1_after=1d → claimable).
    _, task_escalate = await _seed_workflow_objects(
        org_id,
        assignee_id,
        due_at=past_due,
        task_type=TaskType.APPROVE,
        task_state=TaskState.PENDING,
        remind_1_sent_at=_STAMPED,
        overdue_notified_at=_STAMPED,
    )

    async with get_sessionmaker()() as s:
        ids = await _due_task_ids(s, _BASE)

    assert task_remind.id in ids, "pending remind_1 task must still be claimed"
    assert task_overdue.id in ids, "pending overdue task must still be claimed"
    assert task_escalate.id in ids, "pending escalate task must still be claimed"
```

- [ ] **Step 3: Verify the tests are well-formed (local) + reason the red (CI-authoritative)**

Run (from `apps/api`):
```bash
cd apps/api && uv run ruff check tests/integration/test_notification_timer_sweep.py \
  && uv run ruff format --check tests/integration/test_notification_timer_sweep.py \
  && uv run pytest --collect-only tests/integration/test_notification_timer_sweep.py -q
```
Expected: ruff clean; `--collect-only` lists the three new tests (`test_due_task_ids_excludes_fully_fired_escalate_enabled`, `test_due_task_ids_excludes_fully_fired_doc_ack`, `test_due_task_ids_still_claims_pending_steps`) with no import/collection error.

Mutation argument (why the two exclusion tests are RED on the current code): the current `_due_task_ids` claim is `remind_1_sent_at IS NULL OR remind_2_sent_at IS NULL OR overdue_notified_at IS NULL OR escalated_1_at IS NULL`. Both fully-fired tasks have `remind_2_sent_at IS NULL` (never stamped) → claimed → `task.id in ids` → the `not in` assertions FAIL. The positive-controls test passes on both. CI's `integration` job is authoritative for observing red/green; the box is Docker-less so these tests cannot run locally.

- [ ] **Step 4: Commit**

```bash
git add apps/api/tests/integration/test_notification_timer_sweep.py
git commit -m "test(s-claim-filter): claim tests — fully-fired tasks must not be re-claimed

Three integration cases for _due_task_ids: a fully-fired OPEN APPROVE task
and a fully-fired OPEN DOC_ACK task must NOT be claimed (the remind_2 /
escalate tautologies), and pending-step tasks must still be claimed
(over-tightening guard). Red on the current 4-way claim; green after the
policy-aware predicate lands.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Make `_due_task_ids` policy-aware

**Files:**
- Modify: `apps/api/src/easysynq_api/services/notifications/escalation.py` (the `_due_task_ids` `.where(...)` predicate + its docstring; ~lines 145-171).

**Interfaces:**
- Consumes: `Task`, `SlaPolicy` ORM models + `select`/`func` (all already imported in this module).
- Produces: `_due_task_ids(session, now) -> list[uuid.UUID]` with the SAME signature; only the claim predicate changes. `sweep_task_timers` (the sole caller) is unchanged.

- [ ] **Step 1: Replace the claim predicate + update the docstring**

Replace the whole `_due_task_ids` function body's `.where(...)` clause and docstring. The new function:

```python
async def _due_task_ids(session: AsyncSession, now: datetime.datetime) -> list[uuid.UUID]:
    """Fetch ids of open tasks with an active SLA policy and at least one CONFIGURED, unstamped
    timer step.

    Policy-aware claim (S-claim-filter): a step is claimable only when its policy offset is configured
    (non-NULL) AND its stamp is NULL — mirroring ``timer.due_steps``' ``policy.<offset> is not None and
    stamps.<stamp> is None`` gating. This closes the recurring tautology where an UNCONFIGURED step's
    perpetually-NULL stamp re-claimed every open task on every sweep forever: ``remind_2_sent_at`` (the
    seed sets ``remind_2_before=None`` for all types, so REMIND_2 never fires/stamps) and, for
    DOC_ACK/PERIODIC_REVIEW, ``escalated_1_at`` (seed ``escalate_1_after=None`` → ESCALATE_1 never
    fires). OVERDUE has no policy offset (it fires at ``due_at``), so it is gated on its stamp alone.

    ⚠ If a future slice activates remind_2 / escalate_2, BOTH this claim AND ``due_steps`` must learn
    the new step together (they are intentionally symmetric). ``remind_2`` is kept here policy-gated and
    inert — ``remind_2_before IS NOT NULL`` is false for every seeded policy, so it is NOT a tautology
    (unlike the old bare ``remind_2_sent_at IS NULL``); the future "distinct remind_2" slice just sets
    ``remind_2_before`` non-NULL with no claim change.

    This is the COARSE pre-filter only — the precise business-day thresholds stay ``due_steps``' job
    inside the locked per-task txn, so over-claiming (e.g. a pre-due OVERDUE row) is harmless. ``now``
    is unused (the seam for the deferred Option-C threshold-gate)."""
    rows = (
        (
            await session.execute(
                select(Task.id)
                .join(
                    SlaPolicy,
                    (SlaPolicy.org_id == Task.org_id) & (SlaPolicy.task_type == Task.type),
                )
                .where(
                    Task.state.in_(_OPEN),
                    Task.due_at.is_not(None),
                    SlaPolicy.active.is_(True),
                    (
                        (SlaPolicy.remind_1_before.is_not(None) & Task.remind_1_sent_at.is_(None))
                        | (SlaPolicy.remind_2_before.is_not(None) & Task.remind_2_sent_at.is_(None))
                        | Task.overdue_notified_at.is_(None)
                        | (SlaPolicy.escalate_1_after.is_not(None) & Task.escalated_1_at.is_(None))
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
```

- [ ] **Step 2: Verify local gates (ruff + mypy)**

Run (from `apps/api`):
```bash
cd apps/api && uv run ruff check src/easysynq_api/services/notifications/escalation.py \
  && uv run ruff format --check src/easysynq_api/services/notifications/escalation.py \
  && uv run mypy src
```
Expected: ruff clean; `mypy src` reports no new errors (the predicate is a `ColumnElement[bool]`; the signature is unchanged).

Note: the integration tests added in Task 1 cannot run locally (Docker-less box). They go green in CI's `integration` job. Do NOT attempt `pytest -m integration` locally.

- [ ] **Step 3: Commit**

```bash
git add apps/api/src/easysynq_api/services/notifications/escalation.py
git commit -m "feat(s-claim-filter): policy-aware timer-sweep claim

_due_task_ids now claims a task only when it has a configured, unstamped
timer step (each stamp-NULL clause gated on its SlaPolicy offset), mirroring
due_steps' gating. Closes the remind_2_sent_at / DOC_ACK-escalate tautology
that re-claimed every open task on every sweep forever. Query-only; no
migration, no new key, no WORM/authz/web change.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Post-implementation: review + verification (handled by the driving session, not a task)

1. **Focused adversarial review** (ultracode workflow): probe — does the new predicate over- or under-claim any edge (e.g. a task with NO matching SlaPolicy; an inactive policy; a task in `CLAIMED` vs `PENDING`)? Are the two exclusion tests genuinely mutation-distinguishing (claimed by the old query)? Is every assertion run-scoped? Does the kept inert remind_2 disjunct ever become a tautology under any seed?
2. **`diff-critic`** whole-branch pass (the load-bearing-invariant reviewer).
3. **`/pr`** — local gate (ruff + format + `mypy src` + full `-m unit`, none of which exercise the new integration tests) then open the PR; CI's `integration` job is the authoritative green for this slice. Confirm the `migrations` job is green (trivially — no migration).
4. **Live-smoke (optional, populated dev DB if reachable):** read-only sanity that a fully-fired open task is absent from `_due_task_ids` and a pending task is present; a real `sweep_task_timers()` returns a clean `{tasks, steps}`.

## Self-review (plan vs spec)

- **Spec §2 (policy-aware claim, query-only, no migration/key/WORM/authz/web):** Task 2. ✓
- **Spec §3 (the exact predicate, remind_2 kept inert, OVERDUE stamp-only, coarse pre-filter):** Task 2 Step 1 + the docstring. ✓
- **Spec §3 closure argument (fully-fired → not claimed):** Task 1 exclusion tests. ✓
- **Spec §4 (mutation-distinguishing integration cases, run-scoped membership, open-state requirement; merged case 1≡case 3):** Task 1 — two exclusion tests + one positive-controls test; open-state + run-scoped membership enforced in Global Constraints + each test. ✓
- **Spec §6 (over-tightening guard, shared-DB discipline, remind_2 reactivation note):** Task 1 positive-controls test; Global Constraints; Task 2 docstring. ✓
- **Placeholder scan:** none — full code in every code step.
- **Type consistency:** `_due_task_ids(session, now) -> list[uuid.UUID]` signature identical across both tasks and the spec; helper signatures copied verbatim from the test file.
