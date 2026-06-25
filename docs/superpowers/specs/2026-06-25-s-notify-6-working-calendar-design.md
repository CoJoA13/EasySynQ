# S-notify-6 — Business-day escalation SLAs: wire `working_calendar` into the timer sweep (R29 close-out) — design (spec)

> The **last functional half of R29.** S-notify-4 shipped the durable `timer_sweep` Beat and the
> manager-graph escalation recipient resolution (`app_user.manager_id` → QM/OrgRole fallback), but it
> computes reminder/overdue/escalation thresholds with **raw wall-clock `timedelta`s** — a reminder
> "3 days before due" fires across the weekend, an escalation "1 day after due" pings a manager on a
> Saturday. R29 + doc 10 §9.5 require those **before/after offsets to be BUSINESS days** evaluated
> against the org's **`working_calendar`** (skip weekends + holidays). The S-notify-4 history recorded
> this as a "documented R29 reconcile-defer; the offsets are INTERVAL so it slots in with no
> `sla_policy` schema change." This slice builds the missing `working_calendar` entity and wires the
> business-day math in. **BE-only** (the admin editor is deferred — owner call §0). SPEC-FIRST per
> CLAUDE.md; the design was approved before any code and is adversarially spec-validated (§9) before
> the plan.

## 0 · Owner decisions (RESOLVED — ratified 2026-06-25 via AskUserQuestion ×2 + a design approval)

- **D-1 — Slice boundary: BE-only.** **Ratified.** This slice = migration `0067` (`working_calendar`
  table + per-org default seed) + the pure business-day math in `timer.py` + the resolver/wiring in
  `escalation.py` + unit/integration tests. **No endpoint, no `openapi.yaml`, no `apps/web`.** It
  closes R29's functional gap (timers honor weekends/holidays). *Deferred:* a `config.update`-gated
  admin editor (GET/PUT endpoint + a Config-tab section) — its own follow-up, reusing the existing
  `config.update` key (the S-notify-5b Config-tab precedent; **no new permission key**). The
  manager-graph half (S-notify-4) shipped without an editor too; this matches that posture.
- **D-2 — Timezone source: a `timezone` column on `working_calendar`, seeded from `organization.timezone`.**
  **Ratified.** `due_at` is stored as a UTC `timestamptz`, so "is today a weekend/holiday" depends on a
  timezone. The calendar **owns its own** IANA `timezone` (TEXT, default `'UTC'`), seeded =
  `organization.timezone` (which already exists — IANA, default UTC, authoritative for date
  interpretation per R8). Self-contained + future multi-site/multi-calendar ready (the schema already
  permits multiple rows per org). This is **additive** to doc 14's listed `working_calendar` columns
  (the Decisions Register supersedes; noted in §2). *Rejected:* reading `organization.timezone`
  directly (couples the calendar to the org tz) and UTC-only (misclassifies a Fri-evening-UTC due
  date that is Sat local).
- **D-3 — Default-calendar seed values.** **Ratified.** `name='Default'`, `working_days=[1,2,3,4,5]`
  (Mon–Fri, ISO weekday), `holidays=[]`, `timezone=organization.timezone`, `is_default=true`.
- **D-4 — Design approved.** **Ratified.** §1–§8 were presented and approved as-is, including the
  business-day semantics (§4), the partial-unique-default index, and the test isolation approach (§7).
- **D-5 — OVERDUE stays raw at `due_at`; the weekend-overdue root cause is named as R39.** **Ratified
  2026-06-25 (AskUserQuestion, post-spec-validation).** The pre-code refute panel (§9, lens L1) proved
  the design's original OVERDUE rationale **factually false**: there is **no** business-day-snapping
  materialize path — `services/workflow/engine.py::_due_at` computes `now() + timedelta(hours=…)`
  (docstring: *"Wall-clock SLA … No working calendar (deferred, R39)"*) and the seeded
  `default_sla={'hours': 120}` is 120 raw clock-hours, so `due_at` frequently lands on a weekend.
  **Decision:** this slice business-day-shifts **only the reminder/escalation OFFSETS** (which have
  offsets); **OVERDUE has no offset → stays `now >= due_at`, unchanged.** The real fix for a
  weekend-overdue ping is **business-day-snapping `due_at` itself at materialize**, which is **R39**
  (a separate upstream slice) — recorded as a named residual (§8). This **preserves the
  critical-overdue quiet-hours PIERCE** (a critical task overdue on a Saturday can still alert
  immediately) and keeps the slice scoped to offsets. *Rejected:* snapping OVERDUE forward to the next
  working day inside the timer — it would defeat the critical-overdue weekend pierce and is a band-aid
  vs the R39 architecture where a snapped `due_at` makes all three steps honor the calendar with no
  one-off timer hack. doc 10 §9.5 line 543's wording (which lists "overdue" among the
  business-day-evaluated offsets) is **tightened at slice-end** to say overdue has no offset.

## 1 · The gap (live-code grounded)

- `services/notifications/timer.py::due_steps` computes thresholds as `due_at - policy.remind_*_before`
  (BEFORE) and `due_at + policy.escalate_1_after` (AFTER) — **raw `timedelta` arithmetic**, no calendar.
- `services/notifications/escalation.py::process_task_timers` loads the `Task`, the `SlaPolicy`, and the
  `WorkflowInstance`, then calls `due_steps(tpolicy, task.due_at, stamps, now)`. No calendar is resolved.
- There is **no `working_calendar` model, table, or migration.** `grep` confirms zero references outside
  doc 10/14/the register.
- Migration head: **`0066`** (next `0067`). The `sla_policy` offsets are `Interval` columns
  (`remind_1_before`, `remind_2_before`, `escalate_1_after`) — **unchanged** by this slice (the
  business-day refinement reinterprets the stored offset; no `sla_policy` schema change).

## 2 · Schema — migration `0067` (revises `0066_awareness_events`)

New operational table **`working_calendar`** (org-scoped):

| column | type | constraints / seed |
|---|---|---|
| `id` | `UUID` | PK `pk_working_calendar` |
| `org_id` | `UUID` | FK→`organization.id` `ON DELETE RESTRICT`, name `fk_working_calendar_org_id_organization`, NOT NULL |
| `name` | `String` (text) | NOT NULL; seed `'Default'` |
| `working_days` | `JSONB` | NOT NULL; ISO weekday ints (1=Mon..7=Sun); seed `[1,2,3,4,5]` |
| `holidays` | `JSONB` | NOT NULL, `server_default '[]'`; array of `"YYYY-MM-DD"` strings; seed `[]` |
| `timezone` | `String` (text) | NOT NULL, `server_default 'UTC'`; IANA; seed = that org's `organization.timezone` |
| `is_default` | `Boolean` | NOT NULL, `server_default false`; seed `true` |
| `created_at` | `timestamptz` | `server_default func.now()`, NOT NULL |
| `updated_at` | `timestamptz` | `server_default func.now()`, NOT NULL |

- **Note (additive to doc 14):** doc 14 §line 131 lists `working_calendar` as `id, org_id, name,
  working_days, holidays, is_default`. The `timezone` column (D-2) and the timestamps are additive; the
  Decisions Register governs and permits this. doc 14 will be updated in the slice-end doc pass.
- **At-most-one-default-per-org:** partial **unique** index
  `uq_working_calendar_one_default ON working_calendar (org_id) WHERE is_default`. Because it carries a
  `WHERE` predicate it is **migration-managed**: registered in `migrations/env.py._MIGRATION_MANAGED_INDEXES`
  and **absent from the ORM `__table_args__`** (the `ix_task_timer_pending` precedent — Alembic reflects
  predicate/expression indexes, so an ORM copy would phantom-DROP/CREATE and turn the `migrations` job
  red; env.py exclusion silences autogenerate in both directions).
- **App-role grants:** the `0010` `ALTER DEFAULT PRIVILEGES … GRANT` auto-grants full DML to
  `easysynq_app` on every new table. Per the operational-table house style and to support the deferred
  editor, **keep INSERT/SELECT/UPDATE, REVOKE DELETE** (a role-exists `DO $$` block, the 0065 shape —
  but REVOKE only `DELETE`, *not* INSERT/UPDATE which `sla_policy` revoked). This is the notification-
  ledger posture (REVOKE DELETE), not the `sla_policy` SELECT-only posture.
- **Seed:** resilient multi-org loop (`SELECT id, timezone FROM organization`, `scalars`/`.all()`, NOT
  `scalar_one_or_none` — multi-org-safe per the 0062/0065 precedent), one `is_default` row per org with
  the D-3 defaults and `timezone = that org's organization.timezone`.
- **Model:** `db/models/working_calendar.py`, imported in `db/models/__init__.py` (+ `__all__`) — the
  sole place `Base.metadata` is populated; a CREATEd table whose model isn't imported phantom-DROPs in
  `alembic check`.
- **Downgrade:** `op.drop_index("uq_working_calendar_one_default", table_name="working_calendar")`
  **then** `op.drop_table("working_calendar")` — Postgres cascades the index with the table so the
  explicit drop is optional, but the 0066 precedent (`ix_awareness_event_pending` is likewise dropped
  before its table) does it for consistency, so we mirror it. **No inbound FK references
  `working_calendar.id`** → no populated-downgrade abort, no NOT-EXISTS seed-delete guard needed
  (unlike 0065's template DELETE). Clean, data-loss-only-for-the-dropped-table.
- `alembic check` clean; up↔down↔check round-trips on a throwaway PG16 **and** on the populated dev DB
  (`/check-migrations` + a manual 0066↔0067 on the live DB).

## 3 · The `Calendar` value object + pure helpers — `services/notifications/timer.py`

`timer.py` stays **session-free / pure** (unit-testable without a DB — the existing contract). Add:

```python
@dataclass(frozen=True)
class Calendar:
    working_weekdays: frozenset[int]   # ISO 1=Mon..7=Sun
    holidays: frozenset[datetime.date]
    tz: zoneinfo.ZoneInfo

class ThresholdDirection(enum.Enum):
    BEFORE = "before"   # reminders: N business days before due_at
    AFTER = "after"     # escalation: N business days after due_at
```

- `DEFAULT_CALENDAR` (module constant): Mon–Fri, no holidays, UTC — the fail-safe (§5).
- `is_working_day(d: datetime.date, cal: Calendar) -> bool`
  = `d.isoweekday() in cal.working_weekdays and d not in cal.holidays`.
- `shift_business_days(anchor: datetime.date, n: int, direction, cal) -> datetime.date`: step day-by-day
  (±1) from `anchor`, **counting only working days**, stopping when `n` working days have been counted;
  return that date. **The anchor day itself is not counted** (we count steps away from it). `n == 0`
  returns `anchor` unchanged.
- `business_threshold(due_at: datetime.datetime, offset: datetime.timedelta, direction, cal) -> datetime.datetime`:
  1. `local = due_at.astimezone(cal.tz)`
  2. `n = offset.days` (whole business days); `remainder = offset - timedelta(days=n)` (≥0 for our
     positive offsets; zero for the whole-day seeds)
  3. `target_date = shift_business_days(local.date(), n, direction, cal)`
  4. `local_threshold = datetime.combine(target_date, local.time(), tzinfo=cal.tz)` — **preserves
     due_at's local time-of-day** on the shifted date
  5. apply remainder: `local_threshold -= remainder` (BEFORE) / `+= remainder` (AFTER)
  6. return `local_threshold.astimezone(datetime.UTC)`

## 4 · Business-day semantics (the heart — precise, for spec-validation)

- **Reminders fire EARLIER than raw, escalations LATER** — both giving real working-day slack:
  - `remind_1_before = 3 business days`, due **Mon**: raw → Fri; business → **Wed** (skip Sat/Sun
    backward). The assignee gets 3 *working* days of lead time.
  - `escalate_1_after = 1 business day`, due **Fri**: raw → Sat; business → **Mon** (skip the weekend
    forward). No manager ping on the weekend.
  - A `holidays` date inside the span shifts the threshold one more day (skipped like a weekend).
- **OVERDUE is unchanged: `now >= due_at`, always-on, no business-day shift** (D-5). Rationale (the
  TRUTH, corrected after the §9/L1 refute): OVERDUE has **no offset** to convert, so there is nothing
  for this slice to business-day-shift. ⚠ **`due_at` is NOT business-day-clean** — the live
  `engine.py::_due_at` is raw wall-clock (`now() + timedelta(hours=…)`, *"No working calendar (deferred,
  R39)"*; seeded `default_sla={'hours': 120}` = 120 clock-hours), so `due_at` can land on a
  weekend/holiday and OVERDUE will fire `task.overdue` then. That is **the upstream R39 gap** (snap
  `due_at` to a working day at materialize), **not** something this offset-shifting slice fixes; snapping
  OVERDUE inside the timer was rejected (D-5) because it defeats the intended critical-overdue weekend
  pierce. doc 10 §9.5 line 543 (which lists "overdue" among the calendar-evaluated offsets) is tightened
  at slice-end: overdue has no offset; the calendar applies to the before/after offsets and, ultimately,
  to `due_at` via R39. This is a **named residual** (§8), stated honestly — not a false "already
  snapped" claim.
- **Time-of-day** is preserved (the shifted date carries `due_at`'s local time) — the faithful analogue
  of the current exact-`timedelta` wall-clock semantics; least surprising; keeps OVERDUE and the
  reminders coherent.
- `due_steps` signature gains `calendar: Calendar`; thresholds use `business_threshold` for REMIND_1/2
  (BEFORE) and ESCALATE_1 (AFTER); OVERDUE stays `due_at`. **The gating predicate (stamp-null AND
  `now >= threshold`, emitted in chronological order) is byte-identical** — only the threshold
  computation changes. Reminders/escalations stay gated on a configured (non-null) offset.

## 5 · Wiring — `services/notifications/escalation.py`

- `resolve_working_calendar(session, org_id) -> Calendar`:
  `SELECT … WHERE org_id == org_id AND is_default IS TRUE LIMIT 1`; build the pure `Calendar` from the
  row. **Defensive + fail-safe, granular:** **bad individual holiday entries are skipped** (kept-good —
  one unparseable date never discards the whole holiday list); a **structurally-broken `working_days`**
  (empty / not a list of valid ISO 1–7 ints) or an **unknown `timezone`** (`ZoneInfoNotFoundError`) or a
  **missing row** → fall back to `DEFAULT_CALENDAR` (Mon–Fri/UTC/no-holidays) + a `logger.warning`. The
  sweep must **never crash** on calendar data (the future editor accepts config.update-gated input; v1
  input is seed-only, but robustness is free). ⚠ Fail-safe-to-DEFAULT (vs fail-safe-to-no-fire) is
  correct (§9/L3): DEFAULT_CALENDAR is itself Mon–Fri, so the weekend skip is **preserved** even under
  fallback — fallback never *re-enables* weekend firing.
- `process_task_timers`: resolve the calendar **exactly once per call, after the `task is None`
  short-circuit** (one snapshot for all steps; §9/L3 confirms this adds no S-drift-1 stale-map risk, no
  lock-ordering deadlock, no `MissingGreenlet` — it is a non-locking read of a row not in the identity
  map). `cal = await resolve_working_calendar(session, task.org_id)`; pass `cal` into
  `due_steps(tpolicy, task.due_at, stamps, now, cal)`. **Nothing else changes** — `pg_advisory_xact_lock(hashtext(str(task_id)))` →
  `FOR UPDATE SKIP LOCKED` + `populate_existing=True` (the S-drift-1 stale-attr trap) → per-step
  recipient resolution (reminders/overdue via `resolve_recipients`; escalate via
  `resolve_escalation_recipients` + `_recipient_for_user` + the single `TASK_ESCALATED` audit on
  `created_ids`) → stamp+enqueue+audit in **one per-task commit** → fresh session per task. Idempotency
  under `task_acks_late` redelivery + concurrent sweeps is **preserved** (the calendar read is inside
  the same locked txn; it does not touch the stamp/lock invariants).
- `_due_task_ids` prefilter is **unchanged** — it selects open tasks with an active policy and ≥1
  **stamp-null** step (a superset); business-day *timing* is decided post-lock in `due_steps`.
  Reminders firing earlier / escalations firing later never *remove* a task from this stamp-null
  superset, so no task is missed.

## 6 · Invariants & constraints honored

- **N9 (locked):** the sweep only reminds/notifies/escalates — **no auto-decide, auto-reassign, or
  task-state flip.** Unchanged (we only move *when* the existing notifications fire).
- **R38 additive-only:** **no new permission key.** Catalog stays **102**; the
  `test_authz.py`/`test_quality_objectives.py` `== 102` assertions are untouched. (`working_calendar` is
  org-scoped reference data with no PEP gate in v1; a future editor reuses `config.update`.)
- **R53 (outbox) / R32 (failed-delivery operational-only):** unchanged — a failed email never gates a
  transition; this slice touches neither the enqueue savepoint nor the drain.
- **D2/WORM:** no append-only ledger touched; `working_calendar` is mutable operational config (REVOKE
  DELETE only).

## 7 · Tests

> **Harness, RESOLVED (was the §9/L5 open question — confirmed by reading `conftest.py` + the live
> tests):** integration tests run migrations as the **OWNER** (`DATABASE_URL_SYNC` = the container
> superuser, which seeds the `SlaPolicy` rows + the `0067` default calendar for orgs existing at
> migration time) but the app / `get_sessionmaker()` connect as the **non-owner `easysynq_app` role**.
> The app role has broad INSERT (org/user/workflow/task **and `working_calendar`**) but is **SELECT-only
> on `sla_policy`** (0065 REVOKE) and **DELETE-revoked on `working_calendar`** (0067 REVOKE). Three
> consequences drive the test design: **(a)** a freshly-created in-test org has **no `SlaPolicy`** and
> the app role can't insert one → **sweep tests MUST reuse the seeded org (AHT)** + the established
> **pre-stamp-other-steps** workaround (`_seed_workflow_objects` already supports this); **(b)** the
> resolver picks the org's **`is_default`** calendar, and a 2nd `is_default` for AHT violates
> `uq_working_calendar_one_default` → to drive a holiday/tz scenario, **UPDATE AHT's seeded default row
> in place and restore in `finally`** (UPDATE is allowed); **(c)** a test must **never INSERT a
> standalone `working_calendar`** it then needs to clean up — it can't be DELETEd by the app role, and
> its RESTRICT FK then blocks deleting the org (cleanup deadlock → `test_restore` `MultipleResultsFound`
> pollution). So: UPDATE-AHT-and-restore, or a calendar-less isolated org for the fallback path.

**Unit** (`tests/unit/test_notification_timer.py`, extending the existing pure-math suite) — **ALL
business-day correctness concentrates here, where the harness cannot false-PASS:**
- `is_working_day`: a working weekday, a weekend day, a holiday.
- `shift_business_days`: BEFORE/AFTER across a weekend; across a holiday; multi-day; `n==0` identity.
- `business_threshold`: 3 biz-days before a Monday → prior Wednesday; 1 biz-day after a Friday → Monday;
  a holiday in the span adds a day; **time-of-day preserved**; a tz-boundary `due_at` near UTC midnight
  whose local date differs (proves the tz conversion); **a DST-transition anchor** (`combine` +
  `astimezone` across a spring-forward/fall-back boundary stays sane for a 5-min-granularity sweep).
- `due_steps` with a `Calendar`: reminder not-yet-due before the business threshold then due after;
  OVERDUE always-on at `due_at` (**no shift** — D-5); ESCALATE_1 after the business offset; chronological
  order; **stamp-null gating unchanged when a Calendar is passed** (a stamped step never re-fires however
  the calendar moves its threshold); reminder/escalate inert when the offset is NULL.
- `DEFAULT_CALENDAR` shape (Mon–Fri/UTC/no-holidays) as the resolver fallback target.

**Integration** (`tests/integration/test_notification_timer_sweep.py` + `test_notification_escalation.py`)
— **proves the wiring** (DB calendar → sweep), not the math:
- **Anti-tautology (the load-bearing wiring test) — weekend skip, NO mutation, NO holiday:** on AHT
  (seeded `escalate_1_after=1d`), resolve the org's default calendar and build `due_at`/`now` **in that
  calendar's own tz** (the weekday of a calendar date is tz-independent, so this is correct for any
  seeded tz): `due_at` = a **Friday** local; pre-stamp remind+overdue. **Test A:** `now` = **Saturday**
  local (past raw `due_at+1d`=Sat, before business `due_at+1 biz day`=Mon) → assert escalation does
  **NOT** fire (`escalated_1_at` stays NULL, no `task.escalated`). **Test B:** `now` = **Monday** local →
  assert escalation fires (stamp set, `task.escalated`→manager). **Mutation-verify:** Test A must FAIL
  against unmodified `timer.py` (old raw code escalates on Saturday) and PASS against the business code.
- **DB calendar resolution:** UPDATE AHT's default row (e.g. set a `holidays` date + assert a custom
  `timezone` round-trips) → `resolve_working_calendar(s, AHT)` returns the matching `Calendar`; **restore
  in `finally`**. A **calendar-less isolated org** → `resolve_working_calendar` returns `DEFAULT_CALENDAR`
  (no crash); FK-cleanup the org in `finally` (no calendar references it → app-role DELETE works).
- **Idempotent re-sweep** (2nd sweep fires 0 steps) + **2-session `asyncio.gather` concurrency** still
  escalates **exactly once** with a calendar in play (reuse the S-notify-4 pattern, on AHT).
- All assertions **delta/run-scoped**; any AHT mutation is restored in `finally` (the S-risk-1b
  normalize-at-start + restore-on-teardown precedent for a shared singleton).

## 8 · Named residuals (honest — not faked)

- **R39 — business-day-snap `due_at` at materialize** (the upstream root cause of a weekend-OVERDUE
  ping, D-5/§4): `engine.py::_due_at` is raw wall-clock (`now()+timedelta(hours=…)`), so `due_at` can
  land on a non-working day and OVERDUE fires then. This slice business-day-shifts only the *offsets*;
  snapping `due_at` itself is the R39 slice (already flagged in the engine docstring). Until then,
  weekend-OVERDUE is a known, bounded gap (a critical overdue can still pierce — by design).
- **The `working_calendar` admin editor** (GET/PUT + Config-tab UI; reuses `config.update`) — deferred
  this slice (D-1).
- **Claim-threshold-filter tautology** (S-notify-4): `remind_2_sent_at IS NULL` is always true (remind_2
  unused) → every open task re-claimed each sweep (0-step no-op; bounded). Out of scope; named.
- **Distinct `remind_2`** (a real second reminder with its own event_key) — out of scope.
- **`escalate_2`** (reassign-from-pool / flag `NEEDS_ATTENTION`, doc 10 §9.5 E2) — out of scope (N9
  bounds it to non-auto-decide).
- **`capa.overdue`** and other family overdue events — out of scope.
- **Multiple named calendars per org / per-team selection** — v1 uses `is_default` only; the schema
  permits more.
- **Holiday recurrence / bulk import / a holiday-source feed** — v1 is a flat date list.
- **Decisions-Register resolution for the as-built `working_calendar` schema** (slice-end doc action,
  §9/L4): the additive `timezone` column + timestamps extend the register-locked `working_calendar`
  entity (R29). Record the as-built schema at slice-end — amend R29's canonical tokens (or a short
  sub-note) — rather than leaving it as a silent doc-14 edit.

## 9 · Adversarial spec-validation (pre-code) — DONE, findings folded

A 5-lens refute panel + per-finding adversarial verify ran **before any migration** (8 agents). Outcome:
**L2 (migration) / L3 (concurrency) / L4 (constraints) verdicts = design-sound** (nits only); **L1
(bizmath) + L5 (tests) = design-flawed**, all confirmed-real (zero refuted majors). Folded back into the
spec:
- **L1 (MAJOR) → §4 + D-5:** the OVERDUE rationale was factually false (no business-day materialize path;
  `due_at` is raw wall-clock). Corrected to the truth + owner-ratified (keep OVERDUE raw, name R39).
- **L5 (2 × MAJOR) → §7 + the harness box:** "create-own-org + own SlaPolicy" is unworkable (app role
  SELECT-only on `sla_policy`; the `is_default` resolver + the per-org default-unique index + the
  DELETE-revoked `working_calendar` cleanup deadlock). Rewritten to reuse-AHT + pre-stamp + the
  build-dates-in-the-calendar-tz weekend test + UPDATE-AHT-and-restore for holiday/tz.
- **Nits folded:** explicit `drop_index` before `drop_table` (§2); resolve the calendar once after the
  `task is None` short-circuit + granular fail-safe fallback (§5); a DST-transition unit test + a
  mutation-verify of the anti-tautology test + an explicit stamp-gating-unchanged unit assertion (§7);
  the register-resolution slice-end action (§8).
- **L3 confirmed the central invariance claim** (threading the calendar cannot break exactly-once / the
  prefilter superset / stamp gating) and that **fail-safe-to-DEFAULT is correct** (DEFAULT is Mon–Fri →
  the weekend skip survives fallback). **L4 confirmed N9 / R38 (==102) / R53 / R32 clean.**
```
