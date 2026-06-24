# S-notify-5b — Notification delivery-health panel + admin Config tab (design)

> Notification family (doc 10 §9, R53/R54), slice 5b. The second of the three slice-5 subsystems
> (5a awareness events → **5b Health panel + Config tab** → 5c SSE), owner-confirmed split + sequencing.
> **FE-heavy + one new read-only BE endpoint. NO migration (head stays 0066). NO new permission key
> (rides `config.update`, catalog stays 102). NO WORM touch.**

- **Date:** 2026-06-24
- **Depends on:** S-notify-1 (the `notification_email` delivery ledger + the `system.email_delivery_failed`
  admin notification), S-notify-3a/4/5a (the digest/timer/awareness rows that populate the ledger), the
  existing `GET`/`PATCH /admin/config` surface (S-rec-3 + S-notify-1/3a), the existing `AdminShell` tabbed
  admin shell (S8d) + `lib/states` primitives (S-design-p3/p4) + the slice-3b settings-form idioms.
- **Migration head:** `0066` → **`0066` (unchanged)**.
- **Validated:** 2026-06-24 — a 6-lens code-anchor + adversarial-refute workflow confirmed every code
  anchor and found **0 critical / 1 major / 2 minor / 2 nit** (all folded below). The three highest-risk
  lenses held with zero confirmed findings: **authz/cross-org/leak** (`config.update` is SYSTEM-only;
  `caller.org_id` isolation; `recent_failures` ⊆ the drain's existing operational-only admin payload, R32
  safe), **query-correctness** (`pending_now` is byte-identical to the drain's real claim predicate;
  FAILED⟹`failed_at`; SUPPRESSED terminal; digest rows don't distort), **contract/scope** (PATCH verb
  correct, no-migration sound, no creep/under-scope). The major finding (the ConfigAdmin page-gate →
  data-403 forbidden flag, §5) + the 4 lesser findings are folded.

---

## 1. Goal & context

Email notifications can fail (SMTP down, bad relay, a rejected recipient). Today the **only** failure
surface is the `system.email_delivery_failed` **in-app admin notification** the drain emits per exhausted
email (`drain.py::_emit_failure`, one per `notification_email` that hits `attempts ≥
notification_max_send_attempts`). There is **no aggregate health view**: an admin cannot see "how many
emails are failing", "how old is the oldest stuck row", "is the awareness fan-out backed up", or "is email
even turned on". The rich state exists in `notification_email` (`status` ∈
`{PENDING, SENT, FAILED, SUPPRESSED}`, `attempts`, `next_attempt_at`, `last_error`, `failed_at`,
`created_at`, `email_kind`) + `awareness_event.fanned_out_at`, but no endpoint reads it.

Separately, the two org-level notification toggles — `notifications_email_enabled` (the per-org email
opt-in, default OFF) and `notifications_escalation_pierce_quiet_hours` (escalation pierces quiet hours,
default ON) — are **fully wired on the backend** (`GET`/`PATCH /admin/config`, gated `config.update`,
audited `CONFIG_UPDATED`, already in `openapi.yaml`) but have **no FE control**. Slice 3b explicitly
deferred the pierce toggle ("there is no admin-config FE at all → it needs a net-new Config tab"); slice 4
re-named the same gap. The slice-2 `NotificationSettingsPage` master toggle even tells the user their
*administrator* must enable org email — but there's nowhere for the admin to do it.

5b closes both: the **first admin Config tab** (homing the two org toggles) **+** a read-only
**delivery-health panel**, made discoverable by the **first admin nav entry**.

## 2. Scope

**In scope:**

**Backend (one read-only endpoint, no migration):**
- A pure aggregator `services/notifications/health.py::get_delivery_health(session, org_id) -> dict` —
  org-scoped counts + the recent-failures list + the awareness backlog (§4).
- One new route `GET /admin/notifications/health` in `api/config.py` (the admin cohort; shares the
  `_config_update` gate), returning the aggregator output.
- `openapi.yaml`: **+** the `/admin/notifications/health` GET path + `NotificationDeliveryHealth` +
  `NotificationEmailFailure` schemas (`additionalProperties: false`). **No** change to the existing
  `/admin/config` path/schemas (already present).

**Frontend:**
- `apps/web/src/admin/ConfigAdmin.tsx` — a new admin page with **two sections**: (A) the notification
  config toggles (consuming the existing `GET`/`PATCH /admin/config`); (B) the read-only delivery-health
  panel (consuming the new endpoint). §5.
- `admin/hooks.ts` — `useOrgConfig` / `useUpdateOrgConfig` / `useNotificationHealth` (React-Query +
  `useApi()`).
- `lib/types.ts` — `OrgConfig`, `OrgConfigUpdate`, `NotificationDeliveryHealth`, `NotificationEmailFailure`.
- `admin/AdminShell.tsx` — **+** a `Config` tab + pathname detection.
- `App.tsx` — **+** `<Route path="config" element={<ConfigAdmin />} />` under `/admin`.
- `app/shell/TopBar.tsx` — **+** an Account-menu `Administration` link (→ `/admin`), gated
  `usePermissions().can("config.update")` (the first discoverable admin nav).
- MSW handlers (stateful `GET`/`PATCH /admin/config` + a `GET /admin/notifications/health` fixture pinned
  via `satisfies`) + tests.

**Out of scope (named residuals, §11):**
- **No requeue/redeliver write action** — FAILED rows are diagnostic only; a `config.update`-gated
  FAILED→PENDING requeue is a named follow-up (owner: read-only panel).
- **No SoD/leadership config flags in the UI** — the Config tab surfaces *only* the two notification
  toggles; `capture_pre_release_templates` / `allow_self_disposition` / `allow_capa_self_verify` /
  `leadership_release_requires_top_management_authorization` (also FE-less) are their own slice (owner:
  notification flags only).
- **No rich delivery stats** — no latency histograms, per-error distribution, digest backlog, or last-drain
  timestamp (owner: moderate depth).
- **No SSE** (5c). **No migration / no new permission key / no new role grant.**
- The slice-4 timer-sweep claim-threshold filter (5a's unused `_pending_event_ids(now)` / the
  `remind_2_sent_at IS NULL` tautology) — unrelated subsystem, **not** folded in.

## 3. Architecture

```
[ admin ] ── TopBar "Administration" (gated config.update) ──▶ /admin ──▶ /admin/config  (AdminShell tab)
                                                                                  │
                                  ┌───────────────────────────────────────────────┴───────────────┐
                                  ▼                                                                 ▼
                       Section A — Config toggles                                  Section B — Delivery health (read-only)
                       useOrgConfig()  ── GET  /admin/config                       useNotificationHealth() ── GET /admin/notifications/health
                       useUpdateOrgConfig() ─ PATCH /admin/config (partial)                         │
                       (working-state + dirty-diff + Save; invalidate                               ▼
                        ["admin-config"] AND ["notification-health"])              services/notifications/health.get_delivery_health(session, org_id)
                                                                                    └─ org-scoped aggregates over notification_email + awareness_event
```

Both halves are gated `config.update` (the whole tab is System-Administrator territory). The config half is
pure FE over an existing endpoint; the health half is a thin admin route over a pure, side-effect-free
aggregator. No migration, no WORM, no concurrency — this slice is deliberately the simplest of slice-5.

## 4. Backend — the health aggregator + endpoint

**`services/notifications/health.py::get_delivery_health(session, org_id) -> dict[str, Any]`** — pure,
read-only, org-scoped. No `now` injection: comparisons use the DB `func.now()`; ages are derived FE-side
from returned ISO timestamps (clock-free + test-deterministic — the AsOf precedent).

Shape (the `_health_view`):
```jsonc
{
  "org_email_enabled": bool,            // from system_config — so a zero panel is contextualised
  "email": {
    "failed": int,                      // status=FAILED
    "pending_now": int,                 // PENDING & (next_attempt_at IS NULL OR <= now())
    "pending_scheduled": int,           // PENDING & next_attempt_at > now()  (backoff lease)
    "suppressed": int,                  // status=SUPPRESSED
    "oldest_pending_at": "ISO" | null   // MIN(created_at) over PENDING
  },
  "recent_failures": [                  // last 10, failed_at DESC NULLS LAST
    { "recipient_email": str, "last_error": str|null, "attempts": int,
      "failed_at": "ISO"|null, "email_kind": "single"|"digest" }
  ],
  "awareness": {
    "pending": int,                     // awareness_event WHERE fanned_out_at IS NULL
    "oldest_pending_at": "ISO" | null   // MIN(created_at) over unfanned
  }
}
```

Queries (3 round-trips, all `WHERE org_id = :org_id`):
1. **Email aggregates** — one statement using conditional aggregates:
   `count(*) FILTER (WHERE status='FAILED')`, `… PENDING AND (next_attempt_at IS NULL OR next_attempt_at <=
   func.now())`, `… PENDING AND next_attempt_at > func.now()`, `… status='SUPPRESSED'`,
   `min(created_at) FILTER (WHERE status='PENDING')` (SQLAlchemy `func.count().filter(...)` →
   `count(*) FILTER (WHERE …)`).
2. **Recent failures** — `select(recipient_email, last_error, attempts, failed_at, email_kind)
   .where(org_id, status=FAILED).order_by(failed_at.desc().nullslast()).limit(10)`.
3. **Awareness backlog** — `count(*)` + `min(created_at)` `WHERE org_id AND fanned_out_at IS NULL`.

`org_email_enabled` from `session.get(SystemConfig, org_id).notifications_email_enabled`.

**Honest semantics (validated):** the `pending_now` predicate is *byte-identical* to the drain's real
claim (`drain.py` — `status='PENDING' AND (next_attempt_at IS NULL OR next_attempt_at <= now())`), and
`pending_now`/`pending_scheduled` are mutually exclusive + exhaustive over PENDING. A FAILED row **always**
has `failed_at` stamped in the same commit as `status=FAILED` (so `ORDER BY failed_at DESC NULLS LAST` is
sound; the `NULLS LAST` is purely defensive against a hand-seeded row), and SUPPRESSED is terminal (so its
bucket is meaningful). One **benign, bounded** transient: an email whose `attempts ≥ max` but whose backoff
has lapsed counts as `pending_now` for up to one drain cycle (~120 s) before the next drain flips it to
FAILED — this is honest "pending a final pass" semantics, not a defect; no special-casing.

**`recent_failures` payload safety (R32):** the fields are `recipient_email` + `last_error` (a truncated
exception string, ≤1000 chars, set by the drain) + counters — **operational-only, no subject/document
metadata**, identical to what the drain already exposes to admins via `system.email_delivery_failed`
(admins hold no `document.read`; R32). `last_error` is rendered as a **text node** FE-side (never HTML).

**Endpoint** — `GET /admin/notifications/health` added to `api/config.py` (same router/prefix `/api/v1`,
tag `admin`), gated `caller: AppUser = Depends(_config_update)` (the same `config.update` gate as
`GET /admin/config` — single permission lights the whole Config tab). Org from `caller.org_id` → strict
org isolation (a caller can only read their own org's health; no `org_id` request param). The aggregator
lives in `services/notifications/` (domain-correct); the route is a thin handler (`api → services`, the
right import direction).

**No migration:** pure reads over existing tables; no ORM/model change → `migrations` CI trivially green
(head stays `0066`). `awareness_event` is already SELECT-able by the app role (0066 granted INSERT/SELECT/
UPDATE); `notification_email` likewise. No new grant needed for reads.

## 5. Frontend — `ConfigAdmin.tsx` (two sections) + wiring

**Hooks (`admin/hooks.ts`, React-Query + `useApi()`):**
- `useOrgConfig()` → `GET /api/v1/admin/config`, `queryKey ["admin-config"]`, `retry:false`,
  **`refetchOnWindowFocus:false` + `refetchOnReconnect:false`** (no clobbering unsaved toggle edits — the
  #273 lesson; the prod `QueryClient` leaves these ON globally). **Derives a `forbidden` flag** =
  `query.error instanceof ApiError && query.error.status === 403` (the `features/capa/hooks.ts` /
  `ProcessesAdmin` precedent) — this is the page's no-access boundary (see the ConfigAdmin gate below),
  **not** a `usePermissions` probe.
- `useUpdateOrgConfig()` → `api.send("PATCH", "/api/v1/admin/config", body)`, **`onSuccess` invalidates
  BOTH `["admin-config"]` AND `["notification-health"]`** (flipping email-on refreshes the health banner).
- `useNotificationHealth()` → `GET /api/v1/admin/notifications/health`, `queryKey ["notification-health"]`,
  `retry:false`. **No auto-poll** (calm; D1 single-host) — a manual Refresh button + `<AsOf>` freshness.

**`admin/ConfigAdmin.tsx`** — no `token` prop (uses `useApi()` from context, the modern notification-hooks
idiom; diverges intentionally from the older token-threaded `UsersAdmin`/`ProcessesAdmin`). **Gate the
page's no-access state on `useOrgConfig().forbidden` (the GET `/admin/config` 403), NOT on
`usePermissions().can(...)`** (the workflow's major finding): `usePermissions().can()` returns `false`
while the `/me/permissions` query is in flight, and the `/admin` subtree never warms that cache (only
`LeftRail` on `/` does), so a `can()`-gate would **flash `NoAccessState` to a legitimate admin** on a
cold-cache deep-link to `/admin/config`. The data-403 is the real boundary, matches the
`ProcessesAdmin`/`CapaBoardPage` precedent, and drops a round-trip — so `ConfigAdmin` does **not** import
`usePermissions` at all. Render order: `cfg.forbidden ? <NoAccessState message="You need config.update to
manage notification configuration." /> : cfg.isError ? <ErrorState onRetry={cfg.refetch}/> : cfg.isLoading
|| !working ? <LoadingState/> : <two sections>` (forbidden → error → loading → content). Two sections:

**Section A — Notification configuration** (mirrors `NotificationSettingsPage` form mechanics; renders only
once the page-level forbidden→error→loading gate above has passed, so `working` is seeded):
- Working-state mirror seeded from `useOrgConfig().data` via `useEffect`; a `buildUpdate` dirty-diff →
  partial PATCH body (only changed fields); Save disabled until `dirty`.
- Two `Switch`es (distinct `aria-label`s):
  - *"Email delivery (organisation-wide)"* → `notifications_email_enabled`; description: opt-in master
    switch; SMTP env must be configured; default off; emails carry summary + link only.
  - *"Escalation pierces quiet hours"* → `notifications_escalation_pierce_quiet_hours`; description: when
    on (default), critical / escalation notifications are delivered immediately even inside a user's quiet
    hours.
- `<MutationErrorState title="Couldn't save configuration" error={update.error}/>` on PATCH failure (the
  `title` prop is required — the `NotificationSettingsPage` precedent); a post-save settled state.
- `OrgConfig` is exactly **`org_id` + 6 boolean toggles** (faithful to `_config_view` — 7 keys total). The
  working-state types **only** the two notification toggles, so the partial-PATCH diff never sets the
  other **4** toggles → they are untouched (additive-safe). The `satisfies OrgConfig` fixture mirrors
  `_config_view`'s 7-key shape verbatim (§7's "copy the serializer, never hand-type" rule).

**Section B — Delivery health** (read-only, same `config.update` gate):
- Load-error gate (`health.isError`→`ErrorState` with Try-again; never an infinite spinner).
- A calm **info `Alert`** when `org_email_enabled === false`: "Email delivery is off for the organisation —
  no emails are being sent." (so an all-zero panel isn't misread as "healthy").
- **Summary stat cards:** Failed · Pending (now) · Scheduled retry · Suppressed · Awareness backlog. The
  **Failed** card carries the canonical danger glyph **✕** + `--es-danger-text` when `> 0` (the S-design-p4
  Drift-"Failing"-count pattern — meaning by shape+colour+label, **never colour alone**, DP-5); the rest
  are neutral. "Oldest pending" shown as a relative age (from `email.oldest_pending_at`) only when
  `pending_now + pending_scheduled > 0`; the awareness card shows its own oldest-pending age when backlogged.
- **Recent failures** table: recipient · error (`last_error` as a **text node**, truncated for display) ·
  attempts · when (relative, from `failed_at`). `<EmptyState message="No delivery failures." />` when the
  list is empty.
- `<AsOf at={health.dataUpdatedAt} prefix="Checked" />` + a Refresh `Button`
  (`onClick={() => health.refetch()}`, `loading={health.isFetching}`).

**Wiring:**
- `AdminShell.tsx`: extend the pathname ladder with `pathname.includes("/admin/config") ? "config" : …`
  and add `<Tabs.Tab value="config">Config</Tabs.Tab>` (after Processes).
- `App.tsx`: add `<Route path="config" element={<ConfigAdmin />} />` inside the existing `/admin` group
  (still under the `operational` gate — the page self-gates on `config.update`).
- `TopBar.tsx`: import `usePermissions`; add `{perms.can("config.update") && <Menu.Item component={Link}
  to="/admin">Administration</Menu.Item>}` to the Account `Menu.Dropdown` (above "Sign out"). Gating on
  `config.update` is the System-Administrator proxy — coarse-but-correct for v1's admin audience (a
  hypothetical process-owner-only admin wouldn't see it; the per-tab self-gating is the real boundary).
  Adding `usePermissions` to TopBar issues the (already widely-cached) `GET /me/permissions` query — TopBar
  renders only inside the authed, operational shell, so the probe is safe; no redirect-loop risk (it gates
  a menu item, never the shell). **Note the asymmetry vs ConfigAdmin:** TopBar uses `can()` for *affordance
  visibility* (a transient `can()===false` merely hides the menu item for a beat — harmless, the `LeftRail`
  precedent), whereas the ConfigAdmin *no-access panel* uses the data-403 forbidden flag (§5) — a transient
  false there would flash a full NoAccessState to a legitimate admin, which is why the page gate must not
  use `can()`.

## 6. Permissions

**No new permission key (R38; catalog stays 102).** Both the config endpoints and the new health endpoint
ride the existing SYSTEM-domain `config.update` (held only by System Administrator; the R35 two-tier guard
keeps a content-tier QMS Owner from holding it). A read gated on an `*.update` key matches the existing
`GET /admin/config` precedent (consistency over a semantically-tidier unused `config.read`). No role/grant
seed.

## 7. Testing

**Backend** (`apps/api/tests`):
- **Unit** (`tests/unit/test_notification_health.py` or service-level integration if it needs the DB):
  `get_delivery_health` over seeded `notification_email` rows across every status (FAILED/PENDING-now/
  PENDING-scheduled/SUPPRESSED/SENT) + a seeded `awareness_event` (fanned + unfanned) → assert each count,
  `oldest_pending_at`, the `recent_failures` ordering (`failed_at DESC NULLS LAST`) + the 10-row cap, and
  `org_email_enabled`. Prove **org isolation** (a second org's rows are excluded). **Delta-based /
  run-scoped** assertions — never assume a clean *or* dirty shared DB (the S-ing-4/S-drift-2 rule);
  FK-ordered cleanup of any org/user/rows the test creates (the S-notify-4 `test_restore` lesson — a leaked
  `Organization` aborts `test_restore`'s `scalar_one()`).
- **Integration** (`tests/integration`): `GET /admin/notifications/health` → **200 for a `config.update`
  holder**, **403 without it** (the gate regression backstop), and a shape assertion against the serializer.

**Frontend** (`apps/web`, vitest + MSW + jest-axe):
- `admin/ConfigAdmin.test.tsx`: reflects loaded config (two switches in the loaded state) + **jest-axe**;
  toggling a switch + Save → a **partial PATCH** carrying only the changed field (stateful MSW: `GET`+`PATCH`
  sharing `let current` so the refetch models prod; a post-save "stays toggled" assertion); the **load-error
  gate** (`isError`→`ErrorState`+Try-again, not an infinite spinner); **no-access** (`config.update` absent
  → `NoAccessState`, no write control); the **no-access flash regression** — a deep-link mount with the
  config GET 403 shows `NoAccessState` **without** a `usePermissions` round-trip (assert no `/me/permissions`
  fetch is required for the panel); the **health panel** — failures render (recipient/error/attempts/when)
  **with** the danger glyph (`toHaveTextContent(TONE_GLYPH.danger)` on the Failed card) on a non-zero Failed
  count **AND a separate negative test** asserting the glyph is **absent** on a `Failed:0` fixture
  (`not.toHaveTextContent` + `queryByText(TONE_GLYPH.danger)` not in document — the DriftStatusPage
  two-test pattern; without the negative a glyph-always-rendered bug false-PASSes), an empty list →
  `EmptyState`, and the email-off `Alert` when `org_email_enabled:false`.
- `admin/AdminShell.test.tsx` (extend or add): the `Config` tab renders + navigates.
- `app/shell/TopBar.test.tsx` (extend or add): the `Administration` item. **Both cases open the Account
  menu first** (`user.click(await screen.findByRole("button",{name:"Account"}))`, the existing TopBar test
  pattern) — never assert on an unopened menu. Shown: with a `server.use()` ALLOW `/me/permissions` override
  → `await findByRole("menuitem",{name:"Administration"})`. Hidden: leans on the **default empty
  `/me/permissions` handler** (returns `permissions:[]`), and after opening the menu asserts
  `queryByRole("menuitem",{name:"Administration"})` is absent (await a present sibling such as "Sign out"
  first, the LeftRail precedent, so absence isn't asserted before the async permissions query settles).
- Traps: `import { expect, it } from "vitest"`; `userEvent.setup()` for Mantine `Menu`/`Switch`; MSW
  fixtures pinned via `satisfies <Type>` from the **real serializer shape** (copy `_config_view` +
  `_health_view`, never hand-typed); the global `scrollIntoView` stub already covers any `Select` (none
  added here).

**Gates:** `/check-api` (ruff/mypy-strict/unit), `/check-contracts` (redocly on the openapi addition),
`/check-web` (eslint/tsc/build/full vitest). `/check-migrations` is a no-op (no migration) but harmless to
run. Pre-PR: `diff-critic` + `web-test-trap-reviewer` on the branch diff; a live-smoke of the Config tab.

## 8. Contract / no-migration checklist

- [ ] `openapi.yaml`: add the `/admin/notifications/health` GET path (tag `admin`, `200` →
      `NotificationDeliveryHealth`, `403` → `ProblemResponse`) + `NotificationDeliveryHealth` +
      `NotificationEmailFailure` schemas, `additionalProperties: false`, redocly-lint clean.
- [ ] **No** change to the existing `/admin/config` path or `OrgConfig`/`OrgConfigUpdate` schemas (already
      present + correct).
- [ ] **No** migration (head stays `0066`); **no** ORM/model change → `alembic check` unaffected.
- [ ] **No** new permission key (catalog stays 102); **no** role/grant seed.
- [ ] `services/notifications/health.py` imports only `db.models` + SQLAlchemy (no `api/` import; the right
      authority direction).

## 9. Owner decisions captured

- **AskUserQuestion (2026-06-24, brainstorming):**
  - Next slice = **5b** (Health panel + Config tab) over 5c (SSE).
  - Health depth = **Moderate** (failure count + recent failures + outbox backlog [pending-now vs
    scheduled-retry] + oldest-pending age + suppressed + awareness fan-out backlog) — not Minimal, not Rich.
  - Config-tab scope = **notification flags only** (the two deferred toggles), not all org-config flags.
  - Retry action = **read-only panel** (no requeue write action; a named follow-up).
  - Admin nav = **add a TopBar admin entry** (the first discoverable admin nav), not URL-only.

## 10. Spec-review questions — open

1. **Health gate key:** ride `config.update` (matches `GET /admin/config`) vs the unused-but-seeded
   `config.read`. **Provisional: `config.update`** (single permission lights the whole tab; precedent).
   Confirm.
2. **`recent_failures` count + window:** last **10** by `failed_at DESC`, no time window. Enough for "what's
   failing right now"? (A 30-day window or a configurable N is a trivial follow-up if 10 proves thin.)
3. **Hooks/component location:** `admin/hooks.ts` + a `token`-less `ConfigAdmin` (the React-Query+`useApi()`
   idiom) vs the older token-threaded admin-page convention. **Provisional: React-Query hooks** (testable,
   matches the notification feature). Confirm the divergence is acceptable. _(The page no-access gate is
   now resolved by the workflow's major finding — the GET `/admin/config` 403 forbidden flag, not
   `usePermissions`; §5.)_

## 11. Named residuals (not faked; out of scope for 5b)

- **A requeue/redeliver action** for FAILED emails (a `config.update`-gated FAILED→PENDING reset clearing
  `attempts`/`next_attempt_at`, idempotent + audited) — owner deferred; read-only for 5b.
- **The other org-config flags in the UI** (`capture_pre_release_templates`, `allow_self_disposition`,
  `allow_capa_self_verify`, `leadership_release_requires_top_management_authorization`) — also FE-less; each
  carries compliance semantics needing careful copy → its own slice.
- **Rich delivery stats** — latency histogram (`sent_at − created_at`), per-`last_error` distribution,
  digest backlog (`notification.digest_due_at ≤ now AND digested_at IS NULL`), last-drain/last-sweep
  timestamps — Rich depth, deferred.
- **A more inclusive admin-nav gate** (show "Administration" for any admin-tab permission, not just
  `config.update`) — only if a non-`config.update` admin role ever lands.
- **5c** (SSE replacing the slice-2 60s bell poll) — the last slice-5 subsystem.
- **The slice-4 timer-sweep claim-threshold filter** (the `remind_2_sent_at IS NULL` tautology / unused
  `_pending_event_ids(now)`) — unrelated subsystem; a focused escalation-sweep follow-up.
