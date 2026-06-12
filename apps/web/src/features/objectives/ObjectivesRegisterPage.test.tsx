import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ObjectivesRegisterPage } from "./ObjectivesRegisterPage";

it("renders the band and a row per objective with a RAG status badge", async () => {
  const { container } = renderWithProviders(<ObjectivesRegisterPage />, { route: "/objectives" });
  await waitFor(() => expect(screen.getByText("OBJ-001")).toBeInTheDocument());
  expect(screen.getByText(/1\s*\/\s*4 on target/i)).toBeInTheDocument();
  const row = screen.getByText("On-time delivery rate").closest("tr")!;
  expect(within(row).getByText("Amber")).toBeInTheDocument();
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
      HttpResponse.json({ total: 0, on_target: 0, by_rag: { green: 0, amber: 0, red: 0, unmeasured: 0 }, objectives: [] }),
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
  // Click the "Red" chip filter.
  await user.click(screen.getByRole("radio", { name: "Red" }));
  // Only the red row (OBJ-002, "Customer complaints per quarter") remains.
  expect(screen.getByText("OBJ-002")).toBeInTheDocument();
  expect(screen.queryByText("OBJ-003")).not.toBeInTheDocument(); // green row gone
  expect(screen.queryByText("OBJ-001")).not.toBeInTheDocument(); // amber row gone
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
