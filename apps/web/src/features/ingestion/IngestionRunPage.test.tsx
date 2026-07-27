import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, test } from "vitest";
import { Route, Routes, useNavigate, useParams } from "react-router-dom";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ingestionFileDetailFixture, ingestionRunFixture } from "../../test/msw/handlers";
import { IngestionRunPage } from "./IngestionRunPage";

const RID = ingestionRunFixture.id;
const SECOND_RID = "b0000000-0000-0000-0000-000000000002";

function renderPage(route = `/ingestion/${RID}`) {
  return renderWithProviders(
    <Routes>
      <Route path="ingestion/:runId" element={<IngestionRunPage />} />
    </Routes>,
    { route },
  );
}

function RunNavigationHarness() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate(`/ingestion/${RID}`)}>
        Go to run A
      </button>
      <button type="button" onClick={() => navigate(`/ingestion/${SECOND_RID}`)}>
        Go to run B
      </button>
      <Routes>
        <Route path="ingestion/:runId" element={<RunRoute />} />
      </Routes>
    </>
  );
}

function RunRoute() {
  const { runId } = useParams();
  return (
    <>
      <span data-testid="active-run">{runId}</span>
      <IngestionRunPage />
    </>
  );
}

test("IngestionRunPage shows a loader before the run resolves", () => {
  renderPage();
  expect(screen.getByLabelText("Loading import run")).toBeInTheDocument();
});

test("IngestionRunPage renders the review cockpit for a Proposed run", async () => {
  renderPage();
  // a cockpit-only affordance: the queue tablist (QueueTabs, Task 7)
  expect(await screen.findByRole("tab", { name: /Needs decision/ })).toBeInTheDocument();
});

test("switching between cached runs remounts the cockpit and clears its prior failure", async () => {
  const user = userEvent.setup();
  const fetchedRuns: string[] = [];
  server.use(
    http.get("/api/v1/admin/imports/:id", ({ params }) => {
      const id = String(params.id);
      fetchedRuns.push(id);
      return HttpResponse.json({ ...ingestionRunFixture, id, status: "Proposed" });
    }),
    http.post("/api/v1/admin/imports/:id/decisions", () =>
      HttpResponse.json(
        {
          code: "bulk_conflict",
          title: "Bulk conflict",
          detail: "Run A's selected review set is stale.",
        },
        { status: 409 },
      ),
    ),
  );
  renderWithProviders(<RunNavigationHarness />, { route: `/ingestion/${RID}` });
  await screen.findByRole("tab", { name: /Needs decision/ });

  // Visit both runs once so their query results are cached; the second A → B switch should keep the
  // same IngestionRunPage tree mounted and exercise ReviewCockpit's run key directly.
  await user.click(screen.getByRole("button", { name: "Go to run B" }));
  await waitFor(() => expect(fetchedRuns).toContain(SECOND_RID));
  await screen.findByRole("tab", { name: /Needs decision/ });
  await user.click(screen.getByRole("button", { name: "Go to run A" }));
  await waitFor(() => expect(screen.getByTestId("active-run")).toHaveTextContent(RID));

  await user.click(screen.getByLabelText("Select SOP-PUR-014 Purchasing.docx"));
  await user.click(screen.getByRole("button", { name: "Bulk accept all High" }));
  expect(await screen.findByText("Run A's selected review set is stale.")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Go to run B" }));
  await waitFor(() => expect(screen.getByTestId("active-run")).toHaveTextContent(SECOND_RID));
  expect(screen.queryByText("Run A's selected review set is stale.")).not.toBeInTheDocument();
});

test("IngestionRunPage renders the commit-progress face for a Committing run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Committing" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Committing to the vault/)).toBeInTheDocument();
});

test("IngestionRunPage renders the scan-progress face for a pre-Proposed run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Scanning" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Scanning the source/)).toBeInTheDocument();
});

test("an unknown/additive status degrades calmly to the scan-progress face (invariant 6)", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "SomeFutureStage" }),
    ),
  );
  renderPage();
  // the default switch branch routes an unknown status to ScanProgress, which shows the generic
  // "Working…" in both the heading and the caption (Task 16 fix) rather than crashing or going blank.
  expect((await screen.findAllByText(/Working…/)).length).toBeGreaterThan(0);
});

test("IngestionRunPage renders the terminal summary for a Completed run", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ ...ingestionRunFixture, status: "Completed" }),
    ),
  );
  renderPage();
  expect(await screen.findByText(/Import complete/)).toBeInTheDocument();
});

test("a PartiallyCommitted run shows a Resume button that re-POSTs commit", async () => {
  let resumed = false;
  server.use(
    // Resume is gated on import.commit — grant it so the button renders (else onResume is undefined).
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.commit", effect: "ALLOW", source: "role" }],
      }),
    ),
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({
        ...ingestionRunFixture,
        status: "PartiallyCommitted",
        counts: { commit: { committed: 3, failed: 1 } },
      }),
    ),
    http.post("/api/v1/admin/imports/:id/commit", () => {
      resumed = true;
      return HttpResponse.json({ ...ingestionRunFixture, status: "Committing" }, { status: 202 });
    }),
  );
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: "Resume commit" }));
  await waitFor(() => expect(resumed).toBe(true));
});

test("a blocked partial resume exposes owner and identifier corrections, then retries", async () => {
  const user = userEvent.setup();
  const affectedFileId = ingestionFileDetailFixture.id;
  let commitAttempts = 0;
  let correctedFileId = "";
  let correctionBody: Record<string, unknown> | null = null;

  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.commit", effect: "ALLOW", source: "role" }],
      }),
    ),
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({
        ...ingestionRunFixture,
        status: commitAttempts >= 2 ? "Committing" : "PartiallyCommitted",
        counts: { commit: { committed: 3, failed: 1 } },
      }),
    ),
    http.post("/api/v1/admin/imports/:id/commit", () => {
      commitAttempts += 1;
      if (commitAttempts === 1) {
        return HttpResponse.json(
          {
            code: "commit_blocked",
            title: "Resolve the blocking conflicts before committing",
            status: 422,
            blocking: [
              {
                type: "owner_not_found",
                owner: "Quality Manager",
                file_id: affectedFileId,
              },
              {
                type: "blank_identifier",
                identifier: " ",
                file_id: affectedFileId,
              },
            ],
          },
          { status: 422 },
        );
      }
      return HttpResponse.json({ ...ingestionRunFixture, status: "Committing" }, { status: 202 });
    }),
    http.get("/api/v1/admin/imports/:id/files/:fid", () =>
      HttpResponse.json({
        ...ingestionFileDetailFixture,
        review: {
          effective: {
            ...(ingestionFileDetailFixture.review.effective as unknown as Record<string, unknown>),
            identifier: " ",
            owner: "Quality Manager",
            kind: "DOCUMENT",
            commit_ready: true,
          },
          decision_history: [],
        },
        commit: {
          result: "failed",
          vault_document_id: null,
          vault_version_id: null,
          error: "owner_not_found",
          committed_at: "2026-07-27T14:40:00Z",
        },
      }),
    ),
    http.post("/api/v1/admin/imports/:id/files/:fid/decision", async ({ params, request }) => {
      correctedFileId = String(params.fid);
      correctionBody = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ok: true });
    }),
  );

  const { container } = renderPage();
  await user.click(await screen.findByRole("button", { name: "Resume commit" }));

  expect(await screen.findByText("Resume needs corrections")).toBeInTheDocument();
  expect(screen.getByText("Resolve the blocking conflicts before committing")).toBeInTheDocument();
  const identifierInput = await screen.findByLabelText(
    "New identifier for SOP-PUR-014 Purchasing.docx",
  );
  expect(await axe(container)).toHaveNoViolations();
  await user.type(identifierInput, "SOP-PUR-014-REPAIRED");
  const [ownerInput] = screen.getAllByLabelText("New owner for SOP-PUR-014 Purchasing.docx");
  await user.click(ownerInput!);
  await user.click(await screen.findByRole("option", { name: /Mara Quality/ }));
  await user.click(
    screen.getByRole("button", {
      name: "Save correction for SOP-PUR-014 Purchasing.docx",
    }),
  );

  await waitFor(() => expect(correctionBody).not.toBeNull());
  expect(correctedFileId).toBe(affectedFileId);
  expect(correctionBody).toEqual({
    action: "correct",
    after: {
      identifier: "SOP-PUR-014-REPAIRED",
      owner: "bbbb1111-1111-1111-1111-111111111111",
    },
  });
  expect(await screen.findByText("Correction saved")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Resume commit" }));
  await waitFor(() => expect(commitAttempts).toBe(2));
  expect(await screen.findByText(/Committing to the vault/)).toBeInTheDocument();
});

test("a PartiallyCommitted run hides Resume without import.commit", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({
        ...ingestionRunFixture,
        status: "PartiallyCommitted",
        counts: { commit: { committed: 3, failed: 1 } },
      }),
    ),
  );
  renderPage();
  // the partial heading still renders; the gated Resume affordance does not (default no permissions).
  await screen.findByText("Import partially committed");
  expect(screen.queryByRole("button", { name: "Resume commit" })).not.toBeInTheDocument();
});

test("IngestionRunPage shows a calm not-found panel on a 404", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("Import run not found.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Back to imports/ })).toBeInTheDocument();
});

test("IngestionRunPage shows a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderPage();
  expect(await screen.findByText("You don't have access to import review.")).toBeInTheDocument();
});

test("IngestionRunPage has no a11y violations (cockpit)", async () => {
  const { container } = renderPage();
  await screen.findByRole("tab", { name: /Needs decision/ });
  expect(await axe(container)).toHaveNoViolations();
});

test("IngestionRunPage has no a11y violations (404)", async () => {
  server.use(
    http.get("/api/v1/admin/imports/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  const { container } = renderPage();
  await screen.findByText("Import run not found.");
  expect(await axe(container)).toHaveNoViolations();
});
