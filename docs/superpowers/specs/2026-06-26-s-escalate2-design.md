# S-escalate2 — a second (final) escalation tier

**Date:** 2026-06-26
**Slice:** `s-escalate2` (branch `feat/s-escalate2`)
**Scope:** BE-only · migration **0069** (`0068 → 0069`) · NO new permission key (catalog stays 102) · NO WORM/authz change · NO web/FE · NO OpenAPI change
**Builds on:** S-notify-4/6 (the timer sweep + business-day offsets), S-claim-filter (#296, the policy-aware claim), S-remind2 (#298, the distinct-event-key precedent)

---

## 1. Summary

The escalation timer-sweep has exactly **one** escalation tier today: `ESCALATE_1` fires `task.escalated`
to the assignee's manager (→ QMS Owner fallback) **+1 business day** after a task's due date. This slice
adds a **second (final) escalation tier**: when an escalation-enabled task is *still* unactioned at
**+3 business days** past due, fire a **distinct** `task.escalated_final` notification to **Top Management**
(→ QMS Owner floor), reusing the existing task-timer machinery end-to-end.

The seam is already reserved in code: `escalation.py` (the `_due_task_ids` docstring) explicitly names
`escalate_2` as "a future tier that must teach both the claim and `due_steps` together." This slice is the
clean continuation of the machinery refined across S-notify-6 → S-claim-filter → S-remind2.

### Policy decisions (owner-ratified)

- **Tier-2 recipient:** the seeded `"Top Management"` permission role (resolved by `Role.name` via
  `users_with_roles`, owned by migration 0038, R39) → if no holder, fall back to `"QMS Owner"` so it
  always delivers. **No grand-manager walk** — `app_user.manager_id` is unpopulated in practice (no
  writer exists), so a manager's-manager hop would be dead code that always falls through. Intentionally
  skipped (the manager-graph writer is a separate, named-not-faked deferral).
- **Tier-2 timing:** **+3 business days** after due (= 2 business days after the tier-1 manager/QM
  escalation). Seeded as `INTERVAL '3 days'`, interpreted as 3 *business* days by `business_threshold`
  exactly like `escalate_1_after`'s `INTERVAL '1 day'`.
- **Carve-out preserved:** DOC_ACK and PERIODIC_REVIEW never escalated at tier-1
  (`escalate_1_after = NULL`, the 0065 `_NO_ESCALATE_AT_0065` set), so the seed `UPDATE` is gated on
  `escalate_1_after IS NOT NULL` and they get **no** tier-2 either.

---

## 2. Current-state facts (grounded, with refs)

- **`due_steps` (pure step-math, `timer.py:161`)** returns the chronological steps whose threshold has
  passed and whose stamp is NULL. Today: `REMIND_1` (−3bd), `REMIND_2` (−1bd), `OVERDUE` (at due, raw),
  `ESCALATE_1` (+1bd). The universal firing pattern for an offset step is
  `policy.<offset> is not None AND stamps.<stamp> is None AND now_is_working AND now >= business_threshold(due_at, <offset>, <DIRECTION>, calendar)`.
  `ESCALATE_1` uses `ThresholdDirection.AFTER` and is the template `ESCALATE_2` copies — **no new
  threshold math is needed** (`business_threshold` is reused unchanged).
- **The policy-aware claim (`_due_task_ids`, `escalation.py:150`)** is a 4-way OR over configured-and-
  unstamped steps; it MUST stay **symmetric** with `due_steps` (the claim is a superset of `due_steps`'
  firing condition — it can only DELAY, never drop, a real notification). The docstring mandates a new
  tier teach both together.
- **`_STAMP_COL` (`escalation.py:43`)** maps each `TimerStep` → Task stamp column.
- **The dispatch loop (`escalation.py:319`)** is `if REMIND_1/REMIND_2 … elif OVERDUE … else # ESCALATE_1`.
  The `ESCALATE_1` branch resolves recipients, emits per-recipient, and writes **one** `TASK_ESCALATED`
  `AuditEvent` only when ≥1 genuinely-new row was created (R4-1, exactly-once-per-escalation), with
  `after = {task_id, escalated_to, via, due_at}` and `scope_ref = str(task.id)`.
- **`resolve_escalation_recipients` (`escalation.py:55`)** = assignee's manager (active, same-org,
  non-self) → else `users_with_roles(org, ["QMS Owner"])` → else `[]`. `_recipient_for_user`
  (`escalation.py:86`) rebuilds + re-filters (cross-org/inactive) each id at emit time.
- **Event keys are plain TEXT** (`notification.event_key`, `constants.py` docstring) — no enum migration
  for a new key. The **dedup index** `uq_notification_dedup_task (recipient_user_id, task_id, event_key)
  WHERE task_id IS NOT NULL` (0063) is why a 2nd escalation under the *same* `task.escalated` key would
  collapse and deliver nothing — hence a **distinct** key (the S-remind2 lesson for REMIND_2).
- **`"Top Management"` is a real seeded `Role.name`** (migration 0038, resolved by `Role.name`; the same
  second-tier-authorization pattern 0053/0054 use). `users_with_roles(org, ["Top Management"])` returns
  its holders today with **zero schema change** (excludes `managed_by`-scoped grants, which is desired).
- **`VARIABLE_WHITELIST` (`constants.py:33`)** — a new event key absent here makes the renderer
  **silently drop all template variables** (broken-but-present; the S-remind2 trap). `_TASK_EVENT_VARS`
  is the shared task-lifecycle variable set, reused by every `task.*` event.
- **`classes.py:44`** maps `task.escalated → CRITICAL` (pierce set; default mode IMMEDIATE → pierces
  quiet hours).
- **Audit-test assertions are key-scoped** (`ae.after.get("via")`, `… in ae.after.get("escalated_to")`)
  — no whole-dict equality — so adding a `tier` key to the `after` payload is **additive-safe**.

---

## 3. The change-set

### 3.1 Migration 0069 (`0069_escalate2_final_tier`, down_revision `0068_remind2_final_reminder`)

**DDL (real, unlike the pure-seed 0068):**
- `op.add_column("sla_policy", sa.Column("escalate_2_after", sa.Interval(), nullable=True))`
- `op.add_column("task", sa.Column("escalated_2_at", sa.DateTime(timezone=True), nullable=True))`

**Seed (data):**
- Global `task.escalated_final` template, via raw `INSERT … ON CONFLICT (event_key, locale) WHERE
  is_effective DO NOTHING` (no `org_id`; copy the 0068 task.due_final shape, swap key + bodies). Because
  it is global, a fresh-DB `upgrade head` exercises this seed.
- `UPDATE sla_policy SET escalate_2_after = INTERVAL '3 days' WHERE escalate_2_after IS NULL AND
  escalate_1_after IS NOT NULL` (idempotent; preserves the DOC_ACK/PERIODIC_REVIEW carve-out). Exercised
  on `upgrade head` via the 0002 DEFAULT org, but the **per-org effect is NOT covered by the migrations
  CI job** (fresh CI DB row count) → live-smoke on the populated DB.

**Downgrade (reverse order, populated-DB-safe):**
- `UPDATE sla_policy SET escalate_2_after = NULL` (unguarded — an UPDATE, not a delete).
- Guarded template `DELETE … WHERE event_key = 'task.escalated_final' AND NOT EXISTS (SELECT 1 FROM
  notification n WHERE n.template_id = notification_template.id)` (the RESTRICT-FK guard; 0068 precedent).
- `op.drop_column("task", "escalated_2_at")`; `op.drop_column("sla_policy", "escalate_2_after")`.

**ORM mirrors (REQUIRED — else `alembic check` phantom-DROPs → migrations CI red):**
- `sla_policy.py`: `escalate_2_after: Mapped[datetime.timedelta | None] = mapped_column(Interval,
  nullable=True)` (after `escalate_1_after`).
- `workflow.py` (Task): `escalated_2_at: Mapped[datetime.datetime | None]` (after `escalated_1_at`).

The `ix_task_timer_pending` partial index keys only on `due_at` + `state` (not stamp columns), so the new
stamp needs **no index change** (Option-B index narrowing remains a separate, deferred residual).

### 3.2 Pure step-math (`timer.py`)

- `TimerStep.ESCALATE_2 = "escalate_2"` (StrEnum).
- `TimerPolicy`: add `escalate_2_after: datetime.timedelta | None`.
- `TimerStamps`: add `escalated_2_at: datetime.datetime | None`.
- `due_steps`: add a 5th branch **after** `ESCALATE_1`, `ThresholdDirection.AFTER`, `now_is_working`-gated:
  `if policy.escalate_2_after is not None and stamps.escalated_2_at is None and now_is_working and now >=
  business_threshold(due_at, policy.escalate_2_after, ThresholdDirection.AFTER, calendar): steps.append(TimerStep.ESCALATE_2)`.
- **Frozen-dataclass ripple:** the new fields are added **without defaults** (so a forgotten wiring site
  fails loudly rather than silently disabling the step). This breaks **positional** constructors — the
  unit fixture `NONE = TimerStamps(None, None, None, None)` and any positional `TimerPolicy(...)` — which
  are updated in one pass. `escalation.py` constructs both with keywords (safe).

### 3.3 Orchestrator (`escalation.py`)

- `_STAMP_COL[TimerStep.ESCALATE_2] = "escalated_2_at"`.
- `_due_task_ids`: add a 5th claim disjunct
  `(SlaPolicy.escalate_2_after.is_not(None) & Task.escalated_2_at.is_(None))` — configured-offset-gated
  (adds **no** S-claim-filter-style churn; symmetric with the new `due_steps` step).
- Build `TimerPolicy(... escalate_2_after=policy.escalate_2_after)` and
  `TimerStamps(... escalated_2_at=task.escalated_2_at)`.
- Dispatch loop: convert the final `else:  # TimerStep.ESCALATE_1` into
  `elif step is TimerStep.ESCALATE_1:` and add `else:  # TimerStep.ESCALATE_2`.
- New resolver `resolve_escalation_2_recipients(session, task) -> tuple[list[uuid.UUID], str]`:
  `tm = await users_with_roles(session, task.org_id, ["Top Management"])`; if `tm` →
  `return tm, "top_management"`; else
  `return await users_with_roles(session, task.org_id, [_QM_ROLE]), "qm_fallback"`.
  (Returns `via` directly so the branch needs no second query; a module constant
  `_TOP_MGMT_ROLE = "Top Management"` mirrors `_QM_ROLE`.)
- `ESCALATE_2` branch: mirror the `ESCALATE_1` emit/stamp/audit shape but
  (a) recipients + `via` from `resolve_escalation_2_recipients`,
  (b) `event_key = EVENT_TASK_ESCALATED_FINAL`,
  (c) the `AuditEvent` keeps `event_type = TASK_ESCALATED`, `scope_ref = str(task.id)`, with
  `after = {task_id, escalated_to, via, due_at, "tier": 2}`. Reusing `TASK_ESCALATED` **avoids an
  `ALTER TYPE event_type`**; the `tier` discriminator distinguishes the two audits. Add `"tier": 1` to the
  existing `ESCALATE_1` audit `after` for symmetry (additive-safe — tests are key-scoped). The existing
  `should_stamp` / three-case idempotency logic is unchanged.

### 3.4 Event wiring

- `constants.py`: `EVENT_TASK_ESCALATED_FINAL = "task.escalated_final"` **and**
  `VARIABLE_WHITELIST[EVENT_TASK_ESCALATED_FINAL] = _TASK_EVENT_VARS` (omitting the whitelist silently
  drops vars). Add the code that references the constant in the same pass as the import (the ruff
  `--fix` F401 hook strips a not-yet-used import).
- `classes.py`: `"task.escalated_final": NotificationClass.CRITICAL` (pierces quiet hours, same as
  `task.escalated` — a final escalation must pierce).

---

## 4. Known blast-radius (the S-remind2 lesson, repeated)

Enabling `escalate_2_after` makes the new claim disjunct **live**, which re-claims any escalate-enabled
task whose `escalated_2_at` is NULL:

1. **`test_due_task_ids_excludes_fully_fired_escalate_enabled`** (S-claim-filter) stamps an APPROVE
   task's 4 steps and asserts `task.id NOT IN _due_task_ids`. The 5th disjunct now re-claims it → the
   assertion flips. **Fix:** stamp the 5th step (`escalated_2_at = _STAMPED`) in that fixture.
2. **The DOC_ACK exclusion test is unaffected** — the carve-out keeps `escalate_2_after = NULL` for
   DOC_ACK, so the new disjunct (`escalate_2_after IS NOT NULL & …`) is false there.
3. **The S-remind2 distinct-reminder test is unaffected.**
4. **The unit `NONE = TimerStamps(None, None, None, None)` positional fixture** gains a 5th `None`.

**Refined timing check:** at the suite clock `_BASE = Wed 2026-06-24 10:00` with `due_at = _BASE − 2d`
(Mon 06-22), escalate_2's +3bd threshold is **Thu 06-25 > Wed** → tier-2 does **NOT** fire at `_BASE`, so
the existing tier-1 escalation tests (which assert `audit_count == 1` etc.) keep passing without
pre-stamping. **The implementer must still walk every escalation test** and confirm none uses a clock
≥ +3bd (which would now also fire tier-2); pre-stamp `escalated_2_at` where any does. (This is the
"walk ALL timer tests" discipline the S-remind2 final review applied.)

---

## 5. Tests

**Unit (`test_notification_timer.py`):**
- Extend `POLICY` with `escalate_2_after` (e.g. 3d) and update the positional `NONE` fixture.
- Ordering `[REMIND_1, REMIND_2, OVERDUE, ESCALATE_1, ESCALATE_2]` with `ALL_DAYS` (raw-equivalence).
- A `MON_FRI` business-day case: the +3bd threshold lands on the correct working day; fires only past it.
- Stamp-gating: `escalated_2_at` set ⇒ `ESCALATE_2` not returned.
- Null-offset-disables: `escalate_2_after = None` ⇒ `ESCALATE_2` never returned.
- `now_is_working` gate: a non-working `now` suppresses `ESCALATE_2`.

**Integration (`test_notification_timer_sweep.py`):**
- Add an `escalated_2_at` pre-stamp kwarg to `_seed_workflow_objects`.
- **Tier-2 wiring test:** pin a clock where +3bd has passed (e.g. `due_at = Mon 06-22`, `now = Fri 06-26`,
  a working day in the seeded 2026-06 audit partition); seed a `"Top Management"` role holder; isolate by
  pre-stamping steps 1–4; assert `task.escalated_final` delivered to the Top Management holder +
  `escalated_2_at` stamped + a `TASK_ESCALATED` audit with `after["tier"] == 2` and
  `after["via"] == "top_management"`. **Mutation-verify** it fails against the old code.
- **QMS-floor fallback test:** no Top Management holder → recipients = QMS Owner, `via == "qm_fallback"`.
- Update `test_due_task_ids_excludes_fully_fired_escalate_enabled` to stamp all **five** steps; add a
  positive control that a pending-escalate_2 task IS claimed.
- Honour the `_BASE` / audit-partition / run-scoped `_count_*` / delta-based discipline (shared session
  DB); a render-asserting test (not just a count) guards the whitelist silent-drop.

---

## 6. Explicitly NOT in scope

- No new permission key (catalog stays 102) · no WORM/authz change · no web/FE · no OpenAPI change.
- No `app_user.manager_id` writer (grand-manager stays dead, intentionally) — separate deferral.
- No `escalate_3`, no Option-C pre-due threshold-gate, no Option-B index narrowing, no `capa.overdue`,
  no working-calendar depth, no R48 include_subprocesses. (All remain named residuals.)

---

## 7. Verification

- `/check-migrations` (DDL round-trip up↔down + `alembic check` on a throwaway PG16).
- Local `cd apps/api && uv run pytest -m unit` (full) + `sg docker -c "cd apps/api && uv run pytest -m integration"`.
- `/check-api` (ruff + format + mypy-strict + unit) + `/check-contracts` (no OpenAPI change expected).
- **Real-stack migration live-smoke:** `docker cp` 0069 into `easysynq-api-1`, `alembic upgrade head` then
  `downgrade -1` then `upgrade head` against the populated `easysynq` DB (role `easysynq`) — confirm the
  two columns add/drop cleanly, the global template seeds, and all escalate-enabled `sla_policy` rows get
  `escalate_2_after = 3 days` while DOC_ACK/PERIODIC_REVIEW stay NULL. **DB-only — NO live
  `sweep_task_timers`** (would fire real reminders/emails).
- Pre-PR: `diff-critic` + `migration-reviewer` + an opus whole-branch review; fold confirmed findings.

---

## 8. Process

Brainstorm (owner chose escalate_2; tier-2 = Top Management → QMS floor; +3bd) → this spec → writing-plans
(task breakdown) → subagent-driven build (fresh implementer + task-reviewer each; migration via
`migration-reviewer`) → final whole-branch review (opus + diff-critic) → green local gate → `/pr` →
local `-m integration` + live-smoke.
