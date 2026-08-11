# S-mutation-feedback design

**Status:** Owner approved in conversation on 2026-08-10; written-spec review pending

**Programme:** Programme 1 — frontend resilience and accessibility

**Slice:** 4 of 8 — mutation feedback

**Date:** 2026-08-10

**Baseline:** `323c79b` (`main`, squash merge of `S-app-route-boundary`)

## 1. Outcome

EasySynQ must make failed user-initiated notification writes visible, understandable, and safely
recoverable without discarding the operator's intent. This slice covers marking one notification read,
marking all notifications read, and saving notification preferences. Every failure is announced in
context. Inputs and the exact failed operation remain available where retry is safe.

Opening an unread notification keeps navigation immediate. If the background mark-read fails after the
notification row unmounts, a persistent notice follows the operator into the destination route. The
notice never disappears on a timer. It remains until the operator dismisses it or a retry succeeds.

The three notification operations are non-regulated personal state and are already effectively
idempotent at the server boundary. This slice proves those repeat semantics with API integration tests
instead of adding an idempotency ledger. Regulated writes remain governed by the takeover rule: an
ambiguous retry requires a named idempotency-key contract or proof that the first attempt did not commit.

## 2. Current defect and evidence

The notification UI has three inconsistent failure postures:

- `NotificationItem` invokes `useMarkRead().mutate(id)` from both the explicit mark-read button and the
  notification link. Neither path renders or announces `mutation.error`. Link navigation immediately
  unmounts the row, so a late failure cannot be made local after the fact.
- `NotificationBell` and `NotificationsPage` invoke `useMarkAllRead().mutate()` and only use
  `isPending`. A failed request silently returns the button to its idle state.
- `NotificationSettingsPage` already renders `MutationErrorState` after a failed partial PUT and keeps
  its working values. The shared state, however, has no explicit live-region or Retry/Dismiss contract,
  and the page does not distinguish a retry of the failed body from a newly edited intent.

`MutationErrorState` currently renders a calm Mantine `Alert` and safely unwraps `ApiError` messages, but
its only interface is `title`, `error`, and `fallback`. It has no action row and no explicit announcement
semantics.

The API endpoints expose no `Idempotency-Key` header. Their current effective behavior is nevertheless
repeat-safe for this personal state:

- `POST /notifications/{id}/read` updates the same caller-scoped row to read and repeats successfully
  while that row exists;
- `POST /notifications/read-all` updates only unread caller-scoped rows, so a repeat marks zero or fewer
  rows; and
- `PUT /me/notification-preferences` applies the same supplied values to the same user preference row.

The baseline implementation has tests for successful writes and preference-save error visibility, but
no failed mark-one/mark-all interaction tests, no cross-route failure test, and no repeated-write API
proof.

## 3. Scope

This slice includes:

- accessible inline mutation failure presentation with optional Retry and Dismiss actions;
- a small operational-route feedback store and AppShell outlet for failures that outlive their source
  component;
- immediate notification-link navigation with persistent background mark-read failure feedback;
- row-local failure feedback for the explicit mark-read action;
- local mark-all failure feedback in both the notification bell and full notification page;
- preference-save failure feedback that preserves working values and distinguishes retry from a new
  edited intent;
- explicit retry eligibility for transport errors, HTTP 408/429, and 5xx responses;
- effective-idempotence integration tests for the three existing notification endpoints;
- focused accessibility, route-lifetime, intent-preservation, and retry tests; and
- fresh affected-suite evidence and authority-document updates.

This slice does not include:

- a repository-wide migration of existing mutation surfaces;
- a general toast/snackbar system or notification dependency;
- automatic mutation retries;
- an idempotency-key ledger, migration, API request schema, or generated-contract change;
- retry permission for regulated writes without their own approved idempotency contract;
- changes to notification delivery, email, digest, SSE, deep-link resolution, or unread-query semantics;
- authentication, setup, route-error, URL-state, table semantics, responsive-view, or Playwright work;
- success toasts for operations whose refreshed UI already shows the result;
- Fedora two-media proof or live deployment; or
- unrelated formatting, refactoring, or residual closure.

## 4. Chosen architecture

The owner selected an intent-aware feedback layer over an all-global banner queue or local-only errors.
Local errors stay beside the controls that caused them. The persistent shell surface is used only when
the initiating component is expected to disappear before the request settles.

### 4.1 Shared inline presentation

`MutationErrorState` remains the write-side sibling of `ErrorState`. It gains:

- explicit `role="alert"` and atomic announcement semantics;
- optional `onRetry`, `retrying`, `onDismiss`, and action-label inputs;
- a disabled/loading Retry action that cannot double-submit; and
- a Dismiss action that clears presentation state without changing application data.

The component does not decide whether a mutation is safe to retry. A caller supplies `onRetry` only when
its operation and current error satisfy the approved retry contract. This keeps presentation reusable
without turning a generic UI component into product or server authority.

API problem text continues through the existing `ApiError` path. A non-API transport failure receives a
fixed generic message. Raw exceptions, stacks, response bodies, request URLs, tokens, notification IDs,
or query-cache details are never rendered.

### 4.2 Persistent feedback store and outlet

A focused `lib/mutationFeedback.tsx` owns route-persistent failures. The provider is mounted around the
operational route table after authentication and trusted setup-state gates, so it survives child-route
navigation but does not leak feedback into startup or setup flows. `MutationFeedbackOutlet` renders
inside `AppShell.Main`, after the breadcrumb and before routed page content, outside the route-content
error boundary.

Each entry contains only:

- a stable deduplication key;
- fixed display title and already-normalized safe message;
- accessible Retry/Dismiss labels;
- an optional feature-supplied asynchronous retry operation; and
- transient retry state owned by the provider.

The store may hold multiple distinct outstanding entries. Reporting the same key updates that entry
instead of duplicating it. Dismiss removes only the named entry. There is no timer, persistence to
storage, cross-tab synchronization, or server-side feedback record.

The provider owns retry execution so it remains valid after the originating component unmounts. It does
not retain a React mutation-hook callback. A feature-level notification request function supplies the
same API operation used by the hook; the retained retry closure captures only the authenticated API
client, query client, and immutable notification intent. On success it invalidates the
`["notifications"]` prefix and removes the entry. On another failure it updates the same entry and
re-evaluates whether Retry is still eligible.

### 4.3 Retry classification

`isRetryableMutationError(error)` is deliberately conservative:

- a fetch transport `TypeError` is retryable;
- `ApiError` status 408, 429, or 500–599 is retryable; and
- other and unknown failures are not retryable.

HTTP 400, 401, 403, 404, 409, and 422 failures are announced but receive no Retry action. Preference
validation/conflict feedback remains beside its editable form; authentication and access failures retain
their existing application ownership; and a missing notification remains unread only if it still appears
on a later authoritative list. This classification does not prove a mutation itself safe; it is only the
second gate after the caller has named a repeat-safe operation.

No mutation is retried automatically. Every retry is an explicit operator action.

## 5. Notification write flows

### 5.1 Explicit mark-one action

The row's existing `Mark read: <title>` action remains the mutation control. A failure renders
`Couldn't mark this notification read` directly beneath that row. The unread marker and mark-read action
remain available. A retry uses the same notification ID. A successful request invalidates notification
queries, after which the authoritative refetch removes the unread state.

A retryable failure shows Try again and Dismiss. A non-retryable failure shows Dismiss only. Dismiss
clears the mutation presentation but does not optimistically mark the row read.

### 5.2 Open-and-mark action

The notification link remains a native React Router link and navigation is not delayed by the network.
For an unread row, the click starts the mark-read request and invokes the existing navigation/close
behavior immediately. Modified-click and native link behavior remain intact; this slice does not replace
the link with a button or gate the destination on persistence.

The mutation's hook-level error handling, rather than an observer callback passed to `mutate`, reports a
late failure. This is required because TanStack per-call callbacks may be detached when the row unmounts.
The report key is derived from the mark-read operation and notification ID, while the visible title uses
the already-rendered notification title. The persistent message explains that the notification remains
unread.

The destination route keeps its normal route-chrome focus. The alert announces without stealing focus.
Try again repeats the same notification ID. Success removes the alert, invalidates notification data,
and writes `Notification marked read` to a persistent polite announcer before clearing the visible
entry. Dismiss removes the alert without changing read state.

### 5.3 Mark-all action

The bell and full page keep separate mutation instances and local errors. A failed bell action renders
inside the open popover below its header controls. A failed full-page action renders below the page
header controls. Neither failure enters the persistent store because neither source is automatically
unmounted by the action.

Try again repeats the no-variable mark-all operation. The button and Retry action share one pending gate,
so rapid activation cannot create overlapping requests from that component. Success invalidates all
notification queries and clears the local error through the mutation's normal successful state.

### 5.4 Notification preferences

The current working-state and partial-body calculation remain authoritative. A failed Save leaves every
control value unchanged and renders `Couldn't save your preferences` beneath the Save group.

Retry repeats `update.variables`, the exact partial body that failed. If the operator changes any
control after failure, the old mutation error and retry intent reset; the next Save computes a new
partial body from current working state. A successful save keeps the existing invalidation/refetch path
and `Saved.` confirmation.

## 6. Effective idempotence contract

No production API behavior changes are required. Integration tests extend the existing notification API
suites to make repeat safety executable:

1. Marking one caller-owned notification read twice returns success both times and leaves it read. The
   test asserts effective state, not equality of the internal `read_at` timestamp.
2. Marking all read twice returns a second count of zero and leaves all caller-owned rows read without
   changing another caller's rows.
3. Sending the same partial notification-preferences PUT twice returns the same effective preference
   view and preserves fields absent from the partial body.

These proofs authorize explicit retry for these three personal-state operations only. They do not create
a reusable inference that POST, PUT, or any other EasySynQ mutation is safe to retry. Later regulated
mutation slices must cite their own pre-commit evidence or stable server idempotency key.

## 7. Accessibility and interaction contract

- Error Alerts use a named title, fixed safe body copy, `role="alert"`, and atomic announcement.
- The alert never takes focus. A route change continues to focus the destination heading or main region
  under the approved route-chrome contract.
- Persistent success is announced politely before the visible error clears.
- Retry exposes pending state and cannot be activated twice while a request is in flight.
- Dismiss is always available for persistent feedback and for local failed writes that otherwise have
  no state change to clear them.
- Multiple persistent errors have distinct accessible Retry and Dismiss names derived from safe
  notification titles.
- Actions meet the existing 44 CSS px target requirement; compact visual treatment may not reduce the
  hit area below that minimum.
- No color is the sole error signal. Existing Mantine Alert structure, text, and tokens are reused.
- No animation, auto-dismiss timing, focus timer, or live-region timer is introduced.
- Affected loaded and failed states receive `jest-axe` coverage. Browser-level screen-reader, forced
  colors, and zoom proof remain part of the later browser-failure slice.

## 8. File and component ownership

Expected production changes are bounded to:

- `apps/web/src/lib/states.tsx` — inline mutation error actions and announcement semantics;
- `apps/web/src/lib/mutationFeedback.tsx` — retry classification, persistent provider/store, outlet,
  and polite announcer;
- `apps/web/src/App.tsx` — stable provider placement around operational routes;
- `apps/web/src/app/shell/AppShell.tsx` — persistent outlet placement outside route content;
- `apps/web/src/features/notifications/mutations.ts` — shared request functions and hook-level failure
  reporting seam;
- `apps/web/src/features/notifications/NotificationItem.tsx` — local versus persistent mark-one paths;
- `apps/web/src/features/notifications/NotificationBell.tsx` — local mark-all feedback;
- `apps/web/src/features/notifications/NotificationsPage.tsx` — local mark-all feedback; and
- `apps/web/src/features/notifications/NotificationSettingsPage.tsx` — exact retry intent and edit reset.

Expected test changes are the matching existing web test files, a focused
`lib/mutationFeedback.test.tsx`, route-lifetime coverage in `App.test.tsx` or `AppShell.test.tsx`, and the
existing API notification integration suites. Exact file selection is locked in the implementation plan
after inspecting the narrowest stable seams.

Documentation changes are this design, its implementation plan, `docs/current-status.md`, and
`docs/slice-history.md`. No authority register, residual registry, OpenAPI source, generated contract,
migration, dependency, or production API file changes are expected.

## 9. Test strategy

Implementation starts from focused failing behavior proofs.

### 9.1 Shared feedback

- `MutationErrorState` announces safe API and transport messages, renders optional actions, disables
  Retry while pending, and has no axe violations.
- Retry classification accepts only transport, 408/429, and 5xx failures.
- The persistent store deduplicates by key, preserves distinct entries, dismisses one entry, prevents
  duplicate retry, updates a repeated failure, clears on success, invalidates through the feature retry
  callback, and announces success politely.

### 9.2 Notification UI

- Explicit mark-one failure stays on the row, retains unread state, and safely retries the same ID.
- Opening an unread notification reaches the destination before the mark request settles; a later
  failure survives row unmount, is announced without changing destination focus, and retries the exact
  ID.
- A non-retryable mark-one response has no Try again action.
- Bell and page mark-all failures render in their respective local contexts, retain the original action,
  and do not create persistent entries.
- Preference-save failure preserves every edited control. Retry repeats the failed body. Editing after
  failure clears the stale intent, and the next Save submits the newly computed body.
- Affected states remain axe-clean.

### 9.3 API and broad evidence

- Existing API integration fixtures prove the three repeated-write behaviors and caller isolation.
- Focused web files run first, followed by the complete affected notification/shell/state group.
- Web typecheck, ESLint, scoped Prettier, and production build run fresh.
- API formatting/lint/type checks applicable to changed tests and the affected integration tests run
  fresh in the repository environment.
- Repository authority, compatibility hooks, no-site-data, and diff guards run before handoff.
- The final branch receives an independent requirements and quality review before publication.

## 10. Acceptance criteria

The slice is complete when fresh evidence proves all of the following:

1. All three notification mutation families surface failed attempts in visible, announced context.
2. Notification-link navigation remains immediate and its late mark-read failure survives the source
   row's unmount.
3. Persistent failures remain until Dismiss or successful Retry; no timer removes them.
4. Retry replays the exact retained intent and cannot overlap itself.
5. Only transport, 408/429, and 5xx failures expose Retry, and only for these independently proven
   repeat-safe notification operations.
6. Explicit mark-one, mark-all, and preference-save state stays local and preserves the user's current
   intent.
7. Editing preferences after failure replaces, rather than accidentally replays, the old failed body.
8. Repeated mark-one, mark-all, and preference PUT requests produce the same effective server state and
   preserve caller isolation.
9. Route focus, shell/error boundaries, query-client identity, auth/setup gates, notification delivery,
   and existing successful-write behavior remain unchanged.
10. Focused and affected tests, static checks, build, authority guards, site-data scan, and final diff
    review pass, with every unavailable browser or environment proof reported honestly.

## 11. Risks and controls

- **Unmounted mutation callbacks:** per-call observer callbacks can disappear with the row. The design
  uses hook-level error ownership and a provider-owned retry operation, proven across real navigation.
- **Unsafe generic retry:** the UI component never infers mutation safety. Notification callers opt in
  only after the API repeat tests pass, and status classification is an additional conservative gate.
- **Stale form replay:** the failed preference body is immutable; any later edit resets that intent.
- **Duplicate or lost notices:** stable keys update the same operation while distinct notification IDs
  remain independently dismissible.
- **Focus regression:** alerts announce without focus, and route-transition tests assert final focus at
  the destination.
- **Sensitive error leakage:** persistent state stores normalized display copy only; raw errors and
  request details never render or persist.
- **Provider scope drift:** the provider mounts only after trusted operational startup and owns no cache,
  auth, route, or server state. It does not alter QueryClient identity or lifecycle.

## 12. Owner decisions

The owner approved on 2026-08-10:

1. a notification-complete vertical slice plus a reusable feedback pattern, not notification-only or a
   cross-application mutation sweep;
2. immediate navigation for notification links, with route-persistent mark-read failure feedback;
3. persistent feedback until explicit Dismiss or successful Retry, with no auto-dismiss timer;
4. effective-idempotence API proof for these non-regulated notification operations instead of adding an
   `Idempotency-Key` contract; and
5. the intent-aware split: local errors remain local, while only failures that outlive their source
   component enter the operational shell feedback store.
