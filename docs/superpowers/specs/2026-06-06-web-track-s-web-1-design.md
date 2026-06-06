# EasySynQ Web-UI Track — S-web-1 Design (App Shell + Design-System Foundation)

> **Status:** Draft for owner review · **Date:** 2026-06-06 · **Owner:** CoJoA13
> **Work-stream:** the deferred web-UI track (first increment). API is feature-complete at migration head `0044`.
> This spec records the **SPA architecture** (net-new — no existing doc covers it) and the **S-web-1** slice.
> The *visual* design is NOT defined here — it is authoritative in `docs/11-ui-ux-design-system.md` and realized
> in `mockup/easysynq-mockup.html` + `mockup/styles.css`. This spec ports that into the live app.

## 1. Context & goal

`apps/web` (React 18 + TypeScript + Mantine 7 + TanStack Query 5 + react-router 7 + oidc-client-ts, Tailwind 3)
today contains only the first-run **setup wizard** (`SetupWizard.tsx`) and **admin stubs** (`admin/`). The whole
feature surface is unbuilt while the API became rich (vault/lifecycle/approvals, records, evidence packs, ingestion,
audits/findings/CAPA, DCR, search, compliance-checklist). The goal of this track is to turn the headless backend
into a usable QMS.

The owner-approved mockup (`mockup/easysynq-mockup.html`, 8 screens: dashboard · library · document · review · audit
· capa · ingestion · setup) and its tokenized `styles.css` (722 design-token variables) mean the **look is settled**.
The first increment is therefore a faithful **port**, not a design exercise.

## 2. Scope decision — vertical journey, built in 3 slices

**Decision:** build the first increment as a **vertical operational journey** (not horizontal breadth, not
foundation-only): the shell + the screens needed for **UJ-3 (review & approve a change)** end-to-end, so a real
approver can find a change, read the redline, and approve it from the browser on day one.

The journey is delivered as **three PR-sized slices**, each on a `feat/s-web-N` branch → green CI → squash-merge
(main is protected), matching the project's slice + diff-critic review rhythm:

- **S-web-1 — App shell + design-system foundation** (this spec): token port + shell chrome + auth/api plumbing +
  a thin real Library list to prove the shell.
- **S-web-2 — Library + Document detail** (read surfaces): faceted Library + clause-spine tree + the detail drawer
  with real content; the Document page (artifact header DP-5, tabs, version timeline, where-used) — read-only.
- **S-web-3 — Review & Approve** (the action): two-pane redline diff + sticky decision card + signature slot
  (DP-10), wired to `POST /tasks/{id}/decision`. This slice makes the journey operational.

This spec details **S-web-1**; slices 2 & 3 are outlined in §10 for coherence (the foundation is built to support
them) but each gets its own spec/plan when reached.

## 3. Architecture (net-new)

### 3.1 Routing

The real `<AppShell>` becomes the operational `/` layout; today's placeholder `Shell` (readiness + account cards in
`App.tsx`) is removed. Nested layout routes:

```
/                 → <AppShell>   (top bar · clause-spine rail · breadcrumb · detail drawer)
   index          → Home         (calm placeholder card in S-web-1; full PDCA dashboard later)
   /library       → Library      (thin real list in S-web-1; full in Slice 2)
   /documents/:id → Document     (route reserved; built in Slice 2)
   /tasks/:id     → Review/Approve(route reserved; built in Slice 3)
/setup            → <SetupWizard>(unchanged)
/admin            → <AdminShell> (unchanged; folds into the rail in a later slice)
```

The existing operational gate (the `/api/v1/setup/state` probe → `OPERATIONAL`) continues to guard `/`; a
non-operational install still redirects to `/setup`.

### 3.2 Provider tree

Extend the existing `main.tsx` stack with an auth context:

```
MantineProvider (defaultColorScheme="auto")
  └ QueryClientProvider
     └ BrowserRouter
        └ AuthProvider        ← new: holds {user, token, login, logout} in context
           └ <App/> routes
```

### 3.3 API client + auth

- **`AuthProvider`** lifts the existing `useAuth()` OIDC logic (Auth-Code + PKCE against Keycloak, **in-memory
  tokens only**) into a context so any component reads `{user, token, login, logout}` without prop-threading.
- **Token-aware api client:** extend `lib/api.ts` so `apiGet`/`apiSend` obtain the bearer token from the auth
  context (via a `useApi()` hook), instead of the current manual token-passing and the mixed raw-`fetch`/`apiGet`
  patterns in `App.tsx`. Keep the RFC 9457 `ApiError` (surfaces `problem.code`).
- **React Query hooks per resource:** `useDocuments(filters)` → `GET /documents`; `useClauses()` → `GET /clauses`
  (left-rail spine). Typed responses, stable query keys.
- **Auth posture:** in-memory tokens are retained deliberately (no `localStorage`); a hard reload re-authenticates
  silently via Keycloak. Accepted for v1; deep links resolve after auth.

### 3.4 Folder structure

```
src/
  app/shell/   TopBar · LeftRail · Breadcrumb · DetailDrawer · AppShell
  features/    library/  documents/  tasks/   (documents/, tasks/ are stubs in S-web-1)
  lib/         api (token-aware) · auth (AuthProvider + useAuth) · query hooks
  theme/       tokens.css · mantine.ts · tailwind config
```

## 4. Design-system token port (DP-7 — single source)

Port the realized tokens (doc 11 §3, embodied in `mockup/styles.css`) into the app as one source consumed by both
styling systems:

1. Lift the `:root { --color / --type / --space / --elev / --motion }` definitions + dark-scheme overrides from
   `styles.css` into `src/theme/tokens.css` — the single source of truth.
2. **Mantine theme** (`mantine.ts`) maps those vars into the Mantine theme object: color palette, `fontFamily`
   (Inter + JetBrains Mono for identifiers/diffs/hashes), radius, shadows (the 4-step elevation ramp), spacing.
3. **Tailwind config** references the same CSS vars (e.g. `colors: { canvas: 'var(--bg-canvas)', … }`) so utility
   classes and Mantine share one source — re-theming is a token edit, not a rebuild.
4. **Fonts self-hosted** (Inter + JetBrains Mono via `@font-face`) — no outbound fetch (D1 self-hosted constraint).
5. Light/dark via the existing `defaultColorScheme="auto"` + the dark `:root` override.

All token pairings already satisfy WCAG 2.2 AA contrast (doc 11 §3); the state/record status colors carry an
icon + label (never color-only).

## 5. App-shell components

- **`<AppShell>`** — built on Mantine's `AppShell` primitive (header / navbar / aside) for built-in responsive
  collapse and ARIA landmarks; the `aside` region is the detail drawer.
- **`<TopBar>`** (56px) — brand, a ⌘K global-search **slot** (opens a placeholder in S-web-1; behaviour later),
  task + acknowledgement bells (counts), user menu (sign out).
- **`<LeftRail>`** (264px, collapses to a 64px icon rail ≤1280px, auto-collapses ≤1024px) — clause spine grouped
  under PLAN / DO / CHECK / ACT plus Home and Library; active-section highlight. Clause nodes from `GET /clauses`,
  kept simple (full clause pages are a later slice).
- **`<Breadcrumb>`** — path trail + clause chip (chip opens a read-only clause reference in the drawer; light in
  S-web-1).
- **`<DetailDrawer>`** — right panel, 420px (resizable 360–640px), **deep-linkable via URL**, closes on ESC or
  scrim click, focus-trapped (escapable). In S-web-1 it ships as a scaffold **plus** a basic Library-row → overview
  to prove the load-bearing DP-3 pattern early; real content arrives in Slice 2.

## 6. S-web-1 — definition of done

1. **Token port** — `tokens.css` (full doc-11 set) + Mantine theme mapping + Tailwind config + self-hosted fonts;
   light/dark.
2. **Shell** — `AppShell` + `TopBar` + `LeftRail` (lens groups + Home/Library) + `Breadcrumb` + `DetailDrawer`
   scaffold.
3. **Auth/API plumbing** — `AuthProvider` + token-aware api client + React Query hooks (`useDocuments`, `useClauses`).
4. **Routing** — `AppShell` at `/`; Home + Library live; `/documents/:id` + `/tasks/:id` routes reserved;
   `/setup` + `/admin` unchanged.
5. **Thin Library list** — a real table from `GET /documents` with columns: identifier · title · type · state
   badge (state-color tokens) · clause chips · owner; row click → drawer overview. Light pagination; facets
   deferred to Slice 2. *(The mockup's "Next review" column is intentionally omitted — it depends on
   `next_review_due`, which the schema does not have yet; see §11.)*
6. **Home placeholder** — one calm card so `/` is not empty (full PDCA dashboard is a later slice).
7. **Accessibility** — skip-link, semantic landmarks, visible focus ring, keyboard-operable rail + drawer,
   `prefers-reduced-motion`; **jest-axe** assertion wired into CI.

## 7. Data flow & error handling

```
AuthProvider (OIDC) ──token──▶ api client (RFC 9457 ApiError) ──▶ React Query hooks ──▶ UI
```

- **401** → trigger login (token absent/expired).
- **423 `setup_incomplete`** → route to `/setup` (the latch; should not occur when OPERATIONAL).
- **403** → quiet "no access" (DP-6 — never render a control the server would reject; the read list is
  row-filtered server-side, so no client-side permission logic is needed for reads).
- **`problem.code`** → inline, field-level messages.
- **Loading** → skeletons; all motion respects reduced-motion (DP-8).

## 8. Testing & accessibility

- **vitest + @testing-library/react** for shell + Library components.
- **MSW (mock service worker)** intercepts `GET /documents` and `GET /clauses` so component tests run against
  realistic responses **without the Docker stack**.
- **jest-axe** asserts zero accessibility violations on the shell and the Library list.
- The web CI job gains `vitest run` + the axe assertion alongside the existing `eslint` / `tsc` / `vite build`.
- **WCAG 2.2 AA is a hard release gate** (doc 11 §11 / NFR): the axe gate is wired from this first slice; a manual
  keyboard-only + screen-reader pass is part of release sign-off.
- **Live end-to-end** (against the real API + Keycloak) is a manual smoke test once Docker is available; it is not
  required to build, type-check, unit-test, or CI this slice.

## 9. Out of scope (YAGNI — later slices)

Library facets / saved searches (Slice 2) · Document detail page (Slice 2) · Review & Approve (Slice 3) · the full
PDCA Home dashboard (later; deliberately not depending on a dashboard-overview endpoint that has not been verified
to exist) · ⌘K global-search behaviour (slot only) · process-map / clause-page lenses.

## 10. Slices 2 & 3 (outline, for coherence)

- **S-web-2 — Library + Document detail:** full faceted Library (Type/Status/Owner/Clause/Date) + clause-spine
  tree + detail drawer with real content (DP-3); Document page with the one artifact header (DP-5), tabs, version
  timeline, approvals stepper, where-used — read-only. API: `GET /documents`, `/documents/{id}`,
  `/documents/{id}/versions`, `/documents/{id}/where-used`.
- **S-web-3 — Review & Approve:** two-pane decision surface — redline diff (LEFT;
  `GET /documents/{id}/versions/{vid}/diff?from={vid2}`) + sticky decision card with radio + comment + the
  signature slot (DP-10), RIGHT — wired to `GET /tasks/{id}` + `POST /tasks/{id}/decision`. Honors deny-by-default
  (task in the user's queue implies the right to act) and surfaces SoD reassurance.

## 11. Dependencies, risks, open items

- **Docker is currently down** on the dev machine (daemon inactive; user not in the `docker` group). It blocks the
  *live* click-through only — not build/type-check/unit-test/CI. Bring it up with `sudo systemctl enable --now
  docker` + `sudo usermod -aG docker $USER` (re-login) when convenient.
- **Node is v26** here (CI/docs reference Node 22); the existing web build passes on it.
- **The shell + drawer are the load-bearing seams** — get the `AppShell` regions, the deep-linkable drawer, and
  the token mapping right in S-web-1; downstream screens lean on them.
- **`GET /dashboards/overview` is unverified** — avoided (Home is a placeholder); confirm its existence before the
  dashboard slice.
- **`next_review_due` does not exist in the schema yet** — it is deferred to the v1.x drift family (D5 scheduled
  re-review). The mockup's Library/Document "Next review" column and any "overdue review" signal stay omitted (or
  rendered as placeholder) until that lands; don't wire a column to absent data.

## 12. Decisions log

- **Work-stream:** web-UI track chosen as the next move (over drift / owner-assignment / search-depth).
- **D1 — vertical journey** (operational UJ-3) over horizontal breadth / foundation-only.
- **D2 — 3-slice plan** (shell → read surfaces → approve) over 2-slice or 1-PR.
- **A — Mantine `AppShell`** primitive for the shell (built-in responsive + landmarks).
- **B — left rail** shows lens groups + Home/Library now; clause nodes simple.
- **C — drawer in S-web-1** = scaffold + a basic Library-row → overview (prove DP-3 early).
- **D — a11y gate** (jest-axe + CI) wired from slice 1.
- **E — MSW** for test-time API mocking (stack-free tests).
- **F — Home is a placeholder card** in S-web-1 (no dependency on an unverified dashboard endpoint).
- **Auth** — in-memory tokens retained; reload re-authenticates (accepted for v1).
- **Routing** — real `AppShell` replaces the placeholder `/`; `/setup` + `/admin` unchanged.
