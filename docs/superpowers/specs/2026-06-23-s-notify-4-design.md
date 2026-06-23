# S-notify-4 — escalation timers: reminders + overdue + escalate-to-manager (Notification family, slice 4)

> **Date:** 2026-06-23 · **Branch:** `feat/s-notify-4` · **Type:** BE-only (migration)
> **Predecessor:** S-notify-3a (the digest engine — per-class cadence, quiet hours + the **critical-class
> pierce**, R54, migration 0064) and S-notify-3b (the preference matrix UI). This slice supplies the
> **events** that finally make the 3a quiet-hours pierce reachable, and the durable `timer_sweep` that
> drives reminders/overdue/escalation off task due dates.

## 1 · Context & goal

doc 10 **§9.5** specs escalation as the durable `timer_sweep`: a Beat that, off a task's `due_at` + an
`SlaPolicy`, sends pre-due **reminders**, marks/notifies **overdue**, and **escalates** an overdue task to
the assignee's manager (QM fallback) — auditing the escalation. The machinery to *deliver* a
critical-class event that pierces quiet hours shipped in 3a; **nothing emits `task.overdue` today**, so the
pierce is built-but-unreachable, and tasks silently rot past their due date with no reminder or escalation.

This slice ships the **MVP escalation loop** (owner-scoped): **remind → overdue → escalate-to-manager**,
idempotent and audited. It is deliberately *not* the whole of §9.5 (see §8 deferrals).

### Decomposition position (Notification family)

| Slice | Scope | Status |
|---|---|---|
| 1 — S-notify-1 | BE spine + email (R53, mig 0063) | ✅ `766dc55` |
| 2 — S-notify-fe | SPA bell + center | ✅ `6893c3e` |
| 3a — S-notify-3a | Digest engine (R54, mig 0064) | ✅ `93b8a57` |
| 3b — S-notify-3b | Preference matrix UI | ✅ `9d03390` |
| **4 — this spec** | **Escalation timers: reminders + overdue + escalate-to-manager (BE, migration 0065)** | **now** |
| 5 | Awareness events + Health delivery-failure panel + SSE | deferred |

## 2 · Binding constraints

- **BE-only.** Adds **migration 0065** (head `0064` → `0065`). No web change (slice 5 surfaces the
  escalation/Health UI). No openapi change (no new HTTP endpoint — the sweep is internal).
- **No new permission key** (catalog stays **102**, R38) — the sweep is system-driven; the emitted
  notifications are authenticated-self reads (the 3a posture).
- **R29 (locked):** escalate-to target = the assignee's `app_user.manager_id`; when null, fall back to the
  **`QMS Owner`** role (there is **no "Quality Manager" seeded role** — the Mara/QM persona maps to
  `QMS Owner`, pinned at `0038:45`). Resolved via the authz-layer `role`/`role_assignment`, **never**
  `org_role` (RACI reference data, not PDP-wired).
- **N9 (locked, §9.5):** the sweep **only reminds / notifies / surfaces** — it never auto-decides a
  quality outcome and (this slice) never auto-reassigns. `escalate_2` (reassign-from-pool / NEEDS_ATTENTION)
  is deferred.
- **Wall-clock for now:** timers are evaluated against UTC wall-clock; the `working_calendar` business-day
  refinement (R29) is deferred (§8), and the `SlaPolicy` offsets are modelled as `INTERVAL`s so the
  calendar slots in later **with no schema change** (a documented R29 reconcile-defer).
- **Family migration trap (S-notify-1 lesson):** `0065` adds a **new table** (`sla_policy`) → `0010`'s
  `ALTER DEFAULT PRIVILEGES … GRANT … DELETE` auto-grants the app role all DML on it → the migration must
  **REVOKE** to the intended posture (app role: SELECT on `sla_policy`; it is admin-seeded reference data).

## 3 · The as-built integration points (verified)

- **`Task`** (`db/models/workflow.py:143`): columns `id, org_id, instance_id, stage_key, assignee_user_id`
  (nullable FK), `candidate_pool` (JSONB), **`type` (`TaskType`)**, `action_expected`, `state`
  (`TaskState`, default `PENDING`), **`due_at`** (nullable tz), `client_token`. **No `created_at`, no
  `subject_type`** on the task (subject lives on `WorkflowInstance` via `instance_id`).
- **`TaskState`** (`_workflow_enums.py:67`): `PENDING, CLAIMED, DONE, SKIPPED, ESCALATED, EXPIRED`. **Open
  (timer-eligible) = `PENDING`, `CLAIMED`**; terminal = `DONE, SKIPPED, EXPIRED`. (`ESCALATED` exists but
  this slice does **not** flip task state — see §4.4.)
- **`TaskType`** (`_workflow_enums.py:52`): `APPROVE, REVIEW, PERIODIC_REVIEW, AUDIT_TASK, FINDING_ACK,
  CAPA_STAGE, CAPA_ACTION, VERIFY, MR_INPUT, MR_ACTION, DCR_TRIAGE, DOC_ACK`. **The `SlaPolicy` keys on
  `TaskType`** (on the task directly — join-free; more granular than subject_type; "task-kind" per the
  owner decision).
- **Enqueue:** `_enqueue_one(session, *, instance, task, subject, recipient, due_at, org_enabled,
  org_pierce, now, event_key)` (`dispatch.py:78`) is already **event-key-generic** — it renders the
  template, looks up the digest class via `class_of(event_key)`, applies email-eligibility +
  quiet-hours/pierce, inserts the in-app `Notification` + the conditional `NotificationEmail`. The public
  `enqueue_task_notifications` only *hardcodes* `EVENT_TASK_ASSIGNED` + forces the assignee-pool. So slice 4
  adds thin wrappers over `_enqueue_one` (§4.3).
- **Event classes** (`classes.py`): `task.due_soon` → `ACTION_REQUIRED` (exists); `task.overdue` →
  `CRITICAL` (exists, **in the pierce set**); **`task.escalated` is net-new** → add one line, `CRITICAL`.
- **`audit_event`** (`db/models/audit_event.py`): write a row with `event_type`, `object_type`, `object_id`,
  `actor_type`, `actor_id` (nullable), `scope_ref`, `after` (JSONB); the `prev_hash`/`row_hash`/`chained_at`
  are **NULL until the single-threaded `audit.chain_link` Beat stamps them** (R12) — so a sweep just
  `session.add(AuditEvent(...))` with null hashes, like every other writer. A system-driven write sets
  `actor_id=NULL` + a system `actor_type` (verify the `ActorType` enum's system value).
- **Role resolver:** `users_with_roles(session, …)` (`workflow/repository.py:56`) returns org-scoped user
  ids holding a named `role`. The QM fallback resolves `"QMS Owner"`.
- **Sweep template:** mirror the **3a digest sweep** (`digest.py`: per-unit advisory lock + `FOR UPDATE
  SKIP LOCKED` + fresh session per unit + an idempotent stamp) and the existing `services/vault/review.py`
  / `services/ack/sweep.py` overdue/claim patterns.

## 4 · Design

### 4.1 · `sla_policy` (new table)

Org-scoped reference data, one row per `(org_id, task_type)`:

| column | type | notes |
|---|---|---|
| `id` | uuid pk | |
| `org_id` | uuid FK organization | |
| `task_type` | `task_type` enum | UNIQUE `(org_id, task_type)` |
| `remind_1_before` | `INTERVAL` null | reminder #1 lead before `due_at` (null = off) |
| `remind_2_before` | `INTERVAL` null | reminder #2 lead before `due_at` (null = off) |
| `escalate_1_after` | `INTERVAL` null | escalate #1 lag after `due_at` (null = off) |
| `active` | bool, default true | |
| `created_at` / `updated_at` | tz | |

ORM in `db/models/sla_policy.py` (+ imported in `db/models/__init__.py` — the phantom-DROP trap). The
migration **seeds** one active row per `TaskType` with sane wall-clock defaults (e.g. `remind_1_before =
3 days`, `remind_2_before = 1 day`, `escalate_1_after = 1 day`; tune per type — DOC_ACK/REVIEW may differ),
using the resilient name-agnostic org lookup (`SELECT id FROM organization`, the 0053/0062 precedent), and
**REVOKEs** the app-role DML that `0010` auto-grants (SELECT-only). A task whose `type` has no active
policy row gets **no timers** (explicit opt-in by seeding).

### 4.2 · Task timer-state (additive columns on `task`)

Four nullable `timestamptz` columns + a sweep index:
`remind_1_sent_at`, `remind_2_sent_at`, `overdue_notified_at`, `escalated_1_at`.

These are the **idempotency guard** (a step with a non-null stamp never re-fires) and the record of what
fired. The ORM additions mirror existing nullable columns. A **partial index** backs the sweep claim:
`ix_task_timer_pending ON task (due_at) WHERE state IN ('PENDING','CLAIMED') AND due_at IS NOT NULL` —
migration-managed (added to `env.py::_MIGRATION_MANAGED_INDEXES` + absent from the ORM, the S-notify-3a
`ix_notification_digest_pending` lesson).

### 4.3 · The enqueue wrappers (`dispatch.py`)

Reuse `_enqueue_one` (event-key-generic) under the same best-effort SAVEPOINT shape, but parameterised:

- `enqueue_due_reminder(session, instance, task, *, event_key, now)` — to the **assignee** (resolve via the
  existing `resolve_recipients(task)`), `event_key ∈ {task.due_soon, task.overdue}`.
- `enqueue_escalation(session, instance, task, recipients, *, now)` — to a **specific** recipient set (the
  manager/QM users), `event_key = task.escalated`.

Both run the full class-aware pipeline → `task.overdue`/`task.escalated` (CRITICAL) pierce quiet hours when
the org flag is on; `task.due_soon` (ACTION_REQUIRED) bundles/holds per the user's prefs. Three `en`
notification **templates** are seeded (`task.due_soon`, `task.overdue`, `task.escalated`) — `render()`
looks up by `event_key`, so a missing template logs + drops the email (the slice MUST seed them).

### 4.4 · The `timer_sweep` Beat (`tasks/notifications.py` → `easysynq.notifications.timer_sweep` @ 300 s)

Registered in the Beat schedule (`tasks/app.py`) + asserted in the task-registration unit test (the
phantom-name trap). Mirrors the digest sweep's safety **verbatim**:

1. **Claim:** `SELECT … FROM task JOIN sla_policy USING (task type) WHERE state IN ('PENDING','CLAIMED') AND
   due_at IS NOT NULL AND policy.active AND (remind_1_sent_at IS NULL OR remind_2_sent_at IS NULL OR
   overdue_notified_at IS NULL OR escalated_1_at IS NULL)` — `FOR UPDATE SKIP LOCKED`, a bounded batch.
   (A coarse claim — any un-stamped step on an open due-dated task with a policy; the per-task processor is
   the precise per-step *threshold* gate. Keeping the threshold math out of the claim SQL keeps it simple
   and index-friendly.)
2. **Per task** (fresh session per unit + a **per-task `pg_advisory_xact_lock`** so two overlapping sweeps
   can't double-fire): recompute the four step thresholds from the policy
   (`remind_1 = due_at − remind_1_before`, `remind_2 = due_at − remind_2_before`, `overdue = due_at`,
   `escalate_1 = due_at + escalate_1_after`); for each step where `now ≥ threshold` **and** the stamp is
   null, in **chronological order** (remind_1, remind_2, overdue, escalate_1):
   - `remind_1` / `remind_2` → `enqueue_due_reminder(event_key=task.due_soon)` → stamp `remind_*_sent_at`.
   - `overdue` → `enqueue_due_reminder(event_key=task.overdue)` → stamp `overdue_notified_at`. **(This is
     the event that makes the 3a pierce reachable.)**
   - `escalate_1` → resolve recipients (`resolve_escalation_recipients(task)`, §4.5);
     `enqueue_escalation(event_key=task.escalated)` to them; **write a `TASK_ESCALATED` `audit_event`**
     (§4.6); stamp `escalated_1_at`. The enqueue + stamp + audit commit in the **same per-task txn** (the
     stamp is the idempotency key — a crash before commit leaves no notification and no stamp → retried).
3. **No task-state mutation:** the slice does **not** flip `state → ESCALATED` (that touches the workflow
   engine's state machine + the assignee's actionable-list queries — out of scope and risk-bearing). The
   `escalated_1_at` stamp is the "already escalated" marker; surfacing escalated tasks in the UI is slice 5.

Each step is independent and idempotent; a task can fire multiple steps across sweeps as its thresholds
pass. The reaper/lock-liveness concern is moot (the sweep holds no long lease — each task is its own
advisory-xact-lock'd txn).

### 4.5 · `resolve_escalation_recipients(task) → list[user_id]` (`services/notifications/recipients.py`)

1. Load the assignee (`task.assignee_user_id`; if the task is pool-only with no assignee, the escalation
   targets the **pool's** members' managers? — **No:** for the MVP, a task with no `assignee_user_id`
   escalates to the **QM fallback** directly, avoiding a pool fan-out; documented).
2. `manager_id = assignee.manager_id` → if set, return `[manager_id]`.
3. Else return `users_with_roles(session, "QMS Owner")` (org-scoped). If that is also empty, log
   `escalation.no_recipient` and **still stamp** `escalated_1_at` (don't re-attempt forever) — the overdue
   notification already reached the assignee; the escalation is best-effort.

### 4.6 · The escalation audit (`audit_event`)

On `escalate_1`, `session.add(AuditEvent(org_id, occurred_at=now, actor_id=NULL, actor_type=<system>,
event_type=TASK_ESCALATED, object_type=<task>, object_id=task.id, scope_ref=<task/instance ref>,
after={"escalated_to": [user_ids], "via": "manager"|"qm_fallback", "due_at": …}))` — hashes left NULL for
the `audit.chain_link` Beat (R12). **`event_type += TASK_ESCALATED`** is an additive `ALTER TYPE … ADD
VALUE` (sourced from the ORM `EVENT_TYPE_VALUES`, the 0011 precedent); **`object_type`** reuses an existing
task/workflow audit-object value if one exists, else adds one additively (verify in the plan). Only
`escalate_1` is audited — reminders and the overdue-to-assignee notification are operational, recorded by
the notification rows + the task stamps, not the WORM chain.

## 5 · Files

| File | Action |
|---|---|
| `db/models/sla_policy.py` | Create (+ register in `db/models/__init__.py`) |
| `db/models/workflow.py` | Modify — 4 timer-stamp columns on `Task` |
| `db/models/_workflow_enums.py` / `_audit_enums.py` | (no enum change for task; `EventType += TASK_ESCALATED` lives in the audit enums) |
| `services/notifications/classes.py` | Modify — `task.escalated` → CRITICAL |
| `services/notifications/constants.py` | Modify — the `task.due_soon`/`task.overdue`/`task.escalated` key constants |
| `services/notifications/dispatch.py` | Modify — `enqueue_due_reminder` + `enqueue_escalation` wrappers |
| `services/notifications/recipients.py` | Modify — `resolve_escalation_recipients` |
| `services/notifications/timer.py` (or `escalation.py`) | Create — the pure threshold/step logic + `bundle`-style per-task processor |
| `tasks/notifications.py` + `tasks/app.py` | Modify — register `timer_sweep` @ 300 s |
| `migrations/versions/0065_*.py` | Create — `sla_policy` (+ REVOKE) + 4 task columns + the partial index + `EventType` ALTER + seeds (3 templates, the per-type policies) |
| `migrations/env.py` | Modify — add `ix_task_timer_pending` to `_MIGRATION_MANAGED_INDEXES` |

## 6 · Testing

- **Unit** (pure, no DB): the step-threshold math (`remind_1/2`, `overdue`, `escalate_1` from a policy +
  `due_at`); the policy lookup; `resolve_escalation_recipients` (manager set → `[manager]`; manager null →
  QM-role users; both empty → `[]` + the stamp-anyway path).
- **Integration** (testcontainers): a task with `due_at` + a seeded policy →
  (a) before `remind_1` → no notification; (b) past `remind_1` → exactly one `task.due_soon`; **re-run the
  sweep → no duplicate** (the stamp); (c) past `due_at` → a `task.overdue` (+ assert it's CRITICAL-class /
  pierces); (d) past `escalate_1` with a manager set → the manager gets `task.escalated` + a
  `TASK_ESCALATED` audit_event with the right `object_id`/`after`; (e) manager null → the `QMS Owner`
  user(s) get it; (f) a **2-session `asyncio.gather` concurrency test** that the per-task advisory lock
  makes a double-overlapping sweep escalate **once**; (g) a terminal task (`DONE`) past every threshold →
  **no** timers.
- **Migration:** `/check-migrations` round-trip + a live check that the `sla_policy` grants are SELECT-only
  (the REVOKE actually bit — the S-notify-1 lesson; a clean `alembic check` does NOT prove the grant
  posture) and the seeds land.
- **Review:** `migration-reviewer` + `diff-critic` + an opus whole-branch; Codex (this is migration- +
  worker-adjacent — the engine class where Codex earns its keep). Watch the recurring worker traps
  (idempotency under `task_acks_late` redelivery; the advisory-lock concurrency; fresh-session-per-unit).

## 7 · Risks & mitigations

| Risk | Mitigation |
|---|---|
| Double-send under overlapping sweeps / Celery redelivery | Per-task `pg_advisory_xact_lock` + the stamp written in the same txn as the enqueue (the digest-sweep template); the concurrency test gates it. |
| A notification template missing → email silently dropped | The migration seeds `task.due_soon`/`task.overdue`/`task.escalated` `en` templates; a test asserts each renders. |
| `0010` default-privilege grant masks the intended SELECT-only on `sla_policy` | Explicit `REVOKE` in the migration + a live grant check (not just `alembic check`). |
| New `event_type`/`object_type` enum value not mirrored / wrong-token | Source the value from the ORM `*_VALUES`; additive `ALTER TYPE`; round-trip; the bare-token CHECK lesson doesn't apply (no CHECK here) but the env.py partial-index exclusion does. |
| Flipping `state → ESCALATED` breaks the assignee's actionable queries | **Not done** — the slice uses the `escalated_1_at` stamp only; state-machine integration is deferred. |
| Sweep starves under a huge open-task set | Bounded batch + `SKIP LOCKED` + the partial index; the sweep is resumable (the stamp is the progress marker). |

## 8 · Out of scope (named, not faked)

- **`escalate_2`** — reassign-from-pool / flag `NEEDS_ATTENTION` (the riskier auto-action; N9-sensitive).
- **`working_calendar`** — business-day SLAs (R29). This slice is wall-clock; the `INTERVAL` offsets are
  designed so the calendar slots in later with no schema change (a documented R29 reconcile-defer).
- **`capa.overdue`** — CAPA-stage SLAs (a separate event source off `capa_stage` due dates).
- **The escalation/Health UI + SSE** — slice 5 surfaces escalated tasks + the delivery-failure panel.
- **Flipping the task `state` to `ESCALATED`** + the assignee-list integration — deferred (§4.4).
- **Per-type policy tuning UI / admin editing of `sla_policy`** — seeded defaults only this slice.
