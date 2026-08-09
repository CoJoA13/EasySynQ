import { QueryClient } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { App, LegacyImportRedirect } from "./App";
import { AuthContext, type AuthState } from "./lib/auth";
import { renderWithProviders, TEST_AUTH } from "./test/render";

afterEach(() => sessionStorage.removeItem("es_auth_redirect"));

function AppWithAuth({ auth }: { auth: AuthState }) {
  return (
    <AuthContext.Provider value={auth}>
      <App />
    </AuthContext.Provider>
  );
}

test("auth loading renders the named startup boundary without shell or setup", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithProviders(
    <AppWithAuth
      auth={{
        ...TEST_AUTH,
        status: { kind: "loading", operation: "bootstrap" },
        user: null,
        token: null,
      }}
    />,
    { route: "/library", queryClient },
  );

  expect(screen.getByRole("status", { name: "Connecting to sign-in" })).toBeInTheDocument();
  expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
  await waitFor(() => expect(queryClient.getQueryState(["setup-state"])?.status).toBe("success"));
});

test("auth error renders recovery without shell or setup", async () => {
  const retry = vi.fn(async () => undefined);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithProviders(
    <AppWithAuth
      auth={{
        ...TEST_AUTH,
        status: {
          kind: "error",
          failure: { kind: "callback", recovery: "redirect" },
        },
        user: null,
        token: null,
        retry,
      }}
    />,
    { route: "/library", queryClient },
  );

  expect(screen.getByRole("heading", { name: "Sign-in was not completed" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try sign-in again" })).toBeInTheDocument();
  expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
  await waitFor(() => expect(queryClient.getQueryState(["setup-state"])?.status).toBe("success"));
});

test("operational app renders the shell + Library at /library", async () => {
  renderWithProviders(<App />, { route: "/library" });
  await waitFor(() => expect(screen.getByText("Document Library")).toBeInTheDocument());
  expect(screen.getAllByRole("link", { name: "Home" }).length).toBeGreaterThan(0); // shell rail
});

test("the /search route renders the results page", async () => {
  renderWithProviders(<App />, { route: "/search?q=supplier" });
  expect(await screen.findByRole("heading", { name: "Search" })).toBeInTheDocument();
});

test("the /compliance route renders the checklist", async () => {
  renderWithProviders(<App />, { route: "/compliance" });
  expect(await screen.findByRole("heading", { name: "Compliance Checklist" })).toBeInTheDocument();
});

test("operational app with no token bounces to sign-in (in-memory tokens, post-reload)", async () => {
  sessionStorage.removeItem("es_auth_redirect");
  const login = vi.fn(async () => undefined);
  renderWithProviders(<App />, {
    route: "/library",
    auth: {
      status: { kind: "ready" },
      user: null,
      token: null,
      login,
      retry: async () => undefined,
      logout: async () => undefined,
    },
  });
  await waitFor(() => expect(login).toHaveBeenCalledTimes(1)); // auto-redirect to Keycloak
  expect(screen.getByRole("status", { name: "Connecting to sign-in" })).toBeInTheDocument();
  expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
});

test("one automatic redirect does not loop after error and explicit retry resets the latch", async () => {
  const user = userEvent.setup();
  const login = vi.fn(async () => undefined);
  const retry = vi.fn(async () => undefined);
  const readyAuth: AuthState = {
    ...TEST_AUTH,
    status: { kind: "ready" },
    user: null,
    token: null,
    login,
    retry,
  };
  const errorAuth: AuthState = {
    ...readyAuth,
    status: {
      kind: "error",
      failure: { kind: "redirect", recovery: "redirect" },
    },
  };
  const rendered = renderWithProviders(<AppWithAuth auth={readyAuth} />, { route: "/library" });

  await waitFor(() => expect(login).toHaveBeenCalledTimes(1));
  expect(sessionStorage.getItem("es_auth_redirect")).toBe("1");

  rendered.rerender(<AppWithAuth auth={errorAuth} />);
  expect(
    await screen.findByRole("heading", { name: "Sign-in could not be opened" }),
  ).toBeInTheDocument();
  expect(login).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("button", { name: "Try sign-in again" }));
  expect(sessionStorage.getItem("es_auth_redirect")).toBeNull();
  expect(retry).toHaveBeenCalledTimes(1);

  rendered.rerender(<AppWithAuth auth={errorAuth} />);
  await act(async () => undefined);
  expect(login).toHaveBeenCalledTimes(1);
});

test("redirect loop latch suppresses an automatic tokenless login", async () => {
  sessionStorage.setItem("es_auth_redirect", "1");
  const login = vi.fn(async () => undefined);
  renderWithProviders(
    <AppWithAuth
      auth={{
        ...TEST_AUTH,
        status: { kind: "ready" },
        user: null,
        token: null,
        login,
      }}
    />,
    { route: "/library" },
  );

  expect(await screen.findByText("Connecting to sign-in…")).toBeInTheDocument();
  await act(async () => undefined);
  expect(login).not.toHaveBeenCalled();
  expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
});

test("the /imports route renders the runs landing", async () => {
  renderWithProviders(<App />, { route: "/imports" });
  expect(await screen.findByRole("heading", { name: "Import" })).toBeInTheDocument();
});

test("the /imports/:runId route renders the run page cockpit", async () => {
  renderWithProviders(<App />, {
    route: "/imports/10000000-0000-0000-0000-000000000001",
  });
  // the Proposed run fixture rests at the review cockpit (IngestionRunPage → ReviewCockpit)
  expect(await screen.findByRole("region", { name: "Review cockpit" })).toBeInTheDocument();
});

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <div data-testid="location">{pathname + search}</div>;
}

test("legacy ingestion bookmarks redirect to imports and preserve the query", async () => {
  const runId = "10000000-0000-0000-0000-000000000001";
  renderWithProviders(
    <Routes>
      <Route path="/ingestion/:runId" element={<LegacyImportRedirect />} />
      <Route path="/imports/:runId" element={<LocationProbe />} />
    </Routes>,
    { route: `/ingestion/${runId}?queue=high` },
  );

  expect(await screen.findByTestId("location")).toHaveTextContent(`/imports/${runId}?queue=high`);
});
