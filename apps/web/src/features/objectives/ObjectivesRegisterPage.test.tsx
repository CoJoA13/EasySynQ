import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { objectiveFixtures } from "../../test/msw/handlers";
import { TONE_GLYPH } from "../../lib/status";
import type { Objective, ObjectiveScorecard } from "../../lib/types";
import { ObjectivesRegisterPage } from "./ObjectivesRegisterPage";

it("renders the band and a row per objective with a RAG status badge", async () => {
  const { container } = renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByText(/1\s*\/\s*4 on target/i)).toBeInTheDocument();
  const row = screen.getByText("On-time delivery rate").closest("tr")!;
  // The amber row's RAG pill is the canonical StatusBadge: amber → warning → ◔ glyph + the MEANING
  // label "Needs attention" (never the colour word "Amber"; DP-5). Scope to the row — other rows carry
  // RAG pills too.
  expect(within(row).getByText("Needs attention")).toBeInTheDocument();
  expect(within(row).getByLabelText("Status: Needs attention")).toBeInTheDocument();
  expect(within(row).getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  expect(within(row).getByText("92 / 95 %")).toBeInTheDocument();
  // unmeasured row shows an em dash for the current value
  const unmeasured = screen.getByText("Supplier defect rate").closest("tr")!;
  expect(within(unmeasured).getByText("— / 2 %")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() =>
    expect(screen.getByText(/don't have access to quality objectives/i)).toBeInTheDocument(),
  );
});

it("shows a calm error (not an infinite loader) on a non-403 failure", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({ code: "internal_error", title: "boom" }, { status: 500 }),
    ),
  );
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() =>
    expect(screen.getByText(/couldn't load quality objectives/i)).toBeInTheDocument(),
  );
});

it("shows an empty state when there are no objectives", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({
        total: 0,
        on_target: 0,
        by_rag: { green: 0, amber: 0, red: 0, unmeasured: 0 },
        objectives: [],
      }),
    ),
  );
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText(/no quality objectives yet/i)).toBeInTheDocument());
});

it("RAG filter narrows visible rows client-side", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  // Default: all four objectives visible.
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByText("OBJ-002")).toBeInTheDocument();
  expect(screen.getByText("OBJ-003")).toBeInTheDocument();
  expect(screen.getByText("OBJ-004")).toBeInTheDocument();
  // Click the red ("Action required") chip filter — the segment shows the meaning, not the colour word.
  await user.click(screen.getByRole("radio", { name: "Action required" }));
  // Only the red row (OBJ-002, "Customer complaints per quarter") remains.
  expect(screen.getByText("OBJ-002")).toBeInTheDocument();
  expect(screen.queryByText("OBJ-003")).not.toBeInTheDocument(); // green row gone
  expect(screen.queryByText("OBJ-001")).not.toBeInTheDocument(); // amber row gone
});

it("sorts the Status column by triage severity (worst first), not the raw rag key", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  // Click the Status column → ascending by severity: red → amber → green → unmeasured. The row anchors
  // (role=link) reflect the table order: OBJ-002 (red/Action required) first, OBJ-004 (unmeasured) last
  // — NOT the alphabetical raw-rag order (amber, green, red, unmeasured) the old sort produced (Codex P3).
  await user.click(screen.getByRole("button", { name: "Sort by Status" }));
  const order = screen.getAllByRole("link").map((a) => a.textContent);
  expect(order).toEqual(["OBJ-002", "OBJ-001", "OBJ-003", "OBJ-004"]);
});

it("debounced search filters rows by identifier and title", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  // Default: all four objectives visible.
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByText("OBJ-002")).toBeInTheDocument();
  expect(screen.getByText("OBJ-003")).toBeInTheDocument();
  expect(screen.getByText("OBJ-004")).toBeInTheDocument();
  // Type a term matching only OBJ-002's title ("Customer complaints per quarter").
  await user.type(screen.getByLabelText("Search"), "complaints");
  // The debounced filter (150ms) settles → only the matching row survives.
  await waitFor(() => expect(screen.queryByText("OBJ-001")).not.toBeInTheDocument());
  expect(screen.getByText("OBJ-002")).toBeInTheDocument();
  expect(screen.queryByText("OBJ-003")).not.toBeInTheDocument();
  expect(screen.queryByText("OBJ-004")).not.toBeInTheDocument();
});

it("default fixtures (all Draft) each show a 'Draft' state chip", async () => {
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  // Every default row is Draft — there should be at least one "State: Draft" chip.
  const chips = screen.getAllByLabelText("State: Draft");
  expect(chips.length).toBeGreaterThanOrEqual(1);
});

it("marks non-Effective rows with a state chip and leaves Effective rows clean", async () => {
  // Build two rows: one Effective (OBJ-001 re-labeled as OBJ-101) and one UnderRevision (OBJ-102).
  const effectiveRow: Objective = {
    ...objectiveFixtures[0]!,
    id: "ob000101-0101-0101-0101-000000000101",
    identifier: "OBJ-101",
    current_state: "Effective",
  };
  const underRevisionRow: Objective = {
    ...objectiveFixtures[1]!,
    id: "ob000102-0102-0102-0102-000000000102",
    identifier: "OBJ-102",
    title: "Under-revision objective",
    current_state: "UnderRevision",
  };

  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({
        total: 2,
        on_target: 1,
        by_rag: { green: 0, amber: 1, red: 1, unmeasured: 0 },
        objectives: [effectiveRow, underRevisionRow],
      } satisfies ObjectiveScorecard),
    ),
  );

  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText("OBJ-101")).toBeInTheDocument());

  // Effective row: NO state chip.
  const effRow = screen.getByText("OBJ-101").closest("tr")!;
  expect(within(effRow).queryByLabelText(/^State:/)).toBeNull();

  // UnderRevision row: chip present with correct aria-label.
  const urRow = screen.getByText("OBJ-102").closest("tr")!;
  expect(within(urRow).getByLabelText("State: Under revision")).toBeInTheDocument();
});

it("create modal resets on close and reopen", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "objective.manage", effect: "ALLOW", source: "test" }],
      }),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  // Wait for the band (scorecard) to render so the page is fully loaded.
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  // Open the create modal.
  await user.click(screen.getByRole("button", { name: /new objective/i }));
  const dialog = await screen.findByRole("dialog");
  // Type something into the Objective field inside the dialog.
  const objectiveField = within(dialog).getByLabelText(/^objective/i);
  await user.type(objectiveField, "Some draft text");
  expect(objectiveField).toHaveValue("Some draft text");
  // Close via Cancel.
  await user.click(within(dialog).getByRole("button", { name: /cancel/i }));
  // Reopen the modal.
  await user.click(screen.getByRole("button", { name: /new objective/i }));
  const reopenedDialog = await screen.findByRole("dialog");
  // The Objective field in the fresh modal must be empty — state was reset on unmount.
  expect(within(reopenedDialog).getByLabelText(/^objective/i)).toHaveValue("");
});
