import { screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { LeftRail } from "./LeftRail";

test("LeftRail shows Home/Library nav + PDCA clause groups", async () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Library" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("PLAN")).toBeInTheDocument());
  expect(screen.getByText("DO")).toBeInTheDocument();
  expect(screen.getByText("CHECK")).toBeInTheDocument();
  expect(screen.getByText("ACT")).toBeInTheDocument();
});

test("LeftRail shows the Review & Approve nav link", () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  expect(screen.getByRole("link", { name: "Review & Approve" })).toHaveAttribute("href", "/tasks");
});
