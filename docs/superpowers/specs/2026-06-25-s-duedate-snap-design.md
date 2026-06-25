# S-duedate-snap — Snap a task's `due_at` to a working day at materialize (R29 weekend-overdue close-out, R55) — design (spec)

> **The un-numbered residual S-notify-6 AND S-notify-7 both deferred.** S-notify-6 wired the
> `working_calendar` into the timer **OFFSETS** (reminders/escalation skip weekends + holidays) but
> deliberately KEPT **OVERDUE raw** (`now >= due_at`, no offset) because the root cause is upstream:
> `due_at` itself is raw wall-clock (`services/workflow/engine.py::_due_at` = `now() + timedelta(hours=…)`;
> seeded `default_sla={'hours':120}` = 120 clock-hours) and frequently lands on a weekend, so an
> unshifted OVERDUE fires `task.overdue` (escalation-class, quiet-hours-pierce-eligible) on a Saturday —
> which **doc 10 §9.5 forbids** ("timers do not fire on non-working days"). S-notify-6's as-built note
> named the fix: *"snapping `due_at` itself to a working day at materialize"* — and corrected the
> engine docstring's stale `R39` mis-citation (R39 is the Audits/CAPA decision) to an **un-numbered**
> deferral. **This slice numbers it R55 and ships it.** SPEC-FIRST per CLAUDE.md; the design is owner-
> ratified (§0) and adversarially spec-validated (§9) before the plan.

## 0 · Owner decisions (RESOLVED — ratified 2026-06-25 via AskUserQuestion ×4)

- **D-1 — Register number + snap rule = R55, forward-only, preserve time-of-day.** **Ratified.** A
  materialized `due_at` whose **date (evaluated in the org `working_calendar`'s tz)** is a non-working
  day (weekend or holiday) is snapped **FORWARD to the next working day, preserving its local
  time-of-day**; a `due_at` already on a working day is **unchanged** (exact instant preserved). New
  decision **R55** (next free; current max R54). *Rejected:* normalizing the snapped time to a fixed
  business hour (changes existing due-time semantics for the `now+hours` engine path); snapping
  *backward* (shortens the SLA window — makes a task overdue earlier than intended).
- **D-2 — Scope = ALL FIVE materialize sites (uniform).** **Ratified.** Every site that stores a
  `Task.due_at` snaps, so every stored due date lands on a working day and no future site silently
  regresses: the **workflow engine** (`_materialize_stage`), the **DOC_ACK** sweep override, the
  **PERIODIC_REVIEW** sweep override, **MR_ACTION** spawn, and **MR_INPUT** cadence. (The brief named
  only the first three; the two Management-Review sites were discovered during scoping — see §1.) All
  12 `TaskType`s have a seeded `SlaPolicy` (reminders+overdue for all; escalation for all but
  DOC_ACK/PERIODIC_REVIEW), so **any** task type can fire a weekend OVERDUE — uniform coverage is the
  only complete fix. *Rejected:* the 3 brief-named only (leaves MR weekend-overdue); now+duration only
  (leaves PERIODIC_REVIEW + MR).
- **D-3 — PERIODIC_REVIEW: snap the task `due_at`, accept the `review_state` badge divergence.**
  **Ratified.** `review.py:175` deliberately anchors the PERIODIC_REVIEW task `due_at` on
  `next_review_due`'s org-midnight to align with the document's **derived** `review_state` badge
  (`review_state(next_review_due, today)` flips "overdue" at org-tz midnight of `next_review_due`).
  This slice snaps the **task** `due_at` forward; the **stored `next_review_due` is unchanged**, so the
  badge still flips on the calendar due date (possibly a weekend) and may **lead the working-day
  notification by 1–2 days**. This split is **intentional and documented**: the badge is a calendar
  fact ("this review is past due"); the notification respects working days per §9.5. *Rejected:*
  leaving PERIODIC_REVIEW unsnapped (a §9.5 violation persists); snapping the stored `next_review_due`
  too (a larger change to the cadence/confirm-reset recompute path, out of scope).
- **D-4 — No backfill: new-only, NO migration.** **Ratified.** Pure compute change at the materialize
  sites; **migration head stays 0067**. Existing OPEN tasks keep their (possibly weekend) `due_at` —
  they fire at most one weekend overdue then resolve naturally (a small, transient residual, §8).
  *Rejected:* a one-shot data migration snapping every open task's weekend `due_at` (adds a migration +
  Python date math in alembic + a populated-DB downgrade guard for a transient benefit).
- **D-5 — Date-anchored due dates build in the CALENDAR's tz (the TZ-1 resolution).** **Ratified
  2026-06-25 (AskUserQuestion, post-spec-validation).** Spec-validation (§9 lens L-tz, MAJOR) proved
  the design had a real frame-mismatch: the three **date-anchored** sites (`vault/review.py`,
  `mgmt_review/spawn.py`, `mgmt_review/cadence.py`) build `due_at` at **midnight in
  `easysynq_org_timezone`** (an **env** var, default `UTC`, changeable only by redeploy) while the snap
  + the timer judge working-days in the **`working_calendar.timezone`** (a **DB** column set by the
  setup wizard / S-notify-7 editor). These are **independent sources** (the dev box already diverges:
  env `UTC`, calendar `America/Chicago`) — under divergence the snap re-judged an operator's date in a
  different frame (e.g. pushed an operator-set working Monday to Tuesday, or missed a genuine weekend).
  **Decision:** the three date-anchored sites now **resolve the calendar first and build `due_at` at
  midnight in `cal.tz`**, then snap with that same calendar — so a single business-day frame (the
  calendar's, the S-notify-7-designated "the calendar owns the business-day tz" authority) governs
  build + snap + timer. **Under a correctly-configured deployment (env tz == calendar tz) this is a
  ZERO behavior change.** The two **instant** sites (engine `now+hours`, DOC_ACK `now+days`) have no
  "calendar day" intent — they are true instants and just snap in `cal.tz` (no build-tz change).
  *Rejected:* keep building in env-tz + require env==calendar config (a divergent config silently
  shifts dates — the default-reachable bug); unify `_org_tz()`/`today_org()`/`review_state` onto the
  calendar tz (a cross-cutting R8-date-display change — its own slice, named §8).
- **D-6 — `review_state` badge stays env-tz; the divergence is accepted (extends D-3).** **Ratified.**
  `vault/review.py::review_state` (and `today_org()`) still derive from `easysynq_org_timezone`, while
  the PERIODIC_REVIEW task `due_at` is now built+snapped in `cal.tz` (D-5). Under env==calendar config
  there is no divergence; under a divergent config the badge↔notification gap grows by the tz offset on
  top of the D-3 snap lead. Accepted (the badge is a calendar fact; the notification respects the
  business calendar). Fully closing it = the D-5-rejected `_org_tz()` unification (§8).

## 1 · The gap (live-code grounded)

- `services/workflow/engine.py::_due_at(stage, default_sla)` returns `_now() + timedelta(hours=…)` —
  **raw wall-clock**, no calendar (its docstring already names this as the deferred residual). The
  result is stored as `Task.due_at` in `_materialize_stage` (line 208) for every engine-materialized
  task (APPROVE/REVIEW/CAPA_STAGE/CAPA_ACTION/VERIFY/AUDIT_TASK/FINDING_ACK/DCR_TRIAGE/…).
- `services/ack/sweep.py` computes `due_at = _now() + timedelta(days=ack_due_days)` (line 174) and
  PATCHES it onto the DOC_ACK task (line 225 `update(Task)…values(due_at=…)`) — raw, can land on a
  weekend.
- `services/vault/review.py` computes `due_at = combine(next_review_due, midnight, _org_tz())` (line
  180) and PATCHES it onto the PERIODIC_REVIEW task (line 184) — a calendar date that can be a weekend.
- `services/mgmt_review/spawn.py::_action_due_at(due_date)` = `combine(due_date, midnight, org_tz)`
  (line 51), stored on the MR_ACTION task (line 107) — an operator-set date that can be a weekend.
- `services/mgmt_review/cadence.py` computes `due_at = combine(due, midnight, _org_tz())` (line 268),
  stored on the MR_INPUT task (line 282) — a cadence date that can be a weekend.
- **NOT a snap site:** `services/workflow/service.py::instantiate_approval` creates its APPROVE task
  with **no `due_at`** (NULL → excluded by the timer claim's `Task.due_at IS NOT NULL`). Leave it.
- `services/notifications/timer.py` already has the pure business-day machinery
  (`Calendar`/`is_working_day`/`shift_business_days`/`business_threshold`/`DEFAULT_CALENDAR`) and
  `services/notifications/escalation.py::resolve_working_calendar(session, org_id)` is the fail-safe
  per-org resolver. **This slice reuses both — no fork of the business-day logic.**

## 2 · The snap rule (precise — for spec-validation)

Given a stored instant `due_at` (UTC `timestamptz`) and the org's `Calendar cal`:

1. `local = due_at.astimezone(cal.tz)`; `d = local.date()`.
2. If `is_working_day(d, cal)` → **return `due_at` unchanged** (preserve the exact original instant;
   no tz round-trip, no DST-fold artifact).
3. Else walk **forward** one calendar day at a time (bounded, see fail-safe) to the first
   `d' > d` with `is_working_day(d', cal)`; return
   `datetime.combine(d', local.time(), tzinfo=cal.tz).astimezone(UTC)` — the **same local
   time-of-day** on the next working date (DST-ambiguous wall times default `fold=0`, the
   `business_threshold` precedent — within tolerance for a 5-min-granularity sweep).
4. **Fail-safe:** if the bounded forward walk exhausts without finding a working day (a pathological
   all-holiday/empty-workweek calendar — the resolver already rejects an empty working set, so this is
   an extreme edge), **return `due_at` UNCHANGED** (NOT the `shift_business_days` year-9999 sentinel:
   a far-future due date would make the task *never* overdue = fail-OPEN for OVERDUE; keeping the
   original instant is the conservative, no-worse-than-today fallback).

Key properties:
- **Idempotent:** `snap(snap(x)) == snap(x)`. The output is either a working day (a re-snap
  short-circuits at step 2) OR, on the pathological-calendar exhaustion fail-safe (step 4), the
  unchanged input which re-exhausts to the identical value. (NOT "always a working day" — the
  exhaustion branch is the one exception; the DST-gap branch is *enforced* to a working day by §3's
  re-check.)
- **Monotonic forward:** the snapped due_at ≥ the raw due_at (never shortens the SLA window). Proven by
  the validation across all 598 zones, 1970–2040 (0 counterexamples, incl. date-line skips): the walk
  only moves to a later DATE, which dominates any ±1h DST wall-clock adjustment.
- **tz of record = the calendar's tz** (`working_calendar.timezone` via `resolve_working_calendar`),
  NOT the env `easysynq_org_timezone`. The snap evaluates working-day-ness in `cal.tz` because that is
  **the exact frame the timer uses** (`business_threshold`/`is_working_day(now)` all `astimezone(cal.tz)`)
  — so the snap is consistent with the timer **by construction**. ⚠ **The env `easysynq_org_timezone`
  and the DB `working_calendar.timezone` are INDEPENDENT sources** (the env var is set at deploy; the
  calendar tz is set by the wizard / S-notify-7 editor; a missing calendar row falls back to
  `DEFAULT_CALENDAR`'s UTC). They are **not structurally guaranteed equal** — a wizard tz change leaves
  the env at its `UTC` default (the dev box: env `UTC`, calendar `America/Chicago`). **D-5** removes the
  resulting mismatch at the build sites: the three date-anchored sites build `due_at` in `cal.tz` too,
  so build + snap + timer share one frame. Unifying the env var itself with the calendar tz is a named
  residual (§8). The DEFAULT_CALENDAR(UTC) fallback for an un-seeded org is *consistent* (the timer
  uses the SAME resolver/fallback → snap and timer always agree; only absolute tz correctness for an
  un-seeded non-UTC org is affected — §8).

## 3 · The pure helper — `services/notifications/timer.py::snap_to_working_day`

```python
def snap_to_working_day(due_at: datetime.datetime, cal: Calendar) -> datetime.datetime:
    """Return ``due_at`` if its date (in ``cal.tz``) is a working day; else the next working day
    forward at the same local time-of-day, as UTC. Forward-only; preserves time-of-day; the returned
    instant's ``cal.tz`` date is ALWAYS a working day (re-checked — a midnight-crossing DST gap can
    push the reconstructed wall time onto an adjacent day, so trust the verified instant, not the
    wall-time). Idempotent: a re-snap short-circuits at the first guard. Fail-safe: a pathological
    calendar with no reachable working day returns ``due_at`` UNCHANGED (NEVER a far-future sentinel —
    that would make the task never overdue = fail-OPEN)."""
    local = due_at.astimezone(cal.tz)
    if is_working_day(local.date(), cal):
        return due_at
    d = local.date()
    for _ in range(366 + 7):  # bounded; matches shift_business_days' exhaustion guard
        d = d + datetime.timedelta(days=1)
        if not is_working_day(d, cal):
            continue
        cand = datetime.datetime.combine(d, local.time(), tzinfo=cal.tz).astimezone(datetime.UTC)
        # IDEM-1: a nonexistent (spring-forward gap) wall time can normalize ACROSS midnight, so the
        # candidate's real cal.tz date may not be `d`. Trust the instant: re-check, keep walking if it
        # landed on a non-working day. Guarantees a working-day result + idempotency for every tz/mask.
        if is_working_day(cand.astimezone(cal.tz).date(), cal):
            return cand
    return due_at  # fail-safe: keep the original instant
```

Pure (no DB/I/O); a NEW function ADDED to `timer.py` — the existing helpers
(`Calendar`/`is_working_day`/`shift_business_days`/`business_threshold`/`due_steps`) are untouched.
Unit-tested exhaustively incl. the DST-gap edge (§7).

## 4 · The seam — `services/notifications/duedate.py` (new, tiny)

```python
from .timer import Calendar, snap_to_working_day  # re-export snap_to_working_day for the date sites


async def resolve_calendar(session: AsyncSession, org_id: uuid.UUID) -> Calendar:
    """The org's fail-safe working ``Calendar`` — the single tz/working-day frame for both BUILDING a
    date-anchored due_at (D-5) and snapping it. Lazy-imports the resolver to avoid an import cycle."""
    from .escalation import resolve_working_calendar  # lazy: avoid an import cycle
    return await resolve_working_calendar(session, org_id)


async def snap_due_at(
    session: AsyncSession, org_id: uuid.UUID, due_at: datetime.datetime | None
) -> datetime.datetime | None:
    """Resolve + snap, for INSTANT sites (engine now+hours, DOC_ACK now+days). ``None`` passes
    through (an undated/SLA-less task stays undated)."""
    if due_at is None:
        return None
    cal = await resolve_calendar(session, org_id)
    return snap_to_working_day(due_at, cal)
```

Two usage shapes (D-5):
- **Instant sites** (engine `now+hours`, DOC_ACK `now+days`): `await snap_due_at(session, org_id, x)`
  — resolve + snap; no build-tz concern (an instant has no "calendar day").
- **Date-anchored sites** (review/spawn/cadence): resolve FIRST so the date is built in the calendar
  frame — `cal = await resolve_calendar(session, org_id)`; `due_at = combine(date, midnight, cal.tz)`;
  `snapped = snap_to_working_day(due_at, cal)`. (Replaces the `_org_tz()` build tz.)

Notes:
- **`None` passthrough** preserves open-ended MR actions / SLA-less stages (no `sla.hours`).
- **Lazy import** of `resolve_working_calendar` mirrors the existing `from ..notifications.dispatch
  import enqueue_task_notifications` pattern — `escalation.py` is **untouched** (its integration tests
  import `resolve_working_calendar` from `escalation` and assert on the returned `Calendar`; keeping it
  in place keeps them byte-identical-green). ⚠ A **top-level** import of `escalation` here WOULD cycle
  (escalation → workflow package `__init__` → workflow.engine, one of the call sites — empirically
  confirmed by the validation); the lazy import is **load-bearing** and commented as such.
- **Per-call resolve** (one indexed single-row read per snap): D1 single-org + already-query-heavy
  sweeps make this negligible (validation L-perf signed off). A per-org memo is a deferred trivial
  optimization (§8); spec deliberately keeps the seam minimal and uniform.

## 5 · Wiring — the five materialize sites

**INSTANT sites (resolve+snap; no build-tz change):**

1. **`workflow/engine.py::_materialize_stage`** — after `due = _due_at(stage, default_sla)` (line 196),
   `due = await snap_due_at(session, instance.org_id, due)` before the task loop. Covers every
   engine-materialized type. (For DOC_ACK/PERIODIC_REVIEW instances the value is harmlessly snapped
   here and then overwritten by their sweeps — no special-casing needed.) Update `_due_at`'s docstring
   (drop "NOT snapped … un-numbered reconcile" → "snapped at `_materialize_stage` via `snap_due_at`,
   R55").
2. **`ack/sweep.py`** — `due_at = _now() + timedelta(days=ack_due_days)` is a true instant. Inside the
   mint loop snap **per doc-org**: `snapped = await snap_due_at(session, doc.org_id, due_at)` used in
   BOTH the `update(Task)…values(due_at=snapped)` (line 225) AND `enqueue_task_notifications(…,
   due_at_override=snapped)` (line 235) — override + notification MUST agree. Assign to a NEW local
   `snapped` (do NOT reassign the loop-invariant `due_at` in place — a multi-org sweep would re-snap an
   already-snapped value against the next org's calendar; RECON-3-sites guard).

**DATE-ANCHORED sites (D-5: resolve FIRST, build midnight in `cal.tz`, then pure-snap):**

3. **`vault/review.py`** — replace `due_at = combine(next_review_due, midnight, _org_tz())` (line 180)
   with: `cal = await resolve_calendar(session, doc.org_id)`; `due_at = combine(next_review_due,
   midnight, cal.tz)`; `snapped = snap_to_working_day(due_at, cal)`; use `snapped` in the
   `update(Task)…values(…)` (line 184) AND `due_at_override` (line 194). Stored `next_review_due`
   **unchanged** (D-3); update the line-175 comment (was env-tz-midnight aligned with `review_state`;
   now cal-tz-midnight + snapped — the badge may diverge, D-6).
4. **`mgmt_review/spawn.py`** — resolve `cal = await resolve_calendar(session, review.org_id)` once
   before the output loop; change `_action_due_at(due_date)` → `_action_due_at(due_date, cal)` building
   midnight in `cal.tz`; `due_at=snap_to_working_day(_action_due_at(output.due_date, cal), cal)` (None
   passes through for open-ended actions — guard the None before snap).
5. **`mgmt_review/cadence.py`** — replace `combine(due, midnight, _org_tz())` (line 269) with `cal =
   await resolve_calendar(session, org_id)`; `due_at = snap_to_working_day(combine(due, midnight,
   cal.tz), cal)` (None passes through) before the `Task(…)` (line 273).

No other code changes. **`timer.py::due_steps` and the other existing helpers are behaviorally
UNCHANGED** (only the new `snap_to_working_day` is ADDED to the module) — see §6.

## 6 · The reconcile — why the existing `timer.py` functions need NO behavioral change and there is NO double-shift (the crux)

`business_threshold(due_at, offset, BEFORE/AFTER, cal)` walks business days **from `due_at`**. It does
**not** snap `due_at` first — it computes the offset relative to whatever `due_at` is. Therefore:

- **Before this slice:** a Saturday `due_at` → REMIND_1 (3 business days BEFORE) lands on the prior
  Wednesday; OVERDUE = `now >= Sat due_at` → fires **on Saturday** (the bug).
- **After this slice:** `due_at` is snapped to Monday at materialize. REMIND_1 = 3 business days
  before **Monday** = prior Wednesday; OVERDUE = `now >= Mon due_at` → fires **on Monday** (a working
  day). The offsets are computed from the snapped (working-day) anchor — **no double-shift** (nothing
  snaps twice; `business_threshold` still receives one `due_at`, now already a working day).
- **OVERDUE stays `now >= due_at`** — unchanged. Because `due_at` is now a working day, OVERDUE lands
  on a working day **in the design-target case (a prompt sweep on a stable calendar)** — *without* the
  timer special-casing it (the whole point of doing the fix at the source, per S-notify-6 D-5). The
  critical-overdue **quiet-hours pierce** still works. ⚠ OVERDUE has **no `now_is_working` gate**
  (S-notify-6 D-5, intentional, to preserve the weekend pierce) and the snap is **materialize-time only
  with no re-snap** (D-4), so two narrow edges this slice does NOT close can still fire OVERDUE on a
  non-working day: **(a)** a sweep delayed across a working→non-working boundary (worker down from a
  Friday due into Saturday — the same delayed-sweep risk the gate defends reminders/escalate from, but
  OVERDUE is exempt by design); **(b)** a **post-materialize `working_calendar` edit** (now possible via
  the S-notify-7 editor) that turns an already-due date into a holiday. Both are named residuals (§8);
  deferred full-closure = a `now_is_working` gate on OVERDUE or a re-snap at sweep.
- **Escalation/reminder timing shifts (benign, documented):** because the offsets are now measured from
  the snapped anchor, ESCALATE_1 (1 business day AFTER) moves from (e.g.) Monday to Tuesday when a
  Saturday due snaps to Monday; reminders likewise re-anchor. This is the intended consequence of
  measuring relative to the real (working-day) due date — never EARLIER than the §9.5 intent.
- **The `now_is_working` gate on reminders/escalate is retained** — it still defends a *delayed*
  sweep from firing a reminder on a non-working day. With a snapped due_at the thresholds already land
  on working days, so the gate is a happy-path no-op but a real delayed-sweep guard.
- **S-notify-6's business-day offset tests still hold:** they pass explicit `due_at`s to `due_steps`
  and assert the offset math; the existing `timer.py` functions
  (`Calendar`/`is_working_day`/`shift_business_days`/`business_threshold`/`due_steps`) are **unchanged**
  — this slice only ADDS the pure `snap_to_working_day` helper (§3) + the seam (§4), so those tests
  (which call `due_steps` directly) are unaffected. Snapping happens strictly upstream, in the
  materialize services.

## 7 · Tests

**Unit (`tests/unit/test_duedate_snap.py`) — exhaustive on the pure helper (no DB):**
- Saturday/Sunday `due_at` → snaps to Monday, **same time-of-day** (e.g. 14:30 local preserved).
- Working-day `due_at` → returned **unchanged** (exact instant: `snap(x) is x` / `== x`).
- Holiday on a weekday → snaps to the next non-holiday working day.
- Friday-holiday + weekend → snaps across Fri/Sat/Sun to Monday.
- **Idempotency:** `snap(snap(x)) == snap(x)` (incl. the DST-gap case below).
- **Monotonic:** `snap(x) >= x` always.
- **tz boundary — BOTH signs (TZ-2):** EASTWARD: a Fri-23:00-UTC due that is **Sat 08:00 in
  Asia/Tokyo (UTC+9, no DST)** → local date is Saturday → **snaps** (evaluated in cal.tz). WESTWARD: a
  Sat-01:00-UTC due that is **Fri 20:00 in a UTC−5 calendar** → local date is Friday → does **NOT
  snap**. State explicitly these two prove the snap evaluates the date in `cal.tz`, not UTC.
- **DST-gap (IDEM-1, the case Mon–Fri/UTC cannot catch):** `cal = Calendar(frozenset({1,2,3,4,6}),
  frozenset(), ZoneInfo("America/Nuuk"))` (Saturday working, midnight DST transition) with a
  Fri-2024-03-29 23:30 due → assert the result is a **working day** AND `snap(snap(x)) == snap(x)`
  (the §3 re-check must keep walking when the reconstructed wall time normalizes across midnight onto a
  non-working day).
- **Fail-safe:** an all-holiday-for-400-days calendar → returns the input **unchanged** (NOT
  year-9999); assert `snap(x) == x` (do NOT assert `is_working_day` on the fail-safe branch — §2 / L-FAILSAFE-2).
- **DEFAULT_CALENDAR (Mon–Fri/UTC):** the common path.

**Unit on the timer non-regression:** the S-notify-6 `due_steps` tests already prove the offset math on
explicit `due_at`s; this slice leaves those functions unchanged, so no new timer-unit test is required
beyond a note that `due_steps` receives the (now snapped) `due_at` and computes offsets from it.

**Integration (`tests/integration/test_duedate_snap.py`) — the DB→snap wiring + the end-to-end fix:**
- **The headline test (mutation-verified):** materialize a workflow APPROVE task whose **raw**
  `_due_at` would land on a **Saturday** (pin `now` so `now + sla.hours` is a Saturday in the resolved
  calendar tz), then assert the **stored `Task.due_at` is the following Monday** at the same
  time-of-day. Then run a real `timer_sweep` at a Monday `now ≥ due_at` and assert OVERDUE fires (a
  `task.overdue` notification + `overdue_notified_at` stamp) on the **working day**. **Mutation check:**
  the test FAILS against the pre-slice code (raw Saturday due_at).
- **DOC_ACK** sweep (instant): `now + ack_due_days` on a weekend → the minted task's stored `due_at` is
  the next working day; the `due_at_override` passed to the notification matches (snap the NEW local,
  not the loop-invariant).
- **PERIODIC_REVIEW** sweep (date-anchored, D-5): a `next_review_due` on a Saturday → the task `due_at`
  snaps to Monday **built in cal.tz**; assert the stored `next_review_due` is **unchanged** (D-3).
- **MR_ACTION** / **MR_INPUT** (date-anchored, D-5): a due_date/cadence date on a weekend → the spawned
  task's `due_at` snaps (built in cal.tz); a `None` due_date → `None` due_at (passthrough).
- **Divergent env-tz vs calendar-tz lock (TZ-1/D-5, mutation-verified):** monkeypatch
  `easysynq_org_timezone` to a non-UTC zone **differing** from the seeded calendar tz (clear the
  `get_settings` lru_cache), seed a working_calendar with a known tz, and assert an operator-set
  **working-day** date (e.g. a Monday) is **NOT** pushed forward (because D-5 builds it in cal.tz, not
  env-tz). **Mutation check:** this FAILS against a build-in-env-tz implementation (the Monday→Tuesday
  regression TZ-1 named).

**Test-harness traps to honor (engineering-patterns):**
- **Update the PRE-EXISTING `test_periodic_review.py` (RECON-1 sites, MAJOR):** `:300-301` asserts
  `task.due_at.date() == horizon_date` where `horizon_date = datetime.now(_org_tz()).date() + 30d`
  (real-now) — after Site-3's snap this is **weekday-flaky** (~2/7 days `horizon_date` is a weekend →
  Monday != Saturday). Fix: pin `next_review_due` to a fixed **working weekday** before the sweep so the
  snap is a no-op and the equality holds (preserves the test's boundary intent), OR assert against the
  snapped expected value. **And GREP the whole integration suite** (`grep -rn '\.due_at' tests/integration`)
  for any other exact `due_at` assertion against a real-now-derived date (the MR_ACTION
  `2026-12-31` assertion is a Thursday → snap no-op → stays green; confirm during the plan).
- **`audit_event` monthly-partition trap:** any integration test writing an audit (the timer sweep
  emits `TASK_ESCALATED`) must use a `now`/`occurred_at` inside a **seeded partition month** (migration
  0010 seeds **2026-06/07/08** only) → pin to a fixed date in 2026-06/07.
- **Weekday-flaky trap:** pin `now` to a **fixed weekday** computed in the **resolved calendar's own
  tz**, never `datetime.now(UTC)`. Build the raw "lands on Saturday" due_at deterministically; mutation-verify it FAILS against the old code.
- **Run-scoped / delta assertions:** never assume a clean or dirty shared DB; scope to this run's rows.
- **`app_under_test` fixture** even for service-level (no-HTTP) tests (repoints `get_sessionmaker`).
- **REVOKE-DELETE cleanup:** if a test mutates a seeded org's calendar, restore via UPDATE (don't rely
  on DELETE); prefer not committing throwaway-org rows that can't be cleaned (the S-notify-4/7 trap).
- **The PostToolUse ruff `--fix` strips a just-added unused import** — add the using code first.

## 8 · Named residuals (honest — not faked)

- **No backfill (D-4):** existing OPEN tasks keep weekend `due_at`s; they fire at most one weekend
  overdue, then resolve. A one-shot data migration is the deferred option.
- **OVERDUE can still fire on a non-working day in two narrow edges (§6, RECON-2):** (a) a sweep
  delayed across a working→non-working boundary (OVERDUE has no `now_is_working` gate — the pre-ratified
  S-notify-6 D-5 weekend-pierce exemption); (b) a post-materialize `working_calendar` edit (via the
  S-notify-7 editor) that turns an already-due date into a holiday (genuinely new with S-notify-7).
  Deferred full-closure: a `now_is_working` gate on OVERDUE, or a re-snap at sweep.
- **PERIODIC_REVIEW badge↔notification divergence (D-3 + D-6):** the `review_state` badge (env-tz) can
  lead the working-day task notification (cal-tz, snapped); snapping `next_review_due` is deferred.
- **Two un-unified org-tz sources (TZ-1, D-5):** the env `easysynq_org_timezone` and the DB
  `working_calendar.timezone` are independent; D-5 makes the due-at BUILDS use the calendar tz, but
  `review_state`/`today_org()`/R8 date display still read the env var. Unifying `_org_tz()` onto the
  calendar tz (eliminating the divergence everywhere) is a cross-cutting deferred slice. Relatedly, an
  un-seeded org falls back to `DEFAULT_CALENDAR`(UTC) for BOTH the snap and the timer (consistent — no
  snap-vs-timer disagreement — but absolute tz-correctness for an un-seeded non-UTC org is deferred).
- **Per-call calendar resolve (§4):** a per-org/per-sweep memo is a trivial optimization, deferred.
- **The carried Notification tail** (unchanged by this slice): the claim-threshold-filter tautology
  (`remind_2_sent_at IS NULL` always true while remind_2 is unused) · a distinct `remind_2` · a second
  escalation step (`escalate_2`)/auto-reassign · `capa.overdue` · multiple named calendars per org ·
  holiday recurrence / bulk-import.

## 9 · Adversarial spec-validation (pre-code) — DONE, findings folded

A 6-lens refute panel (ultracode Workflow, opus, 19 agents / ~2.3M tokens) ran with per-finding
adversarial verification (only confirmed-real findings survive). **Verdict: the central design HOLDS —
monotonic-forward PROVEN (0 counterexamples, all 598 zones 1970–2040), no double-shift, the materialize
5-site inventory is exhaustively complete, the lazy-import cycle is correctly handled, `due_steps`
unchanged.** 7 real findings confirmed (2 MAJOR, 5 MINOR) + 4 rejected; **all 7 folded above**:

- **TZ-1 (MAJOR, tz)** → **D-5** + §2/§4/§5/§7: env-tz vs calendar-tz mismatch at the date-anchored
  sites; resolved by building those due dates in `cal.tz` (owner-ratified). + the divergent-tz lock test.
- **RECON-1 (MAJOR, sites)** → §7: the snap turns the pre-existing `test_periodic_review.py` exact
  `due_at` assertion weekday-flaky; pin to a working weekday + grep the suite for exact due_at asserts.
- **IDEM-1 (MINOR→real, idem)** → §3: a midnight-crossing DST gap could yield a non-working-day /
  non-idempotent result; the helper now re-checks the reconstructed instant + keeps walking. + Nuuk test.
- **RECON-1 (MINOR, reconcile)** → §6/§3: "timer.py byte-unchanged" reworded to "existing functions
  unchanged; a new helper is added."
- **RECON-2 (MINOR, reconcile)** → §6/§8: the OVERDUE "always a working day" overclaim softened; the
  delayed-sweep + post-materialize-calendar-edit residuals named.
- **TZ-2 (MINOR, tz)** → §7: the tz-boundary unit example fixed (eastward Tokyo snaps + westward UTC−5
  no-snap, both signs).
- **L-FAILSAFE-2 (MINOR, failsafe)** → §2: the idempotency property reworded so it does not claim
  "always a working day" (the exhaustion fail-safe branch is the exception).

Plus 2 verify-step agents hit the StructuredOutput retry cap (no verdict); their underlying lens
findings were assessed directly and are doc-level: the **escalation-timing shift** (folded into §6 as a
benign documented re-anchoring) and the **DEFAULT_CALENDAR(UTC) fallback for an un-seeded non-UTC org**
(folded into §8 — snap and timer use the SAME resolver/fallback, so they always agree; only absolute tz
correctness for an un-seeded org is deferred). Rejected (not folded): per-call resolve cost
(already-deferred §4/§8), the lazy-import-depth nuance (spec already keeps it lazy), the MR_ACTION
fixed-date test (Thursday → snap no-op, stays green), the in-place-vs-`snapped`-local reassign (already
specified as a distinct local in §5).
