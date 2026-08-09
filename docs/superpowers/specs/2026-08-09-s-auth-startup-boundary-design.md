# S-auth-startup-boundary design

**Status:** Pending owner review of the written specification  
**Programme:** Programme 1 — frontend resilience and accessibility  
**Slice:** 1 of 8 — startup/authentication boundary  
**Date:** 2026-08-09  
**Baseline:** `1499734` (`main`, Programme 0 squash merge)

## 1. Outcome

EasySynQ must never leave a user at an unnamed or indefinite authentication spinner. Every authentication startup attempt ends in one of three explicit states:

1. a named loading state while bounded work is in progress;
2. a ready state that allows setup or authenticated routing to continue; or
3. a visible, actionable recovery state that stops automatic redirects until the user chooses what to do.

The owner selected the explicit-auth-state approach and the bordered full-page recovery panel. The approved visual direction is [EasySynQ — Recovery State](https://superdesign.dev/teams/81a47a41-fffd-47be-95bc-bbef545ae6b8/projects/6d7c21ea-07f0-442e-9f3b-bd90da61fa20?node=draft-variant-f3b43b85-78d4-414e-a5d3-50617431d73f). The generated HTML is a visual reference, not production source.

## 2. Current failure modes

`apps/web/src/lib/auth.tsx` currently launches an unhandled async bootstrap effect. Failures from configuration fetch, response decoding, `UserManager` creation, or `getUser()` do not set `ready`, so `App` renders a bare loader forever. A failed callback is silently converted into a logged-out state, and `login()` discards redirect failures.

`apps/web/src/App.tsx` also:

- combines auth startup and setup-state loading behind one unnamed spinner;
- invokes automatic sign-in without observing the returned result;
- provides no failure state when `signinRedirect()` rejects or stalls; and
- represents authentication readiness with a boolean that cannot explain loading, failure, or recovery.

## 3. Scope

This slice includes:

- an explicit authentication status contract;
- bounded authentication bootstrap and redirect attempts;
- safe failure classification and copy;
- the approved full-page loading and recovery UI;
- user-controlled retry and reload actions;
- callback-query cleanup before failure is shown;
- redirect-loop prevention; and
- focused unit, interaction, timeout, and accessibility tests.

This slice does not include:

- setup-state query failure handling (Programme 1 slice 2);
- the root route error boundary or not-found page (slice 3);
- mutation notification work (slice 4);
- URL-state, keyboard, responsive data-view, or Playwright programme slices;
- Keycloak/API behavior changes;
- token persistence changes; or
- a visual redesign of the authenticated shell.

## 4. Chosen architecture

### 4.1 Auth context contract

Replace `ready: boolean` with an explicit status:

```ts
type AuthStatus =
  | { kind: "loading"; operation: "bootstrap" | "redirect" }
  | { kind: "ready" }
  | { kind: "error"; failure: AuthFailure };

type AuthFailureKind =
  | "configuration"
  | "callback"
  | "session"
  | "redirect"
  | "timeout";

interface AuthState {
  status: AuthStatus;
  user: User | null;
  token: string | null;
  login: () => Promise<void>;
  retry: () => Promise<void>;
  logout: () => Promise<void>;
}
```

`AuthFailure` carries only a safe category and the recovery operation required by the provider. Raw thrown values do not enter React state and are never rendered. `user` and `token` remain available to existing consumers; the shared test fixture gains `status`, `retry`, and promise-returning actions.

This is intentionally smaller than a general-purpose state-machine dependency. The union still makes invalid startup combinations unrepresentable and gives `App` one exhaustive rendering branch.

### 4.2 Authentication attempt ownership

`AuthProvider` owns bootstrap, callback completion, stored-user loading, redirect startup, timeout, and retry transitions. `App` decides only which full-page state or route tree to render.

Each attempt receives a monotonically increasing generation identifier. Only the current generation may commit a user or status. This prevents an OIDC promise that resolves after a timeout or retry from overwriting the newer attempt.

An active attempt is bounded by a single 15-second watchdog:

- configuration fetch receives an abort signal;
- OIDC promises that cannot be cancelled are ignored if they settle after the generation expires;
- unmount invalidates the active generation and removes the `userLoaded` listener; and
- timeout always transitions to a visible error state.

The timeout is a safety ceiling, not a progress estimate. The UI shows no percentage, countdown, or automatic retry.

### 4.3 Configuration and manager behavior

Authentication configuration must satisfy all of the following before manager construction:

- the response is successful;
- the body is valid JSON; and
- `issuer` and `client_id` are non-empty strings.

A failed or explicitly retried initialization clears the cached manager before rebuilding it, so retry re-fetches current configuration instead of reusing a potentially stale partial initialization. Concurrent callers must share the same in-flight manager creation rather than issuing duplicate configuration requests.

Tokens remain memory-only. No local-storage, session-storage token, cookie, or logging behavior changes.

### 4.4 Callback failure

When the URL contains an OIDC callback:

1. the provider attempts callback validation inside the bounded bootstrap attempt;
2. success restores only a path accepted by `safeReturnTo`;
3. failure removes the callback query from browser history before rendering recovery; and
4. failure is no longer downgraded to an anonymous ready state.

The original callback exception, query values, state, and provider URL are not displayed.

### 4.5 Redirect and loop behavior

The existing `es_auth_redirect` tab-scoped guard remains the single automatic-attempt latch:

- an operational, ready, tokenless app may start one automatic redirect;
- a redirect rejection or timeout enters `error` and cannot trigger another effect-driven redirect;
- an explicit retry clears the latch before starting the provider-selected recovery operation; and
- successful user loading clears the latch.

`login()` returns a promise and owns redirect errors. Callers may fire it with `void`, but a rejected OIDC operation is converted into context state rather than becoming an unhandled rejection.

Retry repeats the operation appropriate to the failure:

- configuration, stored-session, or bootstrap timeout failures start a fresh bootstrap with a rebuilt manager;
- callback or redirect failures start a fresh sign-in redirect after unsafe callback parameters have been removed; and
- only the user's click authorizes that post-failure redirect.

## 5. UI design

### 5.1 Loading

The initial state is a full-viewport, pre-shell composition on `--es-bg`:

- real `/easysynq-mark.svg` at 64 px;
- bordered `--es-surface` panel, maximum width 440 px;
- named status `Connecting to sign-in…`;
- concise supporting copy;
- indigo Mantine loader; and
- `role="status"` with a polite accessible announcement.

The loader has no fake progress. Reduced-motion behavior comes from the existing global rule.

### 5.2 Recovery

The recovery state uses the same panel geometry to avoid layout movement:

- focusable `h1` receives programmatic focus with `tabIndex={-1}` after failure;
- a short, failure-specific heading and one sentence of safe guidance;
- primary full-width **Try sign-in again** button;
- secondary **Reload EasySynQ** button styled as a subtle/text action;
- support hint: `If this keeps happening, contact your EasySynQ administrator.`; and
- no authenticated navigation shell behind the panel.

The panel uses only existing EasySynQ tokens: system font, indigo action, neutral surfaces/text, 8 px radius, hairline border, small shadow, and the existing focus ring. The logo's blue and teal remain confined to the supplied brand asset.

On narrow screens the canvas uses 24 px side padding, the panel may become visually borderless, and the primary action remains full width with a minimum 44 px target. The content never requires horizontal scrolling at 320 CSS px.

### 5.3 Safe copy mapping

| Failure | Heading | Guidance |
| --- | --- | --- |
| Configuration or manager creation | Sign-in is unavailable | EasySynQ could not connect to its sign-in service. |
| Callback validation | Sign-in was not completed | Your sign-in response could not be verified. |
| Stored-user loading | Your session could not be loaded | EasySynQ could not restore your sign-in session. |
| Redirect startup | Sign-in could not be opened | EasySynQ could not open the sign-in page. |
| Timeout | Sign-in is taking too long | The sign-in service did not respond in time. |

Raw exceptions, response bodies, URLs, issuer/realm/client values, OIDC state, tokens, and stack traces are prohibited UI content. Developer logging may include the failure stage and a sanitized error name/message, but never tokens or callback parameters.

### 5.4 Reload

**Reload EasySynQ** invokes `window.location.reload()` through a passed callback so the view remains unit-testable. It is secondary because reload can reproduce the same failure; retry is the preferred recovery action.

## 6. Component placement

Add one pure startup view component under `apps/web/src/app/startup/` rather than expanding the general query-state primitives in `lib/states.tsx`. Authentication startup is a full-application boundary with focus management and recovery actions; it is not interchangeable with an in-page query error.

Expected production surfaces:

- `apps/web/src/lib/auth.tsx` — explicit state, bounded attempts, retry behavior;
- `apps/web/src/app/startup/AuthStartupScreen.tsx` — approved loading/recovery UI;
- `apps/web/src/App.tsx` — exhaustive startup rendering and redirect latch;
- `apps/web/src/test/render.tsx` — updated auth fixture; and
- focused tests adjacent to those files.

No generated Superdesign HTML, remote font, CDN dependency, or prototype JavaScript enters the application.

## 7. Test contract

### 7.1 Provider tests

- successful anonymous and authenticated bootstraps reach `ready`;
- renewed-user events still replace the in-memory token;
- configuration network, HTTP, JSON, schema, and manager-construction failures become actionable errors;
- `getUser()` rejection becomes a session error;
- callback rejection strips query parameters and becomes a callback error;
- callback success preserves the guarded return path;
- bootstrap and redirect timeouts become timeout errors at 15 seconds;
- late resolutions after timeout/retry cannot change current state;
- redirect rejection becomes a redirect error without an unhandled promise;
- retry clears stale manager initialization and can recover to `ready`;
- callback/redirect retry starts only after explicit activation; and
- unmount removes the event listener and prevents state commits.

### 7.2 App and view tests

- loading has a named status region and no authenticated shell;
- each failure category renders only its approved safe copy;
- raw thrown text is absent;
- the error heading receives focus;
- retry is disabled/busy while its promise is active and cannot double-submit;
- reload invokes the injected reload callback;
- the automatic redirect runs once per tab;
- failure never causes an automatic redirect loop;
- explicit retry clears the latch and permits one new attempt; and
- `jest-axe` reports no violations for loading and every recovery variant.

Use controlled promises and fake timers; tests must not wait 15 real seconds.

### 7.3 Regression verification

Run the focused auth/App/view tests first, then the complete web unit suite, typecheck, lint, build, repository authority/site-data guards, and `git diff --check`. The slice must not reduce existing callback open-redirect coverage or token-renewal coverage.

## 8. Documentation and handoff

On completion, update `docs/current-status.md` and `docs/slice-history.md` through the repository's finish-slice contract. The status update must distinguish Programme 0's merged repository-foundation work from the still-pending real Fedora VM acceptance; it must not convert that pending proof into a pass.

The local `.superdesign/` discovery context is not application authority and must not be committed as a duplicate source-code mirror. The durable design authority is this specification plus the selected canvas link.

## 9. Acceptance criteria

1. No authentication startup or redirect promise can leave EasySynQ on an indefinite spinner.
2. Every failure category reaches the approved bordered recovery screen within the bounded attempt.
3. No failure automatically retries after the one permitted tab-scoped redirect.
4. Explicit retry is actionable, single-flight, and capable of recovering.
5. Callback parameters are removed before callback-failure recovery is rendered.
6. Raw authentication details never render in the document.
7. Loading and recovery meet the specified keyboard, focus, announcement, target-size, reduced-motion, narrow-viewport, and axe contracts.
8. The authenticated shell never flashes behind startup or recovery.
9. Existing memory-only token, renewal, callback-return, and open-redirect protections remain intact.
10. Focused and full verification are green before handoff.
