import { InMemoryWebStorage, type User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";

interface AuthConfig {
  issuer: string;
  client_id: string;
  audience: string;
}

function parseAuthConfig(value: unknown): AuthConfig {
  const config = value as Record<string, unknown> | null;
  if (
    typeof value !== "object" ||
    config === null ||
    typeof config.issuer !== "string" ||
    !config.issuer.trim() ||
    typeof config.client_id !== "string" ||
    !config.client_id.trim()
  ) {
    throw new Error("invalid auth configuration");
  }
  return value as AuthConfig;
}

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

function logAuthFailure(stage: string, error: unknown): void {
  const allowedNames = new Set(["Error", "TypeError", "SyntaxError", "AbortError", "NetworkError"]);
  const candidateName = error instanceof Error ? error.name : "UnknownError";
  const name = allowedNames.has(candidateName) ? candidateName : "Error";
  // Exception messages at this trust boundary may contain response fragments, callback parameters,
  // provider URLs, or token-shaped values. Keep diagnostics useful without forwarding attacker- or
  // provider-controlled text.
  console.error(`[auth:${stage}]`, `${name}: authentication operation failed`);
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

export const AUTH_ATTEMPT_TIMEOUT_MS = 15_000;

export type AuthOperation = "bootstrap" | "redirect";
export type AuthFailureKind = "configuration" | "callback" | "session" | "redirect" | "timeout";
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

export const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>({ kind: "loading", operation: "bootstrap" });
  const managerRef = useRef<UserManager | null>(null);
  const managerPromiseRef = useRef<Promise<UserManager> | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const generationRef = useRef(0);
  const managerEpochRef = useRef(0);
  const activeAttemptRef = useRef<AttemptControl | null>(null);
  const retryPromiseRef = useRef<Promise<void> | null>(null);
  const statusRef = useRef(status);
  statusRef.current = status;
  const navigate = useNavigate();
  const navRef = useRef(navigate);
  navRef.current = navigate;

  const retireManagerCreation = useCallback(() => {
    if (!managerPromiseRef.current) return;
    managerEpochRef.current += 1;
    managerPromiseRef.current = null;
  }, []);

  // Manager ownership is provider-local: the real root retains one memory-only manager while every
  // test/provider render starts from an isolated cache. In-flight construction is shared.
  const loadManager = useCallback((signal: AbortSignal): Promise<UserManager> => {
    if (managerRef.current) return Promise.resolve(managerRef.current);
    if (managerPromiseRef.current) return managerPromiseRef.current;

    const epoch = managerEpochRef.current;
    const creation = (async () => {
      const response = await fetch("/api/v1/auth/config", { signal });
      if (!response.ok) throw new Error("auth configuration request failed");
      const cfg = parseAuthConfig(await response.json());
      if (epoch !== managerEpochRef.current) throw new Error("stale auth manager creation");

      const manager = new UserManager({
        authority: cfg.issuer,
        client_id: cfg.client_id,
        redirect_uri: `${window.location.origin}/`,
        post_logout_redirect_uri: `${window.location.origin}/`,
        response_type: "code",
        scope: "openid profile email",
        userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
      });
      if (epoch !== managerEpochRef.current) throw new Error("stale auth manager creation");
      managerRef.current = manager;
      return manager;
    })().finally(() => {
      if (managerPromiseRef.current === creation) managerPromiseRef.current = null;
    });
    managerPromiseRef.current = creation;
    return creation;
  }, []);

  const runBootstrap = useCallback(
    async ({ fresh }: { fresh: boolean }): Promise<void> => {
      const generation = generationRef.current + 1;
      generationRef.current = generation;
      activeAttemptRef.current?.cancel();
      retireManagerCreation();
      setStatus({ kind: "loading", operation: "bootstrap" });

      if (fresh) {
        unsubscribeRef.current?.();
        unsubscribeRef.current = null;
        managerEpochRef.current += 1;
        managerRef.current = null;
        managerPromiseRef.current = null;
      }

      const attempt = createAttemptControl();
      activeAttemptRef.current = attempt;
      const stage = { current: "configuration" as "configuration" | "callback" | "session" };

      try {
        const result = await attempt.run(
          (async () => {
            const manager = await loadManager(attempt.signal);
            if (generationRef.current !== generation) throw new AuthAttemptCancelled();

            unsubscribeRef.current?.();
            unsubscribeRef.current = manager.events.addUserLoaded((loadedUser: User) => {
              if (generationRef.current === generation && managerRef.current === manager) {
                setUser(loadedUser);
              }
            });

            const params = new URLSearchParams(window.location.search);
            const hasCallback = params.has("state") && (params.has("code") || params.has("error"));
            if (hasCallback) {
              stage.current = "callback";
              const callbackUser = await manager.signinRedirectCallback();
              return {
                user: callbackUser,
                returnTo: safeReturnTo(
                  (callbackUser.state as { returnTo?: string } | undefined)?.returnTo,
                ),
              };
            }

            stage.current = "session";
            return { user: await manager.getUser(), returnTo: null };
          })(),
        );

        if (generationRef.current !== generation) return;
        setUser(result.user);
        if (result.returnTo !== null) navRef.current(result.returnTo, { replace: true });
        setStatus({ kind: "ready" });
      } catch (error) {
        if (error instanceof AuthAttemptCancelled || generationRef.current !== generation) return;

        if (stage.current === "callback") {
          window.history.replaceState({}, "", window.location.pathname);
        }
        generationRef.current += 1;
        unsubscribeRef.current?.();
        unsubscribeRef.current = null;
        const failure: AuthFailure =
          error instanceof AuthAttemptTimedOut
            ? { kind: "timeout", recovery: "bootstrap" }
            : stage.current === "configuration"
              ? { kind: "configuration", recovery: "bootstrap" }
              : stage.current === "callback"
                ? { kind: "callback", recovery: "redirect" }
                : { kind: "session", recovery: "bootstrap" };
        logAuthFailure(stage.current, error);
        setStatus({ kind: "error", failure });
      } finally {
        if (activeAttemptRef.current === attempt) activeAttemptRef.current = null;
      }
    },
    [loadManager, retireManagerCreation],
  );

  useEffect(() => {
    void runBootstrap({ fresh: false });
    return () => {
      generationRef.current += 1;
      activeAttemptRef.current?.cancel();
      activeAttemptRef.current = null;
      retireManagerCreation();
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
    };
  }, [retireManagerCreation, runBootstrap]);

  const login = useCallback(async (): Promise<void> => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    activeAttemptRef.current?.cancel();
    retireManagerCreation();
    setStatus({ kind: "loading", operation: "redirect" });

    const attempt = createAttemptControl();
    activeAttemptRef.current = attempt;
    let stage: "configuration" | "redirect" = "configuration";

    try {
      await attempt.run(
        (async () => {
          const manager = await loadManager(attempt.signal);
          if (generationRef.current !== generation) throw new AuthAttemptCancelled();
          stage = "redirect";
          await manager.signinRedirect({
            state: { returnTo: window.location.pathname + window.location.search },
          });
        })(),
      );
      if (generationRef.current !== generation) return;
      setStatus({
        kind: "error",
        failure: { kind: "redirect", recovery: "redirect" },
      });
    } catch (error) {
      if (error instanceof AuthAttemptCancelled || generationRef.current !== generation) return;
      const failure: AuthFailure =
        error instanceof AuthAttemptTimedOut
          ? { kind: "timeout", recovery: "redirect" }
          : stage === "configuration"
            ? { kind: "configuration", recovery: "bootstrap" }
            : { kind: "redirect", recovery: "redirect" };
      logAuthFailure(stage, error);
      setStatus({ kind: "error", failure });
    } finally {
      if (activeAttemptRef.current === attempt) activeAttemptRef.current = null;
    }
  }, [loadManager, retireManagerCreation]);

  const retry = useCallback(async (): Promise<void> => {
    if (retryPromiseRef.current) return retryPromiseRef.current;
    const currentStatus = statusRef.current;
    const recovery = currentStatus.kind === "error" ? currentStatus.failure.recovery : null;
    if (!recovery) return;

    const recoveryAttempt = recovery === "bootstrap" ? runBootstrap({ fresh: true }) : login();
    const trackedAttempt = recoveryAttempt.finally(() => {
      if (retryPromiseRef.current === trackedAttempt) retryPromiseRef.current = null;
    });
    retryPromiseRef.current = trackedAttempt;
    return trackedAttempt;
  }, [login, runBootstrap]);

  const logout = useCallback(async (): Promise<void> => {
    const manager = await loadManager(new AbortController().signal);
    await manager.signoutRedirect();
    setUser(null);
  }, [loadManager]);

  const value: AuthState = {
    status,
    user,
    token: user?.access_token ?? null,
    login,
    retry,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
