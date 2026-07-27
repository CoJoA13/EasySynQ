import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { ingestionChecklistFixture, ingestionRunFixture } from "../../test/msw/handlers";
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
  expect(
    await screen.findByText(/already controlled in the vault are skipped/i),
  ).toBeInTheDocument();
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

test("surfaces a failed row decision instead of silently dropping it", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports/:id/files/:fid/decision", () =>
      HttpResponse.json(
        {
          code: "decision_conflict",
          title: "Decision conflict",
          detail: "Another reviewer changed this file.",
        },
        { status: 409 },
      ),
    ),
  );
  renderCockpit();
  const filename = await screen.findByText("SOP-PUR-014 Purchasing.docx");
  const row = filename.closest("tr");
  expect(row).not.toBeNull();

  await user.click(within(row!).getByRole("button", { name: "Accept" }));

  expect(await screen.findByText("Another reviewer changed this file.")).toBeInTheDocument();
  expect(screen.getByText("Review action failed")).toBeInTheDocument();
});

test("surfaces a failed bulk decision", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports/:id/decisions", () =>
      HttpResponse.json(
        {
          code: "bulk_conflict",
          title: "Bulk conflict",
          detail: "The selected review set is stale.",
        },
        { status: 409 },
      ),
    ),
  );
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  await user.click(screen.getByLabelText("Select SOP-PUR-014 Purchasing.docx"));
  await user.click(screen.getByRole("button", { name: "Bulk accept all High" }));

  expect(await screen.findByText("The selected review set is stale.")).toBeInTheDocument();
});

test("surfaces a failed split action", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports/:id/split", () =>
      HttpResponse.json(
        {
          code: "split_conflict",
          title: "Split conflict",
          detail: "This group changed before the split completed.",
        },
        { status: 409 },
      ),
    ),
  );
  renderCockpit();
  const filename = await screen.findByText("SOP-PUR-014 Purchasing.docx");
  const row = filename.closest("tr");
  expect(row).not.toBeNull();
  await user.click(within(row!).getByRole("button", { name: "Open" }));
  await user.click(await screen.findByRole("button", { name: "Split out of group" }));

  expect(
    await screen.findByText("This group changed before the split completed."),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("dialog")).getByText("This group changed before the split completed."),
  ).toBeInTheDocument();
});

test("shows the latest drawer-action error when an earlier failure is still undismissed", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports/:id/files/:fid/decision", () =>
      HttpResponse.json(
        {
          code: "decision_conflict",
          title: "Decision conflict",
          detail: "An earlier file decision failed.",
        },
        { status: 409 },
      ),
    ),
    http.post("/api/v1/admin/imports/:id/split", () =>
      HttpResponse.json(
        {
          code: "split_conflict",
          title: "Split conflict",
          detail: "The later split action failed.",
        },
        { status: 409 },
      ),
    ),
  );
  renderCockpit();
  const filename = await screen.findByText("SOP-PUR-014 Purchasing.docx");
  const row = filename.closest("tr");
  expect(row).not.toBeNull();
  await user.click(within(row!).getByRole("button", { name: "Open" }));
  const drawer = await screen.findByRole("dialog");

  await user.click(within(drawer).getByRole("button", { name: "Accept item" }));
  expect(await within(drawer).findByText("An earlier file decision failed.")).toBeInTheDocument();

  await user.click(within(drawer).getByRole("button", { name: "Split out of group" }));
  expect(await within(drawer).findByText("The later split action failed.")).toBeInTheDocument();
  expect(within(drawer).queryByText("An earlier file decision failed.")).not.toBeInTheDocument();
});

test("surfaces a failed commit inside the commit card", async () => {
  const user = userEvent.setup();
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.commit", effect: "ALLOW", source: "role" }],
      }),
    ),
    http.get("/api/v1/admin/imports/:id/checklist", () =>
      HttpResponse.json({
        ...ingestionChecklistFixture,
        ready: true,
        blocking: [],
        review: { ...ingestionChecklistFixture.review, commit_ready: 1 },
      }),
    ),
    http.post("/api/v1/admin/imports/:id/commit", () =>
      HttpResponse.json(
        {
          code: "commit_conflict",
          title: "Commit conflict",
          detail: "The checklist changed before commit.",
        },
        { status: 409 },
      ),
    ),
  );
  renderCockpit();
  await user.click(await screen.findByRole("button", { name: "Commit 1 confirmed" }));

  expect(await screen.findByText("The checklist changed before commit.")).toBeInTheDocument();
  expect(screen.getByText("Commit failed")).toBeInTheDocument();
});

test("has no axe violations", async () => {
  const { container } = renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  expect(await axe(container)).toHaveNoViolations();
});
