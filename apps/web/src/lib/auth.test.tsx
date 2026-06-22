import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, test, vi } from "vitest";
import { AuthProvider, safeReturnTo, useAuth } from "./auth";

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
