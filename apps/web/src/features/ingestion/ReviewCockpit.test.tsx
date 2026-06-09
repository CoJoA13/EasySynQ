import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { ReviewCockpit } from "./ReviewCockpit";

const RID = ingestionRunFixture.id;

function renderCockpit(route = `/ingestion/${RID}?queue=high`) {
  return renderWithProviders(<ReviewCockpit runId={RID} run={ingestionRunFixture} />, { route });
}

test("the High tab shows the 2 high-band rows", async () => {
  renderCockpit();
  const table = await screen.findByRole("table", { name: "Triage queue" });
  // SOP-PUR-014 (HIGH_DOC) + SOP-PUR v2 FINAL (DUP_FILE) are the two band=HIGH rows.
  expect(await within(table).findByText("SOP-PUR-014 Purchasing.docx")).toBeInTheDocument();
  expect(within(table).getByText("SOP-PUR v2 FINAL.docx")).toBeInTheDocument();
  expect(within(table).queryByText("Final Inspection WI rev1.docx")).not.toBeInTheDocument();
});

test("switching to the Needs-decision tab refetches the undecided rows", async () => {
  const user = userEvent.setup();
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  await user.click(screen.getByRole("tab", { name: /Needs decision/ }));
  // review_status=undecided returns all four classified rows (the quarantine row is excluded).
  expect(await screen.findByText("Final Inspection WI rev1.docx")).toBeInTheDocument();
  expect(await screen.findByText("scan0421.pdf")).toBeInTheDocument();
});

test("selecting a row reveals the bulk action bar", async () => {
  const user = userEvent.setup();
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
  await user.click(screen.getByLabelText("Select SOP-PUR-014 Purchasing.docx"));
  expect(await screen.findByRole("region", { name: "Bulk actions" })).toBeInTheDocument();
});

test("the commit button is disabled when the run is not ready (fixture ready=false)", async () => {
  // Grant import.commit so CommitCard renders the button (without the key it shows the held-by-role
  // note instead). The button is then disabled because the fixture checklist.ready === false.
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.commit", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  const commit = await screen.findByRole("button", { name: /Commit/ });
  expect(commit).toBeDisabled();
});

test("the 'Already in vault' tab shows the explainer, not the files table", async () => {
  const user = userEvent.setup();
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  await user.click(screen.getByRole("tab", { name: /Already in vault/ }));
  // The calm registry explainer renders; the triage table does NOT (the empty {} filter would
  // otherwise list page 1 of ALL files while the badge says 0).
  expect(await screen.findByText(/already controlled in the vault are skipped/i)).toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Triage queue" })).not.toBeInTheDocument();
});

test("changing the confidence facet clears the current selection", async () => {
  const user = userEvent.setup();
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  // Select a row → the bulk action bar appears.
  await user.click(screen.getByLabelText("Select SOP-PUR-014 Purchasing.docx"));
  expect(await screen.findByRole("region", { name: "Bulk actions" })).toBeInTheDocument();
  // Narrow the confidence facet (the SegmentedControl radio) → the selection (which may now be hidden)
  // is dropped, so the bulk bar disappears.
  await user.click(screen.getByRole("radio", { name: "High" }));
  expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(await axe(container)).toHaveNoViolations();
});
