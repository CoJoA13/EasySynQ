# S-setup-state-boundary design

**Status:** Owner approved on 2026-08-09

**Programme:** Programme 1 — frontend resilience and accessibility

**Slice:** 2 of 8 — setup-state boundary

**Date:** 2026-08-09

**Baseline:** `a21128b` (`main`, squash merge of `S-auth-startup-boundary`)

## 1. Outcome

EasySynQ must never treat a failed or untrusted `/api/v1/setup/state` response as evidence that an
installation is pre-operational. Authentication startup remains the first application boundary. Once
authentication is ready, every setup-state attempt ends in one of three explicit outcomes:

1. a named, bounded loading state while EasySynQ verifies the installation;
2. a validated `OPERATIONAL`, `UNINITIALIZED`, or `IN_SETUP` state that authorizes exactly its existing
   route family; or
3. a visible, actionable recovery state that mounts neither the application shell nor `SetupWizard`.

After setup finalization reports success, EasySynQ enters a verification-only phase. A failed or
contradictory state read cannot redisplay the wizard or repeat finalization. Explicit recovery rereads
setup state only.

## 2. Current defect and adjacent ambiguity

`apps/web/src/App.tsx` currently derives:

```ts
const operational = setupState.data?.setup_state === "OPERATIONAL";
```

Every query failure, invalid body, missing field, and unknown value therefore becomes `false`. Routing
treats that false value like a pre-operational installation, so an existing deployment can be redirected
to `/setup` and shown `SetupWizard`. The public response is only asserted through a TypeScript generic;
there is no runtime validation against the three-state OpenAPI enum.

The initial loading view is also a bare loader. After a successful finalize mutation,
`SetupWizard.onFinalized` starts an unobserved state refetch. Until that refetch returns, or if it fails,
the wizard can remain mounted with its finalization action available. This creates an unsafe retry
ambiguity: recovery from a read failure must never replay a mutation that the server already reported as
successful.

## 3. Scope

This slice includes:

- a closed setup-state type and runtime response parser;
- one abortable, 15-second-bounded setup-state request;
- deterministic query policy with no automatic retries or incidental refetches;
- a setup-specific pre-shell loading and recovery component;
- strict App routing based only on a validated state;
- a post-finalization verification-only phase;
- single-flight, read-only retry;
- safe user-facing copy with no raw API or server details; and
- focused parser, timeout, routing, mutation-falsifier, interaction, and accessibility tests.

This slice does not include:

- authentication provider, callback, redirect-latch, or token-persistence changes;
- API, OpenAPI, Keycloak, database, or migration changes;
- setup-detail query redesign or general setup-wizard mutation feedback;
- a root render boundary, not-found route, URL-state, keyboard, responsive-table, or Playwright work;
- general query-state primitive changes; or
- any current open residual or unrelated cleanup.

## 4. Chosen architecture

### 4.1 Closed setup-state contract

The frontend defines the setup-state vocabulary as the exact union already published by OpenAPI:

```ts
export type SetupState = "UNINITIALIZED" | "IN_SETUP" | "OPERATIONAL";

export interface SetupStateResponse {
  setup_state: SetupState;
}
```

A small pure parser accepts `unknown` and returns `SetupStateResponse` only when the value is a
non-array object whose `setup_state` is one of those three strings. Invalid JSON, null, arrays, missing
fields, non-string fields, and unknown strings all fail. No fallback value exists, and the parser never
coerces or defaults a response to `UNINITIALIZED`.

The screen interface receives only a classified display phase. Raw exceptions, response bodies, status
codes, and rejected values do not enter rendered state.

### 4.2 Bounded state request

The dedicated setup-state fetcher owns the public `GET /api/v1/setup/state` boundary. It:

1. receives TanStack Query's cancellation signal;
2. composes it with an exact 15,000 ms deadline;
3. requires a successful HTTP response;
4. decodes JSON;
5. validates the decoded body through the closed parser; and
6. clears its timeout on success, failure, cancellation, and unmount.

The generic `apiGet` helper remains unchanged. Setup-state validation and timeout behavior stay local to
this trust boundary rather than widening every API request. A timed-out or cancelled request cannot
commit a late response.

The setup query uses:

- `retry: false`;
- `staleTime: Infinity`;
- no focus-triggered refetch;
- no reconnect-triggered refetch; and
- no interval polling.

The initial load therefore issues one request. Each accepted retry action issues exactly one additional
request. The recovery component uses an in-flight promise guard as well as visible busy state, so rapid
or repeated activation cannot start parallel attempts.

### 4.3 App boundary ordering

`App` renders boundaries in this order:

1. **Authentication not ready:** render the existing `AuthStartupScreen` unchanged.
2. **Setup read pending or explicitly verifying:** render setup loading with no shell or wizard.
3. **Setup read failed or untrusted:** render setup recovery with no shell or wizard.
4. **Validated `OPERATIONAL`:** preserve the shipped tokenless redirect behavior, then render existing
   operational routes when authenticated.
5. **Validated `UNINITIALIZED` or `IN_SETUP`:** expose the existing setup route and redirect non-setup
   routes to it.

Query failure always takes precedence over cached data. A failed refetch cannot authorize routing from a
previous response. Automatic sign-in is gated on a currently validated `OPERATIONAL`; pending, failed,
unknown, and pre-operational states cannot start it.

This preserves the auth boundary shipped in `S-auth-startup-boundary`: its loading/error screen wins
while auth is unresolved, its tab-scoped redirect latch remains unchanged, and neither setup component
modifies auth context behavior.

### 4.4 Post-finalization verification-only phase

The finalization handshake distinguishes a reported mutation success from the read used to confirm
routing:

1. `SetupWizard` issues `POST /api/v1/setup/finalize` exactly as it does today.
2. Only after that POST resolves successfully does it await the App-provided finalization callback.
3. App synchronously records that finalization was acknowledged for the current mount, hiding the wizard
   before starting the state refetch.
4. App renders the post-finalization loading phase while it rereads `/setup/state`.
5. A validated `OPERATIONAL` exits setup normally.
6. A read failure, malformed response, timeout, or contradictory `UNINITIALIZED`/`IN_SETUP` response
   renders post-finalization recovery and keeps the wizard hidden.
7. **Try again** performs one setup-state GET. It cannot call the finalization callback or mutation.

The acknowledgement is local UI safety state, not a replacement for server truth. A full reload starts a
fresh public state read. If the finalize POST itself reports failure, the existing wizard mutation-error
behavior remains: the server did not report success, so this read-only recovery phase has not begun.

## 5. UI design

### 5.1 Placement and visual language

Add a pure `SetupStartupScreen` under `apps/web/src/app/startup/`. It remains distinct from
`AuthStartupScreen` so this slice does not generalize or alter the newly shipped authentication state
contract.

The setup screen reuses the established pre-shell visual language:

- full-viewport `--es-bg` canvas;
- real `/easysynq-mark.svg` at 64 px;
- bordered `--es-surface` panel with maximum width 440 px;
- existing radius, shadow, typography, feedback, and focus tokens;
- no application shell behind the panel; and
- no copied prototype HTML, remote font, CDN asset, or new visual dependency.

This small amount of presentational duplication deliberately isolates the two trust boundaries. A shared
startup framework is not justified by this slice.

### 5.2 Safe copy

| Phase                     | Heading or status                          | Guidance                                                                                                                    |
| ------------------------- | ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| Initial loading           | Checking setup status…                     | Please wait while EasySynQ verifies this installation.                                                                      |
| Post-finalization loading | Verifying setup…                           | Setup was saved. EasySynQ is confirming that the installation is ready.                                                     |
| Initial failure           | Setup status is unavailable                | EasySynQ could not confirm whether this installation is ready. Setup changes are disabled until the status can be verified. |
| Post-finalization failure | Setup was saved, but could not be verified | Try checking the setup status again. EasySynQ will not repeat finalization.                                                 |

Both failures provide:

- primary **Try again**;
- secondary **Reload EasySynQ**; and
- `If this keeps happening, contact your EasySynQ administrator.`

The UI must not render raw exception messages, API problem titles/details/codes, response bodies, HTTP
statuses, URLs, database or host details, malformed values, or stack traces. The component accepts no raw
error or arbitrary detail prop.

### 5.3 Focus, announcements, and narrow layout

- Loading uses a named polite `role="status"` region and no fake percentage, countdown, or automatic
  retry.
- On transition to failure, a focusable `h1` receives programmatic focus once and carries
  `tabIndex={-1}`.
- Retry remains disabled and exposes busy state until its one request settles.
- Both actions have a minimum 44 px target.
- The canvas uses 24 px side padding at the narrow breakpoint; the panel and its children use
  `min-width: 0` where needed.
- At 320 CSS px, the state requires no document-level horizontal scrolling.
- Existing global reduced-motion, focus-ring, dark-mode token, and forced-colors behavior remains
  authoritative.

## 6. Component and file ownership

Expected production surfaces:

- `apps/web/src/app/startup/setupState.ts` — closed type, parser, timeout, fetch boundary;
- `apps/web/src/app/startup/SetupStartupScreen.tsx` — pure loading/recovery view and single-flight action;
- `apps/web/src/App.tsx` — boundary ordering, strict routing, query policy, finalization verification;
- `apps/web/src/SetupWizard.tsx` — minimal promise-returning `onFinalized` seam; and
- focused tests adjacent to those files.

The default setup-state MSW handler remains the operational happy path. Focused tests add per-test
network, HTTP, malformed-body, unknown-state, pre-operational, retry, timeout, and finalization handlers.
No generated contract artifact changes because the published OpenAPI enum already defines the required
state vocabulary.

## 7. Test contract

### 7.1 Parser and request tests

- all three exact states parse successfully;
- missing, null, array, non-string, and unknown states fail;
- HTTP, network, JSON, and schema failures reject without a fallback;
- the request times out at exactly 15,000 ms;
- a late response after timeout cannot produce success;
- cancellation and every settled path clear the timer; and
- one explicit attempt issues one GET.

Use controlled promises and fake timers; no test waits 15 real seconds.

### 7.2 Screen tests

- both loading phases render their approved named status and no recovery actions;
- both failure phases render only approved copy;
- the type interface rejects raw-error and arbitrary-detail props;
- failure focuses the `h1` once;
- rapid repeated retry activation invokes the callback once and remains busy until settlement;
- reload invokes the injected callback;
- both actions satisfy the 44 px contract;
- narrow canvas geometry remains bounded; and
- `jest-axe` reports no violations for all four states.

### 7.3 App and wizard integration tests

- setup-state network and server failure render recovery, not `SetupWizard` or the shell;
- failed setup-state reads issue no setup POST, PATCH, PUT, or DELETE;
- invalid JSON, missing state, and unknown state fail closed;
- `OPERATIONAL` still routes to the application and preserves tokenless auth redirect behavior;
- `UNINITIALIZED` and `IN_SETUP` still route to setup;
- one explicit retry performs one GET and can recover;
- repeated clicks remain single-flight;
- successful finalization followed by a failed state read issues finalization once, hides the wizard,
  and presents read-only recovery;
- retry after that failure issues only a state GET and can recover to operational;
- a contradictory pre-operational response after acknowledged finalization also keeps the wizard hidden;
  and
- existing auth loading, error, stale-latch, explicit-retry, and shell-hiding tests remain unchanged and
  green.

## 8. Verification and documentation

Implementation begins with focused failing tests and proceeds in RED→GREEN tasks. After focused setup,
App, wizard, and auth regression tests pass, run the complete web unit suite, typecheck, lint, production
build, scoped Prettier on touched files, repository authority and Claude-hook tests, site-data guards, and
`git diff --check`.

On completion, update `docs/current-status.md` and `docs/slice-history.md` only from fresh evidence. Keep
the intentional `baseline_commit: faf35b4` history until the implementation evidence supplies its
replacement; do not substitute the prior squash SHA mechanically. Preserve the pending real Fedora VM
acceptance and every current residual exactly unless fresh in-scope evidence changes them.

## 9. Acceptance criteria

1. A setup-state failure, timeout, malformed response, or unknown value never renders `SetupWizard`,
   never renders the operational shell, and never starts authentication redirect.
2. The frontend never infers or defaults setup state to `UNINITIALIZED`.
3. Only the three published states authorize routing, with existing operational and legitimate
   pre-operational behavior preserved.
4. Every setup-state attempt is bounded to 15 seconds and there are no hidden retries or incidental
   refetches.
5. Explicit retry is accessible, single-flight, issues exactly one state GET, and can recover.
6. A state-probe failure issues no setup mutation.
7. After finalization reports success, the wizard remains hidden and no recovery action can repeat the
   finalization mutation.
8. No raw API/server details or malformed values render in the document.
9. Loading and recovery satisfy the focus, announcement, target-size, 320 px, reduced-motion,
   forced-colors, and axe contracts.
10. The authentication startup behavior shipped in `S-auth-startup-boundary` remains unchanged.
11. Focused and complete affected verification is green before handoff.
