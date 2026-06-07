import { axe } from "jest-axe";
import { screen } from "@testing-library/react";
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
