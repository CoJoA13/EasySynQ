# Snap task `due_at` to a working day at materialize (S-duedate-snap, R55) — Implementation Plan

> Spec: `docs/superpowers/specs/2026-06-25-s-duedate-snap-design.md` (owner-ratified §0 D-1..D-6;
> 6-lens spec-validation §9, all 7 findings folded). BE-only. **NO migration** (head stays 0067),
> **NO new permission key** (engine logic), **NO web/openapi change**.

## Global constraints

- **R38** additive-only — no new key. **N9/R53/R32** unchanged. WORM/append-only/signature_event/
  vault→mirror authority all untouched (this only changes a `Task.due_at` value at materialize).
- **The snap evaluates working-day-ness in the CALENDAR's tz** (`resolve_working_calendar`), the exact
  frame the timer uses → consistent by construction. **D-5:** the 3 date-anchored sites BUILD their
  due_at in `cal.tz` too (replacing `_org_tz()`); the 2 instant sites just snap.
- Pure date math in the session-free `timer.py`; the DB resolve in the `duedate.py` seam (lazy-imports
  `resolve_working_calendar` — a top-level import CYCLES). `escalation.py` is untouched.
- **`timer.py::due_steps` and the other existing helpers are behaviorally UNCHANGED** — only the new
  pure `snap_to_working_day` is added to the module.
- Gates: `/check-api` (ruff+mypy-strict+unit; no Docker) + the integration suite (`sg docker -c …`,
  sharded + sequential). No `/check-web`, `/check-contracts`, `/check-migrations`.

## File structure

| File | Change |
|---|---|
| `apps/api/.../services/notifications/timer.py` | ADD pure `snap_to_working_day` (IDEM-1 DST re-check) |
| `apps/api/.../services/notifications/duedate.py` | NEW: `resolve_calendar`, `snap_due_at` (lazy import) |
| `apps/api/.../services/workflow/engine.py` | snap `due` in `_materialize_stage`; `_due_at` docstring |
| `apps/api/.../services/ack/sweep.py` | snap the DOC_ACK override (new `snapped` local) |
| `apps/api/.../services/vault/review.py` | build midnight in `cal.tz` + snap; update line-175 comment |
| `apps/api/.../services/mgmt_review/spawn.py` | `_action_due_at(due_date, cal)` in `cal.tz` + snap |
| `apps/api/.../services/mgmt_review/cadence.py` | build midnight in `cal.tz` + snap |
| `apps/api/tests/unit/test_duedate_snap.py` | NEW: exhaustive pure-helper unit tests |
| `apps/api/tests/integration/test_duedate_snap.py` | NEW: instant-site wiring (headline + DOC_ACK) |
| `apps/api/tests/integration/test_duedate_snap_dates.py` | NEW: date-anchored wiring + divergent-tz lock |
| `apps/api/tests/integration/test_periodic_review.py` | UPDATE the weekday-flaky exact-due_at assert |

## Stage 1 — the crux (pure helper + seam + unit tests) — TDD, hands-on

- [ ] **Step 1: Write the failing unit test** `tests/unit/test_duedate_snap.py` covering §7:
  weekend→Monday (time preserved) · working-day unchanged · weekday holiday · Fri-holiday+weekend ·
  idempotency · monotonic · EASTWARD Tokyo Sat-local snaps · WESTWARD UTC−5 Fri-local no-snap ·
  Nuuk midnight-DST-gap (Sat-working mask, Fri 23:30 → result is a working day + idempotent) ·
  all-holiday-400d fail-safe returns input unchanged · DEFAULT_CALENDAR. Run → fails (no function).
- [ ] **Step 2: Implement `snap_to_working_day`** in `timer.py` (spec §3 verbatim, incl. the
  post-reconstruction `is_working_day(cand.astimezone(cal.tz).date(), cal)` re-check). Run → green.
- [ ] **Step 3: Create `duedate.py`** (`resolve_calendar`, `snap_due_at` with the lazy import). Add a
  unit test for `snap_due_at(None) → None` (the None short-circuit needs no DB). Run → green.
- [ ] **Step 4: `/check-api`** (ruff+mypy+unit) on the new files. Commit.

## Stage 2 — wiring (parallel subagents, disjoint files) — depends on Stage 1

### Agent B — INSTANT sites (engine + DOC_ACK)
- [ ] `engine.py::_materialize_stage`: `due = await snap_due_at(session, instance.org_id, due)` after
  `_due_at`; update `_due_at` docstring (drop the deferral → "snapped at `_materialize_stage`, R55").
- [ ] `ack/sweep.py`: snap into a NEW `snapped` local per doc-org; feed it to the `update(Task)` AND
  `due_at_override` (must agree); do NOT reassign the loop-invariant `due_at`.
- [ ] `tests/integration/test_duedate_snap.py`: the headline workflow APPROVE Saturday→Monday +
  real timer_sweep OVERDUE-on-working-day (mutation-noted); DOC_ACK weekend→working (override match).
  Honor the partition trap (`now` in 2026-06/07) + fixed-weekday pinning + run-scoped asserts.
- [ ] Self-check `/check-api`.

### Agent C — DATE-ANCHORED sites (review + spawn + cadence, build in cal.tz — D-5)
- [ ] `vault/review.py`: `cal = await resolve_calendar(...)`; `due_at = combine(next_review_due,
  midnight, cal.tz)`; `snapped = snap_to_working_day(due_at, cal)`; feed `snapped` to `update(Task)` +
  `due_at_override`; `next_review_due` UNCHANGED; update the line-175 comment (cal-tz + snapped, D-6).
- [ ] `mgmt_review/spawn.py`: resolve `cal` once before the loop; `_action_due_at(due_date, cal)` builds
  in `cal.tz`; snap (guard None).
- [ ] `mgmt_review/cadence.py`: build midnight in `cal.tz` + snap (guard None).
- [ ] UPDATE `tests/integration/test_periodic_review.py` — pin `next_review_due` to a fixed working
  weekday so the snap is a no-op and the exact `due_at.date()` equality holds (RECON-1 sites).
- [ ] `tests/integration/test_duedate_snap_dates.py`: PERIODIC_REVIEW snap + `next_review_due`
  unchanged; MR_ACTION/MR_INPUT weekend→working + None passthrough; the divergent env-tz≠cal-tz lock
  (monkeypatch `easysynq_org_timezone`, clear `get_settings` cache, assert an operator Monday is NOT
  pushed — mutation-verifies D-5).
- [ ] Self-check `/check-api`.

## Stage 3 — central verification (hands-on)
- [ ] `/check-api` full (catches cross-file mypy/ruff the per-file runs miss).
- [ ] `grep -rn '\.due_at' apps/api/tests/integration` — confirm no other exact-due_at assert against a
  real-now date breaks (the MR `2026-12-31` is a Thursday → no-op; verify).
- [ ] Run the integration suite (`sg docker -c`, sharded): the 3 new/updated due_date files +
  `test_notification_timer_sweep` + `test_acknowledgements` + `test_workflow_engine` +
  `test_mgmt_review*` + `test_periodic_review`. Mutation-verify the headline FAILS on pre-slice code.

## Stage 4 — multi-lens review Workflow (to convergence)
- [ ] diff-critic (branch diff) + whole-branch (opus) — probe: no double-shift, the 5 sites covered,
  the build-in-cal-tz correctness, the DST re-check, no WORM/authz drift. Fold confirmed findings.

## Stage 5 — live-smoke (Chrome MCP; owner does the Keycloak login)
- [ ] Materialize a task whose raw instant is a Chicago weekend → confirm stored due_at snaps to the
  next working day; a real worker `timer_sweep` fires OVERDUE on the working day, not the weekend.

## Stage 6 — finish
- [ ] `/finish-slice` (slice-history + CLAUDE.md learning + Current-status pointer + memory note +
  **record R55 in `docs/decisions-register.md`** + correct the R29 as-built note's "un-numbered" →
  R55 + the engine `_due_at` docstring). PR via `/pr`. Codex rounds to 👍.
