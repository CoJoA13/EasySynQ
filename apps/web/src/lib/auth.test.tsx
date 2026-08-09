import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type User, UserManager } from "oidc-client-ts";
import { StrictMode } from "react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, test, vi } from "vitest";
import { AuthProvider, safeReturnTo, useAuth } from "./auth";

// Mock oidc-client-ts: one UserManager whose methods are hoisted module spies we reconfigure per test.
// (vi.mock is hoisted above const decls → the spies must come from vi.hoisted.)
const {
  signinRedirect,
  signinRedirectCallback,
  getUser,
  addUserLoaded,
  removeUserLoaded,
  emitUserLoaded,
  resetUserLoaded,
} = vi.hoisted(() => {
  let userLoadedHandler: ((user: unknown) => void) | null = null;
  const removeUserLoaded = vi.fn();
  return {
    signinRedirect: vi.fn<() => Promise<void>>(async () => undefined),
    signinRedirectCallback: vi.fn(async () => null as unknown),
    getUser: vi.fn(async () => null as unknown),
    addUserLoaded: vi.fn((handler: (user: unknown) => void) => {
      userLoadedHandler = handler;
      return () => {
        removeUserLoaded();
        if (userLoadedHandler === handler) userLoadedHandler = null;
      };
    }),
    removeUserLoaded,
    emitUserLoaded: (user: unknown) => userLoadedHandler?.(user),
    resetUserLoaded: () => {
      userLoadedHandler = null;
    },
  };
});
vi.mock("oidc-client-ts", () => ({
  // vitest 4: a vi.fn() invoked with `new` must use the `function`/`class` keyword —
  // an arrow returning an object throws "is not a constructor" (the constructor-mock change).
  UserManager: vi.fn(function (this: Record<string, unknown>) {
    this.signinRedirect = signinRedirect;
    this.signinRedirectCallback = signinRedirectCallback;
    this.getUser = getUser;
    this.events = { addUserLoaded };
    this.removeUser = vi.fn();
    this.signoutRedirect = vi.fn();
  }),
  InMemoryWebStorage: vi.fn(),
  WebStorageStateStore: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(UserManager).mockClear();
  signinRedirect.mockClear();
  signinRedirectCallback.mockReset();
  signinRedirectCallback.mockResolvedValue(null);
  getUser.mockReset();
  getUser.mockResolvedValue(null);
  addUserLoaded.mockClear();
  removeUserLoaded.mockClear();
  resetUserLoaded();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    Response.json({ issuer: "https://id.test", client_id: "web", audience: "easysynq-api" }),
  );
  window.history.pushState({}, "", "/"); // login()/callback read window.location, not the MemoryRouter
});
afterEach(() => {
  vi.useRealTimers();
  vi.mocked(globalThis.fetch).mockRestore();
  window.history.pushState({}, "", "/");
});

function Probe() {
  const { status, token } = useAuth();
  return (
    <div>
      status:{status.kind} token:{token ?? "none"}
      {status.kind === "error" ? (
        <span data-testid="failure">{JSON.stringify(status.failure)}</span>
      ) : null}
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
function ActionsProbe() {
  const { login, retry } = useAuth();
  return (
    <>
      <button type="button" onClick={() => void login()}>
        login
      </button>
      <button type="button" onClick={() => void retry()}>
        retry
      </button>
      <button
        type="button"
        onClick={() => {
          void retry();
          void retry();
        }}
      >
        retry twice
      </button>
    </>
  );
}
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function renderAuthProbe() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Probe />
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function renderAuthActions() {
  return render(
    <MemoryRouter initialEntries={[window.location.pathname + window.location.search]}>
      <AuthProvider>
        <Probe />
        <ActionsProbe />
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function readFailure() {
  return JSON.parse(screen.getByTestId("failure").textContent ?? "null") as unknown;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("AuthProvider exposes status:ready auth context to children", async () => {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());
  expect(screen.getByText(/token:none/)).toBeInTheDocument();
});

it("owns manager creation within each provider instance", async () => {
  const first = renderAuthProbe();
  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());
  first.unmount();

  renderAuthProbe();
  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());

  expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  expect(UserManager).toHaveBeenCalledTimes(2);
});

it("restarts an aborted Strict Mode bootstrap instead of reusing its manager promise", async () => {
  vi.mocked(globalThis.fetch)
    .mockImplementationOnce(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    )
    .mockImplementationOnce(async () =>
      Response.json({ issuer: "https://id.test/strict", client_id: "web", audience: "api" }),
    );

  render(
    <StrictMode>
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <Probe />
        </AuthProvider>
      </MemoryRouter>
    </StrictMode>,
  );

  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());
  expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  expect(UserManager).toHaveBeenCalledTimes(1);
  expect(getUser).toHaveBeenCalledTimes(1);
});

it.each([
  ["HTTP", new Response("", { status: 503 })],
  ["JSON", new Response("{", { status: 200 })],
  ["issuer", Response.json({ issuer: "", client_id: "web" })],
  ["client", Response.json({ issuer: "https://id.test", client_id: "" })],
])("classifies invalid %s configuration", async (_case, response) => {
  vi.mocked(globalThis.fetch).mockResolvedValue(response);
  renderAuthProbe();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  expect(readFailure()).toEqual({
    kind: "configuration",
    recovery: "bootstrap",
  });
});

it("classifies a configuration network failure", async () => {
  vi.mocked(globalThis.fetch).mockRejectedValue(new Error("network secret"));
  renderAuthProbe();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  expect(readFailure()).toEqual({ kind: "configuration", recovery: "bootstrap" });
  expect(document.body).not.toHaveTextContent("network secret");
});

it("classifies a UserManager constructor failure without exposing details", async () => {
  vi.mocked(UserManager).mockImplementationOnce(function () {
    throw new Error("constructor secret");
  });
  renderAuthProbe();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  expect(readFailure()).toEqual({ kind: "configuration", recovery: "bootstrap" });
  expect(document.body).not.toHaveTextContent("constructor secret");
});

it("updates the in-place bearer token when oidc-client-ts emits a renewed User", async () => {
  getUser.mockResolvedValue({ access_token: "initial-token" });
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/token:initial-token/)).toBeInTheDocument());
  expect(addUserLoaded).toHaveBeenCalledTimes(1);

  act(() => emitUserLoaded({ access_token: "renewed-token" }));

  await waitFor(() => expect(screen.getByText(/token:renewed-token/)).toBeInTheDocument());
  expect(signinRedirect).not.toHaveBeenCalled();
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

it("classifies a redirect rejection without exposing raw details", async () => {
  renderAuthActions();
  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());
  signinRedirect.mockRejectedValueOnce(new Error("https://id.test/realm?secret=value"));

  await userEvent.click(screen.getByRole("button", { name: "login" }));
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());

  expect(readFailure()).toEqual({ kind: "redirect", recovery: "redirect" });
  expect(document.body).not.toHaveTextContent("secret=value");
});

it("times out redirect startup at the exact deadline and ignores a late result", async () => {
  vi.useFakeTimers();
  const redirect = deferred<void>();
  signinRedirect.mockReturnValueOnce(redirect.promise);
  renderAuthActions();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText(/status:ready/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "login" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(readFailure()).toEqual({ kind: "timeout", recovery: "redirect" });

  redirect.resolve();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(readFailure()).toEqual({ kind: "timeout", recovery: "redirect" });
});

it("retries configuration bootstrap with a rebuilt manager", async () => {
  vi.mocked(globalThis.fetch)
    .mockResolvedValueOnce(new Response("", { status: 503 }))
    .mockImplementation(async () =>
      Response.json({ issuer: "https://id.test/new", client_id: "web", audience: "api" }),
    );
  renderAuthActions();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());

  await userEvent.click(screen.getByRole("button", { name: "retry" }));
  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());

  expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  expect(UserManager).toHaveBeenCalledTimes(1);
  expect(UserManager).toHaveBeenLastCalledWith(
    expect.objectContaining({ authority: "https://id.test/new" }),
  );
});

it("retries a stored-session failure with a rebuilt manager", async () => {
  getUser.mockRejectedValueOnce(new Error("stored-user secret")).mockResolvedValueOnce(null);
  renderAuthActions();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  expect(UserManager).toHaveBeenCalledTimes(1);

  await userEvent.click(screen.getByRole("button", { name: "retry" }));
  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());

  expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  expect(UserManager).toHaveBeenCalledTimes(2);
});

it("redirect recovery waits for an explicit retry and never replays callback parameters", async () => {
  window.history.pushState({}, "", "/settings/notifications?code=abc&state=secret");
  signinRedirectCallback.mockRejectedValueOnce(new Error("bad callback"));
  renderAuthActions();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());

  expect(signinRedirect).not.toHaveBeenCalled();
  expect(window.location.search).toBe("");
  await userEvent.click(screen.getByRole("button", { name: "retry" }));
  await waitFor(() => expect(signinRedirect).toHaveBeenCalledTimes(1));
  expect(signinRedirect).toHaveBeenCalledWith({
    state: { returnTo: "/settings/notifications" },
  });
});

it("treats an OIDC error callback as callback recovery and strips it before retry", async () => {
  window.history.pushState(
    {},
    "",
    "/settings/notifications?error=access_denied&state=callback-secret",
  );
  signinRedirectCallback.mockRejectedValueOnce(new Error("access_denied state=callback-secret"));
  render(
    <MemoryRouter initialEntries={["/settings/notifications"]}>
      <AuthProvider>
        <Probe />
        <ActionsProbe />
      </AuthProvider>
    </MemoryRouter>,
  );

  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  expect(readFailure()).toEqual({ kind: "callback", recovery: "redirect" });
  expect(getUser).not.toHaveBeenCalled();
  expect(window.location.search).toBe("");
  expect(document.body).not.toHaveTextContent("access_denied");
  expect(document.body).not.toHaveTextContent("callback-secret");
  expect(signinRedirect).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: "retry" }));
  await waitFor(() => expect(signinRedirect).toHaveBeenCalledTimes(1));
  expect(signinRedirect).toHaveBeenCalledWith({
    state: { returnTo: "/settings/notifications" },
  });
});

it("keeps retry single-flight when recovery is activated twice synchronously", async () => {
  const retryConfig = deferred<Response>();
  vi.mocked(globalThis.fetch)
    .mockResolvedValueOnce(new Response("", { status: 503 }))
    .mockReturnValueOnce(retryConfig.promise);
  renderAuthActions();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());

  fireEvent.click(screen.getByRole("button", { name: "retry twice" }));
  expect(globalThis.fetch).toHaveBeenCalledTimes(2);

  retryConfig.resolve(
    Response.json({ issuer: "https://id.test/retry", client_id: "web", audience: "api" }),
  );
  await waitFor(() => expect(screen.getByText(/status:ready/)).toBeInTheDocument());
  expect(UserManager).toHaveBeenCalledTimes(1);
});

it("does not let a timed-out manager creation replace a successful retry manager", async () => {
  vi.useFakeTimers();
  const originalConfig = deferred<Response>();
  vi.mocked(globalThis.fetch)
    .mockReturnValueOnce(originalConfig.promise)
    .mockImplementation(async () =>
      Response.json({ issuer: "https://id.test/current", client_id: "web", audience: "api" }),
    );
  renderAuthActions();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(readFailure()).toEqual({ kind: "timeout", recovery: "bootstrap" });

  fireEvent.click(screen.getByRole("button", { name: "retry" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText(/status:ready/)).toBeInTheDocument();
  expect(UserManager).toHaveBeenCalledTimes(1);

  originalConfig.resolve(
    Response.json({ issuer: "https://id.test/stale", client_id: "old-web", audience: "api" }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  fireEvent.click(screen.getByRole("button", { name: "login" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  expect(UserManager).toHaveBeenCalledTimes(1);
  expect(UserManager).toHaveBeenLastCalledWith(
    expect.objectContaining({ authority: "https://id.test/current", client_id: "web" }),
  );
  expect(signinRedirect).toHaveBeenCalledTimes(1);
});

it("a redirect superseding manager creation retires the aborted promise", async () => {
  vi.mocked(globalThis.fetch)
    .mockImplementationOnce(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        }),
    )
    .mockImplementationOnce(async () =>
      Response.json({ issuer: "https://id.test/redirect", client_id: "web", audience: "api" }),
    );
  renderAuthActions();
  fireEvent.click(screen.getByRole("button", { name: "login" }));
  await waitFor(() => expect(signinRedirect).toHaveBeenCalledTimes(1));

  expect(globalThis.fetch).toHaveBeenCalledTimes(2);
  expect(UserManager).toHaveBeenCalledTimes(1);
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

it("a failed callback strips the query and exposes safe callback recovery", async () => {
  window.history.pushState({}, "", "/?code=abc&state=xyz");
  signinRedirectCallback.mockRejectedValue(new Error("bad callback"));
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <Probe />
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  expect(readFailure()).toEqual({ kind: "callback", recovery: "redirect" });
  expect(screen.getByText(/token:none/)).toBeInTheDocument();
  expect(window.location.search).toBe("");
  expect(screen.getByTestId("loc")).toHaveTextContent("/");
  expect(screen.getByTestId("loc")).not.toHaveTextContent("code");
});

it("classifies a stored-user rejection without exposing raw details", async () => {
  getUser.mockRejectedValueOnce(new Error("stored-user secret"));
  renderAuthProbe();
  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  expect(readFailure()).toEqual({ kind: "session", recovery: "bootstrap" });
  expect(document.body).not.toHaveTextContent("stored-user secret");
});

it("times out stored-session loading at the exact deadline and ignores a late user", async () => {
  vi.useFakeTimers();
  const stored = deferred<User | null>();
  getUser.mockReturnValueOnce(stored.promise);
  renderAuthProbe();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(getUser).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(readFailure()).toEqual({ kind: "timeout", recovery: "bootstrap" });

  stored.resolve({ access_token: "late-token" } as User);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText(/token:none/)).toBeInTheDocument();
  expect(readFailure()).toEqual({ kind: "timeout", recovery: "bootstrap" });
});

it("ignores a userLoaded event emitted after bootstrap has timed out", async () => {
  vi.useFakeTimers();
  const stored = deferred<User | null>();
  getUser.mockReturnValueOnce(stored.promise);
  renderAuthProbe();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(addUserLoaded).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000);
  });
  expect(readFailure()).toEqual({ kind: "timeout", recovery: "bootstrap" });

  act(() => emitUserLoaded({ access_token: "late-event-token" }));

  expect(screen.getByText(/token:none/)).toBeInTheDocument();
  expect(readFailure()).toEqual({ kind: "timeout", recovery: "bootstrap" });
});

it("cancels bootstrap cleanup on unmount and ignores an OIDC promise that settles late", async () => {
  vi.useFakeTimers();
  const stored = deferred<User | null>();
  getUser.mockReturnValueOnce(stored.promise);
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const view = renderAuthProbe();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(addUserLoaded).toHaveBeenCalledTimes(1);

  view.unmount();
  stored.resolve({ access_token: "late-token" } as User);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  expect(removeUserLoaded).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
  expect(consoleError).not.toHaveBeenCalled();
  consoleError.mockRestore();
});

it("logs only a sanitized callback-stage diagnostic", async () => {
  window.history.pushState({}, "", "/?code=abc&state=secret");
  const callbackError = new Error("https://id.test/realm?code=abc&state=secret");
  callbackError.name = "custom-sensitive-name";
  signinRedirectCallback.mockRejectedValueOnce(callbackError);
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  renderAuthProbe();

  await waitFor(() => expect(screen.getByText(/status:error/)).toBeInTheDocument());
  const diagnostic = JSON.stringify(consoleError.mock.calls);
  expect(diagnostic).toContain("callback");
  expect(diagnostic).not.toContain("https://id.test");
  expect(diagnostic).not.toContain("abc");
  expect(diagnostic).not.toContain("secret");
  expect(diagnostic).not.toContain("custom-sensitive-name");
  consoleError.mockRestore();
});

it("the bootstrap effect runs once — an in-app navigation does not re-fetch the user", async () => {
  function NavButton() {
    const n = useNavigate();
    return (
      <button type="button" onClick={() => n("/other")}>
        go
      </button>
    );
  }
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <NavButton />
        <LocationProbe />
      </AuthProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(getUser).toHaveBeenCalledTimes(1));
  await userEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/other"));
  // flush any pending microtasks so a spurious re-run would have resolved by now
  await Promise.resolve();
  expect(getUser).toHaveBeenCalledTimes(1); // effect did NOT re-run on navigation
});
