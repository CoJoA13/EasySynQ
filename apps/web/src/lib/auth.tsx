import { InMemoryWebStorage, type User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

interface AuthConfig {
  issuer: string;
  client_id: string;
  audience: string;
}

let _manager: UserManager | null = null;

// The SPA reads the realm/client from the API, then runs Authorization-Code + PKCE directly
// against Keycloak. Tokens live in memory only (InMemoryWebStorage) — never localStorage — so a
// page reload re-authenticates rather than persisting a token.
async function getManager(): Promise<UserManager> {
  if (_manager) return _manager;
  const cfg = (await (await fetch("/api/v1/auth/config")).json()) as AuthConfig;
  _manager = new UserManager({
    authority: cfg.issuer,
    client_id: cfg.client_id,
    redirect_uri: `${window.location.origin}/`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: "code",
    scope: "openid profile email",
    userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
  });
  return _manager;
}

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

export interface AuthState {
  ready: boolean;
  user: User | null;
  token: string | null;
  login: () => void;
  logout: () => void;
}

export const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const navigate = useNavigate();
  const navRef = useRef(navigate);
  navRef.current = navigate;

  useEffect(() => {
    let cancelled = false;
    let unsubscribeUserLoaded: (() => void) | undefined;

    // oidc-client-ts renews an access token in place (normally via the refresh token) and emits the
    // replacement User. Keep React's bearer-token source synchronized so every useApi consumer sees
    // the renewed token without unmounting the app or discarding an in-progress form.
    const onUserLoaded = (loadedUser: User) => {
      if (!cancelled) setUser(loadedUser);
    };

    void (async () => {
      const mgr = await getManager();
      if (cancelled) return;
      unsubscribeUserLoaded = mgr.events.addUserLoaded(onUserLoaded);

      const params = new URLSearchParams(window.location.search);
      if (params.has("code") && params.has("state")) {
        try {
          const u = await mgr.signinRedirectCallback();
          if (cancelled) return;
          setUser(u);
          // Restore the path stashed in the OIDC state (also strips ?code&state from the URL).
          navRef.current(safeReturnTo((u.state as { returnTo?: string } | undefined)?.returnTo), {
            replace: true,
          });
        } catch {
          if (cancelled) return;
          // invalid/expired callback — strip the query, fall through to logged-out
          window.history.replaceState({}, "", window.location.pathname);
        }
      } else {
        const storedUser = await mgr.getUser();
        if (cancelled) return;
        setUser(storedUser);
      }
      setReady(true);
    })();

    return () => {
      cancelled = true;
      unsubscribeUserLoaded?.();
    };
  }, []); // once-on-mount bootstrap: navRef.current is always latest, no navigate identity dep

  const value: AuthState = {
    ready,
    user,
    token: user?.access_token ?? null,
    login: () =>
      void getManager().then((m) =>
        m.signinRedirect({
          state: { returnTo: window.location.pathname + window.location.search },
        }),
      ),
    logout: () =>
      void getManager().then(async (m) => {
        await m.removeUser();
        setUser(null);
        await m.signoutRedirect();
      }),
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
