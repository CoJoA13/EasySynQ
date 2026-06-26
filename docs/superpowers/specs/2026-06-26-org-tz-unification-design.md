# S-orgtz-unify — unify the org timezone onto the working calendar

> Design spec. Status: **approved (design)** — pending spec review, then `writing-plans`.
> Date: 2026-06-26. Owner-ratified decisions captured inline as **D-N**.

## 1. Problem

EasySynQ has **three** org-timezone sources that drift apart:

| Source | Frame it drives | Read mechanism |
|---|---|---|
| env `easysynq_org_timezone` (default `UTC`) | `next_review_due`, `review_state` badge, mgmt-review cadence, `_document()` serializer, checklist WHERE-clause | `_org_tz()` — **sync, session-free** (`services/vault/review.py:50`) |
| `organization.timezone` (R8 "authoritative for effective dates") | seed source for the calendar; set by SetupWizard / `set_org_profile` | DB (`migration 0012`) |
| `working_calendar.timezone` (R29/R55 "canonical for timer eval") | snap / timer / escalation / `due_at` | DB via `resolve_working_calendar` (`migration 0067`) |

The **review/cadence** frame reads env (UTC by default); the **snap/timer** frame reads the DB calendar. On any non-UTC org they diverge — this is the accepted **R55 D-3** "`review_state` badge leads the working-day notification" divergence, plus the `_fmt_date` notification render mismatch (an email showing the wrong date for an east-of-UTC org). On the dev box this is already live: env `UTC`, org/calendar `America/Chicago`.

**Goal:** one canonical, DB-resolved org timezone that *every* date-level derivation, badge, snap/timer, and render shares — so the two frames become one. Closes the named cross-cutting residual ("unify `_org_tz()` onto the calendar") and R55 D-3.

## 2. Decisions (owner-ratified in brainstorming)

- **D-1 — Canonical source = the calendar, with fallback.** The unified resolver returns `working_calendar.timezone (is_default) → organization.timezone → env easysynq_org_timezone → UTC`. The literal residual intent ("onto the calendar"); completely closes D-3 (review dates now share the snap/timer frame); preserves S-notify-7's editable calendar tz as the control surface (no regression). In the common case `organization.timezone == working_calendar.timezone`, so nothing changes.
- **D-2 — Mechanism = hybrid (contextvar + explicit).** Ambient `today_org()` becomes contextvar-backed (set once at the auth boundary + per-org in the two sweeps); the genuine date-*transform* functions take an explicit `org_tz` param (pure + unit-testable). This avoids touching the 68 `_document()` call sites while keeping the correctness-critical math explicit.
- **D-3 — Scope = comprehensive.** Fold in `_fmt_date` render hardening, the OVERDUE `now_is_working` gate, and a one-time backfill of stored review dates — all under one org-tz-correctness theme, phased for independent review.
- **D-4 — R8 cutover stays UTC.** Only date-level *display/derivation* moves to the canonical tz. Effective-date *cutover* remains UTC-clock-authoritative (R8). No effective-date backfill.

## 3. Architecture

### 3.1 The unified resolver — *parity by construction*
New module `services/common/org_clock.py`:
- `async resolve_org_tz(session, org_id) -> ZoneInfo` — the **fail-safe** chain of D-1 (is_default calendar tz if the row exists and the IANA name is valid → `organization.timezone` if valid → env `easysynq_org_timezone` → `UTC`). Never raises (mirrors `resolve_working_calendar`'s granular fail-safe doctrine).
- **`resolve_working_calendar` (escalation.py) is refactored to source its `.tz` from `resolve_org_tz`.** The calendar resolver and `today_org()` then physically cannot disagree on tz — one code path, not two parallel ones (the S-notify-7 `calendar_spec` "validation parity by construction" principle). Side-benefit: a calendar-less org (a new org created post-0067, before the S-notify-7 editor synthesizes its row) now snaps in its `organization.timezone` instead of `UTC`.
- `org_clock` imports only models (`WorkingCalendar`, `Organization`) + `config` — **never** `workflow`/`engine`/`escalation`, so it introduces no import cycle (the `duedate.py` lazy-import trap does not recur here).

### 3.2 `today_org()` → contextvar-backed (68 `_document` sites: zero churn)
- `org_clock` holds `ContextVar[ZoneInfo | None] _org_tz_var`, plus:
  - `current_org_tz() -> ZoneInfo` — returns the var if set, else the env fallback (`ZoneInfo(get_settings().easysynq_org_timezone)`), so an unset context degrades to **today's behaviour** (safe).
  - `using_org_tz(tz)` — a context manager that sets + resets the var (mirrors `request_id_var`).
- `services/vault/review.py::_org_tz()` and `today_org()` keep their **exact signatures**, re-implemented to delegate to `current_org_tz()`. Every importer — `_document()` (×68 call sites), the MR badge, `checklist.py`'s WHERE-clause builder, `cadence.py` — is untouched.
- **Boundaries that set the var:**
  1. `auth/dependencies.py::get_current_user` resolves `resolve_org_tz(session, user.org_id)` and sets the var once per request (the dependency runs in the same async task as the handler → the var propagates into the serializers; the `request_id_var` precedent). One extra indexed DB read per request.
  2. `sweep_reviews` + `sweep_mgmt_reviews` wrap each per-org iteration in `using_org_tz(tz)`.
  3. The escalation/digest enqueue sweeps already resolve the calendar per task/org → wrap the emit/render in `using_org_tz(calendar.tz)` so `_fmt_date` (§3.4) reads the right tz.
- Unset (e.g. a worker serializing a document via `_document` in the ingestion-commit task) → safe env fallback; `review_state` is a derived projection, recomputed correctly on the next UI read.

### 3.3 Compute paths → explicit `org_tz` param
The genuine date-*transform* functions gain an explicit `org_tz: ZoneInfo` (no hidden state in the math):
- `compute_next_review_due(review_period_months, last_reviewed_at, effective_from, org_tz)`.
- the `cadence.py` anchor helper(s) that convert `effective_from` to an org-tz date.

~6 callers — all already hold a session — resolve and pass: the release path (`lifecycle.py`), `decide_periodic_review`, `sweep_reviews`, `sweep_mgmt_reviews`/`_last_released_effective_from`, ingestion review. In a request path they may pass `current_org_tz()` (already set); in a sweep they pass the per-org resolved tz. `review_state(next_review_due, today)` is already pure (takes `today`) — unchanged; its callers compute `today_org()` (contextvar).

### 3.4 `_fmt_date` render hardening
A *later* sweep reads `task.due_at` back from PostgreSQL as a **UTC-aware** instant (psycopg returns `timestamptz` in UTC), so `_fmt_date(value).date()` yields the UTC date — off-by-one for east-of-UTC orgs. (The Codex #291 fix only covered the *in-transaction* value, still a `cal.tz`-aware object.) Fix in `services/notifications/render.py::_fmt_date`: when the value is an **aware** `datetime`, `value.astimezone(current_org_tz()).date()`; a naive `datetime` or a `date` passes through unchanged. The escalation/digest sweeps set the per-org contextvar (§3.2.3), so the renderer reads the org's tz.

### 3.5 OVERDUE `now_is_working` gate (reverses R55 D-5's exemption)
Add an `is_working_day(now, calendar)` gate to the OVERDUE step (mirroring REMIND/ESCALATE, which got it in S-notify-6 R1) so `task.overdue` never fires on a weekend/holiday. Closes both ratified edges: a delayed sweep crossing a working→non-working boundary, and a post-materialize calendar edit turning an already-due date into a holiday. **This deliberately reverses R55 D-5's weekend-pierce exemption** → register amendment (§6). Trade-off (accepted): an overdue notice that would have pierced quiet-hours on a Saturday now waits to the next working day — exactly what doc 10 §9.5 ("no weekend overdue/escalation") wants.

### 3.6 Backfill CLI (one-time correction of stored dates)
**Only `documented_information.next_review_due` is stored** (MR next-due is *derived* — `next_mr_due` recomputes on every read, so it lands in the canonical tz automatically once the contextvar is set; no MR backfill). A `cli/` command (idempotent, `--dry-run`): per org → `resolve_org_tz` → for each `documented_information` with a non-null `next_review_due`, recompute `compute_next_review_due(period, last_reviewed_at, effective_from, org_tz)` and `UPDATE` only where the value changed. Recompute needs Python (`add_months` month-clamp + per-org tz), so a CLI — not raw SQL in a migration; a migration row-step also isn't CI-exercised (fresh DB has zero orgs), so it is verified on the populated DB / live-smoke.

## 4. Out of scope / invariants preserved

- **No migration** (both DB columns already exist); **no new permission key**; **no web/FE change** (`review_state`/MR badge are server-computed — the values just get more correct; the SetupWizard + WorkingCalendarEditor tz fields stay).
- **env `easysynq_org_timezone` is kept** as the bottom fallback (not removed).
- `organization.timezone` and `working_calendar.timezone` **stay separate** (cal-canonical-with-fallback per D-1) — no S-notify-7 regression, no register reversal of the editable-tz decision.
- WORM / append-only / authz invariants untouched.

## 5. Testing

- **Unit:** `resolve_org_tz` fallback chain (calendar/org/env/UTC, invalid-IANA degrade); `using_org_tz`/`current_org_tz` set+reset+unset-fallback; `compute_next_review_due` under a non-UTC tz (month-boundary date differs from UTC); `_fmt_date` re-conversion (**mutation-distinguishing**: an east-of-UTC instant → the correct local date, fails against the old `.date()`); OVERDUE gate (fires on a working-day `now`, suppressed on a weekend `now`); backfill recompute (changed vs unchanged rows; idempotent second run).
- **Integration (shared-DB-safe, divergent-tz aware** — the S-duedate-snap harness lesson): resolve the *actual* org calendar and assert review/badge dates in the resolved frame (never assume the shared-DB org's tz). The `test_mgmt_review` UTC-coupling hardening folds in here. Any `audit_event` write uses an `occurred_at` in a seeded monthly partition.
- Mutation-verify the unification: neutering `resolve_org_tz` to return UTC must fail the divergent-tz assertions.

## 6. Docs / register

- **New R56** — org-tz unification: the `resolve_org_tz` chain (D-1); `today_org`/review dates/badges/render all judge in the canonical tz; cutover stays UTC (D-4); hybrid contextvar+explicit mechanism (D-2).
- **Amend-note on R8** — display/derivation tz = the resolved canonical tz (calendar-first); cutover unchanged (UTC-authoritative).
- **Amend-note on R55 D-5** — OVERDUE is now `now_is_working`-gated (the weekend-pierce exemption is closed); the snap reconcile is unchanged.
- CLAUDE.md "Recent learnings" entry + `docs/slice-history.md` narrative + the memory resume note (via `/finish-slice`).

## 7. Phasing (for the implementation plan)

1. `org_clock` (resolver + contextvar) + refactor `resolve_working_calendar`'s tz onto it — unit-tested in isolation.
2. Set the contextvar at the auth boundary + the two review sweeps; `_org_tz()`/`today_org()` delegate.
3. Explicit `org_tz` into the compute functions + their ~6 callers.
4. `_fmt_date` hardening + contextvar in the escalation/digest enqueue paths.
5. OVERDUE `now_is_working` gate.
6. Backfill CLI.
7. Integration/test hardening (divergent-tz) + `test_mgmt_review` UTC-coupling fix.
8. Docs/register + `/finish-slice`.

Each phase is independently reviewable; the PR may land as one or split if the diff grows large.

## 8. Risks / open checks (resolve in plan, not re-litigate)

- **Contextvar propagation** from the `get_current_user` dependency into the handler/serializers — expected per the `request_id_var` precedent (same async task); confirm with a focused test.
- **Worker-context `_document` serialization** (ingestion commit, etc.) runs with the var unset → env fallback for `review_state` in that one path (acceptable; recomputed on next UI read). If any such path is user-facing-authoritative, wrap it in `using_org_tz`.
- **Behaviour change (no backfill window):** until the backfill CLI runs, stored `next_review_due` values computed in env-UTC differ from the canonical tz by at most ±1 day at month boundaries; they self-correct on the next release/confirm/PATCH regardless.
- **OVERDUE timing change:** weekend overdue notices defer to the next working day (intended, doc 10 §9.5).
