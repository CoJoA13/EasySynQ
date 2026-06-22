# S-deeplink-login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A logged-out user who opens any in-app deep route (the email `prefs_link` `/settings/notifications`, every notification deep-link, a bookmark) is returned to **that route** after Keycloak login, instead of landing on Home.

**Architecture:** Carry the requested path in the **OIDC `state`** on `signinRedirect` (the oidc-client-ts *stateStore* already survives the full-page Keycloak round-trip — that's how PKCE works today; only the *userStore* is in-memory), then restore it with react-router `navigate(returnTo, {replace:true})` after `signinRedirectCallback`. A pure `safeReturnTo` guard blocks open-redirects. Contained to `apps/web/src/lib/auth.tsx`.

**Tech Stack:** React/TS, oidc-client-ts ^3.1.0, react-router-dom, vitest + Testing Library.

## Global Constraints

- **FE-only.** No BE, migration, contract, permission-key, or Keycloak-realm change. `redirect_uri` stays `${origin}/` (the path rides the OIDC `state`, not the redirect URI).
- The login path is **load-bearing** — a bug breaks ALL sign-ins. Verify a live re-login before merge.
- `safeReturnTo` accepts ONLY a same-origin absolute path: a single leading `/`, NOT `//host`, NOT an absolute URL, NOT `/\`. Everything else → `/`.
- We navigate via react-router `navigate` (never `window.location`) — defense-in-depth against open-redirect.
- `AuthProvider` is rendered inside `BrowserRouter` (main.tsx) → `useNavigate` is valid; `auth.test.tsx` is the ONLY direct render of `AuthProvider` and must now wrap it in a router.
- Test discipline: every test file `import { expect, it/test, vi } from "vitest"`.
- Verify before PR: full `/check-web` (eslint + strict `tsc` + build + the whole vitest suite).

---

### Task 1: `safeReturnTo` — the open-redirect guard (pure)

**Files:**
- Modify: `apps/web/src/lib/auth.tsx` (add + export `safeReturnTo`)
- Test: `apps/web/src/lib/auth.test.tsx` (extend)

**Interfaces:**
- Produces: `safeReturnTo(p: unknown): string` — a same-origin absolute path, else `/`.

- [ ] **Step 1: Write the failing test** — append to `apps/web/src/lib/auth.test.tsx` (and add `safeReturnTo` to the existing `./auth` import):

```tsx
import { describe, expect, it } from "vitest";
import { safeReturnTo } from "./auth";

describe("safeReturnTo", () => {
  it("passes a same-origin absolute path (with query) through", () => {
    expect(safeReturnTo("/settings/notifications")).toBe("/settings/notifications");
    expect(safeReturnTo("/capa?capa=c1")).toBe("/capa?capa=c1");
  });
  it("rejects a protocol-relative or absolute URL → /", () => {
    expect(safeReturnTo("//evil.com")).toBe("/");
    expect(safeReturnTo("https://evil.com/x")).toBe("/");
    expect(safeReturnTo("/\\evil.com")).toBe("/");
  });
  it("rejects non-path / missing values → /", () => {
    expect(safeReturnTo(undefined)).toBe("/");
    expect(safeReturnTo("")).toBe("/");
    expect(safeReturnTo("relative/path")).toBe("/");
    expect(safeReturnTo(42)).toBe("/");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npx vitest run src/lib/auth.test.tsx -t safeReturnTo`
Expected: FAIL — `safeReturnTo` is not exported.

- [ ] **Step 3: Add the implementation** — in `apps/web/src/lib/auth.tsx`, after the `getManager` function and before `AuthProvider`:

```tsx
// A logged-out deep-link must survive the Keycloak round-trip: we stash the requested path in the OIDC
// `state` on signinRedirect and restore it after the callback. `safeReturnTo` is the open-redirect guard —
// accept ONLY a same-origin absolute PATH (a single leading slash); anything else (protocol-relative
// "//host", an absolute URL, a "/\" backslash trick, a non-string) falls back to "/". We navigate via
// react-router (never window.location), so this guard is defense-in-depth.
export function safeReturnTo(p: unknown): string {
  if (typeof p !== "string" || !p.startsWith("/") || p.startsWith("//") || p.startsWith("/\\")) {
    return "/";
  }
  return p;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npx vitest run src/lib/auth.test.tsx -t safeReturnTo`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/auth.tsx apps/web/src/lib/auth.test.tsx
git commit -m "feat(s-deeplink-login): safeReturnTo open-redirect guard"
```

---

### Task 2: capture the path on login + restore it after the callback

**Files:**
- Modify: `apps/web/src/lib/auth.tsx` (`login()` state arg; `useNavigate` import; callback restore)
- Test: `apps/web/src/lib/auth.test.tsx` (router-wrap the existing test; add capture + restore + guard integration tests)

**Interfaces:**
- Consumes: `safeReturnTo` (Task 1); `useNavigate` from `react-router-dom`.
- Produces: `login()` now calls `signinRedirect({ state: { returnTo: <path> } })`; the callback navigates to `safeReturnTo(user.state.returnTo)`.

- [ ] **Step 1: Write the failing tests** — REPLACE the body of `apps/web/src/lib/auth.test.tsx`'s existing render-based test and add the new ones. The full non-`safeReturnTo` portion of the file becomes:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, test, vi } from "vitest";
import { AuthProvider, useAuth } from "./auth";

// Mock oidc-client-ts: one UserManager whose methods are hoisted module spies we reconfigure per test.
// (vi.mock is hoisted above const decls → the spies must come from vi.hoisted.)
const { signinRedirect, signinRedirectCallback, getUser } = vi.hoisted(() => ({
  signinRedirect: vi.fn(async () => undefined),
  signinRedirectCallback: vi.fn(async () => null as unknown),
  getUser: vi.fn(async () => null as unknown),
}));
vi.mock("oidc-client-ts", () => ({
  UserManager: vi.fn(() => ({
    signinRedirect,
    signinRedirectCallback,
    getUser,
    removeUser: vi.fn(),
    signoutRedirect: vi.fn(),
  })),
  InMemoryWebStorage: vi.fn(),
  WebStorageStateStore: vi.fn(),
}));

beforeEach(() => {
  signinRedirect.mockClear();
  signinRedirectCallback.mockReset();
  signinRedirectCallback.mockResolvedValue(null);
  getUser.mockReset();
  getUser.mockResolvedValue(null);
  window.history.pushState({}, "", "/"); // login()/callback read window.location, not the MemoryRouter
});
afterEach(() => {
  window.history.pushState({}, "", "/");
});

function Probe() {
  const { ready, token } = useAuth();
  return (
    <div>
      ready:{String(ready)} token:{token ?? "none"}
    </div>
  );
}
function LoginProbe() {
  const { login } = useAuth();
  return (
    <button type="button" onClick={login}>
      login
    </button>
  );
}
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

test("AuthProvider exposes auth context to children", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/ready:true/)).toBeInTheDocument());
  expect(screen.getByText(/token:none/)).toBeInTheDocument();
});

it("login() stashes the current path in the OIDC returnTo state", async () => {
  window.history.pushState({}, "", "/settings/notifications?x=1");
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <LoginProbe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "login" }));
  await waitFor(() => expect(signinRedirect).toHaveBeenCalled());
  expect(signinRedirect).toHaveBeenCalledWith({
    state: { returnTo: "/settings/notifications?x=1" },
  });
});

it("the callback restores the returnTo path via react-router", async () => {
  window.history.pushState({}, "", "/?code=abc&state=xyz");
  signinRedirectCallback.mockResolvedValue({
    state: { returnTo: "/settings/notifications" },
    access_token: "t",
  });
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent("/settings/notifications"),
  );
});

it("the callback applies the open-redirect guard (foreign returnTo → /)", async () => {
  window.history.pushState({}, "", "/?code=abc&state=xyz");
  signinRedirectCallback.mockResolvedValue({
    state: { returnTo: "//evil.com" },
    access_token: "t",
  });
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/"));
  expect(screen.getByTestId("loc")).not.toHaveTextContent("evil.com");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/web && npx vitest run src/lib/auth.test.tsx`
Expected: FAIL — `login()` doesn't pass `state` (capture test), and the callback uses `replaceState` not `navigate` (restore/guard tests don't update the MemoryRouter location). (The `safeReturnTo` tests from Task 1 still pass.)

- [ ] **Step 3: Wire `auth.tsx`** — apply three edits:

(a) Add the import at the top (after the existing react import):

```tsx
import { useNavigate } from "react-router-dom";
```

(b) In `AuthProvider`, add `const navigate = useNavigate();` (after the `ready` useState) and replace the callback branch + the effect deps. The effect becomes:

```tsx
  const navigate = useNavigate();

  useEffect(() => {
    void (async () => {
      const mgr = await getManager();
      const params = new URLSearchParams(window.location.search);
      if (params.has("code") && params.has("state")) {
        try {
          const u = await mgr.signinRedirectCallback();
          setUser(u);
          // Restore the path stashed in the OIDC state (also strips ?code&state from the URL).
          navigate(safeReturnTo((u.state as { returnTo?: string } | undefined)?.returnTo), {
            replace: true,
          });
        } catch {
          // invalid/expired callback — strip the query, fall through to logged-out
          window.history.replaceState({}, "", window.location.pathname);
        }
      } else {
        setUser(await mgr.getUser());
      }
      setReady(true);
    })();
  }, [navigate]);
```

(c) Change `login` in the `value` object to stash the path:

```tsx
    login: () =>
      void getManager().then((m) =>
        m.signinRedirect({
          state: { returnTo: window.location.pathname + window.location.search },
        }),
      ),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/web && npx vitest run src/lib/auth.test.tsx`
Expected: PASS (the original context test + capture + restore + guard + the 3 `safeReturnTo` cases).

- [ ] **Step 5: Typecheck the touched file’s package**

Run: `cd apps/web && npx tsc --noEmit`
Expected: 0 errors. (If `m.signinRedirect({state})` or `u.state` typecheck-complains, the cast `(u.state as { returnTo?: string } | undefined)` and the `SigninRedirectArgs.state` field are the right shapes for oidc-client-ts ^3.)

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/auth.tsx apps/web/src/lib/auth.test.tsx
git commit -m "feat(s-deeplink-login): preserve the requested path through the Keycloak login round-trip"
```

---

### Task 3: full gate + adversarial review + live re-login

**Files:** none (verification only).

- [ ] **Step 1: Full web gate**

Run: `cd apps/web && npm run lint && npx tsc --noEmit && npm run build && npx vitest run`
Expected: eslint clean (incl. react-hooks/exhaustive-deps on the `[navigate]` effect), tsc clean, build OK, the whole vitest suite green (no other test rendered `AuthProvider` un-wrapped — confirmed only `auth.test.tsx` does).

- [ ] **Step 2: Adversarial review**

Run `diff-critic` on the branch diff. Focus: the login path can't break (the catch still strips the query + falls through to logged-out on a failed callback); the guard rejects every non-same-origin form; `navigate` is stable in the effect deps (no re-run loop); no behavior change for the normal root login (`returnTo === "/"`).

- [ ] **Step 3: Live re-login smoke (the load-bearing check)** — via Chrome MCP (owner does the Keycloak login; rebuild the web image first: `docker compose --env-file .env -f infra/compose/compose.yml -f infra/compose/compose.s.yml up -d --build web`). Verify:
  1. Logged-out, open `http://localhost/settings/notifications` → Keycloak → land back on **`/settings/notifications`** (not Home).
  2. Logged-out, open `http://localhost/risks` → land on `/risks`.
  3. Plain root login (`http://localhost/`) still works and lands on Home.
  4. The "Sign in again" fallback button still works.
  5. An active SSO session deep-link returns to the deep route silently.

- [ ] **Step 4:** Commit any review fixes, then hand to the PR flow.

---

## Self-review notes (author)

- **Spec coverage:** §3 login-state-capture → Task 2 step 3c; §3 callback-restore → Task 2 step 3b; §3 guard → Task 1; §4 edge cases (root/absent/foreign) → Task 1 + Task 2 tests; §5 files (auth.tsx + auth.test.tsx only) → matches; §6 vi.mock test plan → Task 2; §6 live re-login → Task 3. Full coverage.
- **Placeholder scan:** none — every step has complete code/commands.
- **Type consistency:** `safeReturnTo(p: unknown): string` used identically in Task 1 + Task 2; `returnTo` key spelled consistently in `login()`, the callback cast, and every test.
- **Load-bearing caveat surfaced:** the catch path is preserved (a failed callback never wedges sign-in), and Task 3 step 3 is a mandatory live re-login.
