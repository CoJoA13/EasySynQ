# S-capa-overdue — Design Spec

> **Slice:** S-capa-overdue · **Date:** 2026-06-26 · **Migration:** `0070` (head `0069 → 0070`)
> **New permission key:** none · **New event types:** `CAPA_OVERDUE` + `CAPA_TARGET_DATE_SET` (additive enum) · **New notification event:** `capa.overdue`

## Goal

Wire `capa.overdue` — the last class-mapped-but-unwired notification event (`classes.py` already maps it
`CRITICAL`) — end-to-end. A CAPA gains a **severity-defaulted, editable target-completion date**; a new daily
Beat sweep notifies the **QMS Owner** role when an open CAPA passes that date. BE + a minimal FE surface
(target-date row + Overdue badge + inline edit on the CAPA drawer) + a backfill CLI.

## Context & the architectural decision

A `capa` row is a `kind=RECORD` shared-PK subtype with a **mutable** `close_state` FSM column and an
**append-only** `capa_stage` trail. Crucially, **a CAPA has no `Task` row** — the action-plan *approval*
spawns an engine-routed task (which the escalation timer already covers via `task.overdue`/`task.escalated`),
but the CAPA itself does not. So the existing timer sweep (`services/notifications/escalation.py`) **cannot
see CAPAs**.

**Decision (D-1): a new state-scan Beat sweep**, mirroring `services/vault/review.py::sweep_reviews` (the
closest precedent: session-scoped advisory lock · `using_org_tz` · one commit · idempotent · returns
`dict[str,int]`). It is genuinely distinct from the approval task's `task.overdue` (different subject, different
date) → **no double-notify**.

**Decision (D-2, the deadline model — owner-ratified):** ONE `target_completion_date` (a calendar `DATE`) on
the mutable `capa` row, **auto-defaulted at raise from severity** (30 / 60 / 90 calendar days for
Critical / Major / Minor — a code constant for v1; the admin-editable offset table is a named deferral) and
**editable** via a `capa.update`-gated endpoint. Overdue ⟺ `today(org_tz) > target_completion_date AND
close_state NOT IN (Closed, Rejected)`. Rejected alternatives: per-stage SLA deadlines (overkill — ISO
auditors check the single CAPA-level target date); a manual/nullable-only date (re-opens the
"where's the target date?" audit gap until someone fills it in).

**Decision (D-3, recipient):** the seeded **`QMS Owner`** role holders (always present; accountable for CAPA
closure timeliness), resolved via `users_with_roles(session, capa.org_id, ["QMS Owner"])` → `_recipient_for_user`
per uid (cross-org-filtered) — the escalation-tier pattern. Process-owner / raiser routing is a named deferral.

**Decision (D-4, existing CAPAs):** a backfill CLI (the `backfill_review_dates` precedent) sets the date for
existing non-terminal CAPAs; the operator runs it when ready. Not run automatically; not part of the migration.

## 1 · Data model — migration `0070`

On the **mutable** `capa` table (never the WORM `capa_stage`), both nullable, both **ORM-mirrored** in
`db/models/capa.py` (else `alembic check` phantom-DROPs):

- `target_completion_date DATE NULL` — the auditor-checked deadline.
- `overdue_notified_at TIMESTAMPTZ NULL` — the sweep's claim-filter + once-per-breach stamp (the
  `task.overdue_notified_at` mirror). No `server_default`.

Plus:

- **Additive enum (two values):** `ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'CAPA_OVERDUE'` and
  `… 'CAPA_TARGET_DATE_SET'` (each in its own autocommit block), with matching `EventType` members in
  `db/models/_audit_enums.py`, sourced from `EVENT_TYPE_VALUES` (a from-scratch `upgrade head` rebuilds the type
  from the ORM tuple). No-op downgrade for the enum values. (`CAPA_OVERDUE` = the sweep's breach audit;
  `CAPA_TARGET_DATE_SET` = the edit-endpoint deadline-change audit — a mutable compliance deadline must leave a
  WORM trail of who moved it, the deadline-gaming audit concern.)
- **Global template seed:** `INSERT INTO notification_template (… event_key='capa.overdue', locale='en',
  version=1, is_effective=TRUE, …) ON CONFLICT (event_key, locale) WHERE is_effective DO NOTHING` (the `0069`
  shape). No `org_id` → **CI-covered** by `alembic upgrade head`. Downgrade `DELETE … WHERE event_key='capa.overdue'
  AND NOT EXISTS (SELECT 1 FROM notification n WHERE n.template_id = notification_template.id)` (the RESTRICT-FK
  guard; fresh-DB CI is blind to a populated downgrade otherwise).

The migration adds **no data seed to `capa`** (existing CAPAs get NULL → never overdue until the backfill CLI
or an edit sets a date). No per-org seed → no CI-blind seed risk.

## 2 · Default-at-raise

A pure code constant in `domain/capa` (e.g. `fsm.py` or a new `targets.py`):

```python
CAPA_TARGET_DAYS: dict[NcSeverity, int] = {
    NcSeverity.Critical: 30, NcSeverity.Major: 60, NcSeverity.Minor: 90,
}
def default_target_date(severity: NcSeverity, raised_on: datetime.date) -> datetime.date:
    return raised_on + datetime.timedelta(days=CAPA_TARGET_DAYS[severity])
```

Set `target_completion_date = default_target_date(severity, today(org_tz))` at the **two** `Capa(...)`
construction sites in `services/capa/service.py`: `build_capa` (covers `raise_capa`, the S-aud-2 NC→CAPA
auto-link, and the risk-spawn) **and** the inline `Capa(...)` in `spawn_capa_from_complaint`. `today(org_tz)`
via `org_clock` (the unified resolver). No behaviour change for any other field.

## 3 · The overdue sweep — `services/capa/overdue.py` + `tasks/capa.py`

`async def sweep_capa_overdue(session) -> dict[str, int]` (mirrors `sweep_reviews`):

1. `pg_advisory_lock(session, LOCK_CAPA_OVERDUE_SWEEP)` — add `LOCK_CAPA_OVERDUE_SWEEP = 7710010` to
   `services/common/pg_locks.py`. Skip the tick if not held.
2. Resolve org tz; **`now_is_working`-gate** the firing (don't email an overdue CAPA on a weekend — the
   OVERDUE/R56 parity). Compute `today = now(org_tz).date()`.
3. **Claim:** `SELECT … FROM capa WHERE target_completion_date < today AND close_state NOT IN (Closed,
   Rejected) AND overdue_notified_at IS NULL`. (The stamp-NULL claim is the S-claim-filter discipline — only
   un-notified rows; the claim is bounded.)
4. For each claimed CAPA, resolve `users_with_roles(session, capa.org_id, ["QMS Owner"])` → `_recipient_for_user`.
5. **Emit (task-less / awareness path):** for each recipient, insert a deduped `notification` row keyed on
   `uq_notification_dedup_awareness (recipient_user_id, event_key, subject_type, subject_id, subject_version_id)
   WHERE task_id IS NULL`, with `event_key='capa.overdue'`, `subject_type='capa'`, `subject_id=capa.id`, and
   **`subject_version_id = uuid5(_NS, f"{capa.id}:{target_completion_date.isoformat()}")`**.
   **Emit-helper seam (resolve in the plan, do NOT reinvent dedup):** the awareness path today is
   release-driven — `record_awareness_event` writes the `awareness_event` outbox, and `fanout.py::fan_out_awareness`
   does the per-recipient `notification` INSERT (template lookup → render → `ON CONFLICT` on the dedup index).
   The sweep needs the **per-recipient INSERT half** directly (the escalation-sweep posture; it must NOT go
   through the outbox, which is for transactional release events). The plan must either (a) factor the
   per-recipient core of `fan_out_awareness` into a shared `emit_awareness_notification(session, *, recipient,
   event_key, subject_type, subject_id, subject_version_id, context)` and call it from both sites, or (b) if a
   reusable insert helper already exists, call it. Pin the chosen helper's signature in the plan; the dedup `ON
   CONFLICT` clause is reused, never re-hand-rolled.
6. **Stamp + audit + commit (ONE txn):** set `capa.overdue_notified_at = now`; append one `CAPA_OVERDUE`
   `AuditEvent` (`object_type=record` — `capa.id` IS a record id, the CAPA audit precedent; `scope_ref=str(capa.id)`;
   `after={"capa_id", "target_completion_date", "severity"}`). The stamp guarantees once-per-breach idempotency
   across sweeps and `acks_late` redelivery (a committed run excludes the row next time; an uncommitted crash
   left zero side effects).
7. **Beat:** a `"capa-overdue-sweep"` entry in `tasks/app.py::beat_schedule` (daily, `86400.0`); a
   `tasks/capa.py` wrapper (`@task(name="easysynq.capa.overdue_sweep")`, the `tasks/review.py` engine-per-run
   precedent) registered in `tasks/__init__.py` (+ the `app.tasks` membership unit test).

⚠ **THE load-bearing trap:** `subject_version_id` MUST vary by the target date. After a date **extension**
(§5, which clears the stamp to re-arm the claim), the re-armed breach must NOT collapse onto the first
notification's dedup row — a per-(capa, date) `subject_version_id` gives it a fresh key. A constant/NULL
`subject_version_id` here is the S-remind2/S-escalate2 dedup-collapse bug in a new guise.

## 4 · Notification wiring

- `constants.py`: `EVENT_CAPA_OVERDUE = "capa.overdue"` + a `VARIABLE_WHITELIST` entry — a CAPA var set
  `{recipient.first_name, subject.identifier, subject.title, target_completion_date, deep_link, prefs_link}`.
  (⚠ omitting the whitelist entry renders every `{{…}}` slot blank — a count test still passes.)
- `classes.py`: **already** maps `capa.overdue → CRITICAL` (immediate, pierces quiet hours — appropriate for a
  compliance-deadline breach). No change.
- `subjects.py` / `render.py`: add a `capa` subject resolver (identifier/title from the record header) if not
  already present, and expose `target_completion_date` to the renderer.

## 5 · Edit endpoint + serializer

- `PATCH /api/v1/capas/{capa_id}` with body `{ "target_completion_date": <date|null> }`, gated by the existing
  **`capa.update`** key at the CAPA's PROCESS scope (the `_capa_update` dependency; **no new permission key**).
  Behaviour: load `FOR UPDATE`; **409 on a terminal CAPA** (Closed/Rejected — no live deadline); set the date;
  **clear `overdue_notified_at`** (re-arm the claim); append a `CAPA_TARGET_DATE_SET` audit via
  `emit_record_event` (`object_type=record`; `before={"target_completion_date": <old>}` /
  `after={"target_completion_date": <new>}` — the WORM record of who moved the deadline). Commit.
- `_capa()` serializer (shared by list + detail) gains `target_completion_date` (ISO date or null) and a
  **server-computed `overdue: bool`** (`target_completion_date is not None AND today(org_tz) >
  target_completion_date AND close_state not in terminal`) — tz-correct, so the FE renders a boolean, not a
  client-tz comparison. Document both fields + the PATCH in `packages/contracts/openapi.yaml`.

## 6 · Backfill CLI — `cli/backfill_capa_target_dates.py`

The `backfill_review_dates` precedent: idempotent, `--dry-run`. For non-terminal CAPAs where
`target_completion_date IS NULL`, set `= created_at::date + offset[severity]` (the record header's `created_at`).
A result already in the past is correctly overdue on the next sweep. Reports counts; never mutates a terminal
CAPA. Not shipped in the migration; the operator runs it.

## 7 · FE (minimal — `apps/web/`)

- `lib/types.ts` `Capa`: add `target_completion_date: string | null` + `overdue: boolean` (`satisfies`-pinned to
  the serializer).
- `features/capa/CapaDrawer.tsx`: a target-completion-date row near the close_state badge; an
  `<StatusBadge tone="danger" label="Overdue" kind="CAPA" />` when `overdue` (colour-safe glyph + distinct
  `aria-label` — the `ReviewStateBadge` / `CompliancePage` precedent); an inline `<TextInput type="date">` edit
  affordance (the `audits/PlanForm` pattern), gated on `usePermissions().can("capa.update")` (SYSTEM fallback in
  v1) — don't render the edit to a caller who can't exercise it.
- `features/capa/mutations.ts`: `useCapaSetTargetDate(id)` → PATCH; `onSuccess` invalidates `["capa", id]` +
  `["capas"]` (server-of-truth, no optimistic update — the CAPA-mutation house pattern).
- `test/msw/handlers.ts`: extend the CAPA fixtures with the two new fields (pinned to the real serializer); a
  `CapaDrawer.test.tsx` case (overdue badge renders; edit field present/gated) + a `mutations.test.tsx` case
  (the PATCH fires + invalidates). Import `expect`/`it` from `"vitest"` (the jest-dom×vitest trap).

## 8 · Testing strategy

- **Unit:** `default_target_date` boundary math; the serializer `overdue` boolean; `test_notification_classes`
  (capa.overdue → CRITICAL, already-present assertion); the `subject_version_id` derivation determinism.
- **Integration (mutation-distinguishing):** a `sweep_capa_overdue` test that an open CAPA past its target with
  a QMS-Owner holder gets exactly one `capa.overdue` notification + a stamp + a `CAPA_OVERDUE` audit; a
  terminal or not-yet-due CAPA is NOT claimed; a re-armed (date-extended) breach fires a SECOND distinct notification
  (proves the `subject_version_id` discriminator). ⚠ **Pin the clock to a fixed weekday** in the resolved
  calendar's tz (the `now_is_working` gate + business-calendar weekday-flaky trap); **`occurred_at` in a seeded
  monthly partition** (`2026-06`); **run-scoped / delta-based** assertions; **self-provide preconditions**
  (delete leaked QMS-Owner SYSTEM grants if a count is asserted); reuse the default org (a 2nd `Organization`
  trips `scalar_one`); keep the `app_under_test` fixture. Do NOT gate on the full local `-m integration` run
  (~54 env/pollution false-fails) — scoped file + the 4 CI shards are authoritative.
- **HTTP:** PATCH happy-path (date set, stamp cleared) + 409-on-terminal + the `capa.update` deny path; the
  serializer surfaces the two fields.
- **Web:** `/check-web` (the full eslint + strict tsc + build + vitest run).
- **Review gate:** the new `notification-wiring-reviewer` agent + `migration-reviewer` + whole-branch `diff-critic`.
- **Live-smoke:** DB-only — `0069→0070` round-trip on a throwaway PG16 + SELECT the new columns/template/enum
  value; **never run the live sweep** (it emails real QMs).

## 9 · Delta summary

| Surface | Change |
|---|---|
| Migration | **`0070`** — `capa.target_completion_date` + `capa.overdue_notified_at`; `ALTER TYPE event_type ADD VALUE 'CAPA_OVERDUE'` + `'CAPA_TARGET_DATE_SET'`; global `capa.overdue` template seed |
| Permission key | **none** (reuses `capa.update`) |
| Event types | `CAPA_OVERDUE` + `CAPA_TARGET_DATE_SET` (additive enum) + `EVENT_CAPA_OVERDUE='capa.overdue'` notification event |
| Endpoints | `PATCH /capas/{id}` (new) + `target_completion_date`/`overdue` on the CAPA serializer (list + detail) — documented in `openapi.yaml` |
| Beat | `capa-overdue-sweep` (daily) + `tasks/capa.py` + `LOCK_CAPA_OVERDUE_SWEEP` |
| CLI | `backfill_capa_target_dates.py` |
| FE | CAPA drawer target-date row + Overdue badge + inline edit |
| WORM/authz | none touched (CAPA audit reuses `object_type=record`; no new key; no append-only table altered) |

## 10 · Named deferrals (not faked)

- Admin-editable severity offsets (the SLA-offset-editor residual, generalized to CAPA).
- Process-owner / CAPA-raiser routing (needs a process→owner resolver).
- A CAPA-overdue **escalation tier** (a 2nd notification to leadership after N days past target).
- Per-stage SLA deadlines.
- `capa.overdue` in the daily digest rollup.
