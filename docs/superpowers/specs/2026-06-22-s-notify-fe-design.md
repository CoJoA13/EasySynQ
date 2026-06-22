# S-notify-fe — the SPA notification bell + center (Notification family, slice 2)

> **Date:** 2026-06-22 · **Branch:** `feat/s-notify-fe` · **Type:** FE-only
> **Predecessor:** S-notify-1 (the BE notification spine + email delivery, R53, migration 0063, merged
> `766dc55`). This slice puts a face on that spine — it *consumes* the existing endpoints.

## 1 · Context & goal

S-notify-1 shipped the backend notification spine: a transactional-outbox, the `outbox_drain` worker,
email delivery (opt-in, default OFF), `task.assigned` wired at all six task-creation sites, and the
authenticated-self read/preference endpoints. **Nothing surfaces those rows in the SPA yet.** A user only
learns of an assignment by email (if enabled) or by happening onto `/tasks`.

This slice delivers the **in-app awareness UI** (doc 10 §9.1 — "Always on; the bell + center"): a TopBar
notification **bell** with an unread badge and a notification **center** (popover + a full page) for
read/unread triage with deep-links back into the vault, plus the **minimal per-user master email toggle**
that un-breaks the email "Manage notifications" link slice 1 left dangling.

Doc 10 §9 draws a deliberate line: **Notifications = awareness; My Tasks = work.** This slice honours that
— the bell is awareness; the existing Tasks entry stays the work surface.

### Decomposition position (from the slice-1 spec §2)

| Slice | Scope | Status |
|---|---|---|
| 1 | BE spine + email | ✅ merged (`766dc55`) |
| **2 — this spec** | **In-app FE — the bell + center, read/unread, deep links + the minimal master email toggle** | **now** |
| 3 | Preferences matrix + digests + quiet hours; DOC_ACK email switches on | deferred |
| 4 | Escalation timers (`SlaPolicy`/`working_calendar`/`timer_sweep`) | deferred |
| 5 | Awareness events (`doc.released`/…) read-scope filtered + Health-dashboard delivery-failure panel | deferred |

## 2 · Binding constraints (what this slice must NOT do)

- **FE-only.** No migration (head stays **0063**). No new permission key (catalog stays **102**, R38).
  No contract change → **`packages/contracts/openapi.yaml` is untouched** (the slice-1 endpoints are
  already documented there).
- **No decisions-register entry.** This is a consumption slice under the existing R53; confirmed with the
  owner. (If the deferred subtype-routing residual is ever picked up it is a *BE* change to
  `services/notifications/subjects.py`, out of scope here.)
- **Authenticated-self only.** Every notification read/write is self-scoped server-side (the `GET /tasks`
  posture). The SPA adds **zero gating** beyond "you are signed in" — no `usePermissions` probe, no
  `forbidden`/`NoAccessState` path (a self endpoint cannot 403 the caller). Errors are handled calmly
  (loading / error-with-retry / empty), never as a no-access panel.
- **D2 vault→mirror:** the `deep_link` resolves *back into the vault* (an in-app react-router navigation);
  it never points the user at the read-only mirror.

## 3 · The endpoints consumed (verified against `apps/api/src/easysynq_api/api/notifications.py::_view`)

```
GET  /api/v1/notifications?unread_only={bool}&limit={int}
     → 200 [{ id, event_key, subject_type, subject_id|null, title, body,
              deep_link, created_at, read_at|null }]   // newest-first; read_at null = unread; limit≤200
POST /api/v1/notifications/{id}/read   → 200 { status: "ok" }   // 404 on a foreign/unknown id
POST /api/v1/notifications/read-all    → 200 { marked: int }
GET  /api/v1/me/notification-preferences → 200 { email_enabled: bool }   // default true if no row
PUT  /api/v1/me/notification-preferences → 200 { email_enabled: bool }   // echoes the body
```

`deep_link` is an **absolute** URL (`app_base_url` + a route fragment), e.g.
`http://localhost/documents/<id>`, `/dcrs?dcr=<id>`, `/capa?capa=<id>`, `/improvement?initiative=<id>`,
`/management-reviews/<id>`, with a `/tasks` fallback for unmapped subject types. The SPA must relativise it
(strip the origin → `pathname + search`) and navigate via react-router.

## 4 · The four owner decisions (locked via AskUserQuestion, 2026-06-22)

1. **Bell — merge into one bell.** The TopBar `IconBell` becomes the notification bell (opens the center
   popover; badge = unread-notification count). The standalone **ack-count Indicator is retired**; the
   **Tasks** ActionIcon stays as the explicit *work* entry. This is a faithful "acks + notifications
   together" because, since slice 1, a DOC_ACK assignment generates a `task.assigned` notification — so
   the one bell's unread badge already encompasses new-ack awareness. **Accepted consequence:** the header
   no longer shows a persistent "N open acks to do" work-count; open acks remain reachable via the Tasks
   icon and via each ack notification's deep-link. `useAckCount` is **kept** (Home's `DoCard` still uses
   it) — only the TopBar usage changes.
2. **Center — popover + full `/notifications` page.** A quick-triage popover off the bell *and* a dedicated
   page reached via "See all" (progressive disclosure: inline before full screen).
3. **Preferences — ship the minimal master-email-toggle now** at `/settings/notifications`. A single
   on/off Switch bound to `GET/PUT /me/notification-preferences`. This un-breaks the email `{{prefs_link}}`
   (`services/notifications/subjects.py::prefs_link()` already points at `/settings/notifications`). The
   per-event **digest matrix / quiet hours stays deferred to slice 3**.
4. **Polling — 60s background poll + refetch on open.** The unread-count badge refetches every 60 s; the
   popover/page lists refetch on open. (Doc 10 §9.1 anticipates "SPA polls / SSE"; SSE is a future slice.)

## 5 · Architecture

A new `apps/web/src/features/notifications/` directory, mirroring the established feature-dir shape
(`hooks.ts` + `mutations.ts` + components + a `.test` per unit). Each unit has one clear purpose and a
narrow interface.

### 5.1 Data layer — `hooks.ts`

Self-scoped react-query hooks. Query keys are prefixed `["notifications", …]` so a single
`invalidateQueries({ queryKey: ["notifications"] })` refreshes the badge **and** every list together.

```ts
// queryKey: ["notifications", "count"]  — the badge; the only polled query.
export function useNotificationCount(): { count: number; isError: boolean; isLoading: boolean }
//   queryFn: api.get<Notification[]>("/api/v1/notifications?unread_only=true&limit=99")
//   refetchInterval: 60_000, retry: false
//   count = data?.length ?? 0, but reported ALONGSIDE isError (mirror useAckCount exactly): the bell
//   reads count ONLY when !isError, so an error renders the indeterminate dot — never a fake 0. The 0
//   here is a placeholder consumed only behind the isError guard, not a confident "you have no unread".

// queryKey: ["notifications", "list", { scope }]  — the popover (scope:"recent", limit 15) + page (scope:"all", limit 50)
export function useNotifications(scope: "recent" | "all", enabled = true): UseQueryResult<Notification[]>
//   recent: limit 15, read+unread;  all: limit 50, read+unread;  retry:false

// queryKey: ["notification-preferences"]
export function useNotificationPreferences(): UseQueryResult<{ email_enabled: boolean }>
```

`Notification` is a local TS type pinned to `_view`:
```ts
export interface Notification {
  id: string; event_key: string; subject_type: string; subject_id: string | null;
  title: string; body: string; deep_link: string; created_at: string; read_at: string | null;
}
```

### 5.2 Mutations — `mutations.ts`

```ts
export function useMarkRead()      // POST /notifications/{id}/read; onSuccess → invalidate ["notifications"]
export function useMarkAllRead()   // POST /notifications/read-all;   onSuccess → invalidate ["notifications"]
export function useSetEmailEnabled() // PUT /me/notification-preferences; onSuccess → invalidate ["notification-preferences"]
```

No optimistic updates (the web-trap reviewer flags optimistic mutations; the 60 s cadence + on-success
invalidation is calm and correct). A `useMarkRead` 404 (foreign/already-deleted id) is swallowed quietly
— the list will simply not contain it after the refetch.

### 5.3 `deepLink.ts` — pure relativiser

```ts
// Absolute app URL → a react-router-navigable "pathname+search". Falls back to "/tasks" on any parse
// failure so a malformed/foreign link never throws or leaves a dead click. Same-origin trusted (server
// builds it from app_base_url); we navigate IN-APP via useNavigate, so there is no open-redirect surface.
export function toRoutePath(deepLink: string): string {
  try { const u = new URL(deepLink); return (u.pathname || "/tasks") + u.search; }
  catch { return "/tasks"; }
}
```

### 5.4 Components

- **`NotificationBell.tsx`** (TopBar). A Mantine `Popover` whose target is the `IconBell` `ActionIcon`
  wrapped in an `Indicator`. The badge reuses the existing bell's **three-state, never-confident-zero**
  pattern from `TopBar`/`useAckCount`:
  - unread > 0 and no error → numeric label (display `"99+"` when the count hits the 99 cap);
  - error → a small gray **indeterminate dot**, `aria-label` "Notifications (count unavailable)";
  - genuine zero → no badge.
  The popover body lists the 15 most-recent via `<NotificationItem>`; a footer row holds **Mark all read**
  (disabled while the mutation is pending or when nothing is unread), a **Notification settings** gear
  (`Link` → `/settings/notifications`), and **See all** (`Link` → `/notifications`). Opening the popover
  enables/refetches the `recent` list. The bell's `aria-label` includes the unread count
  ("Notifications, 3 unread" / "Notifications" / "Notifications (count unavailable)").
- **`NotificationItem.tsx`**. One row: an **unread marker = a filled dot glyph + an `aria-label`/visually-
  hidden "Unread" text** (DP-5: status by shape+label, never colour alone) shown only when `read_at` is
  null; a **bold** title when unread (normal when read); a dimmed `body` (rendered as a plain **text node**
  — never `dangerouslySetInnerHTML`); a `formatRelativeTime(created_at)` stamp with a `title` of
  `formatTimestamp(created_at)` (absolute, tz-explicit). The whole row is a button: clicking **marks read
  then navigates** `toRoutePath(deep_link)` (and closes the popover via an `onNavigate` callback). A
  separate, explicitly-labelled **"Mark read"** icon-button (shown only when unread) marks read **without**
  navigating (`stopPropagation`). Props: `{ notification, onNavigate?: () => void }`.
- **`NotificationsPage.tsx`** (`/notifications`). A `Container` + `Title` "Notifications", a **Mark all
  read** button, and the `all`-scope list rendered with `lib/states` (`LoadingState` → `SkeletonList` while
  loading, `ErrorState` with retry on failure, `EmptyState` "You're all caught up" when empty). Reuses
  `<NotificationItem>` (no `onNavigate` → it navigates directly).
- **`NotificationSettingsPage.tsx`** (`/settings/notifications`). A `Container` + `Title` "Notification
  settings", a single Mantine **Switch** "Email notifications" bound to `email_enabled` (controlled by the
  query value, `onChange` → `useSetEmailEnabled`), `LoadingState` while reading, `MutationErrorState` on a
  failed save, and a calm "Saved" affordance. Copy notes that email is **org-gated** (an admin must enable
  email delivery org-wide) and carries summaries + links only — never controlled content. A back link to
  the app. (The per-event matrix is explicitly "coming in a later release".)

### 5.5 Shell + routing edits

- **`app/shell/TopBar.tsx`**: replace the standalone ack `Indicator`+`ActionIcon` block with
  `<NotificationBell />`. Keep the Tasks `ActionIcon`. Add a **"Notification settings"** `Menu.Item`
  (`component={Link} to="/settings/notifications"`) to the Account menu, above "Sign out". Remove the now-
  unused `useAckCount` import **from TopBar only** (the hook file stays; `DoCard` imports it).
- **`App.tsx`**: add two routes under the root `AppShell` layout — `notifications` → `NotificationsPage`
  and `settings/notifications` → `NotificationSettingsPage`. No nested layout (one screen each; YAGNI).
- **No LeftRail entry** — awareness lives in the bell; the page is reached via "See all" and the settings
  via the Account menu / popover gear / the email link.

## 6 · Accessibility & design (DP-5; WCAG 2.2 AA; reduced-motion)

- Unread is carried by **dot-glyph + "Unread" label + bold weight** — never colour alone. Read rows are
  visually quieter (normal weight, no dot) but fully legible.
- Distinct `aria-label`s throughout (no `getByLabelText` collisions): the bell, the per-row "Mark read"
  button (unique enough or scoped via `within`), "Mark all read", "See all", "Notification settings".
- No custom animation (Mantine's Popover honours reduced-motion). No colour-only signalling.
- Calm, restrained density: the popover shows a bounded recent set; the page paginates by the server limit
  (50) — a "showing the 50 most recent" note keeps the cap honest (no silent truncation).

## 7 · Testing strategy (mirrors the web-track conventions + the recurring traps)

- **Every test file `import { expect, it } from "vitest"`** (the jest-dom×vitest trap — only `tsc`/full
  `/check-web` catches a bare global `expect`).
- **MSW fixtures pinned via `satisfies Notification[]`** to the `_view` shape (copied from `api/notifications.py`,
  never guessed/from the mockup). Add handlers for all five endpoints to `test/msw/handlers.ts` (the count
  handler must honour `unread_only`; the `read`/`read-all` handlers return the documented shapes;
  `/me/notification-preferences` GET+PUT).
- **Determinism:** render timestamps via `formatRelativeTime(created_at, FIXED_NOW)` in component code is
  not possible (the prop is implicit `Date.now()`), so tests **freeze the clock** (`vi.useFakeTimers()` +
  `vi.setSystemTime(...)`) or assert on the absolute `title`/the stable title/body text rather than the
  relative string. Prefer asserting title/body/unread-state over the exact "x min ago".
- **Unit coverage:**
  - `deepLink.test.ts` — absolute→relative for each subject route + the malformed→`/tasks` fallback.
  - `NotificationItem.test.tsx` — unread dot+label present when unread / absent when read; bold title;
    row click marks-read + navigates; "Mark read" button marks-read without navigating; body rendered as
    text (an embedded `<script>`/markup in `body` is shown literally, not executed).
  - `NotificationBell.test.tsx` — badge three states (numeric / indeterminate-on-error / none-on-zero);
    popover opens and lists; "Mark all read"/"See all"/"settings" present; the 99+ cap.
  - `NotificationsPage.test.tsx` — loading/empty/error/list; mark-all-read.
  - `NotificationSettingsPage.test.tsx` — reads the toggle value; flips it → PUT fires; save error shows
    `MutationErrorState`.
  - `hooks.test.tsx` — count reports `isError` (never a fake 0); list scopes hit the right URLs.
  - **Update `TopBar.test.tsx`** — the bell now opens a popover (not a `Link` to `/tasks?...`); the ack
    `Indicator` is gone; the Account menu has "Notification settings".
- **Run the FULL `/check-web`** (eslint + strict `tsc --noEmit` + build + the whole vitest suite) — strict
  `noUncheckedIndexedAccess` + cross-file drift only show in the full run.

## 8 · File inventory

**New** (`apps/web/src/features/notifications/`): `types.ts` (or inline in `hooks.ts`), `hooks.ts`,
`mutations.ts`, `deepLink.ts`, `NotificationBell.tsx`, `NotificationItem.tsx`, `NotificationsPage.tsx`,
`NotificationSettingsPage.tsx` + a `.test` for each unit (`deepLink.test.ts`, `NotificationItem.test.tsx`,
`NotificationBell.test.tsx`, `NotificationsPage.test.tsx`, `NotificationSettingsPage.test.tsx`,
`hooks.test.tsx`).

**Edited:** `apps/web/src/app/shell/TopBar.tsx`, `apps/web/src/app/shell/TopBar.test.tsx`,
`apps/web/src/App.tsx`, `apps/web/src/test/msw/handlers.ts` (+ fixtures).

**Untouched:** `useAckCount.ts` (kept), `openapi.yaml`, any BE/migration/seed.

## 9 · Verification & merge

1. `/check-web` (full loop) green.
2. `web-test-trap-reviewer` + `diff-critic` on the branch diff.
3. A small **3-lens adversarial Workflow**: (a) self-scope / no-extra-gating + never-confident-zero;
   (b) a11y / colour-safe unread state / XSS-safe body rendering / deep-link relativisation safety;
   (c) test-fidelity (fixtures pinned to `_view`, determinism, no false-PASS).
4. **Live-smoke** via Chrome MCP (owner does the Keycloak login; rebuild the web image — `vite preview`
   serves a baked build). The BE spine is live, so real `task.assigned` rows exist to render: assign a
   task → see the badge increment → open the popover → click a row → land on the deep-linked surface →
   verify it marks read → toggle the email preference.
5. PR → green CI (all five jobs) → owner squash-merge → `/finish-slice` + a docs follow-up PR.

## 10 · Named residuals (deferred, not faked)

- **Subtype deep-link routing** (`services/notifications/subjects.py`, BE): DOCUMENT-subtype subjects
  (objective/MR) deep-link to `/documents/{id}` (correct and not broken); a richer `/objectives/{id}` /
  `/management-reviews/{id}` link is a small BE follow-up.
- **Preferences matrix + digests + quiet hours** → slice 3 (this slice ships only the master toggle).
- **DOC_ACK email** → slice 3.
- **SSE** replacing the 60 s poll → a future slice (doc 10 §9.1).
- **The retired header ack-work-count** (§4.1 consequence) — if the owner later wants a persistent
  open-ack count, it would ride the Tasks icon (re-separating the surfaces).
