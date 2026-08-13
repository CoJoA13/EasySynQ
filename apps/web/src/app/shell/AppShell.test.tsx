import { axe } from "jest-axe";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { useEffect, useRef } from "react";
import { afterEach, expect, test, vi } from "vitest";
import { Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";
import { LibraryPage } from "../../features/library/LibraryPage";
import { ReportsRegisterPage } from "../../features/reports/ReportsRegisterPage";
import { ReviewCockpit } from "../../features/ingestion/ReviewCockpit";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { NotFoundPage } from "../errors/NotFoundPage";
import { AppShell } from "./AppShell";

afterEach(() => vi.restoreAllMocks());

function RecoveryNavigation({ target }: { target: string }) {
  useRouteChrome();
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(target)}>change-effective-view</button>
      <Outlet />
    </>
  );
}

function AlwaysBrokenRoute(): never {
  throw new Error("route recovery probe");
}

function BrokenTasksUntilAcknowledgements() {
  const { search } = useLocation();
  if (new URLSearchParams(search).get("type") !== "DOC_ACK") {
    throw new Error("task view recovery probe");
  }
  return <h1>Acknowledgements recovered</h1>;
}

function RecoverySequenceNavigation() {
  useRouteChrome();
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate("/tasks?type=DOC_ACK")}>go-acknowledgements</button>
      <button onClick={() => navigate("/library?detail=doc-a")}>go-feature-detail</button>
      <button onClick={() => navigate("/missing")}>go-not-found</button>
      <Outlet />
    </>
  );
}

function FeatureOwnedDetail() {
  const focusTarget = useRef<HTMLButtonElement>(null);
  useEffect(() => focusTarget.current?.focus(), []);
  return <button ref={focusTarget}>Feature-owned detail focus</button>;
}

function PersistentFeatureWriter({ feature }: { feature: "library" | "reports" | "ingestion" }) {
  useRouteChrome();
  const content =
    feature === "library" ? (
      <LibraryPage />
    ) : feature === "reports" ? (
      <ReportsRegisterPage />
    ) : (
      <ReviewCockpit runId={ingestionRunFixture.id} run={ingestionRunFixture} />
    );
  return (
    <>
      {content}
      <Outlet />
    </>
  );
}

test("AppShell renders landmarks, skip-link, and child content", async () => {
  const { container } = renderWithProviders(
    <Routes>
      <Route element={<AppShell />}>
        <Route path="library" element={<h1>Library here</h1>} />
      </Route>
    </Routes>,
    { route: "/library" },
  );
  expect(screen.getByRole("banner")).toBeInTheDocument(); // header
  expect(screen.getByRole("navigation")).toBeInTheDocument(); // navbar
  expect(screen.getByRole("main")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /skip to content/i })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Library here" })).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("not-found mode keeps the shell visible and replaces outlet content", () => {
  renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell notFound />}>
        <Route path="*" element={<h1>Unexpected route content</h1>} />
      </Route>
    </Routes>,
    { route: "/missing/private-segment" },
  );

  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Unexpected route content" }),
  ).not.toBeInTheDocument();
});

test("⌘K opens the command palette", async () => {
  const user = userEvent.setup();
  renderWithProviders(<AppShell />, { route: "/" });
  expect(screen.queryByLabelText("Search query")).not.toBeInTheDocument();
  await user.keyboard("{Meta>}k{/Meta}");
  expect(await screen.findByLabelText("Search query")).toBeInTheDocument();
});

test("clicking the TopBar search box opens the palette", async () => {
  const user = userEvent.setup();
  renderWithProviders(<AppShell />, { route: "/" });
  await user.click(screen.getByRole("button", { name: /search/i }));
  expect(await screen.findByLabelText("Search query")).toBeInTheDocument();
});

test("route retry preserves the original QueryClient provider lifecycle and cache", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  let shouldThrow = true;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["preserved-route-data"], { value: "still here" });
  const mount = vi.spyOn(queryClient, "mount");
  const unmount = vi.spyOn(queryClient, "unmount");
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const refetch = vi.spyOn(queryClient, "refetchQueries");
  const reset = vi.spyOn(queryClient, "resetQueries");
  const remove = vi.spyOn(queryClient, "removeQueries");
  const cancel = vi.spyOn(queryClient, "cancelQueries");
  const clear = vi.spyOn(queryClient, "clear");
  const observedClients: QueryClient[] = [];

  function TransientRoute() {
    observedClients.push(useQueryClient());
    if (shouldThrow) throw new Error("RAW_ROUTE_ERROR_SENTINEL");
    return <h1>Recovered route</h1>;
  }

  const rendered = renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route path="broken" element={<TransientRoute />} />
      </Route>
    </Routes>,
    { route: "/broken", queryClient },
  );

  const lifecycleAfterFailure = {
    mounts: mount.mock.calls.length,
    unmounts: unmount.mock.calls.length,
  };
  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getByLabelText("Breadcrumb")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /skip to content/i })).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "This page couldn't be displayed" }),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("RAW_ROUTE_ERROR_SENTINEL");

  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(
    screen.getByRole("heading", { name: "This page couldn't be displayed" }),
  ).toBeInTheDocument();

  shouldThrow = false;
  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(screen.getByRole("heading", { name: "Recovered route" })).toBeInTheDocument();
  expect(queryClient.getQueryData(["preserved-route-data"])).toEqual({
    value: "still here",
  });
  expect({
    everyRouteReadTheSourceClient: observedClients.every((client) => client === queryClient),
    sourceClientsSeen: new Set(observedClients).size,
    lifecycleAfterFailure,
    lifecycleAfterRetries: {
      mounts: mount.mock.calls.length,
      unmounts: unmount.mock.calls.length,
    },
    explicitCacheOperations: {
      invalidate: invalidate.mock.calls.length,
      refetch: refetch.mock.calls.length,
      reset: reset.mock.calls.length,
      remove: remove.mock.calls.length,
      cancel: cancel.mock.calls.length,
      clear: clear.mock.calls.length,
    },
  }).toEqual({
    everyRouteReadTheSourceClient: true,
    sourceClientsSeen: 1,
    lifecycleAfterFailure: { mounts: 1, unmounts: 0 },
    lifecycleAfterRetries: { mounts: 1, unmounts: 0 },
    explicitCacheOperations: {
      invalidate: 0,
      refetch: 0,
      reset: 0,
      remove: 0,
      cancel: 0,
      clear: 0,
    },
  });

  // renderWithProviders deliberately clears its client during test cleanup. Restore this spy so
  // that harness-owned cleanup is not confused with an application Retry operation.
  clear.mockRestore();
  rendered.unmount();
  expect(mount).toHaveBeenCalledTimes(1);
  expect(unmount).toHaveBeenCalledTimes(1);
});

test("route retry allows the normal stale observer refetch when the route remounts", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  const retryRequests = vi.fn();
  let releaseResponse: () => void = () => undefined;
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  server.use(
    http.get("/api/v1/route-retry-probe", async () => {
      retryRequests();
      await responseGate;
      return HttpResponse.json({ value: "network retry data" });
    }),
  );

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnMount: "always" },
    },
  });
  const retryKey = ["route-retry-stale-probe"] as const;
  queryClient.setQueryData(retryKey, { value: "cached retry data" }, { updatedAt: 1 });

  let shouldThrow = true;

  function RetryRoute() {
    const query = useQuery({
      queryKey: retryKey,
      queryFn: async () => {
        const response = await fetch("/api/v1/route-retry-probe");
        return (await response.json()) as { value: string };
      },
    });
    if (shouldThrow) throw new Error("retry route failed");
    return <h1>{query.data?.value}</h1>;
  }

  renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route path="broken" element={<RetryRoute />} />
      </Route>
    </Routes>,
    { route: "/broken", queryClient },
  );

  expect(
    screen.getByRole("heading", { name: "This page couldn't be displayed" }),
  ).toBeInTheDocument();
  expect(retryRequests).not.toHaveBeenCalled();

  shouldThrow = false;
  try {
    await user.click(screen.getByRole("button", { name: "Try this page again" }));
    expect(screen.getByRole("heading", { name: "cached retry data" })).toBeInTheDocument();
    expect(queryClient.getQueryData(retryKey)).toEqual({ value: "cached retry data" });
    await waitFor(() => expect(retryRequests).toHaveBeenCalledTimes(1));
    expect(queryClient.isFetching({ queryKey: retryKey })).toBe(1);
  } finally {
    releaseResponse();
  }
  expect(await screen.findByRole("heading", { name: "network retry data" })).toBeInTheDocument();
  expect(retryRequests).toHaveBeenCalledTimes(1);
});

test("dashboard navigation clears a deterministic route failure", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();

  function BrokenRoute(): never {
    throw new Error("always broken");
  }

  renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route index element={<h1>Safe dashboard</h1>} />
        <Route path="broken" element={<BrokenRoute />} />
      </Route>
    </Routes>,
    { route: "/broken" },
  );

  await user.click(screen.getByRole("link", { name: "Go to dashboard" }));
  expect(screen.getByRole("heading", { name: "Safe dashboard" })).toBeInTheDocument();
  expect(screen.queryByText("This page couldn't be displayed")).not.toBeInTheDocument();
});

test.each([
  ["ordinary query", "/tasks", "/tasks?q=needle", false],
  ["unknown task selector", "/tasks", "/tasks?type=FUTURE", false],
  ["hash", "/tasks", "/tasks#results", false],
  ["material task view", "/tasks", "/tasks?type=DOC_ACK", true],
  ["document detail selector", "/library", "/library?detail=doc-a", true],
  ["document tab", "/documents/doc-a", "/documents/doc-a?tab=history", true],
  ["document mode", "/documents/doc-a", "/documents/doc-a?mode=visual", true],
  ["document comparison pair", "/documents/doc-a", "/documents/doc-a?from=v1&to=v2", true],
] as const)(
  "route recovery %s resets only for an effective view change",
  async (_, initialRoute, target, resets) => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    const fallbackAppearances = vi.fn();
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node instanceof HTMLElement && node.querySelector("#route-error-heading")) {
            fallbackAppearances();
          }
        }
      }
    });

    renderWithProviders(
      <RouteChromeProvider>
        <Routes>
          <Route element={<RecoveryNavigation target={target} />}>
            <Route path="/" element={<AppShell />}>
              <Route path="tasks" element={<AlwaysBrokenRoute />} />
              <Route path="library" element={<AlwaysBrokenRoute />} />
              <Route path="documents/:id" element={<AlwaysBrokenRoute />} />
            </Route>
          </Route>
        </Routes>
      </RouteChromeProvider>,
      { route: initialRoute },
    );

    const routeError = await screen.findByRole("heading", {
      name: "This page couldn't be displayed",
    });
    expect(document.title).toBe("EasySynQ — Page unavailable");
    expect(routeError).toHaveFocus();
    observer.observe(document.body, { childList: true, subtree: true });

    await user.click(screen.getByRole("button", { name: "change-effective-view" }));
    await waitFor(() => {
      expect(fallbackAppearances).toHaveBeenCalledTimes(resets ? 1 : 0);
    });
    expect(document.title).toBe("EasySynQ — Page unavailable");
    if (resets) {
      expect(
        screen.getByRole("heading", { name: "This page couldn't be displayed" }),
      ).toHaveFocus();
    } else {
      expect(screen.getByRole("button", { name: "change-effective-view" })).toHaveFocus();
    }
    observer.disconnect();
  },
);

test.each([
  ["Library", "library", "/library?state=Effective", "Clear all"],
  ["Reports", "reports", "/reports/document-control", "Status"],
  ["ingestion", "ingestion", "/imports/10000000-0000-0000-0000-000000000001?queue=high", "Medium"],
] as const)(
  "an ordinary %s feature URL write does not reset a captured route fallback",
  async (_, feature, route, actionName) => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderWithProviders(
      <RouteChromeProvider>
        <Routes>
          <Route element={<PersistentFeatureWriter feature={feature} />}>
            <Route element={<AppShell />}>
              <Route path="library" element={<AlwaysBrokenRoute />} />
              <Route path="reports/document-control" element={<AlwaysBrokenRoute />} />
              <Route path="imports/:id" element={<AlwaysBrokenRoute />} />
            </Route>
          </Route>
        </Routes>
      </RouteChromeProvider>,
      { route },
    );

    expect(
      await screen.findByRole("heading", { name: "This page couldn't be displayed" }),
    ).toBeInTheDocument();
    if (feature === "reports") {
      await user.click(screen.getByRole("textbox", { name: actionName }));
      await user.click(await screen.findByRole("option", { name: "Effective" }));
    } else {
      const action =
        feature === "ingestion"
          ? screen.getByRole("tab", { name: new RegExp(actionName) })
          : screen.getByRole("button", { name: actionName });
      await user.click(action);
    }

    expect(
      screen.getByRole("heading", { name: "This page couldn't be displayed" }),
    ).toBeInTheDocument();
    expect(document.title).toBe("EasySynQ — Page unavailable");
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  },
);

test.each([
  ["feature-owned detail", "go-feature-detail", "Feature-owned detail focus"],
  ["not-found", "go-not-found", "Page not found"],
] as const)(
  "does not replay pending acknowledgement chrome over the final %s destination",
  async (_, destinationButton, destinationFocus) => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const user = userEvent.setup();

    renderWithProviders(
      <RouteChromeProvider>
        <Routes>
          <Route element={<RecoverySequenceNavigation />}>
            <Route path="/" element={<AppShell />}>
              <Route path="tasks" element={<AlwaysBrokenRoute />} />
              <Route path="library" element={<FeatureOwnedDetail />} />
              <Route path="missing" element={<NotFoundPage />} />
            </Route>
          </Route>
        </Routes>
      </RouteChromeProvider>,
      { route: "/tasks" },
    );

    expect(
      await screen.findByRole("heading", { name: "This page couldn't be displayed" }),
    ).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "go-acknowledgements" }));
    expect(screen.getByRole("heading", { name: "This page couldn't be displayed" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: destinationButton }));
    const destination =
      destinationButton === "go-feature-detail"
        ? await screen.findByRole("button", { name: destinationFocus })
        : await screen.findByRole("heading", { name: destinationFocus });
    expect(destination).toHaveFocus();
    expect(document.getElementById("main-content")).not.toHaveFocus();
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  },
);

test("releases pending acknowledgement chrome only after route-error ownership clears", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();

  renderWithProviders(
    <RouteChromeProvider>
      <Routes>
        <Route element={<RecoveryNavigation target="/tasks?type=DOC_ACK" />}>
          <Route path="/" element={<AppShell />}>
            <Route path="tasks" element={<BrokenTasksUntilAcknowledgements />} />
          </Route>
        </Route>
      </Routes>
    </RouteChromeProvider>,
    { route: "/tasks" },
  );

  expect(
    await screen.findByRole("heading", { name: "This page couldn't be displayed" }),
  ).toHaveFocus();
  await user.click(screen.getByRole("button", { name: "change-effective-view" }));

  expect(await screen.findByRole("heading", { name: "Acknowledgements recovered" })).toBeVisible();
  expect(document.title).toBe("EasySynQ — Acknowledgements");
  expect(document.getElementById("main-content")).toHaveFocus();
  expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent(
    "Acknowledgements",
  );
});
