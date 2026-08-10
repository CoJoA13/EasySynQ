import { axe } from "jest-axe";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, useQuery } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { afterEach, expect, test, vi } from "vitest";
import { Link, Routes, Route } from "react-router-dom";
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

test("a routed-page render failure preserves shell chrome and retry remounts only content", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  let shouldThrow = true;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["preserved-route-data"], { value: "still here" });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");

  function TransientRoute() {
    if (shouldThrow) throw new Error("RAW_ROUTE_ERROR_SENTINEL");
    return <h1>Recovered route</h1>;
  }

  renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route path="broken" element={<TransientRoute />} />
      </Route>
    </Routes>,
    { route: "/broken", queryClient },
  );

  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getByLabelText("Breadcrumb")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /skip to content/i })).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "This page couldn't be displayed" }),
  ).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent("RAW_ROUTE_ERROR_SENTINEL");

  shouldThrow = false;
  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(screen.getByRole("heading", { name: "Recovered route" })).toBeInTheDocument();
  expect(queryClient.getQueryData(["preserved-route-data"])).toEqual({
    value: "still here",
  });
  expect(invalidate).not.toHaveBeenCalled();
});

test("route retry does not refetch stale cached queries or leak query defaults", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const user = userEvent.setup();
  const retryRequests = vi.fn();
  const ordinaryNavigationRequests = vi.fn();
  server.use(
    http.get("/api/v1/route-retry-probe", () => {
      retryRequests();
      return HttpResponse.json({ value: "network retry data" });
    }),
    http.get("/api/v1/ordinary-navigation-probe", () => {
      ordinaryNavigationRequests();
      return HttpResponse.json({ value: "network navigation data" });
    }),
  );

  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnMount: "always" },
    },
  });
  const originalDefaults = queryClient.getDefaultOptions();
  const originalQueryDefaults = originalDefaults.queries;
  const retryKey = ["route-retry-stale-probe"] as const;
  const navigationKey = ["ordinary-navigation-stale-probe"] as const;
  queryClient.setQueryData(retryKey, { value: "cached retry data" }, { updatedAt: 1 });
  queryClient.setQueryData(navigationKey, { value: "cached navigation data" }, { updatedAt: 1 });

  let shouldThrow = true;
  let useDefaultRefetchPolicy = false;

  function RetryRoute() {
    const query = useQuery({
      queryKey: retryKey,
      queryFn: async () => {
        const response = await fetch("/api/v1/route-retry-probe");
        return (await response.json()) as { value: string };
      },
      refetchOnMount: useDefaultRefetchPolicy ? undefined : false,
    });
    if (shouldThrow) throw new Error("retry route failed");
    return (
      <>
        <h1>{query.data?.value}</h1>
        <Link to="/">Continue to dashboard</Link>
      </>
    );
  }

  function OrdinaryNavigationRoute() {
    const query = useQuery({
      queryKey: navigationKey,
      queryFn: async () => {
        const response = await fetch("/api/v1/ordinary-navigation-probe");
        return (await response.json()) as { value: string };
      },
    });
    return <h1>{query.data?.value}</h1>;
  }

  const rendered = renderWithProviders(
    <Routes>
      <Route path="/" element={<AppShell />}>
        <Route index element={<OrdinaryNavigationRoute />} />
        <Route path="broken" element={<RetryRoute />} />
      </Route>
    </Routes>,
    { route: "/broken", queryClient },
  );

  expect(
    screen.getByRole("heading", { name: "This page couldn't be displayed" }),
  ).toBeInTheDocument();
  expect(retryRequests).not.toHaveBeenCalled();

  useDefaultRefetchPolicy = true;
  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(
    screen.getByRole("heading", { name: "This page couldn't be displayed" }),
  ).toBeInTheDocument();
  expect(queryClient.getDefaultOptions()).toBe(originalDefaults);
  expect(queryClient.getDefaultOptions().queries).toBe(originalQueryDefaults);

  shouldThrow = false;
  await user.click(screen.getByRole("button", { name: "Try this page again" }));
  expect(screen.getByRole("heading", { name: "cached retry data" })).toBeInTheDocument();
  await waitFor(() => expect(queryClient.isFetching({ queryKey: retryKey })).toBe(0));
  expect(retryRequests).not.toHaveBeenCalled();
  expect(queryClient.getDefaultOptions()).toBe(originalDefaults);
  expect(queryClient.getDefaultOptions().queries).toBe(originalQueryDefaults);

  await user.click(screen.getByRole("link", { name: "Continue to dashboard" }));
  expect(
    await screen.findByRole("heading", { name: "network navigation data" }),
  ).toBeInTheDocument();
  expect(ordinaryNavigationRequests).toHaveBeenCalledTimes(1);
  expect(queryClient.getDefaultOptions()).toBe(originalDefaults);
  expect(queryClient.getDefaultOptions().queries).toBe(originalQueryDefaults);

  rendered.unmount();
  expect(queryClient.getDefaultOptions()).toBe(originalDefaults);
  expect(queryClient.getDefaultOptions().queries).toBe(originalQueryDefaults);
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
