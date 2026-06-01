import {
  InMemoryWebStorage,
  type User,
  UserManager,
  WebStorageStateStore,
} from "oidc-client-ts";
import { useEffect, useState } from "react";

interface AuthConfig {
  issuer: string;
  client_id: string;
  audience: string;
}

let _manager: UserManager | null = null;

// The SPA reads the realm/client from the API, then runs Authorization-Code + PKCE
// directly against Keycloak. Tokens live in memory only (InMemoryWebStorage) — never
// localStorage — so a page reload re-authenticates rather than persisting a token.
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

export interface AuthState {
  ready: boolean;
  user: User | null;
  login: () => void;
  logout: () => void;
}

export function useAuth(): AuthState {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    void (async () => {
      const mgr = await getManager();
      const params = new URLSearchParams(window.location.search);
      if (params.has("code") && params.has("state")) {
        try {
          setUser(await mgr.signinRedirectCallback());
        } catch {
          /* invalid/expired callback — fall through to logged-out */
        }
        window.history.replaceState({}, "", window.location.pathname);
      } else {
        setUser(await mgr.getUser());
      }
      setReady(true);
    })();
  }, []);

  return {
    ready,
    user,
    login: () => {
      void getManager().then((m) => m.signinRedirect());
    },
    logout: () => {
      void getManager().then(async (m) => {
        await m.removeUser();
        setUser(null);
        await m.signoutRedirect();
      });
    },
  };
}
