# S-deeplink-login — preserve the requested path through the Keycloak login round-trip

> **Date:** 2026-06-22 · **Type:** FE-only (apps/web auth flow) · **Origin:** the Codex P2 on
> PR #257 ([discussion_r3453707554](https://github.com/CoJoA13/EasySynQ/pull/257#discussion_r3453707554)),
> owner-deferred from S-notify-fe to this focused follow-up because it reaches into the shared
> `AuthProvider` (a cross-cutting auth-flow change, not a notification-feature change).

## 1 · Context & goal

The SPA holds tokens **in memory only** (`InMemoryWebStorage`) — every reload/new tab starts logged-out by
design, and `App.tsx` auto-`login()`s through Keycloak (Authorization-Code + PKCE) when `operational &&
ready && !token`. The problem: **the requested path is lost across that round-trip.**

S-notify-fe added `/settings/notifications` specifically so the email "Manage notifications" link
(`services/notifications/subjects.py::prefs_link()` → `/settings/notifications`) would work, and slices 1–2
ship emails whose body is a **summary + a deep link back into the vault** (`/documents/{id}`, `/risks`,
`/capa?capa=…`, `/management-reviews/{id}`, …). But a recipient who clicks any of those links **while
logged out** (the normal email scenario) lands on **Home**, not the deep route — so the email channel's
deep-link value (and fork-3's stated goal) is only half-realized.

**Goal:** a logged-out user who opens *any* in-app deep route is returned to **that route** after signing
in — making every email deep-link and bookmarked URL land where it points.

## 2 · The current mechanism (confirmed broken)

`apps/web/src/lib/auth.tsx`:
- `redirect_uri: ${window.location.origin}/` (line 26) — hardcoded root.
- `login: () => signinRedirect()` (line 71) — no app state passed.
- callback (lines 52–59): on `?code&state`, `await mgr.signinRedirectCallback()` then
  `window.history.replaceState({}, "", window.location.pathname)` — and at callback time
  `window.location.pathname` is `/` (because `redirect_uri` was `/`).

`apps/web/src/App.tsx`:
- auto-login effect (lines 63–72): `if (operational && !token) login()` (guarded by a one-shot
  `sessionStorage["es_auth_redirect"]` flag against redirect loops).
- a manual **"Sign in again"** fallback button (lines 84–96) that clears the flag and calls `login()`.

**Trace:** open `/settings/notifications` logged-out → `login()` → Keycloak → back to `${origin}/?code&state`
→ callback → `replaceState("/")` → react-router renders `/` → **Home**. The original path is gone.

## 3 · The fix (OIDC `state`, no Keycloak change)

`main.tsx` nests `BrowserRouter > AuthProvider`, so the callback handler can use react-router's
`useNavigate`. The realm registers `redirect_uri = ${origin}/` only — so we keep `redirect_uri` as-is and
carry the path in the **OIDC `state`** (round-tripped by Keycloak, returned on the callback `User`).

**`login()` — capture the path at call time** (both entry points call `login()` while the URL is still the
deep route, so the captured path is correct for the auto-redirect AND the manual button):

```ts
login: () =>
  void getManager().then((m) =>
    m.signinRedirect({
      state: { returnTo: window.location.pathname + window.location.search },
    }),
  ),
```

**Callback — restore the path via react-router** (AuthProvider is inside `BrowserRouter`):

```ts
// inside the existing useEffect, after const mgr = await getManager():
const navigate = useNavigate(); // from "react-router-dom", at the top of AuthProvider
...
if (params.has("code") && params.has("state")) {
  try {
    const u = await mgr.signinRedirectCallback();
    setUser(u);
    const raw = (u.state as { returnTo?: string } | undefined)?.returnTo;
    navigate(safeReturnTo(raw), { replace: true }); // restores the route AND strips ?code&state
  } catch {
    window.history.replaceState({}, "", window.location.pathname); // unchanged failure fallback
  }
} else {
  setUser(await mgr.getUser());
}
setReady(true);
```

**Open-redirect guard** — accept only a same-origin relative path; everything else → `/`:

```ts
function safeReturnTo(p: string | undefined): string {
  // must be a single-slash absolute PATH (not "//host", not "https://…", not "/\…")
  if (!p || !p.startsWith("/") || p.startsWith("//") || p.startsWith("/\\")) return "/";
  return p;
}
```

### Why this is safe + correct
- `navigate(returnTo, { replace: true })` both renders the intended route **and** clears `?code&state`
  from history (so the existing `replaceState`-to-strip-query intent is preserved for the success path).
- After `setUser(u)`, `token` is set → `App.tsx`'s `operational && !token` auto-login does **not** re-fire
  → no loop, no double-redirect. The `es_auth_redirect` one-shot flag is untouched.
- The guard blocks `//evil.com`, `https://evil`, and backslash tricks — we only ever navigate to an
  in-app path. (We navigate via react-router, never `window.location`, so even a slipped value can't
  leave the origin — the guard is defense-in-depth.)
- The normal root login (`returnTo === "/"`) behaves exactly as today.

## 4 · Edge cases

- **Setup flow:** `login()` only fires when `operational`; a captured `returnTo` is always an app route.
  `/setup`/`/admin` paths are still valid relative paths; the route's own gating decides access.
- **Stale/missing state:** a callback whose `state` lacks `returnTo` (older redirect, or external) →
  `safeReturnTo(undefined)` → `/` (today's behavior).
- **Callback error:** unchanged — fall through to logged-out + `replaceState(pathname)`.
- **A route the user can't access** (e.g. `/admin` for a non-admin): not auth's concern — the route's
  own gating renders its calm no-access / redirect.
- **`useNavigate` dependency:** react-router's `navigate` is stable; include it in the effect deps. The
  effect must still run once (the `[]`→`[navigate]` change is inert since `navigate` is stable).

## 5 · Files

- **Edit:** `apps/web/src/lib/auth.tsx` (the `login()` state arg, the `useNavigate` import + callback
  restore, the `safeReturnTo` guard).
- **Test:** `apps/web/src/lib/auth.test.tsx` (extend).
- No `App.tsx` change required (both `login()` call sites already capture the live URL). No BE, migration,
  contract, permission-key, or Keycloak-realm change.

## 6 · Testing strategy

The existing `auth.test.tsx` renders `AuthProvider` without mocking `oidc-client-ts` (the
`/api/v1/auth/config` fetch fails in jsdom → caught → `ready:true, token:none`). The returnTo round-trip
needs the `UserManager` mocked:

- **`vi.mock("oidc-client-ts")`** — stub `UserManager` so `signinRedirect(args)` records `args.state`, and
  `signinRedirectCallback()` resolves a fake `User` with `{ state: { returnTo }, access_token }`.
- **Test A — capture:** render `AuthProvider` at route `/settings/notifications`, trigger `login()`, assert
  `signinRedirect` was called with `state.returnTo === "/settings/notifications"`.
- **Test B — restore:** render at `/?code=x&state=y` with the mocked callback returning
  `state.returnTo = "/settings/notifications"`; assert the rendered location becomes
  `/settings/notifications` (via a `useLocation` probe) and the query is gone.
- **Test C — open-redirect guard:** callback `returnTo = "//evil.com"` (and `"https://evil"`) → location
  becomes `/`, never the external value.
- **Test D — root/normal:** `returnTo = "/"` (or absent) → location `/` (regression of today's behavior).
- Test discipline: `import { expect, it/test, vi } from "vitest"`.

**Verification (the login path is load-bearing — a bug breaks ALL sign-ins):** full `/check-web`; then a
**live re-login smoke** — (1) open a deep route (`/settings/notifications`, `/risks`) while logged out →
Keycloak → land on **that** route; (2) the plain root login still works; (3) the "Sign in again" fallback
still works; (4) an active SSO session deep-link returns to the deep route silently.

## 7 · Decisions-register

No entry needed — this is a behavior fix to the existing auth flow (no new capability, key, or binding
decision). Confirm with the owner; if they want it on record, it's a one-line note, not a new R-number.

## 8 · What it closes

The Codex #257 P2; fork-3's email `prefs_link` (now lands on the settings page from an email, logged out);
**every** notification-email deep-link (slices 1–2); and any bookmarked/shared deep URL into the SPA.
