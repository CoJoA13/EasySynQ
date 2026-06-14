import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";
import type { DcrDetail } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { DcrDiffPage } from "./DcrDiffPage";

const DCR_DIFF_ID = "dcrdiff1-0001-0001-0001-000000000001";

// An Implemented REVISE whose target_document_id + resulting_version_id ALIGN with versionFixture
// (doc 11111111…, versions dddd1111 [seq 2] superseded-from eeee1111 [seq 1]) so the diff handlers
// (which ignore the path ids) serve a coherent pair. Pinned to DcrDetail.
const reviseImplemented = {
  id: DCR_DIFF_ID,
  identifier: "DCR-2026-0010",
  target_document_id: "11111111-1111-1111-1111-111111111111",
  change_type: "REVISE",
  change_significance: "MAJOR",
  reason_class: "audit_finding",
  reason_text: "Audit finding closed via revision.",
  source_link_type: "finding",
  source_link_id: "find0001-0001-0001-0001-000000000001",
  proposed_effective_from: "2026-07-01T00:00:00+00:00",
  resulting_version_id: "dddd1111-1111-1111-1111-111111111111",
  state: "Implemented",
  decision: "Approved by the change board.",
  created_by: "bbbb1111-1111-1111-1111-111111111111",
  created_at: "2026-05-01T09:00:00+00:00",
  stage_events: [],
  capabilities: { assess: false, route: false, implement: false, close: true },
} satisfies DcrDetail;

function serveDcr(dcr: DcrDetail) {
  server.use(http.get("/api/v1/dcrs/:id", () => HttpResponse.json(dcr)));
}

function renderAt(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/dcrs/:id/diff" element={<DcrDiffPage />} />
    </Routes>,
    { route: `/dcrs/${id}/diff` },
  );
}

it("renders the header and the text redline by default for a REVISE Implemented DCR", async () => {
  serveDcr(reviseImplemented);
  const { container } = renderAt(DCR_DIFF_ID);

  await waitFor(() => expect(screen.getByText("DCR-2026-0010")).toBeInTheDocument());
  expect(screen.getByLabelText("State: Implemented")).toBeInTheDocument();

  await waitFor(() => expect(screen.getByText("Control-metadata changes")).toBeInTheDocument());
  expect(screen.getByText("Text redline")).toBeInTheDocument();

  expect(await axe(container)).toHaveNoViolations();
});

it("toggles to the visual page-image diff", async () => {
  serveDcr(reviseImplemented);
  const user = userEvent.setup();
  renderAt(DCR_DIFF_ID);

  await screen.findByText("Control-metadata changes"); // Text mode first
  // Click the SegmentedControl option by its text label — the proven house pattern
  // (VisualDiffViewer.test clicks getByText("After") on its layer SegmentedControl).
  await user.click(screen.getByText("Visual"));
  await screen.findByAltText("Page 2 of 3 — Diff layer (changed)");
});

const CREATE_DCR = {
  ...reviseImplemented,
  change_type: "CREATE",
  target_document_id: null,
} satisfies DcrDetail;
const OPEN_REVISE = {
  ...reviseImplemented,
  state: "Open",
  resulting_version_id: null,
} satisfies DcrDetail;

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

it("shows a calm 'no visual diff' note for a non-REVISE change request", async () => {
  serveDcr(CREATE_DCR);
  renderAt(DCR_DIFF_ID);
  await waitFor(() =>
    expect(screen.getByText(/No visual diff for this change request/)).toBeInTheDocument(),
  );
});

it("shows a calm 'no visual diff' note before a REVISE is implemented (no resulting version)", async () => {
  serveDcr(OPEN_REVISE);
  renderAt(DCR_DIFF_ID);
  await waitFor(() =>
    expect(screen.getByText(/No visual diff for this change request/)).toBeInTheDocument(),
  );
});

it("calm-degrades to a no-access note when the viewer lacks document.read_draft on the target", async () => {
  serveDcr(reviseImplemented);
  server.use(
    http.get("/api/v1/documents/:id/versions", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderAt(DCR_DIFF_ID);
  await waitFor(() =>
    expect(
      screen.getByText("You don't have access to this document's versions."),
    ).toBeInTheDocument(),
  );
});

it("shows 'no prior version' when the resulting version has no predecessor", async () => {
  serveDcr(reviseImplemented);
  server.use(
    http.get("/api/v1/documents/:id/versions", () =>
      HttpResponse.json([
        {
          id: "dddd1111-1111-1111-1111-111111111111",
          document_id: "11111111-1111-1111-1111-111111111111",
          version_seq: 1,
          revision_label: "Rev A",
          version_state: "Effective",
          change_significance: "MAJOR",
          change_reason: "Initial release",
          source_blob_sha256: "sha",
          metadata_snapshot: null,
          author_user_id: "bbbb1111-1111-1111-1111-111111111111",
          effective_from: null,
          effective_to: null,
          superseded_by_version_id: null,
          created_at: null,
        },
      ]),
    ),
  );
  renderAt(DCR_DIFF_ID);
  await waitFor(() =>
    expect(screen.getByText("No prior version to compare against.")).toBeInTheDocument(),
  );
});

it("the back-link returns to the register with the DCR drawer re-opened", async () => {
  serveDcr(reviseImplemented);
  const user = userEvent.setup();
  renderWithProviders(
    <Routes>
      <Route path="/dcrs/:id/diff" element={<DcrDiffPage />} />
      <Route path="/dcrs" element={<LocationProbe />} />
    </Routes>,
    { route: `/dcrs/${DCR_DIFF_ID}/diff` },
  );
  await screen.findByText("DCR-2026-0010");
  await user.click(screen.getByRole("link", { name: /Back to change request/ }));
  await waitFor(() =>
    expect(screen.getByTestId("loc")).toHaveTextContent(`/dcrs?dcr=${DCR_DIFF_ID}`),
  );
});
