import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import type { DcrDetail, DocumentVersion } from "../../lib/types";
import { RouteAnnouncement, RouteChromeProvider, useRouteChrome } from "../../lib/routeChrome";
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

function renderAt(id: string, search = "") {
  return renderWithProviders(
    <Routes>
      <Route path="/dcrs/:id/diff" element={<DcrDiffPage />} />
    </Routes>,
    { route: `/dcrs/${id}/diff${search}` },
  );
}

function DcrExternalModeNavigation() {
  useRouteChrome();
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate(`/dcrs/${DCR_DIFF_ID}/diff?mode=visual`)}>
        External visual mode
      </button>
      <button onClick={() => navigate(`/dcrs/${DCR_DIFF_ID}/diff`)}>External default mode</button>
      <button onClick={() => navigate(`/dcrs/${DCR_DIFF_ID}/diff?mode=unknown-sentinel`)}>
        External unknown mode
      </button>
      <main id="main-content" tabIndex={-1}>
        <RouteAnnouncement />
        <DcrDiffPage />
      </main>
    </>
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

it("follows mounted external DCR mode navigation without changing global route chrome", async () => {
  serveDcr(reviseImplemented);
  const user = userEvent.setup();
  renderWithProviders(
    <RouteChromeProvider>
      <Routes>
        <Route path="/dcrs/:id/diff" element={<DcrExternalModeNavigation />} />
      </Routes>
    </RouteChromeProvider>,
    { route: `/dcrs/${DCR_DIFF_ID}/diff` },
  );
  const main = document.getElementById("main-content");
  const expectNeutralChrome = () => {
    expect(document.title).toBe("EasySynQ — Document change request");
    expect(document.activeElement).not.toBe(main);
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
  };

  expect(await screen.findByText("Control-metadata changes")).toBeInTheDocument();
  expect(screen.getByLabelText("Diff mode")).toBeInTheDocument();
  expectNeutralChrome();

  await user.click(screen.getByRole("button", { name: "External visual mode" }));
  expect(await screen.findByText("Page images")).toBeInTheDocument();
  expect(screen.getByLabelText("Diff mode")).toBeInTheDocument();
  expectNeutralChrome();

  await user.click(screen.getByRole("button", { name: "External default mode" }));
  expect(await screen.findByText("Control-metadata changes")).toBeInTheDocument();
  expectNeutralChrome();

  await user.click(screen.getByRole("button", { name: "External unknown mode" }));
  expect(await screen.findByText("Control-metadata changes")).toBeInTheDocument();
  expect(screen.queryByText("unknown-sentinel")).not.toBeInTheDocument();
  expectNeutralChrome();
});

it("treats unknown and removed modes as text and follows live mode changes", async () => {
  serveDcr(reviseImplemented);
  const user = userEvent.setup();
  renderWithProviders(
    <Routes>
      <Route path="/dcrs/:id/diff" element={<DcrDiffPage />} />
    </Routes>,
    { route: `/dcrs/${DCR_DIFF_ID}/diff?mode=unknown-sentinel` },
  );
  expect(await screen.findByText("Control-metadata changes")).toBeInTheDocument();
  expect(screen.queryByText("unknown-sentinel")).not.toBeInTheDocument();
  await user.click(screen.getByText("Visual"));
  expect(await screen.findByText("Page images")).toBeInTheDocument();
  await user.click(screen.getByText("Text"));
  expect(await screen.findByText("Control-metadata changes")).toBeInTheDocument();
});

it.each(["mode=visual&mode=unknown-sentinel", "mode=unknown-sentinel&mode=visual"])(
  "conflicting duplicate DCR modes resolve content and chrome to text for %s",
  async (search) => {
    serveDcr(reviseImplemented);
    const { container } = renderWithProviders(
      <RouteChromeProvider>
        <Routes>
          <Route path="/dcrs/:id/diff" element={<DcrExternalModeNavigation />} />
        </Routes>
      </RouteChromeProvider>,
      { route: `/dcrs/${DCR_DIFF_ID}/diff?${search}` },
    );

    expect(await screen.findByText("Control-metadata changes")).toBeInTheDocument();
    expect(screen.queryByText("Page images")).not.toBeInTheDocument();
    expect(document.title).toBe("EasySynQ — Document change request");
    expect(screen.getByRole("status", { name: "Page navigation" })).toHaveTextContent("");
    expect(container).not.toHaveTextContent("unknown-sentinel");
  },
);

it("the mode control replaces history and removes the default text mode", async () => {
  serveDcr(reviseImplemented);
  const user = userEvent.setup();
  function DcrHistoryControls() {
    const navigate = useNavigate();
    return (
      <>
        <button
          onClick={() => navigate(`/dcrs/${DCR_DIFF_ID}/diff?sentinel=keep&checkpoint=prepared`)}
        >
          Prepare history
        </button>
        <button onClick={() => navigate(-1)}>Back</button>
      </>
    );
  }
  renderWithProviders(
    <>
      <Routes>
        <Route path="/dcrs/:id/diff" element={<DcrDiffPage />} />
      </Routes>
      <DcrHistoryControls />
      <LocationProbe />
    </>,
    { route: `/dcrs/${DCR_DIFF_ID}/diff?sentinel=keep&checkpoint=baseline` },
  );
  await screen.findByText("Control-metadata changes");
  await user.click(screen.getByRole("button", { name: "Prepare history" }));
  await user.click(screen.getByText("Visual"));
  expect(await screen.findByText("Page images")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).toHaveTextContent("mode=visual");
  await user.click(screen.getByText("Text"));
  expect(await screen.findByText("Control-metadata changes")).toBeInTheDocument();
  expect(screen.getByTestId("loc")).not.toHaveTextContent("mode=");
  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("checkpoint=baseline"));
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

it("calm-degrades to a no-access note when version authorization denies the viewer", async () => {
  serveDcr(reviseImplemented);
  server.use(
    http.get("/api/v1/documents/:id/versions", () =>
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
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
        } satisfies DocumentVersion,
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
