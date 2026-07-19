# S-cleanup-bundle — design

> **Status:** approved (brainstorming) — 2026-07-18
> **Branch:** `feat/s-cleanup-bundle`
> **Origin:** the 2026-07-18 improvement survey (25 verified-still-open opportunities). This slice
> bundles five low-risk, high-signal wins picked from that register.

## Summary

A single migration-free PR bundling five independent cleanup items. Nothing here touches a
load-bearing invariant (WORM / append-only audit chain / vault→mirror authority / deny-wins authz /
blob-row-iff-bytes). No new permission key, no `ALTER TYPE`, no schema change.

| # | Item | Surface | Migration |
|---|------|---------|-----------|
| 19 | Wire `ensure_partitions()` into tests + API startup | api | none |
| 6 | Requeue failed notification emails (structured-log only) | api + web + contract | none |
| 15 | Calm loading/error states in older admin tabs + DocumentDrawer | web | none |
| 16 | Per-route `document.title` + route-change focus management (WCAG 2.4.2) | web | none |
| 17 | `Table.ScrollContainer` + `scope="col"` on the bare registers | web | none |

**Verification gate:** `/check-api` + `/check-web` + `/check-contracts` + the integration suite, then
`diff-critic` and `web-test-trap-reviewer` on the branch diff before the PR.

**Build order** (risk low→isolated, each its own commit): #19 → #17 → #15 → #16 → #6.
#6 is the only item spanning api + web + contract.

---

## Item #19 — Audit-partition runway (the dated landmine)

### Problem
`audit_event` is month-partitioned with **no DEFAULT partition** (by design — a DEFAULT would block
creating a covering month once any row landed in it). Migration `0010_audit.py` seeds a **fixed**
runway of `2026-06 / 2026-07 / 2026-08`. `ensure_partitions()` (idempotent, backed by the
SECURITY-DEFINER `easysynq_create_audit_partition`) keeps the rolling window alive, but it is called
**only** by the daily `roll_partitions` Beat task and the `easysynq audit ensure-partitions` CLI —
never in tests, never at app startup. Two consequences, both dated **2026-09-01**:

1. **CI reds.** The integration suite writes `audit_event` rows with `now()`-relative `occurred_at`;
   the first CI run on/after 2026-09-01 hits `no partition of relation "audit_event" found`.
2. **Fresh install fails at setup.** A fresh install **after Aug 2026** runs migration `0010` (fixed
   past-dated seed) and then first-run setup, which writes audit events immediately — before the
   daily Beat's first tick — so setup itself 500s.

### Fix
No change to `partitions.py` (already correct/idempotent); no migration (`0010` is immutable history;
its fixed seed stays as a floor). Call `ensure_partitions` in two more places:

- **Tests** — `apps/api/tests/integration/conftest.py`: inside the existing owner-engine block (right
  after `command.upgrade(cfg, "head")` and the `setup_state='OPERATIONAL'` update), loop
  `upcoming_month_starts(date.today())` and run `SELECT easysynq_create_audit_partition(:start)` on
  the owner connection already open there. Reuses the pure helper; the owner role can call the
  SECURITY-DEFINER function. This adds current+2 **on top of** the migration's fixed 06/07/08, so the
  union keeps June-pinned tests green *and* real-`now()` tests green past Sept 1.
- **Prod** — `apps/api/src/easysynq_api/main.py` `lifespan` startup (before `yield`):
  ```python
  try:
      async with get_sessionmaker()() as s:
          await ensure_partitions(s)
  except Exception:
      logger.warning("audit.ensure_partitions_on_startup_failed", exc_info=True)
  ```
  Best-effort / fail-open so a transient DB issue never blocks boot. Safe because compose orders
  `api depends_on migrate: service_completed_successfully` → the function exists by boot, and the API
  boots before an operator can run first-run setup (setup flows through the API). Beat's daily
  `roll_partitions` remains the steady-state driver.

### Tests
- Unit: `upcoming_month_starts(date(2026, 10, 15))` returns `[2026-10-01, 2026-11-01, 2026-12-01]`
  (a post-Aug date the fixed seed doesn't cover) — documents the fresh-install case.
- The conftest change is self-proving: the whole integration suite exercises audit writes.

### Notes / non-goals
- Not rewriting `0010` (immutable). Not adding a separate setup-finalize hook — API-lifespan-on-boot
  already covers the fresh-install path, and Beat covers steady state.
- On merge, delete the `audit-partition-runway-deadline` memory (its landmine is closed).

---

## Item #6 — Requeue failed notification emails

### Problem
`get_delivery_health` surfaces FAILED delivery rows (`health.py`), but a `FAILED` outbox row is
**unrecoverable from the UI**: `drain.py` only claims `status=PENDING`, and on exhaustion stamps
`FAILED` terminally with no reset path. Failures are visible; recovery is not. (Deferred deliberately
at S-notify-5b as "read-only panel, no requeue".)

### Decision (owner, 2026-07-18)
**Structured-log only — no `audit_event`, no migration.** Email here is advisory (the `/tasks` inbox
is the authoritative surface), so a requeue is a pure ops-recovery action, not a controlled-record
change. Bulk "requeue all failed for this org" (the health endpoint exposes no per-row ids, and
failures are typically systemic — SMTP down → many fail at once).

### Fix
- **Service** — new `services/notifications/requeue.py`:
  ```python
  async def requeue_failed(session, org_id) -> int:
      # UPDATE notification_email
      #   SET status='PENDING', attempts=0, next_attempt_at=NULL, failed_at=NULL, last_error=NULL
      #   WHERE org_id=:org AND status='FAILED' RETURNING id
      # → logger.info("notifications.requeued", extra={count, org_id, actor})
      # returns count; does NOT commit (route commits — mirrors calendar_admin)
  ```
  **`attempts=0` is load-bearing:** without it the drain would immediately re-FAIL the row without
  sending (it's already ≥ `notification_max_send_attempts`). New module (not `health.py`, whose
  contract is "pure reads; no side effects").
- **Route** — `POST /admin/notifications/requeue-failed`, gated `Depends(_config_update)`, returns
  `{"requeued": N}`, commits. Documented in `packages/contracts/openapi.yaml` in-PR.
- **Web** — `NotificationHealthPanel.tsx`: a "Requeue failed" `Button` beside Refresh, enabled only
  when `failed > 0`; a confirm modal ("Requeue N failed emails? They'll be retried on the next
  drain."); `useMutation` → POST → on success invalidate/refetch the health query + a success toast;
  on error a toast (never a crash). Same `config.update` reach as the panel itself.

### Tests
- **API integration:** seed FAILED + SENT + SUPPRESSED + PENDING rows across **two orgs** → POST for
  org A → only org-A FAILED rows flip to PENDING with `attempts=0` and `next_attempt_at`/`failed_at`/
  `last_error` cleared; SENT/SUPPRESSED/PENDING untouched; org-B FAILED untouched; response
  `{"requeued": <count of A's FAILED>}`. Non-`config.update` caller → 403.
- **Web:** button hidden/disabled when `failed==0`, visible when `failed>0`; confirm → POST →
  refetch; MSW handler for the new endpoint pinned to the real response shape via `satisfies`;
  `import { expect, it } from "vitest"`; aria-labels distinct.

---

## Item #15 — Calm loading/error states (retrofit)

### Problem
The calm state primitives already exist (`lib/states`: `LoadingState`, `ErrorState`,
`MutationErrorState`, `NoAccessState`, `EmptyState`) and newer components use them, but the older
admin tabs and the DocumentDrawer still use a bare `<Loader/>` + `{String(error)}` — which announces
nothing to AT and leaks `Error: HTTP 500` instead of the RFC-9457 message. `DocumentDrawer` handles
no error at all → a **blank-on-error dead-end** on a primary Library flow.

### Fix
- `RolesAdmin.tsx`, `UsersAdmin.tsx`, `ProcessesAdmin.tsx`: page-level `<Loader/>` → `<LoadingState/>`;
  `<Alert>{String(error)}</Alert>` → `<ErrorState onRetry=…/>`. Route ProcessesAdmin's existing 403
  branch through `NoAccessState` (keep the "You need process.read…" copy).
- `DocumentDrawer.tsx`: destructure `isError` too → render `<ErrorState/>` on error, `<LoadingState/>`
  while loading. Closes the blank-on-error dead-end.
- Small inline mutation spinners (`<Loader size="sm"/>`) are left as-is (they're appropriate inline
  affordances, not page-level states).

### Tests
Update the vitest assertions that keyed off the old panel titles / `String(error)` text. jest-axe on
the error/loading branches. `expect`/`it` from `"vitest"`.

---

## Item #16 — Per-route document.title + focus management (WCAG 2.4.2)

### Problem
`index.html` is the sole `<title>` ("EasySynQ", static) across all 40 routes; there is no
focus-management on route change. An external auditor tabbing/bookmarking sees no page identity, and
screen-reader users get no signal that the SPA view changed.

### Fix (centralized, low-touch — avoids editing 40 page components)
- A `useDocumentTitle()` effect mounted in `AppShell`: reads `useLocation().pathname`, matches a
  small **ordered prefix→label** table, sets `document.title = "EasySynQ — <label>"` (fallback
  `"EasySynQ"`).
- **Focus management** (same pathname effect): move focus to the existing `#main-content` landmark
  (add `tabIndex={-1}`) on route change, **skipping the initial mount** so it doesn't steal focus
  from the skip-link on first load. Focus-only (the browser scrolls without animation) → reduced-
  motion-safe by construction; no smooth-scroll call.

### Tests
vitest: `document.title` updates across a simulated nav (assert two distinct routes); focus lands on
`#main-content` after a route change but not on initial mount; jest-axe. A global
`Element.scrollIntoView`/`focus` jsdom stub is already present in `test/setup.ts` if needed.

### Title table (initial)
`/` → Dashboard · `/library` → Library · `/processes` → Processes · `/ncrs` → Nonconformities ·
`/capa` → CAPA · `/audits` → Audits · `/records` → Records · `/risk` → Risk · `/context` → Context ·
`/interested-parties` → Interested Parties · `/compliance` → Compliance · `/admin` → Administration ·
`/setup` → Setup. (Ordered longest-prefix-first; unmatched → bare "EasySynQ". Final list confirmed
against `App.tsx` routes at implementation time.)

---

## Item #17 — Register tables: scroll container + header scope

### Problem
The wide registers render bare `<Table>` with no overflow wrapper and `<Table.Th>` cells with no
`scope`. On a narrow viewport a wide register overflows the page; screen readers can't reliably
associate headers with cells.

### Fix
- Wrap the register `<Table>` in `<Table.ScrollContainer minWidth={…}>` and add `scope="col"` to the
  header cells in `LibraryPage.tsx`, `NcrsPage.tsx`, `CompliancePage.tsx` (the three the survey found
  bare). Purely presentational — no data/logic change.

### Tests
jest-axe + a `scope="col"` assertion on the register headers; keep any looped aria-labels distinct
(single-match `getByLabelText`). `expect`/`it` from `"vitest"`.

---

## Cross-cutting

- **No migration** → the `migrations` CI job is a no-op but still runs (round-trip stays green).
- **Contract** (`openapi.yaml`) gains exactly one endpoint (`POST /admin/notifications/requeue-failed`).
- **Recurring web traps to honor** (`.claude/rules/engineering-patterns.md`): jest-dom×vitest
  `expect`/`it` import; MSW fixtures pinned via `satisfies` to the real serializer shape; distinct
  aria-labels; run the full `/check-web` (strict `tsc` catches `noUncheckedIndexedAccess` nits a
  per-file vitest run misses).
- **Reviewers before PR:** `diff-critic` (whole-branch) + `web-test-trap-reviewer` (the web diff).
