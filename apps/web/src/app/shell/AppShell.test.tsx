import { axe } from "jest-axe";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";
import { Routes, Route } from "react-router-dom";
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
