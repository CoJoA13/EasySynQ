# S-remind2 — a distinct second (final) reminder

> Design doc. Status: **approved scope**, pre-plan.
> Date: 2026-06-26 · Branch: `feat/s-remind2` · Migration head: **0067 → 0068**.

## 1. Problem

The escalation `timer_sweep` (S-notify-4) was specced with two pre-due reminders (REMIND_1 and
REMIND_2) but shipped **one** — `remind_2_before` is NULL for every seeded `sla_policy`, so
`timer.due_steps` never emits REMIND_2. The MVP dropped the second reminder because **both reminders
emitted under the same event key `task.due_soon`**, and the in-app notification dedup is a partial
unique index on `(recipient_user_id, task_id, event_key) WHERE task_id IS NOT NULL` (the
`_enqueue_one` `ON CONFLICT DO NOTHING`). So REMIND_2's insert collided with REMIND_1's existing row →
`"deduped"` → **the second reminder delivered nothing** (a real notification was never created, no
email sent). The S-notify-4 final review caught this (the original test pre-stamped REMIND_2 and
false-passed).

Everything needed for a working second reminder already exists: the `task.remind_2_sent_at` stamp
column, `SlaPolicy.remind_2_before`, the `due_steps` REMIND_2 branch, and — since S-claim-filter — the
**policy-gated claim disjunct** `(SlaPolicy.remind_2_before IS NOT NULL AND remind_2_sent_at IS NULL)`
in `_due_task_ids`, kept inert specifically so this slice is a seed flip with no claim change. The
single missing piece is a **distinct event key** for REMIND_2 so the dedup no longer collapses it.

## 2. Scope

**In:** a real second (final) reminder, fired 1 business day before `due_at`, under a new event key
`task.due_final`, for all task types.

**Out (named, not faked):**
- `escalate_2` (a second escalation tier).
- An admin editor for the SLA offsets — `sla_policy` stays seed-managed / SELECT-only for the app role.
- The **no-runtime-`sla_policy`-seeder gap** — a brand-new org created *after* this migration gets no
  `sla_policy` rows at all (the only seeder is the 0065 migration, which runs for orgs existing at
  migration time; migration 0002 creates the `DEFAULT` org so it is covered). Under single-org D1 a
  second org is not created in practice; fixing new-org policy seeding is a separate concern.
- Any change to REMIND_1, OVERDUE, ESCALATE_1, the claim query, the timer math, the FE, or authz/WORM.

**No** new permission key (notifications use none), **no** WORM/authz touch, **no** web/openapi/FE
change (the SPA renders the stored title/body + the notification *class* for styling — it does not
switch on `event_key`; a new key with the same class renders identically).

## 3. The change (5 touchpoints)

### 3.1 `services/notifications/constants.py`
Add the event key + its variable whitelist (identical var set to the other task events):
```python
EVENT_TASK_DUE_FINAL = "task.due_final"
# in VARIABLE_WHITELIST:
EVENT_TASK_DUE_FINAL: _TASK_EVENT_VARS,
```

### 3.2 `services/notifications/classes.py`
Map the new key to **ACTION_REQUIRED** (same as `task.due_soon`). It is a *pre-due* reminder, so it
MUST NOT be CRITICAL — CRITICAL is the class that the escalation-pierce flag uses to pierce quiet
hours (reserved for `task.overdue` / `task.escalated`). The final reminder follows the same
digest/quiet-hours behaviour as the first reminder.
```python
"task.due_final": NotificationClass.ACTION_REQUIRED,
```

### 3.3 `services/notifications/escalation.py`
Split the REMIND branch in `process_task_timers` so each reminder emits under its own key:
- `TimerStep.REMIND_1` → `EVENT_TASK_DUE_SOON` (unchanged).
- `TimerStep.REMIND_2` → `EVENT_TASK_DUE_FINAL` (new).

The two branches are otherwise identical (resolve recipients, emit per recipient, set `exists`).
Keep the stamping logic byte-identical (`should_stamp = exists or attempted == 0`; the stamp column
is selected by `_STAMP_COL[step]`, already mapping REMIND_2 → `remind_2_sent_at`).

### 3.4 Migration 0068
Two seed operations, mirroring the 0065 patterns:

**a. Seed the global `task.due_final` template** (the `notification_template` table has no `org_id`,
so this is global and CI-covered). Raw INSERT with the partial-index conflict target:
```
INSERT INTO notification_template
  (id, event_key, locale, version, is_effective, in_app_title, in_app_body, email_subject, email_body)
VALUES (:id, 'task.due_final', 'en', 1, TRUE, :in_app_title, :in_app_body, :email_subject, :email_body)
ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING
```
Content (parallel to `task.due_soon`, "final reminder" framing; the `{{…}}` variables are the same
`_TASK_EVENT_VARS` + the `| date` filter):
- `in_app_title`: `Final reminder: {{subject.identifier}}`
- `in_app_body`: `{{task.action_expected}} {{subject.identifier}} — "{{subject.title}}" (due {{task.due_at | date}})`
- `email_subject`: `[EasySynQ] Final reminder: {{subject.identifier}} {{subject.title}}`
- `email_body`:
  ```
  Hi {{recipient.first_name}},

  Reminder — a task in EasySynQ is due very soon: {{task.action_expected}} {{subject.identifier}} — "{{subject.title}}".

    Due by: {{task.due_at | date}}

  Open in EasySynQ: {{deep_link}}

  Manage notifications: {{prefs_link}}
  ```

**b. Set `remind_2_before` on the seeded policies** (org-scoped rows; the `DEFAULT` org created in
0002 makes this exercised by a full `upgrade head`, but seed-DATA effects are confirmed via the
integration test + live-smoke per the 0065/0067 precedent):
```
UPDATE sla_policy SET remind_2_before = INTERVAL '1 day' WHERE remind_2_before IS NULL
```
(`WHERE remind_2_before IS NULL` keeps it idempotent and avoids clobbering any future value.)

**Downgrade:** `UPDATE sla_policy SET remind_2_before = NULL` (reverses the seed intent) + delete the
template **guarded against the RESTRICT FK** (`notification.template_id` → `notification_template`):
```
DELETE FROM notification_template
WHERE event_key = 'task.due_final'
  AND NOT EXISTS (SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)
```
(The S-notify-4 lesson: an unguarded `DELETE FROM notification_template` aborts on a *populated* DB
because of the RESTRICT FK; a fresh-DB round-trip is green and hides it.)

No DDL (no new columns/tables/enums) → `alembic check` is trivially clean; no ORM change.

### 3.5 Existing-test audit (`test_notification_timer_sweep.py`)
Enabling `remind_2_before` makes REMIND_2 newly-live for every task in this suite. REMIND_2 fires when
`remind_2_sent_at IS NULL` AND `now >= due_at − 1 business day` AND `now` is a working day. Audit each
scenario:
- A test that **pre-stamps** `remind_2_sent_at` (to isolate another step) is unaffected — REMIND_2 is
  gated off.
- A test whose `due_at` is far enough in the future that the REMIND_2 threshold (`due − 1bd`) is past
  `_BASE` is unaffected by timing, but any **stale comment** asserting "remind_2 is off (NULL
  remind_2_before)" must be corrected to the real reason (future threshold), and — where a test does
  NOT pre-stamp `remind_2_sent_at` and its `now` now passes `due − 1bd` — it must either pre-stamp
  `remind_2_sent_at` (to keep isolating its target step) or assert the now-expected `task.due_final`
  notification. Concretely review at least `test_remind_fires_once` (due_at = `_BASE + 2d` → REMIND_2
  threshold `due − 1bd` is in the future at `_BASE`, so it does not fire; fix the docstring) and every
  scenario that leaves `remind_2_sent_at` unstamped.

## 4. Tests

**Unit** (`tests/unit/test_*timer*`, pure `due_steps`): with `TimerPolicy(remind_1_before=3d,
remind_2_before=1d, …)` and a working-day calendar, assert REMIND_2 is **absent** before its
threshold and **present** at/after `due − 1 business day` (and that REMIND_1 and REMIND_2 are distinct
steps). Mutation-distinguishing against `remind_2_before=None` (REMIND_2 never appears).

**Integration** (`test_notification_timer_sweep.py`, testcontainer; the migrated DB has
`remind_2_before=1d` on the `DEFAULT` org from 0068): create an OPEN task (e.g. APPROVE) whose
`due_at` places **both reminder thresholds** in the past at the fixed `_BASE` Wednesday — e.g.
`due_at = _BASE - 2d`, so REMIND_1 threshold (`due − 3bd`) and REMIND_2 threshold (`due − 1bd`) are
both ≤ `_BASE`. **Pre-stamp `overdue_notified_at` and `escalated_1_at`** (the existing isolation
pattern) so only the two reminders can fire — otherwise `due_at ≤ _BASE` would also trip OVERDUE and
muddy the assertion. Run a single `sweep_task_timers`; assert **two distinct notifications** exist for
the assignee — exactly one `task.due_soon` and exactly one `task.due_final` — and that
`remind_1_sent_at` and `remind_2_sent_at` are both stamped. This is mutation-distinguishing: under the
old shared-key path the second insert deduped → only one notification. (Pin `_BASE` to the existing
fixed Wednesday in a seeded `audit_event` partition; assertions are run-scoped to this task's ids per
the shared-DB discipline.)

**Live-smoke** (populated dev org, Docker-less box → CI is authoritative for the `migrations`/
`integration` legs): apply 0067→0068 on the populated DB, confirm `sla_policy.remind_2_before` is now
`1 day` for the real org, and a real `sweep_task_timers()` over a suitably-dated task emits a distinct
`task.due_final` in-app row (+ email queued) alongside `task.due_soon`.

## 5. Process

1. Spec (this doc) → write-plan.
2. TDD — unit (`due_steps` REMIND_2) then the integration two-notification proof; migration with the
   round-trip + guarded downgrade.
3. Adversarial review — `migration-reviewer` (the 0068 round-trip + the guarded template delete +
   the idempotent UPDATE) and `diff-critic` whole-branch (the dedup-distinctness claim; the
   existing-test audit completeness; no REMIND_1/OVERDUE/ESCALATE regression).
4. `/pr` — local gate ruff + `mypy src` + `pytest -m unit`; CI `migrations` (DDL round-trip) +
   `integration` (the seed-data behaviour + the two-notification proof) are authoritative.

## 6. Risks / edge cases

- **Existing-test regressions from a newly-live REMIND_2** — the §3.5 audit is the mitigation; the
  full `-m integration` (CI) is the backstop (a missed pre-stamp surfaces as an unexpected
  `task.due_final` count). This mirrors the S-notify-6 "enabling the calendar changed existing tests"
  lesson.
- **REMIND_2 vs OVERDUE proximity** — `remind_2_before = 1 business day` keeps the final reminder
  strictly before `due_at`; OVERDUE fires at `due_at`. With `due_at` snapped to a working day (R55)
  and the 1-business-day offset, the two never coincide.
- **Template missing at emit** — `_enqueue_one` already returns `"no_template"` and the caller does
  not stamp (retries next sweep); the migration seeds the template, and the partial-unique conflict
  target makes the seed idempotent.
- **The ruff `--fix` PostToolUse hook** strips a just-added unused import before its using code lands
  (F401→F821) — add the using code first (the recurring trap).
