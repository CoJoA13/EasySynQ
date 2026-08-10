# S-app-route-boundary design

**Status:** Owner approved on 2026-08-09; QueryClient provider contract clarified and approved on
2026-08-10

**Programme:** Programme 1 — frontend resilience and accessibility

**Slice:** 3 of 8 — application error and route boundary

**Date:** 2026-08-09

**Baseline:** `ae84951` (`main`, squash merge of `S-setup-state-boundary`)

**Owner-approved clarification (2026-08-10):** Retry remounts only the failed route subtree and never
explicitly invalidates, clears, or refetches cached queries. The original `QueryClientProvider`,
`QueryClient` identity, and client mount/unmount lifecycle remain stable. TanStack Query may perform its
normal stale-query refetch when observers remount.

## 1. Outcome

EasySynQ must keep an operator oriented when an operational route is unknown or a rendered page fails.
Unknown URLs remain visible and render a useful not-found state instead of silently becoming the
dashboard. A failure in routed page content preserves the authenticated application shell and offers
bounded recovery. A failure above that content boundary reaches a full-screen last-resort boundary
instead of an empty root or an uncaught React crash.

Recovery never displays raw exception, stack, URL, query-cache, API, or server details. Retrying a failed
page remounts only that page subtree: it does not explicitly invalidate, clear, reset, remove, or refetch
shared query data, issue a mutation, replace the query provider/client, or migrate the application to a
different routing architecture. A stale query observer may refetch normally when that subtree remounts.

## 2. Current defect and evidence

`apps/web/src/App.tsx` currently ends its route table with:

```tsx
<Route path="*" element={<Navigate to="/" replace />} />
```

Every unknown URL is therefore replaced with `/`. The operator loses the requested address and receives
no explanation that the route was invalid. `useRouteChrome` already recognizes an unmapped pathname but
sets only the bare `EasySynQ` title, and `Breadcrumb` would humanize arbitrary path segments if it were
mounted for an unknown route.

`apps/web/src/main.tsx` mounts `QueryClientProvider`, `BrowserRouter`, `AuthProvider`, and `App` without an
error boundary. `AppShell` renders `<Outlet>` directly. A synchronous exception in a route component can
therefore escape the React tree instead of becoming a recoverable content state; there is no smaller
boundary that can keep the header, navigation, breadcrumb, skip link, or command palette mounted.

The baseline at `ae84951` is clean apart from the owner-owned `.superdesign/` in the primary checkout.
In the isolated worktree, all 28 existing `App.test.tsx` tests and web TypeScript typecheck pass.

## 3. Scope

This slice includes:

- a shared React render-error capture and reset mechanism;
- a full-screen last-resort application error screen;
- a shell-contained routed-page error state;
- a shell-contained operational not-found page;
- removal of the wildcard dashboard redirect;
- fixed safe recovery copy and router-independent top-level actions;
- deterministic retry and location-reset behavior without explicit cache operations or query-provider
  replacement;
- not-found title and breadcrumb behavior that does not echo the unknown pathname;
- focused render-failure, routing, recovery, focus, narrow-layout, and axe tests; and
- fresh affected-suite evidence and authority-document updates.

This slice does not include:

- authentication, setup-state, redirect-latch, token, or finalization behavior changes;
- API, OpenAPI, Keycloak, database, migration, or generated-contract changes;
- mutation feedback, idempotency, toast, form-preservation, or retry policy changes;
- URL filter/sort/pagination correctness;
- general keyboard-row or responsive data-table work;
- a React Router data-router migration or Playwright harness;
- telemetry, error reporting, audit events, external services, or new dependencies;
- event-handler exceptions, rejected promises, or errors already owned by query/page state; or
- any current open residual or unrelated cleanup.

## 4. Chosen architecture

### 4.1 Two-tier boundary

The owner selected a two-tier boundary over an always-full-screen fallback.

The shared `ApplicationErrorBoundary` is a small class-based capture primitive because React render
boundaries still require `getDerivedStateFromError` or `componentDidCatch`. It stores only whether the
subtree failed; the thrown value is not copied into view state or passed to a fallback. The boundary
accepts:

- a fallback renderer with an explicit reset callback; and
- an optional reset key that clears a captured failure when its owning location changes.

The reset callback clears the captured state. React has already unmounted the failed descendants, so the
next render creates a fresh subtree. Reset calls no query-client method and issues no mutation. The
original `QueryClientProvider` and exact `QueryClient` remain mounted with unchanged identity and
lifecycle; after the route observer remounts, TanStack Query may apply its normal stale-query
`refetchOnMount` policy.

The same mechanism has two placements and two presentations:

1. **Global boundary:** inside `MantineProvider`, but outside `QueryClientProvider`, `BrowserRouter`,
   `AuthProvider`, and `App`. It catches failures in those application providers, routing, startup, and
   shell descendants while keeping theme-backed recovery available. A failure in `MantineProvider` or
   the static root lookup remains outside this slice's catchable tree.
2. **Route-content boundary:** inside `AppShell`, surrounding only the current outlet/content. A routed
   page failure therefore leaves shell chrome and navigation mounted. Its reset key represents the full
   router location, so pathname, search, or hash navigation clears a stale failure.

The route boundary also has an explicit retry action. The global boundary deliberately does not attempt
an in-place provider/router reset because those dependencies may be the failed state; it offers plain
same-origin navigation and reload instead.

### 4.2 Shell ownership and unknown routes

Normal operational routes continue to render `AppShell` with its protected `<Outlet>`. The wildcard route
becomes a separate operationally guarded `AppShell` not-found mode rather than a redirect. This small
AppShell seam supplies `NotFoundPage` as content and gives `Breadcrumb` a fixed `Page not found` override.
It avoids teaching `Breadcrumb` to infer the route table or exposing arbitrary path segments.

The unknown URL remains in browser history and the address bar until the operator chooses a recovery
link. The existing pre-operational guard remains authoritative: `UNINITIALIZED` and `IN_SETUP` unknown
routes still redirect to `/setup`. Authentication and setup loading/error boundaries still render before
the route table and remain unchanged.

`useRouteChrome` assigns an unmapped operational location the title `EasySynQ — Page not found`. A routed
error fallback temporarily assigns `EasySynQ — Page unavailable`, saves the prior title, and restores it
when retry or navigation unmounts the fallback.

### 4.3 Failure ownership

The boundaries cover exceptions thrown during descendant constructors, rendering, and lifecycle methods.
They do not claim to catch:

- exceptions thrown from event handlers;
- rejected asynchronous promises;
- TanStack Query errors represented by component state;
- auth/setup failures already represented by their startup boundaries; or
- failures in the boundary's own fallback.

The route fallback is itself below the global boundary, so an unexpected route-fallback failure promotes
to the global screen. The global fallback stays deliberately small and has no further application
dependency beyond Mantine and same-origin browser primitives.

## 5. Recovery presentations

### 5.1 Routed-page error

The shell-contained route error uses fixed copy:

- heading: **This page couldn't be displayed**;
- guidance: **EasySynQ encountered a problem while displaying this page. Your shared application data
  has not been cleared.**

Actions appear in this order:

1. primary **Try this page again** — clears only the route boundary;
2. secondary **Go to dashboard** — internal navigation to `/`; and
3. tertiary **Reload EasySynQ** — performs a full browser reload.

Retry does not call React Query invalidation, refetch, reset, remove, clear, or equivalent cache methods,
replace the provider/client, issue a mutation, or replay an operator action. A deterministic render
failure may immediately return to the same fallback; dashboard and reload remain available. When Retry
successfully remounts a component with a stale query observer, that observer retains TanStack Query's
normal refetch-on-mount behavior.

### 5.2 Global application error

The full-screen recovery state uses the established startup-boundary visual language and fixed copy:

- heading: **EasySynQ couldn't be displayed**;
- guidance: **Reload EasySynQ to start again. If the problem continues, contact your EasySynQ
  administrator.**

It offers:

1. primary **Reload EasySynQ**; and
2. secondary **Go to dashboard** through a plain same-origin anchor.

It does not call router hooks, clear caches, retry providers in place, or render the thrown value.

### 5.3 Not found

The operational shell-contained 404 uses:

- heading: **Page not found**;
- guidance: **The page you requested isn't available in EasySynQ.**

It offers two known-safe internal links:

1. **Go to dashboard**; and
2. **Open document library**.

It does not offer browser Back because that could leave EasySynQ. It does not display or interpolate the
unknown pathname, search string, hash, decoded segment, or referrer. The shell breadcrumb is exactly
`Home / Page not found`.

## 6. Accessibility and responsive contract

- Each fallback and not-found presentation has one `h1` and one clear content/main region appropriate to
  its placement.
- When a failure or not-found state first mounts, its `h1` receives focus once with `tabIndex={-1}`.
- Failure content is announced assertively. The ordinary 404 heading is focused without falsely
  presenting navigation as a runtime alert.
- The route shell's existing skip link, navigation, and focus ring remain available after a page crash.
- Actions are native links or buttons with visible accessible names and at least 44 CSS px height.
- Primary, safe-navigation, and reload actions retain the order defined in §5.
- Panels use existing Mantine and EasySynQ theme tokens for color, surface, border, radius, elevation,
  spacing, typography, and focus; no literal palette or new animation is added.
- At 320 CSS px, text and controls wrap/stack, children use no unsafe minimum width, and no
  document-level horizontal scrolling is introduced.
- Existing global reduced-motion, visible-focus, forced-colors, and automatic light/dark rules remain
  authoritative.
- Error and 404 copy contains no raw exception message, stack, component name, response detail, HTTP
  status, pathname, query, hash, database/host detail, or arbitrary diagnostic identifier.

## 7. Component and file ownership

Expected production surfaces:

- `apps/web/src/app/errors/ApplicationErrorBoundary.tsx` — shared boolean capture/reset primitive;
- `apps/web/src/app/errors/ApplicationErrorScreen.tsx` — router-independent full-screen fallback;
- `apps/web/src/app/errors/RouteErrorPage.tsx` — shell-contained error presentation and title restore;
- `apps/web/src/app/errors/NotFoundPage.tsx` — shell-contained fixed 404 presentation;
- `apps/web/src/main.tsx` — global-boundary placement;
- `apps/web/src/app/shell/AppShell.tsx` — route-boundary and not-found content seam;
- `apps/web/src/app/shell/Breadcrumb.tsx` — fixed not-found breadcrumb override;
- `apps/web/src/lib/routeChrome.ts` — fixed not-found document title; and
- `apps/web/src/App.tsx` — wildcard route replacement while preserving operational/setup guards.

Focused tests stay adjacent to these units. A small shared presentational panel may be extracted inside
`app/errors/` only if it removes real duplication without merging global and route recovery semantics.
No general shell, breadcrumb, or route-table refactor is authorized.

## 8. Test contract

### 8.1 Capture and reset

- a descendant render exception produces the supplied fallback;
- the thrown message and stack do not render;
- explicit reset remounts the failed subtree;
- a changed location reset key clears the captured state;
- an unchanged reset key does not create a render loop; and
- reset does not call query invalidation, refetch, reset, removal, clearing, equivalent cache operations,
  or mutation seams; and
- reset does not change the source `QueryClient` identity or add mount/unmount lifecycle calls.

Use deterministic throwing fixtures and suppress expected React console noise only inside the relevant
tests. Do not weaken global test logging.

### 8.2 Route error integration

- a deliberately throwing route renders **This page couldn't be displayed**;
- header, navigation, breadcrumb region, skip link, and command-palette trigger remain mounted;
- the failed route's content is absent;
- **Try this page again** can recover a transient throwing fixture;
- deterministic failure returns to recovery without unbounded rerendering;
- Dashboard navigation clears the failure and renders the dashboard;
- Reload invokes the injected or spied browser seam once;
- route navigation restores the prior document-title ownership;
- every route-side `useQueryClient()` read returns the original source client before and after Retry;
- the source client has one provider-owned mount, no Retry-owned mount/unmount calls, and one unmount when
  the application provider unmounts;
- shared query data remains present across retry;
- Retry invokes no explicit invalidation, refetch, reset, removal, clearing, or equivalent cache method;
- a stale observer remounted by Retry performs its normal configured refetch-on-mount; and
- axe reports no violations.

### 8.3 Global error integration

- an exception below the global boundary renders the full-screen recovery state;
- the application shell is absent in this last-resort state;
- Dashboard uses a plain same-origin destination and Reload invokes the browser seam;
- no router context is required to render or activate the screen;
- raw error content is absent; and
- focus, action targets, 320 px geometry, and axe satisfy §6.

### 8.4 Not-found and routing integration

- an unknown operational URL remains unchanged and renders `Page not found` inside the shell;
- the breadcrumb is exactly `Home / Page not found` and contains no unknown segment;
- the document title is `EasySynQ — Page not found`;
- Dashboard and Document Library links navigate to their exact destinations;
- the old wildcard redirect is absent;
- unknown `UNINITIALIZED` and `IN_SETUP` locations still reach setup;
- auth loading/error and setup pending/error continue to outrank route rendering;
- known operational routes and legacy ingestion redirects remain unchanged; and
- the 404 state passes focus, landmark, target-size, 320 px, raw-path exclusion, and axe checks.

## 9. Verification and documentation

Implementation begins with focused failing proofs and proceeds in small RED→GREEN tasks. Run focused
error-boundary, AppShell, breadcrumb, route-chrome, and App tests first, followed by existing auth/setup
startup regressions and the complete web unit suite. Then run web typecheck, lint, production build,
scoped Prettier on touched files, repository authority and Claude-hook tests, site-data guards, and
`git diff --check`.

On completion, update `docs/current-status.md` and `docs/slice-history.md` only from fresh evidence. The
implementation commit on which the complete web evidence ran becomes the next `baseline_commit`; do not
substitute the later documentation or squash commit mechanically. Preserve migration `0085` / next
`0086`, non-web suite counts, CI topology, the Vite large-chunk advisory, the pre-existing historical
Prettier limitation, pending Fedora proof, and all open residuals unless fresh in-scope evidence changes
one.

The 2026-08-10 provider-contract clarification is a bounded correction wave. It runs the focused and
complete affected route-boundary selection plus typecheck, lint, build, scoped formatting, authority,
site-data, documentation, and diff guards, but deliberately does not launch the complete web suite. The
`6f5676e` complete-suite evidence therefore remains explicitly pre-clarification until a later complete
run replaces it; partial clarification evidence must not change the frontmatter suite counts or baseline.

## 10. Acceptance criteria

1. A synchronous routed-page render failure preserves the operational application shell and renders a
   named, focused, actionable recovery state.
2. Explicit route retry remounts only the failed page subtree, preserves the original query
   provider/client identity and lifecycle, and calls no invalidation, refetch, reset, removal, clearing,
   equivalent cache operation, or mutation seam. A remounted stale observer may perform TanStack Query's
   normal configured refetch-on-mount.
3. Navigation after a route failure clears the captured state and restores normal title ownership.
4. A synchronous failure above routed content reaches a full-screen recovery state that does not require
   a working router or auth provider.
5. An unknown operational URL remains visible and renders a useful shell-contained 404 instead of
   redirecting to the dashboard.
6. The 404 exposes only Dashboard and Document Library recovery links and does not echo the unknown URL
   through content, title, or breadcrumb.
7. Legitimate pre-operational, authentication startup, setup startup, known-route, and legacy redirect
   behavior remains unchanged.
8. No fallback renders raw exception, stack, component, URL, API/server, database/host, or arbitrary
   diagnostic details.
9. Route error, global error, and 404 states satisfy heading focus, announcements, native semantics,
   44 px targets, 320 px layout, reduced-motion, forced-colors, theme, and axe contracts.
10. No API, contract, migration, Keycloak, mutation-feedback, URL-state, Playwright, telemetry, dependency,
    or unrelated routing change enters the slice.
11. Focused and complete affected verification is green before handoff.
