import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "../../app/shell/AppShell";
import { RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";
import { server } from "../../test/msw/server";
import { ingestionChecklistFixture, ingestionRunFixture } from "../../test/msw/handlers";
import { renderWithProviders } from "../../test/render";
import { ReviewCockpit } from "./ReviewCockpit";

const RID = ingestionRunFixture.id;

function HistoryControls() {
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  return (
    <>
      <button
        onClick={() => {
          const params = new URLSearchParams(search);
          params.set("checkpoint", "prepared");
          navigate(`${pathname}?${params.toString()}`);
        }}
      >
        Prepare history
      </button>
      <button onClick={() => navigate(-1)}>Back</button>
      <output aria-label="Current location">{useLocation().search}</output>
    </>
  );
}

function ChromeOutlet() {
  useRouteChrome();
  return <Outlet />;
}

function renderCockpit(route = `/imports/${RID}?queue=high`) {
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

test("queue default removal replaces history while preserving unrelated query state", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <ReviewCockpit runId={RID} run={ingestionRunFixture} />
      <HistoryControls />
    </>,
    { route: `/imports/${RID}?queue=high&sentinel=keep&checkpoint=baseline` },
  );
  await screen.findByRole("table", { name: "Triage queue" });
  await user.click(screen.getByRole("button", { name: "Prepare history" }));
  await user.click(screen.getByRole("tab", { name: /Needs decision/ }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("sentinel=keep");
  expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=prepared");
  expect(screen.getByLabelText("Current location")).not.toHaveTextContent("queue=");

  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=baseline"),
  );
  expect(screen.getByLabelText("Current location")).toHaveTextContent("queue=high");
});

test("confidence default removal replaces history while preserving unrelated query state", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <ReviewCockpit runId={RID} run={ingestionRunFixture} />
      <HistoryControls />
    </>,
    { route: `/imports/${RID}?queue=high&conf=HIGH&sentinel=keep&checkpoint=baseline` },
  );
  await screen.findByRole("table", { name: "Triage queue" });
  await user.click(screen.getByRole("button", { name: "Prepare history" }));
  await user.click(screen.getByRole("radio", { name: "All" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=prepared");
  expect(screen.getByLabelText("Current location")).not.toHaveTextContent("conf=");

  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=baseline"),
  );
  expect(screen.getByLabelText("Current location")).toHaveTextContent("conf=HIGH");
});

test("offset default removal replaces history while preserving unrelated query state", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <ReviewCockpit runId={RID} run={ingestionRunFixture} />
      <HistoryControls />
    </>,
    { route: `/imports/${RID}?queue=high&offset=100&sentinel=keep&checkpoint=baseline` },
  );
  await screen.findByRole("table", { name: "Triage queue" });
  await user.click(screen.getByRole("button", { name: "Prepare history" }));
  await user.click(screen.getByRole("button", { name: "‹ Prev" }));
  expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=prepared");
  expect(screen.getByLabelText("Current location")).not.toHaveTextContent("offset=");

  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current location")).toHaveTextContent("checkpoint=baseline"),
  );
  expect(screen.getByLabelText("Current location")).toHaveTextContent("offset=100");
});

test("an ingestion ordinary queue edit leaves route chrome and focused tab untouched", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <RouteChromeProvider>
      <Routes>
        <Route element={<ChromeOutlet />}>
          <Route element={<AppShell />}>
            <Route
              path="imports/:id"
              element={<ReviewCockpit runId={RID} run={ingestionRunFixture} />}
            />
          </Route>
        </Route>
      </Routes>
    </RouteChromeProvider>,
    { route: `/imports/${RID}?queue=high` },
  );
  await screen.findByRole("table", { name: "Triage queue" });
  const medium = screen.getByRole("tab", { name: /Medium/ });
  await user.click(medium);

  expect(document.title).toBe("EasySynQ — Import run");
  expect(medium).toHaveFocus();
  expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
});

test("a files-list failure renders as an error, not an empty queue", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id/files", () =>
      HttpResponse.json(
        { code: "files_failed", title: "Files failed", detail: "Could not list files." },
        { status: 500 },
      ),
    ),
  );
  renderCockpit();

  expect(await screen.findByText("Couldn't load this queue")).toBeInTheDocument();
  expect(screen.queryByText("Nothing in this queue.")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Triage pagination")).not.toBeInTheDocument();
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

test("a successful bulk decision clears the selection", async () => {
  // Decided rows leave the filtered listing while their ids would stay selected — a later bulk
  // action would silently re-target them (an accept can override a just-made exclude, decisions
  // folding newest-wins). The success path must drop the selection.
  const user = userEvent.setup();
  renderCockpit();
  await screen.findByText("SOP-PUR-014 Purchasing.docx");
  await user.click(screen.getByLabelText("Select SOP-PUR-014 Purchasing.docx"));
  expect(await screen.findByRole("region", { name: "Bulk actions" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Exclude selected" }));
  await user.click(await screen.findByRole("button", { name: "Exclude items" }));

  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument(),
  );
});

test("a successful per-file decision prunes that row from the selection", async () => {
  const user = userEvent.setup();
  renderCockpit();
  const filename = await screen.findByText("SOP-PUR-014 Purchasing.docx");
  await user.click(screen.getByLabelText("Select SOP-PUR-014 Purchasing.docx"));
  expect(await screen.findByRole("region", { name: "Bulk actions" })).toBeInTheDocument();

  const row = filename.closest("tr");
  expect(row).not.toBeNull();
  await user.click(within(row!).getByRole("button", { name: "Accept" }));

  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "Bulk actions" })).not.toBeInTheDocument(),
  );
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

test("keeps a row-action error attached to its originating file", async () => {
  const user = userEvent.setup();
  server.use(
    http.post("/api/v1/admin/imports/:id/files/:fid/decision", () =>
      HttpResponse.json(
        {
          code: "decision_conflict",
          title: "Decision conflict",
          detail: "The first file changed on the server.",
        },
        { status: 409 },
      ),
    ),
  );
  renderCockpit();
  const firstFilename = await screen.findByText("SOP-PUR-014 Purchasing.docx");
  const firstRow = firstFilename.closest("tr");
  expect(firstRow).not.toBeNull();
  await user.click(within(firstRow!).getByRole("button", { name: "Accept" }));
  expect(await screen.findByText("The first file changed on the server.")).toBeInTheDocument();

  const secondFilename = screen.getByText("SOP-PUR v2 FINAL.docx");
  const secondRow = secondFilename.closest("tr");
  expect(secondRow).not.toBeNull();
  await user.click(within(secondRow!).getByRole("button", { name: "Open" }));
  const drawer = await screen.findByRole("dialog");

  expect(
    within(drawer).queryByText("The first file changed on the server."),
  ).not.toBeInTheDocument();
  expect(screen.getByText("The first file changed on the server.")).toBeInTheDocument();
});

test("surfaces an earlier request failure that settles after a later action starts", async () => {
  const user = userEvent.setup();
  let markDecisionStarted!: () => void;
  let releaseDecision!: () => void;
  let markSplitStarted!: () => void;
  const decisionStarted = new Promise<void>((resolve) => {
    markDecisionStarted = resolve;
  });
  const decisionRelease = new Promise<void>((resolve) => {
    releaseDecision = resolve;
  });
  const splitStarted = new Promise<void>((resolve) => {
    markSplitStarted = resolve;
  });
  server.use(
    http.post("/api/v1/admin/imports/:id/files/:fid/decision", async () => {
      markDecisionStarted();
      await decisionRelease;
      return HttpResponse.json(
        {
          code: "decision_conflict",
          title: "Decision conflict",
          detail: "The overlapping file decision failed late.",
        },
        { status: 409 },
      );
    }),
    http.post("/api/v1/admin/imports/:id/split", () => {
      markSplitStarted();
      return HttpResponse.json({ ok: true });
    }),
  );
  renderCockpit();
  const filename = await screen.findByText("SOP-PUR-014 Purchasing.docx");
  const row = filename.closest("tr");
  expect(row).not.toBeNull();
  await user.click(within(row!).getByRole("button", { name: "Open" }));
  const drawer = await screen.findByRole("dialog");

  await user.click(within(drawer).getByRole("button", { name: "Accept item" }));
  await decisionStarted;
  await user.click(within(drawer).getByRole("button", { name: "Split out of group" }));
  await splitStarted;
  releaseDecision();

  expect(
    await within(drawer).findByText("The overlapping file decision failed late."),
  ).toBeInTheDocument();
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
