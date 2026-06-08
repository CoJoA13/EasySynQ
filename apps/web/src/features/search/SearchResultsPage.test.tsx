import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { SearchResultsPage } from "./SearchResultsPage";

test("renders ranked rows + the hidden_by_scope footer for ?q=", async () => {
  renderWithProviders(<SearchResultsPage />, { route: "/search?q=supplier" });
  expect(await screen.findByRole("link", { name: "Supplier Selection & Evaluation" })).toBeInTheDocument();
  expect(screen.getByText(/2 hidden by your access scope/)).toBeInTheDocument();
  expect(screen.getByText(/Effective documents only/)).toBeInTheDocument();
});

test("prompts to type when ?q= is empty", () => {
  renderWithProviders(<SearchResultsPage />, { route: "/search" });
  expect(screen.getByText(/Type a query to search/)).toBeInTheDocument();
});

test("shows a calm no-results state", async () => {
  server.use(
    http.get("/api/v1/search", () =>
      HttpResponse.json({ query: "zzz", results: [], hidden_by_scope: 0 }),
    ),
  );
  renderWithProviders(<SearchResultsPage />, { route: "/search?q=zzz" });
  expect(await screen.findByText("No matching documents.")).toBeInTheDocument();
});

test("has no axe violations (results + empty)", async () => {
  const withResults = renderWithProviders(<SearchResultsPage />, { route: "/search?q=supplier" });
  await screen.findByRole("link", { name: "Supplier Selection & Evaluation" });
  expect(await axe(withResults.container)).toHaveNoViolations();
  withResults.unmount();

  const empty = renderWithProviders(<SearchResultsPage />, { route: "/search" });
  expect(await axe(empty.container)).toHaveNoViolations();
});
