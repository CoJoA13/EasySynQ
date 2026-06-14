import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
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
