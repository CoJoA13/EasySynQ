---
description: Scaffold + checklist for wiring a NEW notification event key end-to-end (constant · variable whitelist · event class · template seed · distinct-key dedup · dispatch · tests), with the two silent-failure gotchas inline
allowed-tools: Read, Edit, Write, Glob, Grep, Bash
---

Wire a new notification event END-TO-END. The event being added is named in $ARGUMENTS (e.g. `capa.overdue`, `task.due_final`). Notification events are TEXT keys, not PG enums (spec §3.1), so a *new event* needs no enum migration — but it has **five wiring touchpoints**, and **two of them fail SILENTLY** (a count-only test still passes while the notification renders blank or never sends). Do all of the following, in order, and pin every claim to the real code — don't trust this file's line numbers if the source has moved.

All paths are under `apps/api/src/easysynq_api/services/notifications/` unless noted.

### 0. Decide the shape first (ask the owner if unclear)
- **What triggers it** — a timer step (reuse the `escalation.py` sweep), a NEW Beat sweep (the `services/vault/review.py::sweep_reviews` precedent — for a subject with no `Task` row, e.g. a CAPA stage), or an inline service emit.
- **Who receives it** — an assignee, a role (`users_with_roles`), a manager graph, or a floor fallback (QM). Decide the **invalid-recipient fallback** now (see §5).
- **Its event class** — does it pierce quiet hours? (only `CRITICAL` does; a pre-due reminder must NOT.)
- **Its dedup discriminator** — see §2. If it can fire twice to the same recipient about the same subject, it needs a key (or a discriminator column) the first firing doesn't share.

### 1. The event-key constant — `constants.py`
Add `EVENT_<NAME> = "<dotted.key>"`. ⚠ **Distinct-key rule (silent-failure #1):** if this is a *second* notification to the same recipient about the same subject as an existing event (a "final" reminder/escalation), it MUST use a **distinct** key — the partial-unique dedup index `uq_notification_dedup_task (recipient_user_id, task_id, event_key)` (migration `0063`; awareness has `uq_notification_dedup_awareness`) collapses a repeat under the SAME key onto the first row and delivers nothing. (This shipped twice: S-remind2, S-escalate2.)

### 2. The variable whitelist — `constants.py::VARIABLE_WHITELIST`
Add `EVENT_<NAME>: <var set>`. ⚠ **Silent-failure #2:** the renderer only substitutes whitelisted slots — a new key with **no** `VARIABLE_WHITELIST` entry renders every `{{...}}` slot BLANK (a broken-but-present notification a count test passes). For a task-lifecycle event the set is `_TASK_EVENT_VARS`; for an **operational/admin** event do NOT include `subject.title`/`subject.identifier` (admins hold no `document.read` — the `system.email_delivery_failed` precedent). The whitelist set MUST match the `{{...}}` slots in the seeded template body (§4).

### 3. The event class — `classes.py::_EVENT_CLASS`
Add `"<dotted.key>": NotificationClass.<CLASS>`. An UNMAPPED key falls back to `ACTION_REQUIRED` with only a `logger.warning` — wrong cadence and wrong **pierce** behavior. `CRITICAL` = immediate + pierces quiet hours (overdue/escalation/integrity); `ACTION_REQUIRED`/`AWARENESS` = digest-able (reminders, FYIs). A pre-due reminder as `CRITICAL` emails at 3am; a real escalation as `ACTION_REQUIRED` is suppressed into a digest. (Note `capa.overdue` is already `CRITICAL`-mapped at ~`classes.py:46` but otherwise unwired — if that's $ARGUMENTS, §1/§2/§4/§5 are the remaining work.)

### 4. The template seed — a NEW migration (no enum, just data)
Seed a GLOBAL `notification_template` (no `org_id`) so the seed is exercised by a fresh-DB `alembic upgrade head` (CI-covered). Mirror the `0069` shape exactly:
- `INSERT INTO notification_template (id, event_key, locale, version, is_effective, in_app_title, in_app_body, email_subject, email_body) VALUES (:id, '<key>', 'en', 1, TRUE, …) ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING` (idempotent).
- `downgrade()`: `DELETE FROM notification_template WHERE event_key = '<key>' AND NOT EXISTS (SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)` — `notification.template_id` is a **RESTRICT** FK; an unguarded delete aborts a *populated* downgrade (fresh-DB CI is blind to it).
- ⚠ A per-org seed (`UPDATE sla_policy …` / `SELECT id FROM organization`) is **NOT** exercised by the `migrations` CI job (fresh DB = zero orgs) → it must be **live-smoked on a populated DB** (`docker cp` into `easysynq-api-1:/migrations/versions/` then `docker exec … alembic upgrade head`; DB-only, never a live sweep).
- New migration: `down_revision` = current head (`alembic heads` / CLAUDE.md Current-status); head moves by exactly one. If it ALSO adds a column (a stamp/offset), mirror it in the ORM or `alembic check` phantom-DROPs it — run the `migration-reviewer` agent + `/check-migrations`.

### 5. The emit/dispatch + recipient resolution
- Emit at the trigger site with `event_key=EVENT_<NAME>` (see the `escalation.py` `due_steps` dispatch loop, ~line 350+ — each step branch passes its own distinct key).
- ⚠ **Recipient resolver must not silently drop:** a non-empty list of all-**invalid** ids (inactive / cross-org / no email) → `attempted == 0` → the sweep stamps the step done → notification lost (the S-escalate2 Codex P1). Fall to a **floor** (e.g. the QM role) on "no VALID recipient", not just on an empty list. Filter cross-org holders via `org_id` at `_recipient_for_user`.
- ⚠ **Claim↔fire symmetry (if it's a timer step):** the coarse SQL claim `escalation.py::_due_task_ids` must stay a strict **SUPERSET** of `timer.py::due_steps`' firing condition (it may delay, never drop). Gate each stamp-NULL disjunct on its configured offset being non-NULL (`SlaPolicy.<offset>.is_not(None)`) — an ungated disjunct re-claims every fully-fired task forever (the S-claim-filter tautology).
- ⚠ **Audit once-per-event:** if this writes an `AuditEvent`, guard `session.add(...)` behind `if created_ids:` (a new send), never on a pure `deduped` sweep (R4-1).

### 6. Tests
- A **unit** test for the class (`test_notification_classes.py`) and any pure step math (`test_notification_timer.py`).
- An **integration** test that proves the WIRING + is **mutation-distinguishing** (fails against the pre-change code). Traps:
  - `audit_event` is monthly-partitioned (only `2026-06/07/08` seeded) → pin the test clock to a seeded month (`_BASE = 2026-06-24`).
  - business-calendar sweeps make `datetime.now(UTC)`-based tests **weekday-flaky** → pin to a fixed **Wednesday**, build `due_at`/`now` in the resolved calendar's own tz.
  - The shared-DB `-m integration` suite → assertions **run-scoped / delta-based**, **self-provide preconditions** (delete leaked SYSTEM-scoped role grants), reuse the default org (a 2nd `Organization` trips `scalar_one`), keep the `app_under_test` fixture.
  - ⚠ Do NOT gate on the full local `-m integration` run (~54 env/pollution false-fails on this box) — run the SCOPED file + a cross-file ordering proof; the 4 CI shards are authoritative (`docs/dev-workflow.md`).

### 7. Verify + review
- `cd apps/api && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -m unit` (fast, clean locally).
- `/check-migrations` for the round-trip; if a column was added, the `migration-reviewer` agent.
- Run the **`notification-wiring-reviewer`** agent on the branch diff — it is pre-loaded with exactly these traps — then the whole-branch `diff-critic`.

Then summarize the five touchpoints you touched (constant · whitelist · class · template seed · dispatch+recipients) + the test deltas, and note which seed effects are CI-covered vs live-smoke-only. Do NOT commit unless asked.
