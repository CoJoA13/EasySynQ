import { axe } from "jest-axe";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../../test/render";
import { AppShell } from "./AppShell";

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
  await user.click(screen.getByLabelText("Open search"));
  expect(await screen.findByLabelText("Search query")).toBeInTheDocument();
});
