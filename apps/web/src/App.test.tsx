import { QueryClient } from "@tanstack/react-query";
import { QueryObserver } from "@tanstack/query-core";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { App, LegacyImportRedirect } from "./App";
import { AuthContext, type AuthState } from "./lib/auth";
import { server } from "./test/msw/server";
import { renderWithProviders, TEST_AUTH } from "./test/render";

const routeCrash = vi.hoisted(() => ({ library: false }));

vi.mock("./features/library/LibraryPage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./features/library/LibraryPage")>();
  return {
    ...actual,
    LibraryPage: function TestableLibraryPage() {
      if (routeCrash.library) throw new Error("known route render failure");
      return <actual.LibraryPage />;
    },
  };
});

afterEach(() => {
  routeCrash.library = false;
  sessionStorage.removeItem("es_auth_redirect");
  vi.restoreAllMocks();
});

function AppWithAuth({ auth }: { auth: AuthState }) {
  return (
    <AuthContext.Provider value={auth}>
      <App />
    </AuthContext.Provider>
  );
}

const SETUP_MUTATION_PATHS = [
  ["post", "/api/v1/setup/bootstrap"],
  ["patch", "/api/v1/setup/org-profile"],
  ["post", "/api/v1/setup/verify-storage"],
  ["post", "/api/v1/setup/configure-backup"],
  ["post", "/api/v1/setup/run-restore-test"],
  ["post", "/api/v1/setup/configure-auth"],
  ["post", "/api/v1/setup/finalize"],
] as const;

function watchSetupMutations() {
  const setupMutation = vi.fn();
  server.use(
    ...SETUP_MUTATION_PATHS.map(([method, path]) =>
      http[method](path, () => {
        setupMutation();
        return HttpResponse.json({});
      }),
    ),
  );
  return setupMutation;
}

function noTokenAuth(login = vi.fn(async () => undefined)): AuthState {
  return {
    ...TEST_AUTH,
    status: { kind: "ready" },
    user: null,
    token: null,
    login,
  };
}

const FINALIZATION_READY_DETAIL = {
  setup_state: "IN_SETUP",
  gates: {
    "G-A": true,
    "G-E": true,
    "G-B": true,
    "G-C": true,
    "G-D": true,
  },
  org_profile: {
    legal_name: "Example Quality Organization",
    short_code: "EXAMPLE",
    timezone: "America/Chicago",
  },
  backup: {
    configured: true,
    destination: "/var/lib/easysynq/backups",
    last_restore_test_at: "2026-08-09T12:00:00Z",
    last_restore_test_result: "PASS",
  },
  auth: {
    configured: true,
    method: "LOCAL",
    last_test_at: "2026-08-09T12:00:00Z",
  },
  tamper_evident: false,
};

test("finalization verification recovers from a failed state read without replaying finalize", async () => {
  const user = userEvent.setup();
  let stateReads = 0;
  let finalizeCalls = 0;
  let finishVerificationRead: ((response: Response) => void) | undefined;
  server.use(
    http.get("/api/v1/setup/state", () => {
      stateReads += 1;
      if (stateReads === 1) return HttpResponse.json({ setup_state: "IN_SETUP" });
      if (stateReads === 2) {
        return new Promise<Response>((resolve) => {
          finishVerificationRead = resolve;
        });
      }
      return HttpResponse.json({ setup_state: "OPERATIONAL" });
    }),
    http.get("/api/v1/setup", () => HttpResponse.json(FINALIZATION_READY_DETAIL)),
    http.post("/api/v1/setup/finalize", () => {
      finalizeCalls += 1;
      return HttpResponse.json({});
    }),
  );

  renderWithProviders(<App />, { route: "/setup" });

  await user.click(await screen.findByRole("button", { name: "Finalize setup" }));

  expect(await screen.findByRole("status", { name: "Verifying setup" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Finalize setup" })).not.toBeInTheDocument();
  expect(finalizeCalls).toBe(1);

  await act(async () => {
    finishVerificationRead?.(HttpResponse.json({ detail: "unavailable" }, { status: 503 }));
  });

  expect(
    await screen.findByRole("heading", { name: "Setup was saved, but could not be verified" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Finalize setup" })).not.toBeInTheDocument();
  expect(finalizeCalls).toBe(1);

  await user.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByRole("heading", { name: "QMS health" })).toBeInTheDocument();
  expect(stateReads).toBe(3);
  expect(finalizeCalls).toBe(1);
});

test("contradictory IN_SETUP after finalization keeps the wizard hidden", async () => {
  const user = userEvent.setup();
  let stateReads = 0;
  let finalizeCalls = 0;
  server.use(
    http.get("/api/v1/setup/state", () => {
      stateReads += 1;
      return HttpResponse.json({ setup_state: "IN_SETUP" });
    }),
    http.get("/api/v1/setup", () => HttpResponse.json(FINALIZATION_READY_DETAIL)),
    http.post("/api/v1/setup/finalize", () => {
      finalizeCalls += 1;
      return HttpResponse.json({});
    }),
  );

  renderWithProviders(<App />, { route: "/setup" });

  await user.click(await screen.findByRole("button", { name: "Finalize setup" }));

  expect(
    await screen.findByRole("heading", { name: "Setup was saved, but could not be verified" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Finalize setup" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  expect(stateReads).toBe(2);
  expect(finalizeCalls).toBe(1);
});

test("finalization verification recovers when the state refetch rejects", async () => {
  const user = userEvent.setup();
  let rejectVerification: ((reason?: unknown) => void) | undefined;
  server.use(
    http.get("/api/v1/setup/state", () => HttpResponse.json({ setup_state: "IN_SETUP" })),
    http.get("/api/v1/setup", () => HttpResponse.json(FINALIZATION_READY_DETAIL)),
    http.post("/api/v1/setup/finalize", () => HttpResponse.json({})),
  );
  vi.spyOn(QueryObserver.prototype, "refetch").mockImplementationOnce(
    () =>
      new Promise<never>((_resolve, reject) => {
        rejectVerification = reject;
      }),
  );

  renderWithProviders(<App />, { route: "/setup" });

  await user.click(await screen.findByRole("button", { name: "Finalize setup" }));

  expect(await screen.findByRole("status", { name: "Verifying setup" })).toBeInTheDocument();
  await act(async () => {
    rejectVerification?.(new Error("state verification rejected"));
  });
  expect(
    await screen.findByRole("heading", { name: "Setup was saved, but could not be verified" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Finalize setup" })).not.toBeInTheDocument();
});

test.each(["/setup", "/library"])(
  "setup state 503 at %s fails closed without mounting setup or the shell",
  async (route) => {
    const login = vi.fn(async () => undefined);
    const setupMutation = watchSetupMutations();
    server.use(
      http.get("/api/v1/setup/state", () =>
        HttpResponse.json({ detail: "unsafe database host" }, { status: 503 }),
      ),
    );

    renderWithProviders(<App />, { route, auth: noTokenAuth(login) });

    expect(
      await screen.findByRole("heading", { name: "Setup status is unavailable" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
    expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
    expect(setupMutation).not.toHaveBeenCalled();
    expect(document.body).not.toHaveTextContent("unsafe database host");
  },
);

test.each(["/setup", "/library"])(
  "setup state network failure at %s fails closed without mounting setup or the shell",
  async (route) => {
    const login = vi.fn(async () => undefined);
    const setupMutation = watchSetupMutations();
    server.use(http.get("/api/v1/setup/state", () => HttpResponse.error()));

    renderWithProviders(<App />, { route, auth: noTokenAuth(login) });

    expect(
      await screen.findByRole("heading", { name: "Setup status is unavailable" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
    expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
    expect(setupMutation).not.toHaveBeenCalled();
  },
);

test.each([
  [
    "invalid JSON",
    new HttpResponse("not JSON", { status: 200, headers: { "Content-Type": "application/json" } }),
  ],
  ["missing setup state", HttpResponse.json({})],
  ["null setup state", HttpResponse.json({ setup_state: null })],
  ["unknown setup state", HttpResponse.json({ setup_state: "MYSTERY" })],
] as const)("setup state %s fails closed", async (_label, response) => {
  server.use(http.get("/api/v1/setup/state", () => response));

  renderWithProviders(<App />, { route: "/setup" });

  expect(
    await screen.findByRole("heading", { name: "Setup status is unavailable" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Welcome to EasySynQ" })).not.toBeInTheDocument();
  expect(screen.queryByText("Document Library")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Home" })).not.toBeInTheDocument();
});

test("OPERATIONAL with a token renders the Document Library", async () => {
  server.use(
    http.get("/api/v1/setup/state", () => HttpResponse.json({ setup_state: "OPERATIONAL" })),
  );

  renderWithProviders(<App />, { route: "/library" });

  expect(await screen.findByText("Document Library")).toBeInTheDocument();
});

test("open-and-mark navigation owns focus while a late failure persists and retries its id", async () => {
  const user = userEvent.setup();
  const requestedIds: string[] = [];
  let finishFirstRequest: ((response: Response) => void) | undefined;
  server.use(
    http.get("/api/v1/notifications", () =>
      HttpResponse.json([
        {
          id: "n1",
          event_key: "task.assigned",
          subject_type: "DOCUMENT",
          subject_id: "d1",
          title: "Review requested: SOP-001",
          body: "You have a review task.",
          deep_link: "http://localhost/library",
          created_at: "2026-06-22T09:00:00Z",
          read_at: null,
        },
      ]),
    ),
    http.post("/api/v1/notifications/:id/read", ({ params }) => {
      requestedIds.push(String(params.id));
      if (requestedIds.length === 1) {
        return new Promise<Response>((resolve) => {
          finishFirstRequest = resolve;
        });
      }
      return HttpResponse.json({ status: "ok" });
    }),
  );

  renderWithProviders(<App />, { route: "/notifications" });
  await user.click(await screen.findByRole("link", { name: /Review requested: SOP-001/ }));

  expect(await screen.findByText("Document Library")).toBeInTheDocument();
  await waitFor(() => expect(requestedIds).toEqual(["n1"]));
  const main = screen.getByRole("main");
  await waitFor(() => expect(main).toHaveFocus());

  await act(async () => {
    finishFirstRequest?.(HttpResponse.json({ detail: "temporarily unavailable" }, { status: 503 }));
  });

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("This notification remains unread: Review requested: SOP-001");
  expect(main).toHaveFocus();

  await user.click(
    screen.getByRole("button", {
      name: "Try marking Review requested: SOP-001 read again",
    }),
  );
  await waitFor(() => expect(requestedIds).toEqual(["n1", "n1"]));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  const mutationStatus = screen
    .getAllByRole("status")
    .find((status) => status.textContent === "Notification marked read");
  expect(mutationStatus).toBeDefined();
  expect(mutationStatus).toHaveTextContent("Notification marked read");
  expect(mutationStatus).toHaveAttribute("aria-live", "polite");
});

test("OPERATIONAL without a token preserves the sign-in redirect latch", async () => {
  const login = vi.fn(async () => undefined);
  server.use(
    http.get("/api/v1/setup/state", () => HttpResponse.json({ setup_state: "OPERATIONAL" })),
  );

  renderWithProviders(<App />, { route: "/library", auth: noTokenAuth(login) });

  await waitFor(() => expect(login).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("status", { name: "Connecting to sign-in" })).toBeInTheDocument();
  expect(sessionStorage.getItem("es_auth_redirect")).toBe("1");
});

test.each(["UNINITIALIZED", "IN_SETUP"] as const)(
  "%s without a token redirects to the existing setup wizard",
  async (setup_state) => {
    const login = vi.fn(async () => undefined);
    server.use(http.get("/api/v1/setup/state", () => HttpResponse.json({ setup_state })));

    renderWithProviders(<App />, { route: "/library", auth: noTokenAuth(login) });

    expect(await screen.findByRole("heading", { name: "Welcome to EasySynQ" })).toBeInTheDocument();
    expect(login).not.toHaveBeenCalled();
  },
);

test("setup state retry performs one additional read and recovers to the shell", async () => {
  const user = userEvent.setup();
  let reads = 0;
  server.use(
    http.get("/api/v1/setup/state", () => {
      reads += 1;
      return reads === 1
        ? HttpResponse.json({ detail: "unavailable" }, { status: 503 })
        : HttpResponse.json({ setup_state: "OPERATIONAL" });
    }),
  );

  renderWithProviders(<App />, { route: "/library" });

  expect(
    await screen.findByRole("heading", { name: "Setup status is unavailable" }),
  ).toBeInTheDocument();
  expect(reads).toBe(1);

  await user.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByText("Document Library")).toBeInTheDocument();
  expect(reads).toBe(2);
});

test("rapid setup state retry activation starts one additional read", async () => {
  const user = userEvent.setup();
  let reads = 0;
  let resolveSecond: ((response: Response) => void) | undefined;
  server.use(
    http.get("/api/v1/setup/state", () => {
      reads += 1;
      if (reads === 1) return HttpResponse.json({ detail: "unavailable" }, { status: 503 });
      return new Promise<Response>((resolve) => {
        resolveSecond = resolve;
      });
    }),
  );

  renderWithProviders(<App />, { route: "/library" });

  const retry = await screen.findByRole("button", { name: "Try again" });
  await user.click(retry);
  await waitFor(() => expect(reads).toBe(2));
  expect(retry).toBeDisabled();
  await user.click(retry);
  expect(reads).toBe(2);

  resolveSecond?.(HttpResponse.json({ setup_state: "OPERATIONAL" }));
  expect(await screen.findByText("Document Library")).toBeInTheDocument();
});

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

test("a stale redirect latch requires explicit recovery before another login", async () => {
  const user = userEvent.setup();
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

  expect(
    await screen.findByRole("heading", { name: "Sign-in could not be opened" }),
  ).toBeInTheDocument();
  await act(async () => undefined);
  expect(login).not.toHaveBeenCalled();
  expect(screen.queryByText("Document Library")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Try sign-in again" }));

  expect(sessionStorage.getItem("es_auth_redirect")).toBeNull();
  expect(login).toHaveBeenCalledTimes(1);
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

function NavigateToUnknownRoute() {
  const navigate = useNavigate();
  return <button onClick={() => navigate("/missing/private-segment")}>Open missing route</button>;
}

function NavigateToKnownRoute({ path, children }: { path: string; children: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(path)}>{children}</button>;
}

function TaskMaterialViewNavigation() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate("/tasks?type=DOC_ACK")}>Open acknowledgements</button>
      <button onClick={() => navigate(-1)}>Back to tasks</button>
    </>
  );
}

test("live Tasks material-view navigation updates chrome once and preserves operational providers", async () => {
  const user = userEvent.setup();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const cacheKey = ["task-material-view", "persistent-cache-value"] as const;
  const cacheValue = { marker: "persists-through-task-view-history" };
  let markReadRequests = 0;

  queryClient.setQueryData(cacheKey, cacheValue);
  server.use(
    http.get("/api/v1/notifications", () =>
      HttpResponse.json([
        {
          id: "notification-persist-1111-1111-1111-111111111111",
          event_key: "task.assigned",
          subject_type: "DOCUMENT",
          subject_id: "document-persist-1111-1111-1111-111111111111",
          title: "Persistent task feedback",
          body: "This notification proves the feedback outlet survives navigation.",
          deep_link: "http://localhost/tasks",
          created_at: "2026-08-11T12:00:00Z",
          read_at: null,
        },
      ]),
    ),
    http.post("/api/v1/notifications/:id/read", () => {
      markReadRequests += 1;
      return HttpResponse.json({ detail: "unavailable" }, { status: 503 });
    }),
  );

  const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");
  renderWithProviders(
    <>
      <App />
      <TaskMaterialViewNavigation />
    </>,
    { route: "/tasks", queryClient },
  );

  await screen.findByRole("heading", { name: "Review and approve" });
  await user.click(screen.getByRole("button", { name: /Notifications/ }));
  await user.click(await screen.findByRole("link", { name: /Persistent task feedback/ }));
  expect(
    await screen.findByText("This notification remains unread: Persistent task feedback"),
  ).toBeInTheDocument();
  expect(queryClient.getQueryData(cacheKey)).toBe(cacheValue);
  expect(markReadRequests).toBe(1);

  await user.click(screen.getByRole("button", { name: "Open acknowledgements" }));
  expect(await screen.findByRole("heading", { name: "Acknowledgements" })).toBeInTheDocument();
  await waitFor(() => expect(document.title).toBe("EasySynQ — Acknowledgements"));
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent(
    "Acknowledgements",
  );
  expect(
    focusSpy.mock.contexts.filter((context) => context === screen.getByRole("main")),
  ).toHaveLength(1);
  expect(queryClient.getQueryData(cacheKey)).toBe(cacheValue);
  expect(
    screen.getByText("This notification remains unread: Persistent task feedback"),
  ).toBeInTheDocument();
  expect(markReadRequests).toBe(1);

  await user.click(screen.getByRole("button", { name: "Back to tasks" }));
  expect(await screen.findByRole("heading", { name: "Review and approve" })).toBeInTheDocument();
  await waitFor(() => expect(document.title).toBe("EasySynQ — Tasks"));
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("Tasks");
  expect(
    focusSpy.mock.contexts.filter((context) => context === screen.getByRole("main")),
  ).toHaveLength(2);
  expect(queryClient.getQueryData(cacheKey)).toBe(cacheValue);
  expect(
    screen.getByText("This notification remains unread: Persistent task feedback"),
  ).toBeInTheDocument();
  expect(markReadRequests).toBe(1);
});

test("an acknowledgement deep link sets content and title without route-navigation focus or announcement", async () => {
  const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");
  renderWithProviders(<App />, { route: "/tasks?type=DOC_ACK" });

  expect(await screen.findByRole("heading", { name: "Acknowledgements" })).toBeInTheDocument();
  expect(document.title).toBe("EasySynQ — Acknowledgements");
  expect(screen.getByRole("main")).not.toHaveFocus();
  expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  expect(
    focusSpy.mock.contexts.filter((context) => context === screen.getByRole("main")),
  ).toHaveLength(0);
});

test("an unknown operational URL remains visible and renders a safe shell-contained 404", async () => {
  renderWithProviders(
    <>
      <App />
      <LocationProbe />
    </>,
    { route: "/missing/private-segment?view=private-segment" },
  );

  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/missing/private-segment?view=private-segment",
  );
  expect(screen.getByRole("main")).not.toHaveTextContent("private-segment");
  expect(screen.getByRole("main")).not.toHaveTextContent("view=private-segment");
  expect(document.title).toBe("EasySynQ — Page not found");
});

test("client-side navigation to an unknown route leaves focus on the 404 heading", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <App />
      <NavigateToUnknownRoute />
    </>,
    { route: "/library" },
  );

  await screen.findByText("Document Library");
  await user.click(screen.getByRole("button", { name: "Open missing route" }));
  const heading = await screen.findByRole("heading", { name: "Page not found" });
  await waitFor(() => expect(heading).toHaveFocus());
});

test("client-side navigation to a crashing known route leaves recovery chrome in control", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <App />
      <NavigateToKnownRoute path="/library">Open library</NavigateToKnownRoute>
      <NavigateToKnownRoute path="/">Open dashboard</NavigateToKnownRoute>
    </>,
    { route: "/" },
  );

  await screen.findByRole("heading", { name: "QMS health" });
  routeCrash.library = true;
  await user.click(screen.getByRole("button", { name: "Open library" }));

  const recoveryHeading = await screen.findByRole("heading", {
    name: "This page couldn't be displayed",
  });
  await waitFor(() => expect(recoveryHeading).toHaveFocus());
  expect(document.title).toBe("EasySynQ — Page unavailable");

  routeCrash.library = false;
  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(await screen.findByText("Document Library")).toBeInTheDocument();
  expect(document.title).toBe("EasySynQ — Library");

  await user.click(screen.getByRole("button", { name: "Open dashboard" }));
  await screen.findByRole("heading", { name: "QMS health" });
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  expect(document.title).toBe("EasySynQ — Dashboard");

  routeCrash.library = true;
  await user.click(screen.getByRole("button", { name: "Open library" }));
  const repeatedRecoveryHeading = await screen.findByRole("heading", {
    name: "This page couldn't be displayed",
  });
  await waitFor(() => expect(repeatedRecoveryHeading).toHaveFocus());
  expect(document.title).toBe("EasySynQ — Page unavailable");

  await user.click(screen.getByRole("link", { name: "Go to dashboard" }));
  await screen.findByRole("heading", { name: "QMS health" });
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  expect(document.title).toBe("EasySynQ — Dashboard");
});

test.each(["UNINITIALIZED", "IN_SETUP"] as const)(
  "unknown %s routes retain the setup authorization boundary",
  async (setup_state) => {
    server.use(http.get("/api/v1/setup/state", () => HttpResponse.json({ setup_state })));
    renderWithProviders(<App />, {
      route: "/missing/private-segment",
      auth: noTokenAuth(),
    });
    expect(await screen.findByRole("heading", { name: "Welcome to EasySynQ" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Page not found" })).not.toBeInTheDocument();
  },
);

test.each([
  ["Go to dashboard", "QMS health"],
  ["Open document library", "Document Library"],
] as const)(
  "404 recovery action %s reaches its safe destination without Back",
  async (action, title) => {
    const user = userEvent.setup();
    renderWithProviders(<App />, { route: "/missing" });

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /back/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: action }));
    expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
  },
);

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
