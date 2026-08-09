# S-auth-startup-boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace EasySynQ's indefinite authentication spinner and discarded OIDC failures with a bounded, explicit auth state and the owner-approved bordered recovery panel.

**Architecture:** `AuthProvider` owns a discriminated startup state, one provider-local manager cache, generation-guarded 15-second attempts, safe failure classification, and retry behavior. `App` renders one pure pre-shell `AuthStartupScreen` for loading/error states and retains one tab-scoped automatic redirect; authenticated/setup routes mount only after auth becomes ready.

**Tech Stack:** React 19, TypeScript, React Router, oidc-client-ts 3, Mantine 7, TanStack Query 5, Vitest 4, Testing Library, jest-axe. Design authority: `docs/superpowers/specs/2026-08-09-s-auth-startup-boundary-design.md`.

## Global Constraints

- The authentication watchdog is exactly 15,000 ms per bootstrap or redirect attempt. Tests use controlled promises and fake timers; never wait 15 real seconds.
- Tokens remain memory-only. Do not add local-storage, session-storage token, cookie, server, Keycloak, API, migration, or dependency changes.
- Raw exceptions, response bodies, URLs, issuer/realm/client values, OIDC state, tokens, callback parameters, and stack traces must never render in the document.
- Configuration requires a successful HTTP response, valid JSON, and non-empty string `issuer` and `client_id`.
- Preserve `safeReturnTo`, callback-query cleanup, user-renewal updates, and the tab-scoped `es_auth_redirect` guard.
- A timed-out or superseded async result must not commit state. Every attempt is generation guarded and clears its timer.
- The recovery screen uses only existing Mantine components and EasySynQ CSS tokens. Do not copy generated Superdesign HTML, add CDN assets, import a web font, or modify the supplied logo.
- The error heading uses `tabIndex={-1}` and programmatic focus. Loading is a polite named status; recovery is announced without repeated assertive updates.
- Retry is single-flight. Configuration/session retry rebuilds auth bootstrap; callback/redirect retry starts a new redirect only after the user's click.
- Keep setup-state query failure handling, root/not-found boundaries, notifications, URL state, broader keyboard work, responsive data views, and Playwright failure proof out of this slice.
- Run each task's focused tests before its commit. Run `git diff --check` and inspect `git status --short` after each task.
- Commit only listed task files with the listed subject. Use command-local repository author identity if Git has no worktree-local identity.

---

## File and ownership map

| File | Responsibility |
| --- | --- |
| `apps/web/src/lib/auth.tsx` | Explicit auth contract, manager lifecycle, bounded bootstrap/redirect operations, callback cleanup, retry |
| `apps/web/src/lib/auth.test.tsx` | Provider transition, timeout, stale-result, callback, retry, renewal, and open-redirect proofs |
| `apps/web/src/app/startup/AuthStartupScreen.tsx` | Pure approved pre-shell loading/recovery UI, copy mapping, focus, retry busy state |
| `apps/web/src/app/startup/AuthStartupScreen.test.tsx` | Copy, raw-detail exclusion, focus, retry/reload, busy, and axe proofs |
| `apps/web/src/App.tsx` | Exhaustive auth boundary rendering and one-shot operational redirect integration |
| `apps/web/src/App.test.tsx` | No-shell loading/error, one automatic redirect, no loop, explicit retry proofs |
| `apps/web/src/test/render.tsx` | Ready-state default `AuthState` fixture |
| `docs/current-status.md` | Post-implementation execution snapshot |
| `docs/slice-history.md` | Durable shipped-slice evidence |

The production interfaces settle on these names:

```ts
export const AUTH_ATTEMPT_TIMEOUT_MS = 15_000;

export type AuthOperation = "bootstrap" | "redirect";
export type AuthFailureKind =
  | "configuration"
  | "callback"
  | "session"
  | "redirect"
  | "timeout";
export type AuthRecovery = "bootstrap" | "redirect";

export interface AuthFailure {
  kind: AuthFailureKind;
  recovery: AuthRecovery;
}

export type AuthStatus =
  | { kind: "loading"; operation: AuthOperation }
  | { kind: "ready" }
  | { kind: "error"; failure: AuthFailure };

export interface AuthState {
  status: AuthStatus;
  user: User | null;
  token: string | null;
  login: () => Promise<void>;
  retry: () => Promise<void>;
  logout: () => Promise<void>;
}
```

---

### Task 1: Build the complete explicit, bounded authentication provider

**Files:**
- Modify: `apps/web/src/lib/auth.tsx:5-51`
- Modify: `apps/web/src/lib/auth.test.tsx:1-94`
- Modify: `apps/web/src/test/render.tsx:9-15`

**Interfaces:**
- Consumes: existing `AuthConfig`, oidc-client-ts `UserManager`, `safeReturnTo`
- Produces: the complete public auth interface; provider-local manager/attempt ownership; bounded bootstrap and redirect; callback/session/configuration classification; single-flight retry

- [ ] **Step 1: Replace the shared fixture and probe with the explicit contract**

Update `TEST_AUTH` and the auth-test probe before production types:

```tsx
export const TEST_AUTH: AuthState = {
  status: { kind: "ready" },
  token: "test-token",
  user: { profile: { sub: "bbbb1111-1111-1111-1111-111111111111" } } as AuthState["user"],
  login: async () => undefined,
  retry: async () => undefined,
  logout: async () => undefined,
};

function Probe() {
  const { status, token } = useAuth();
  return <div>status:{status.kind} token:{token ?? "none"}</div>;
}
```

Add a type-level/provider test that expects `status:ready`, then run:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx src/test/harness.test.tsx
```

Expected RED: `AuthState` still requires `ready` and does not expose `status` or `retry`.

- [ ] **Step 2: Add the exported discriminated types and provider-local cache refs**

Replace `ready` in `AuthState` with the exact interfaces from the ownership map. Inside `AuthProvider`, initialize:

```tsx
const [status, setStatus] = useState<AuthStatus>({
  kind: "loading",
  operation: "bootstrap",
});
const managerRef = useRef<UserManager | null>(null);
const managerPromiseRef = useRef<Promise<UserManager> | null>(null);
const unsubscribeRef = useRef<(() => void) | null>(null);
const generationRef = useRef(0);
const managerEpochRef = useRef(0);
const activeAttemptRef = useRef<AttemptControl | null>(null);
```

Do not export a test-only reset hook. Provider-local ownership gives every test render an isolated cache while retaining one manager in the real root provider.

- [ ] **Step 3: Write configuration failure tests**

Mock `globalThis.fetch` and the constructor. Add separate tests for:

```ts
it.each([
  ["HTTP", new Response("", { status: 503 })],
  ["JSON", new Response("{", { status: 200 })],
  ["issuer", Response.json({ issuer: "", client_id: "web" })],
  ["client", Response.json({ issuer: "https://id.test", client_id: "" })],
])("classifies invalid %s configuration", async (_case, response) => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
  renderAuthProbe();
  await waitFor(() =>
    expect(screen.getByText("status:error")).toBeInTheDocument(),
  );
  expect(readFailure()).toEqual({
    kind: "configuration",
    recovery: "bootstrap",
  });
});
```

Add a constructor-throw case using `vi.mocked(UserManager).mockImplementationOnce(function () { throw new Error("constructor secret"); })`. Assert only the safe failure category is in context.

Run:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx -t "configuration|constructor"
```

Expected RED: fetch status/schema are unchecked and failures never become context state.

- [ ] **Step 4: Implement validated, deduplicated manager creation**

Create small local helpers:

```ts
function parseAuthConfig(value: unknown): AuthConfig {
  if (
    typeof value !== "object" ||
    value === null ||
    typeof (value as Record<string, unknown>).issuer !== "string" ||
    !(value as Record<string, string>).issuer.trim() ||
    typeof (value as Record<string, unknown>).client_id !== "string" ||
    !(value as Record<string, string>).client_id.trim()
  ) {
    throw new Error("invalid auth configuration");
  }
  return value as AuthConfig;
}
```

The provider-local `loadManager(signal)` must:

1. return `managerRef.current` when present;
2. return `managerPromiseRef.current` when creation is already in flight;
3. fetch `/api/v1/auth/config` with `signal`;
4. require `response.ok`;
5. parse and validate;
6. construct the memory-only manager with the existing PKCE settings;
7. assign `managerRef.current` only after successful construction; and
8. clear `managerPromiseRef.current` in `finally`.

A bootstrap retry calls `unsubscribeRef.current?.()`, nulls the unsubscribe and manager refs, and starts a new creation. No rejected promise remains cached.

Manager creation captures `managerEpochRef.current`. A fresh retry increments the epoch. The creation promise may assign `managerRef.current` only while its captured epoch remains current, and its `finally` may clear `managerPromiseRef.current` only when that ref still points to the same promise. Add a controlled-fetch test in which attempt A times out, attempt B succeeds with new configuration, and A resolves late; a subsequent manager use must retain B and must not construct from A.

- [ ] **Step 5: Confirm the contract changes and failure falsifiers are in place**

Run:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx -t "status:ready|configuration|constructor"
git diff --check
git status --short
```

Expected checkpoint: the ready-state contract can pass after the type change; configuration/constructor cases remain RED until Phase B installs the classified bootstrap boundary. Do not commit this partial provider.

#### Phase B: Bound bootstrap, callback, and stored-session loading

**Files:**
- Modify: `apps/web/src/lib/auth.tsx:53-130`
- Modify: `apps/web/src/lib/auth.test.tsx`

**Interfaces:**
- Consumes: Task 1 auth types and provider-local manager loader
- Produces: generation-guarded `runBootstrap({ fresh: boolean }) -> Promise<void>`; one 15-second attempt deadline; callback/session/timeout failures

- [ ] **Step 1: Add rejection and callback cleanup falsifiers**

Replace the old failed-callback expectation. Assert rejection produces:

```ts
expect(readFailure()).toEqual({
  kind: "callback",
  recovery: "redirect",
});
expect(window.location.search).toBe("");
expect(screen.getByTestId("loc")).toHaveTextContent("/");
```

Add `getUser.mockRejectedValueOnce(new Error("stored-user secret"))` and expect `{ kind: "session", recovery: "bootstrap" }`. Assert neither raw message appears in the probe/document.

Run:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx -t "failed callback|stored-user"
```

Expected RED: callback becomes anonymous-ready and `getUser` rejects outside handled state.

- [ ] **Step 2: Add timeout and stale-completion falsifiers**

Use fake timers and one controlled promise:

```ts
vi.useFakeTimers();
const stored = deferred<User | null>();
getUser.mockReturnValueOnce(stored.promise);
renderAuthProbe();

await act(async () => {
  await vi.advanceTimersByTimeAsync(AUTH_ATTEMPT_TIMEOUT_MS);
});
expect(readFailure()).toEqual({
  kind: "timeout",
  recovery: "bootstrap",
});

stored.resolve({ access_token: "late-token" } as User);
await act(async () => Promise.resolve());
expect(screen.getByText(/token:none/)).toBeInTheDocument();
expect(readFailure().kind).toBe("timeout");
```

Add an unmount case and assert a late resolution causes no state-update warning, the registered user-loaded listener is removed, and `vi.getTimerCount()` is zero immediately after unmount even when the mocked OIDC promise ignores `AbortSignal`.

Run:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx -t "timeout|late|unmount"
```

Expected RED: no watchdog/generation protection exists.

- [ ] **Step 3: Implement one exact deadline helper**

Add private timeout/cancellation errors and one cancellable attempt control:

```ts
class AuthAttemptTimedOut extends Error {}
class AuthAttemptCancelled extends Error {}

interface AttemptControl {
  signal: AbortSignal;
  run<T>(work: Promise<T>): Promise<T>;
  cancel(): void;
}

function createAttemptControl(): AttemptControl {
  const controller = new AbortController();
  let cancelRace: (() => void) | null = null;
  return {
    signal: controller.signal,
    run<T>(work: Promise<T>) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const cancelled = new Promise<never>((_resolve, reject) => {
        cancelRace = () => reject(new AuthAttemptCancelled());
      });
      const timeout = new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          controller.abort();
          reject(new AuthAttemptTimedOut());
        }, AUTH_ATTEMPT_TIMEOUT_MS);
      });
      return Promise.race([work, timeout, cancelled]).finally(() => {
        if (timer !== undefined) clearTimeout(timer);
        cancelRace = null;
      });
    },
    cancel() {
      controller.abort();
      cancelRace?.();
    },
  };
}
```

Do not use an untracked standalone timer. Before a new attempt, cancel the prior `activeAttemptRef`; unmount also cancels it. `AuthAttemptCancelled` performs no state commit and no error logging. In `finally`, clear `activeAttemptRef` only if it still points to that attempt. This makes every success, failure, retry, and unmount settle its race and clear its timer even when the underlying OIDC promise cannot be cancelled.

- [ ] **Step 4: Implement generation-guarded bootstrap**

`runBootstrap({ fresh })` must:

1. increment `generationRef.current` and capture it;
2. set `loading/bootstrap`;
3. optionally clear the manager/listener for a fresh retry;
4. run manager creation plus callback or stored-user loading under one `AttemptControl.run`;
5. register exactly one `userLoaded` listener for the active manager;
6. commit user/navigation/ready only when the captured generation is current;
7. strip callback query before committing callback error;
8. map timeout to `timeout/bootstrap`, config/constructor to `configuration/bootstrap`, callback to `callback/redirect`, and `getUser` to `session/bootstrap`; and
9. sanitize developer logging to stage plus a bounded error name/message with URLs, query strings, callback values, and token-shaped data redacted.

Add a console-spy test with an error message containing `https://id.test/realm?code=abc&state=secret`; the recorded diagnostic must contain the stage but none of the URL, `abc`, or `secret`.

The mount effect calls `void runBootstrap({ fresh: false })`. Cleanup increments generation, cancels `activeAttemptRef`, and removes the listener.

- [ ] **Step 5: Re-run callback safety and renewal regressions**

Run:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx
npm --prefix apps/web run typecheck
git diff --check
git status --short
```

Expected: new bootstrap cases pass and existing renewal, deep-link restore, and `safeReturnTo` tests remain green.

#### Phase C: Make redirect and retry explicit, bounded, and single-flight

**Files:**
- Modify: `apps/web/src/lib/auth.tsx`
- Modify: `apps/web/src/lib/auth.test.tsx`

**Interfaces:**
- Consumes: `runBootstrap`, generation/deadline ownership, `AuthFailure.recovery`
- Produces: promise-returning `login`, `retry`, and `logout`; bounded redirect error transitions

- [ ] **Step 1: Add redirect rejection and timeout falsifiers**

Render a probe that exposes `login`, invoke it inside `act`, and assert:

```ts
signinRedirect.mockRejectedValueOnce(new Error("https://id/realm?secret=value"));
await user.click(screen.getByRole("button", { name: "login" }));
await waitFor(() =>
  expect(readFailure()).toEqual({
    kind: "redirect",
    recovery: "redirect",
  }),
);
expect(document.body).not.toHaveTextContent("secret=value");
```

For timeout, return a controlled promise, advance exactly `AUTH_ATTEMPT_TIMEOUT_MS`, and expect `timeout/redirect`. Resolve late and prove status stays error.

Expected RED:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx -t "redirect rejection|redirect timeout"
```

- [ ] **Step 2: Add recovery-mode and single-flight falsifiers**

Expose `retry` and add:

- configuration/session retry: second config/get-user succeeds, manager constructor runs again, status becomes ready;
- callback/redirect retry: no redirect before click, exactly one redirect after click;
- two synchronous retry calls share/ignore the active recovery rather than starting two operations; and
- retry after failure never replays stripped callback parameters in `returnTo`.

Use `/settings/notifications` as the safe retained route.

Expected RED:

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx -t "retry|single-flight"
```

- [ ] **Step 3: Implement bounded `login()`**

`login()` increments generation, sets `loading/redirect`, obtains the current manager, and runs:

```ts
await manager.signinRedirect({
  state: {
    returnTo: window.location.pathname + window.location.search,
  },
});
```

under the same `AttemptControl` deadline/cancellation rules. A normal resolved redirect may leave loading visible until navigation unloads the page. Rejection maps to `redirect/redirect`; deadline maps to `timeout/redirect`. Late results cannot alter the newer generation.

- [ ] **Step 4: Implement recovery dispatch and stable callbacks**

```ts
const retry = useCallback(async () => {
  if (retryPromiseRef.current) return retryPromiseRef.current;
  const recovery =
    status.kind === "error" ? status.failure.recovery : null;
  if (!recovery) return;
  const attempt =
    recovery === "bootstrap"
      ? runBootstrap({ fresh: true })
      : login();
  retryPromiseRef.current = attempt.finally(() => {
    retryPromiseRef.current = null;
  });
  return retryPromiseRef.current;
}, [login, runBootstrap, status]);
```

Use refs where needed to avoid recreating auth actions every render and retriggering `App` effects. `logout()` returns its real promise and preserves the existing remove-user → clear React user → redirect ordering; logout UI error handling remains out of scope.

- [ ] **Step 5: Run the complete provider suite and commit Task 1**

```bash
npm --prefix apps/web run test -- src/lib/auth.test.tsx
npm --prefix apps/web run typecheck
git diff --check
git status --short
git add apps/web/src/lib/auth.tsx apps/web/src/lib/auth.test.tsx apps/web/src/test/render.tsx
git commit -m "fix: bound authentication startup and recovery"
```

Expected: all provider tests green with no unhandled rejection output.

---

### Task 2: Build the approved bordered startup and recovery panel

**Files:**
- Create: `apps/web/src/app/startup/AuthStartupScreen.tsx`
- Create: `apps/web/src/app/startup/AuthStartupScreen.test.tsx`

**Interfaces:**
- Consumes: `AuthStatus` excluding `ready`
- Produces: `AuthStartupScreen({ status, onRetry, onReload })`

```ts
type StartupStatus = Exclude<AuthStatus, { kind: "ready" }>;

interface AuthStartupScreenProps {
  status: StartupStatus;
  onRetry: () => Promise<void>;
  onReload: () => void;
}
```

- [ ] **Step 1: Write loading and safe-copy rendering tests**

Cover `loading/bootstrap`, `loading/redirect`, and every failure kind. Pin the copy table from the design specification:

```ts
const CASES = [
  ["configuration", "Sign-in is unavailable", "EasySynQ could not connect to its sign-in service."],
  ["callback", "Sign-in was not completed", "Your sign-in response could not be verified."],
  ["session", "Your session could not be loaded", "EasySynQ could not restore your sign-in session."],
  ["redirect", "Sign-in could not be opened", "EasySynQ could not open the sign-in page."],
  ["timeout", "Sign-in is taking too long", "The sign-in service did not respond in time."],
] as const;
```

Assert loading has `role=status`, an accessible name, the real logo, no recovery actions, and no shell navigation. Assert each error has only approved copy; the `AuthStartupScreen` interface must not accept a raw error or arbitrary detail prop.

Run:

```bash
npm --prefix apps/web run test -- src/app/startup/AuthStartupScreen.test.tsx
```

Expected RED: module does not exist.

- [ ] **Step 2: Write interaction, focus, and accessibility tests**

Add tests that:

- wait for the error heading to become `document.activeElement`;
- click **Try sign-in again** twice while a controlled promise is pending and observe one call;
- assert the retry button is disabled/busy until settlement;
- click **Reload EasySynQ** and observe one callback;
- assert both actions meet the production 44 px minimum through component props/styles; and
- run `axe(container)` for loading and all five errors.

Expected RED remains missing component.

- [ ] **Step 3: Implement the pure Mantine component**

Use `Center`, `Paper`, `Stack`, `Image`, `Loader`, `Title`, `Text`, and `Button`. Keep geometry local and tokenized:

```tsx
<Center mih="100dvh" p="lg" bg="var(--es-bg)">
  <Paper
    component="main"
    w="100%"
    maw={440}
    p={{ base: "xl", sm: 48 }}
    radius="md"
    withBorder
    shadow="xs"
    bg="var(--es-surface)"
  >
    {/* stable logo/state stack */}
  </Paper>
</Center>
```

Use a ref/effect to focus only on transition into an error identity. Use local `retryBusy`; the click handler catches provider rejection to avoid an unhandled promise and resets busy in `finally`. Use a real button for both actions; secondary is `variant="subtle"`. Do not introduce CSS files unless a tested narrow-viewport rule cannot be expressed through Mantine props.

- [ ] **Step 4: Make the view suite green**

```bash
npm --prefix apps/web run test -- src/app/startup/AuthStartupScreen.test.tsx
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint -- src/app/startup/AuthStartupScreen.tsx src/app/startup/AuthStartupScreen.test.tsx
git diff --check
git status --short
```

- [ ] **Step 5: Commit Task 2**

```bash
git add apps/web/src/app/startup/AuthStartupScreen.tsx apps/web/src/app/startup/AuthStartupScreen.test.tsx
git commit -m "feat: add authentication recovery panel"
```

---

### Task 3: Integrate the boundary and one-shot redirect in `App`

**Files:**
- Modify: `apps/web/src/App.tsx:57-114`
- Modify: `apps/web/src/App.test.tsx:1-34`
- Modify if required by TypeScript: `apps/web/src/SetupWizard.tsx`

**Interfaces:**
- Consumes: explicit `AuthState`, `AuthStartupScreen`
- Produces: exhaustive pre-shell rendering; one automatic redirect and explicit retry latch reset

- [ ] **Step 1: Add App-level loading/error falsifiers**

Render `App` with explicit auth fixtures:

```tsx
auth: {
  ...TEST_AUTH,
  status: { kind: "loading", operation: "bootstrap" },
  user: null,
  token: null,
}
```

and:

```tsx
auth: {
  ...TEST_AUTH,
  status: {
    kind: "error",
    failure: { kind: "callback", recovery: "redirect" },
  },
  user: null,
  token: null,
  retry,
}
```

Assert named startup/recovery UI renders and `Document Library`, Home links, and setup wizard do not.

Expected RED:

```bash
npm --prefix apps/web run test -- src/App.test.tsx -t "auth loading|auth error"
```

- [ ] **Step 2: Add redirect-loop and explicit-retry falsifiers**

For an operational ready tokenless fixture:

1. clear `es_auth_redirect`;
2. assert `login` is called once;
3. rerender with `status:error/redirect`;
4. flush effects and assert `login` remains one call;
5. click **Try sign-in again**;
6. assert the latch is cleared and `retry` is called once; and
7. rerender again without user action and prove there is no second automatic login.

Also seed `es_auth_redirect=1` before render and prove auto-login is suppressed.

Expected RED:

```bash
npm --prefix apps/web run test -- src/App.test.tsx -t "one automatic|redirect loop|explicit retry"
```

- [ ] **Step 3: Replace boolean startup branching**

Destructure `status`, `retry`, and promise-returning `login`. Before setup loading or route rendering:

```tsx
if (status.kind !== "ready") {
  return (
    <AuthStartupScreen
      status={status}
      onRetry={async () => {
        sessionStorage.removeItem("es_auth_redirect");
        await retry();
      }}
      onReload={() => window.location.reload()}
    />
  );
}
```

The automatic redirect effect begins with `if (status.kind !== "ready" || setupState.isLoading) return;`, sets the latch before `void login()`, and never catches/duplicates provider-owned failure handling. Stable provider callbacks prevent effect churn.

Replace the old bare loader and tokenless hand-built interstitial with `AuthStartupScreen` state-driven rendering. If `status` is ready/tokenless in the brief effect-before-state window, render the same synthetic `{ kind: "loading", operation: "redirect" }` panel rather than the authenticated shell; its unused `onRetry` adapter clears the latch and calls `login()` (not `retry()`, because context is still ready).

- [ ] **Step 4: Update route/setup typing without widening scope**

Promise-returning `login` is structurally valid where a void callback is accepted. If TypeScript requires an adapter, pass `() => void login()` to `SetupWizard`; do not change wizard behavior or its public contract in this slice.

- [ ] **Step 5: Run focused integration and complete web suite**

```bash
npm --prefix apps/web run test -- src/App.test.tsx src/lib/auth.test.tsx src/app/startup/AuthStartupScreen.test.tsx
npm --prefix apps/web run test
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web run build
git diff --check
git status --short
```

Expected: complete web suite green; no anonymous loader, redirect loop, raw error, or shell flash.

- [ ] **Step 6: Commit Task 3**

```bash
git add apps/web/src/App.tsx apps/web/src/App.test.tsx apps/web/src/SetupWizard.tsx
git commit -m "feat: enforce authentication startup boundary"
```

Use `git add` only for `SetupWizard.tsx` if it actually changed.

---

### Task 4: Update execution authority and run final gates

**Files:**
- Modify: `docs/current-status.md`
- Modify: `docs/slice-history.md`

**Interfaces:**
- Consumes: all green implementation commits and their exact verification output
- Produces: shipped `S-auth-startup-boundary` execution snapshot and historical evidence

- [ ] **Step 1: Capture fresh verification evidence**

From the worktree root run:

```bash
npm --prefix apps/web run test
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web run build
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
bash scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
bash scripts/check-no-site-data.sh
git diff --check
git status --short
```

Record exact test counts, commit, and date. Do not reuse Programme 0's counts or claim the pending real Fedora VM acceptance passed.

- [ ] **Step 2: Update current status and slice history**

In `docs/current-status.md`:

- set the latest shipped slice to `S-auth-startup-boundary`;
- set the baseline commit to the implementation head;
- update only test/CI facts supported by the fresh run;
- preserve the Fedora real-VM proof as pending; and
- retain `alembic heads` as executable truth without changing its snapshot unless verified.

In `docs/slice-history.md`, add one concise entry covering explicit state, watchdog, recovery panel, redirect-loop proof, selected design link, and exact verification evidence. Do not create a second current residual ledger or migration head.

- [ ] **Step 3: Re-run authority and documentation guards**

```bash
bash scripts/tests/test-agent-authority.sh
bash scripts/tests/test-claude-hooks.sh
bash scripts/check-repo-authority.sh
bash scripts/tests/test-check-no-site-data.sh
bash scripts/check-no-site-data.sh
git diff --check
git status --short
```

Expected: all guards green; only the two authority documents are uncommitted.

- [ ] **Step 4: Commit Task 4**

```bash
git add docs/current-status.md docs/slice-history.md
git commit -m "docs: record authentication startup boundary"
```

- [ ] **Step 5: Independent final review**

Request a fresh code review against the design spec and the complete implementation range. The reviewer must inspect:

- deadline/timer cleanup and stale-result suppression;
- callback query stripping and return-path safety;
- manager/listener replacement;
- redirect-loop and retry single-flight behavior;
- raw error/detail leakage;
- focus, announcements, mobile width, reduced motion, and axe evidence;
- full-suite/authority evidence; and
- no Superdesign HTML/CDN/font or unrelated Programme 1 work.

Address every Critical or Important finding test-first. Re-run the complete Task 4 gate after any fix and commit the minimal correction separately.
