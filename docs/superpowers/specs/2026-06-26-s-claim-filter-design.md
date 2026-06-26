# S-claim-filter — policy-aware timer-sweep claim

> Design doc. Status: **approved scope (Option A)**, pre-plan.
> Date: 2026-06-26 · Branch: `feat/s-claim-filter` · Migration head: **0067 (unchanged)**.

## 1. Problem

`services/notifications/escalation.py::_due_task_ids` is the coarse pre-filter the escalation
`timer_sweep` Beat runs each cycle: it returns the ids of open tasks the per-task worker
(`process_task_timers`) should then lock + evaluate. Today it claims a task when **any** of four
stamp columns is NULL:

```python
(
    Task.remind_1_sent_at.is_(None)
    | Task.remind_2_sent_at.is_(None)
    | Task.overdue_notified_at.is_(None)
    | Task.escalated_1_at.is_(None)
)
```

Two of those four clauses are **tautologies** given the shipped seed (migration 0065):

- `remind_2_sent_at IS NULL` — **always** true. `remind_2_before` is NULL on every seeded
  `sla_policy` row (one-reminder MVP), so `timer.due_steps` never emits `REMIND_2`, so the
  `remind_2_sent_at` column is **never** stamped → the clause never goes false.
- `escalated_1_at IS NULL` — always true **for DOC_ACK and PERIODIC_REVIEW** (their policies seed
  `escalate_1_after = NULL` — `_NO_ESCALATE_AT_0065`), so `ESCALATE_1` never fires for them and the
  column is never stamped.

**Effect:** every open task with an active SLA policy + a `due_at` is re-claimed on **every** 5-minute
sweep, **forever** — even a long-done, fully-fired task. Each re-claim then runs the full
`process_task_timers` no-op: a `pg_advisory_xact_lock` + a `FOR UPDATE SKIP LOCKED` SELECT + a policy
SELECT + an instance GET + a calendar resolve + a commit — only for `due_steps` to return `[]` and
fire 0 steps. Bounded and harmless per task, but it is the dominant per-sweep cost and grows linearly
with the open-task count. This is the recurring deferred Codex P2 (the "claim-threshold filter"
residual), sharpened by the S-notify-4 live-smoke.

## 2. Scope

**In (Option A — query-only):** make `_due_task_ids` **policy-aware** so a task is claimed only when
it has a *configured* unstamped step. Kills both tautologies → a fully-fired task is no longer
re-claimed.

**Out (named residuals, unchanged):**

- **Pre-due claim churn** — a not-yet-overdue task still matches `overdue_notified_at IS NULL` (it
  genuinely has a pending OVERDUE step) and is claimed each sweep until OVERDUE fires. This is correct
  behaviour, not the tautology; eliminating it is the deferred **conservative-threshold-gate**
  (Option C — needs a provably-conservative wall-clock lower bound because business-day thresholds
  are not exactly SQL-expressible). Not in this slice.
- **Index narrowing** (Option B) — narrowing `ix_task_timer_pending`'s predicate to match. Evaluated
  and **dropped**: the precise claim is a *join* qual (it references `sla_policy`), so the planner
  can't push it into the `task` baserel scan; a stamp-disjunction partial index is only usable if a
  redundant task-only predicate is also added to the query, and even then its marginal benefit over A
  is just "the single claim-query scan is smaller" — A already removes the dominant per-task no-op
  loop. Not worth a migration. Stays a possible v1.x storage tidy.
- Reactivating remind_2 (distinct second reminder); escalate_2; capa.overdue.

**No** migration (head stays 0067), **no** new permission key (catalog 102), **no** WORM/authz touch,
**no** web/openapi change. The change is a single SQLAlchemy `.where(...)` predicate.

`due_steps` / `process_task_timers` / stamping / audit / recipient resolution are all **byte-identical**
— this slice tightens only the pre-filter.

## 3. The change

`_due_task_ids`'s claim disjunction becomes **policy-aware** — each step is claimable only when its
policy offset is configured (non-NULL) **and** its stamp is NULL, mirroring `due_steps`' own
`policy.<offset> is not None and stamps.<stamp> is None` gating:

```python
(
    (SlaPolicy.remind_1_before.is_not(None) & Task.remind_1_sent_at.is_(None))
    | (SlaPolicy.remind_2_before.is_not(None) & Task.remind_2_sent_at.is_(None))
    | Task.overdue_notified_at.is_(None)              # OVERDUE: always-on, stamp-only gate
    | (SlaPolicy.escalate_1_after.is_not(None) & Task.escalated_1_at.is_(None))
)
```

Notes:

- **OVERDUE** keeps a stamp-only gate (no policy offset — it fires at `due_at`), matching `due_steps`
  (`stamps.overdue_notified_at is None`).
- **remind_2 is kept, policy-gated and inert.** `remind_2_before IS NOT NULL` is false for every
  seeded policy → the disjunct is provably never satisfiable today → it is **not** a tautology (unlike
  the old bare `remind_2_sent_at IS NULL`). Keeping it (rather than deleting it) is symmetric,
  mirrors `due_steps`, and means the future "distinct remind_2" slice needs **zero** change to the
  claim — it just sets `remind_2_before` non-NULL.
- The claim remains a **coarse pre-filter**: the precise business-day thresholds
  (`business_threshold` / `is_working_day(now)`) stay `due_steps`' job inside the locked per-task txn.
  Over-claiming (e.g. the pre-due OVERDUE row before its threshold) is harmless — `process_task_timers`
  + `due_steps` no-op correctly. The slice only makes the pre-filter *tighter*, never looser, so it
  cannot drop a task that has a fireable step.

### Closure argument (why A fully closes the named bug)

A fully-fired open task — remind_1 stamped, overdue stamped, escalate stamped-or-unconfigured, remind_2
unconfigured:

| disjunct | evaluates to |
|---|---|
| `remind_1_before≠∅ ∧ remind_1_sent_at=∅` | false (remind_1_sent_at set) |
| `remind_2_before≠∅ ∧ remind_2_sent_at=∅` | false (remind_2_before NULL) |
| `overdue_notified_at=∅` | false (overdue stamped) |
| `escalate_1_after≠∅ ∧ escalated_1_at=∅` | false (escalate stamped, or escalate_1_after NULL) |

→ **not claimed.** The permanent, tautology-driven re-claim is gone. A task is claimed iff it has a
configured, not-yet-fired step.

## 4. Test surface

**Unit:** none new — the change is a DB query predicate; no pure logic moves.

**Integration** — reuse `tests/integration/test_notification_timer_sweep.py` scaffolding (seeded
default-org policy `remind_1_before=3d`/`remind_2_before=None`/`escalate_1_after=1d`-except-DOC_ACK/PR;
`_BASE` = a Wednesday in a seeded `audit_event` partition; pre-stamp columns to control state). Import
`_due_task_ids`. Assert **run-scoped membership** — `task.id in / not in await _due_task_ids(s, _BASE)`
— **never a global count** (the `-m integration` suite shares one DB; other files leave tasks behind).

Cases (each mutation-distinguishing — would be CLAIMED by the old 4-way-tautology query, so the test
**fails on the old code**). Every "fully-fired" task **must be created in an open state**
(`PENDING`/`CLAIMED`) with a non-NULL `due_at` — a `DONE` task is excluded by `Task.state.in_(_OPEN)`
regardless, so it would pass trivially and not distinguish the mutation:

1. **Fully-fired escalate-enabled task** (an open task whose type has a seeded non-NULL
   `escalate_1_after` — i.e. any type except DOC_ACK/PERIODIC_REVIEW; remind_1 + overdue + escalate_1
   all stamped) → **not** claimed. *(old: claimed via the remind_2 tautology.)*
2. **Fully-fired DOC_ACK** (remind_1 + overdue stamped; `escalate_1_after` NULL in seed) → **not**
   claimed. *(old: claimed via the escalate + remind_2 tautologies.)*
3. **remind_2-only-NULL task** (all live stamps set; only `remind_2_sent_at` NULL) → **not** claimed.
   *(the headline bug; old: claimed.)*
4. **Still-claimed sanity** — pending remind_1 (remind_1_sent_at NULL) → claimed; pending overdue
   (overdue_notified_at NULL) → claimed; pending escalate (escalate-enabled type, escalated_1_at NULL)
   → claimed. Guards against the predicate over-tightening.

**Live-smoke** (populated dev DB, Docker-less box so CI is authoritative for the integration leg):
run a real `sweep_task_timers()` → clean `{tasks, steps}`; spot-check that a fully-fired open task is
absent from `_due_task_ids` and a genuinely-pending task is present. (No EXPLAIN needed — the index is
unchanged, so no plan-usability question.)

## 5. Process

Ultracode is on. Proportionate to a ~6-line predicate change with a well-understood invariant:

1. **Spec** (this doc) → write-plan.
2. **TDD** — add the four integration cases red-first against the new query.
3. **Focused adversarial review** — a small spec/diff-validation fan-out probing: does the new
   predicate over- or under-claim any edge? are the tests genuinely mutation-distinguishing
   (fail on the old code)? is every assertion run-scoped? Plus the `diff-critic` whole-branch pass.
4. **`/pr`** — local gate = ruff + `mypy src` + targeted unit (the box is Docker-less, so the
   17-failure baseline + integration are CI-authoritative); `/pr` runs the full `-m unit`; CI runs
   `migrations` (trivially green — no migration) + `integration` (the real gate for this slice).

## 6. Risks / edge cases

- **Over-tightening** (dropping a task that has a fireable step) — guarded by case 4 + the fact that
  the new predicate is a strict subset only of the *tautology* disjuncts; every configured step keeps
  its own claim term. `process_task_timers` + `due_steps` remain the authoritative per-step decision,
  so even a hypothetical pre-filter miss would only *delay* (next sweep), never mis-fire.
- **Shared-DB test pollution** — all assertions are membership on this run's task ids, never counts.
- **remind_2 reactivation** — the inert policy-gated disjunct already handles it; the future slice
  sets `remind_2_before` non-NULL with no claim change. Documented in the code comment.
