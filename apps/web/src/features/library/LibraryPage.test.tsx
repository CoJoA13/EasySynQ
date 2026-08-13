import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useLocation, useNavigate } from "react-router-dom";
import { docFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { LibraryPage } from "./LibraryPage";

const DOC_A = docFixture[0]!;
const DOC_B = docFixture[1]!;

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output aria-label="Current location">{pathname + search}</output>;
}

function HistoryControls() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate("/library?sentinel=keep")}>Prepare history</button>
      <button onClick={() => navigate(-1)}>Back</button>
      <button onClick={() => navigate(`/library?detail=${DOC_A.id}`)}>External doc a</button>
      <button onClick={() => navigate(`/library?detail=${DOC_B.id}`)}>External doc b</button>
      <button onClick={() => navigate("/library?sentinel=keep")}>External close</button>
    </>
  );
}

test("lists documents with resolved Type/Owner and is accessible", async () => {
  const { container } = renderWithProviders(<LibraryPage />, { route: "/library" });

  await waitFor(() => expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument());
  // Friendly columns resolved via /document-types + /directory/users (the S-web-2 endpoints) —
  // scoped to the table cell (the facet selects also reference these names).
  expect(screen.getByRole("cell", { name: "Procedure" })).toBeInTheDocument(); // type name, not UUID
  expect(screen.getByRole("cell", { name: "Mara Quality" })).toBeInTheDocument(); // owner name
  expect(screen.getByText("Clause 8.4").closest("[data-clause-badge]")).toHaveAttribute(
    "data-clause-badge",
  );
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

test("the named identifier action opens the deep-linkable detail drawer", async () => {
  renderWithProviders(<LibraryPage />, { route: "/library" });
  await waitFor(() => expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument());

  const open = screen.getByRole("button", {
    name: "Open SOP-PUR-014: Supplier Selection & Evaluation",
  });
  expect(open.closest("tr")).not.toHaveAttribute("tabindex");
  await userEvent.click(open);
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: "Supplier Selection & Evaluation" }),
    ).toBeInTheDocument(),
  );
});

test("ordinary facets and pagination replace history while preserving unrelated parameters", async () => {
  const user = userEvent.setup();
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        data: docFixture,
        page: { limit: 50, offset: 0, returned: docFixture.length, has_more: true },
      }),
    ),
  );
  renderWithProviders(
    <>
      <LibraryPage />
      <HistoryControls />
      <LocationProbe />
    </>,
    { route: "/library?sentinel=keep" },
  );
  await screen.findByText("SOP-PUR-014");
  await user.click(screen.getByRole("button", { name: "Prepare history" }));

  await user.click(screen.getByRole("textbox", { name: "Status" }));
  await user.click(await screen.findByRole("option", { name: "Effective" }));
  await user.click(screen.getByRole("button", { name: "Clear all" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).not.toHaveTextContent("state=Effective"),
  );
  await user.click(screen.getByRole("textbox", { name: "Status" }));
  await user.click(await screen.findByRole("option", { name: "Effective" }));
  await user.click(screen.getByRole("button", { name: "Next ›" }));
  await user.click(screen.getByRole("button", { name: "‹ Prev" }));
  await user.click(screen.getByRole("button", { name: "Next ›" }));
  await user.click(screen.getByRole("textbox", { name: "Page size" }));
  await user.click(await screen.findByRole("option", { name: "50 / page" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("sentinel=keep");
  expect(screen.getByLabelText("Current location")).toHaveTextContent("state=Effective");
  expect(screen.getByLabelText("Current location")).toHaveTextContent("size=50");

  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent("/library?sentinel=keep"),
  );
  expect(screen.getByLabelText("Current location")).not.toHaveTextContent("state=Effective");
  expect(screen.getByLabelText("Current location")).not.toHaveTextContent("size=50");
});

test("Library detail opening pushes but close replaces, and external detail changes control the drawer", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <LibraryPage />
      <HistoryControls />
      <LocationProbe />
    </>,
    { route: "/library?sentinel=keep" },
  );
  await screen.findByText("SOP-PUR-014");
  await user.click(screen.getByRole("button", { name: "Prepare history" }));
  await user.click(
    screen.getByRole("button", { name: "Open SOP-PUR-014: Supplier Selection & Evaluation" }),
  );
  await screen.findByRole("heading", { name: "Supplier Selection & Evaluation" });
  expect(screen.getByLabelText("Current location")).toHaveTextContent(
    "detail=11111111-1111-1111-1111-111111111111",
  );

  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByLabelText("Current location")).toHaveTextContent("sentinel=keep");

  await user.click(
    screen.getByRole("button", { name: "Open SOP-PUR-014: Supplier Selection & Evaluation" }),
  );
  await screen.findByRole("dialog");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  await user.click(screen.getByRole("button", { name: "External doc a" }));
  expect(await screen.findByRole("heading", { name: DOC_A.title })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "External doc b" }));
  expect(await screen.findByRole("heading", { name: DOC_B.title })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "External close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
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

  // Collapse-to-selection: the 8.4 sub-clause button only renders once its top-level clause is
  // selected. The top-level pick itself now returns results (S-clause-rollup: clause 8 rolls up
  // its subtree — both 8.x-mapped fixture docs stay), then the sub-clause narrows within it.
  await userEvent.click(screen.getByRole("button", { name: /8 Operation/ }));
  await waitFor(() => expect(screen.getByText("SOP-PUR-014")).toBeInTheDocument());
  expect(screen.getByText("SOP-PRD-007")).toBeInTheDocument(); // 8.5-mapped — kept by the rollup
  await userEvent.click(
    await screen.findByRole("button", { name: /8\.4 Control of external providers/ }),
  );

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
