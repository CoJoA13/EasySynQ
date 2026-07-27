import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { docFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { LibraryPage } from "./LibraryPage";

test("lists documents with resolved Type/Owner and is accessible", async () => {
  const { container } = renderWithProviders(<LibraryPage />, { route: "/library" });

  await waitFor(() => expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument());
  // Friendly columns resolved via /document-types + /directory/users (the S-web-2 endpoints) —
  // scoped to the table cell (the facet selects also reference these names).
  expect(screen.getByRole("cell", { name: "Procedure" })).toBeInTheDocument(); // type name, not UUID
  expect(screen.getByRole("cell", { name: "Mara Quality" })).toBeInTheDocument(); // owner name
  // State badge (icon + label, never color-only) + effective date.
  expect(screen.getByLabelText("State: Effective")).toBeInTheDocument();
  expect(screen.getByText("2026-03-14")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

test("renders effective timestamps on the organization calendar day", async () => {
  server.use(
    http.get("/api/v1/me", () =>
      HttpResponse.json({
        id: "me",
        keycloak_subject: "subject",
        display_name: "Mara",
        email: "mara@example.test",
        status: "ACTIVE",
        org_timezone: "America/Chicago",
      }),
    ),
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        data: [{ ...docFixture[0], effective_from: "2026-07-01T02:00:00Z" }],
        page: { limit: 50, offset: 0, returned: 1, has_more: false },
      }),
    ),
  );
  renderWithProviders(<LibraryPage />, { route: "/library" });

  expect(await screen.findByText("2026-06-30")).toBeInTheDocument();
  expect(screen.queryByText("2026-07-01")).not.toBeInTheDocument();
});

test("clicking a row opens the deep-linkable detail drawer with the artifact header", async () => {
  renderWithProviders(<LibraryPage />, { route: "/library" });
  await waitFor(() => expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument());

  await userEvent.click(screen.getByText("SOP-PUR-014"));
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "Supplier Selection & Evaluation" }),
    ).toBeInTheDocument(),
  );
});

test("a cold ?detail= deep-link opens the drawer (fetches the doc)", async () => {
  renderWithProviders(<LibraryPage />, {
    route: "/library?detail=11111111-1111-1111-1111-111111111111",
  });
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "Supplier Selection & Evaluation" }),
    ).toBeInTheDocument(),
  );
});

test("filtering by a clause narrows the list and shows a removable chip", async () => {
  renderWithProviders(<LibraryPage />, { route: "/library" });
  await waitFor(() => expect(screen.getByText("SOP-PRD-007")).toBeInTheDocument());

  await userEvent.click(screen.getByRole("button", { name: /8\.4 Control of external providers/ }));

  // The 8.5-mapped doc drops out; the 8.4-mapped doc stays.
  await waitFor(() => expect(screen.queryByText("SOP-PRD-007")).not.toBeInTheDocument());
  expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument();
  // The active-filter chip with an accessible remove control.
  expect(screen.getByRole("button", { name: /Remove filter Clause: 8\.4/ })).toBeInTheDocument();
});

test("drawer tabs lazy-load History and Where-used", async () => {
  renderWithProviders(<LibraryPage />, {
    route: "/library?detail=11111111-1111-1111-1111-111111111111",
  });
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "Supplier Selection & Evaluation" }),
    ).toBeInTheDocument(),
  );

  await userEvent.click(screen.getByRole("tab", { name: "History" }));
  await waitFor(() => expect(screen.getByText("Rev B")).toBeInTheDocument());

  await userEvent.click(screen.getByRole("tab", { name: "Where-used" }));
  await waitFor(() => expect(screen.getByText(/WI-PUR-008/)).toBeInTheDocument());
});

test("empty-with-filters shows a clear-filters affordance", async () => {
  // owner facet set to a user that owns nothing in the fixture → empty result.
  renderWithProviders(<LibraryPage />, { route: "/library?owner=no-such-owner" });
  await waitFor(() =>
    expect(screen.getByText("No documents match these filters.")).toBeInTheDocument(),
  );
  expect(within(document.body).getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
});

test("gives register headers a column scope (a11y)", async () => {
  renderWithProviders(<LibraryPage />, { route: "/library" });
  const header = await screen.findByRole("columnheader", { name: "Identifier" });
  expect(header).toHaveAttribute("scope", "col");
});
