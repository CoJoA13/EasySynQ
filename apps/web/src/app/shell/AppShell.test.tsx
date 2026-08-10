import { axe } from "jest-axe";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { Routes, Route } from "react-router-dom";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { AppShell } from "./AppShell";

afterEach(() => vi.restoreAllMocks());

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
