import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { DcrDetail, DcrImpactList, DcrState, DocumentSummary } from "../../lib/types";
import { DCR_REVISE_ID } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { DcrDrawer } from "./DcrDrawer";

// A fully-typed DocumentSummary for the resolved-target assertions (the default /documents/:id
// handler returns a different fixture, so we override it to a known identifier/title).
const targetDoc: DocumentSummary = {
  id: "doc00001-0001-0001-0001-000000000001",
  identifier: "SOP-001",
  kind: "DOCUMENT",
  title: "Calibration procedure",
  document_type_id: "aaaa1111-1111-1111-1111-111111111111",
  area_code: "CAL",
  folder_path: "/SOPs/Calibration",
  current_state: "Effective",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
  framework_id: "cccc1111-1111-1111-1111-111111111111",
  current_effective_version_id: "ver00099-0099-0099-0099-000000000099",
  effective_from: "2026-03-01T00:00:00+00:00",
  created_at: "2026-03-01T09:00:00+00:00",
  review_period_months: null,
  next_review_due: null,
  last_reviewed_at: null,
  review_state: null,
};

it("renders the DCR header, badges, deep-links, impact and timeline (happy path)", async () => {
  server.use(
    http.get("/api/v1/documents/:id", () => HttpResponse.json(targetDoc)),
    http.get("/api/v1/directory/users", () =>
      HttpResponse.json([
        { id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Priya Author" },
      ]),
    ),
  );

  const screen = renderWithProviders(<DcrDrawer dcrId={DCR_REVISE_ID} onClose={() => {}} />);

  // Identifier + state badge.
  await screen.findByText("DCR-2026-0001");
  expect(screen.getByLabelText("State: Open")).toBeInTheDocument();

  // Reason text.
  expect(screen.getByText("Corrective action requires a procedure revision.")).toBeInTheDocument();

  // Target document deep-link resolves identifier + title and points at /documents/.
  const targetLink = await screen.findByRole("link", { name: /SOP-001/ });
  expect(targetLink.getAttribute("href")).toContain("/documents/");

  // Source CAPA deep-link → /capa?capa=.
  const sourceLink = screen.getByRole("link", { name: "CAPA" });
  expect(sourceLink.getAttribute("href")).toContain("/capa?capa=");

  // Impact (processes dimension, applicable, 2 processes) and the timeline comment.
  expect(await screen.findByText("Applicable · 2 processes")).toBeInTheDocument();
  expect(await screen.findByText("Change request raised.")).toBeInTheDocument();
});

it("degrades calmly when the target document can't be resolved (403)", async () => {
  server.use(http.get("/api/v1/documents/:id", () => new HttpResponse(null, { status: 403 })));

  const screen = renderWithProviders(<DcrDrawer dcrId={DCR_REVISE_ID} onClose={() => {}} />);

  // The target link still renders, falling back to the bare target_document_id (no crash).
  const targetLink = await screen.findByRole("link", { name: /doc00001/ });
  expect(targetLink.getAttribute("href")).toContain("/documents/doc00001");
});

it("renders nothing detail-related when dcrId is null (drawer closed, query disabled)", () => {
  // A closed Mantine Drawer (opened=false) unmounts its title/body, so the fallback "Change
  // request" title only shows once opened — the meaningful null-state invariant is that the detail
  // query is disabled, so the DCR identifier never appears.
  const screen = renderWithProviders(<DcrDrawer dcrId={null} onClose={() => {}} />);

  expect(screen.queryByText("DCR-2026-0001")).not.toBeInTheDocument();
});

it("shows the error body when the DCR fails to load (404)", async () => {
  server.use(http.get("/api/v1/dcrs/:id", () => new HttpResponse(null, { status: 404 })));

  const screen = renderWithProviders(<DcrDrawer dcrId={DCR_REVISE_ID} onClose={() => {}} />);

  expect(await screen.findByText("Couldn't load this change request")).toBeInTheDocument();
});

// ---- S-dcr-ui-3: the "View visual diff" link in the Resulting-version field ----
const DIFF_DCR_ID = "dcrdraw1-0001-0001-0001-000000000009";
function diffBase(): DcrDetail {
  return {
    id: DIFF_DCR_ID,
    identifier: "DCR-2026-0009",
    target_document_id: "11111111-1111-1111-1111-111111111111",
    change_type: "REVISE",
    change_significance: "MAJOR",
    reason_class: "audit_finding",
    reason_text: "Revision needed.",
    source_link_type: null,
    source_link_id: null,
    proposed_effective_from: null,
    resulting_version_id: "dddd1111-1111-1111-1111-111111111111",
    state: "Implemented",
    decision: null,
    created_by: "bbbb1111-1111-1111-1111-111111111111",
    created_at: "2026-05-01T09:00:00+00:00",
    stage_events: [],
    capabilities: { assess: false, route: false, implement: false, close: true },
  };
}
function serveDiffDcr(dcr: DcrDetail) {
  server.use(http.get("/api/v1/dcrs/:id", () => HttpResponse.json(dcr)));
}

it("shows a 'View visual diff' link for an implemented REVISE", async () => {
  serveDiffDcr(diffBase());
  const screen = renderWithProviders(<DcrDrawer dcrId={DIFF_DCR_ID} onClose={() => {}} />);
  const link = await screen.findByRole("link", { name: /View visual diff/ });
  expect(link.getAttribute("href")).toContain(`/dcrs/${DIFF_DCR_ID}/diff`);
});

it("hides the visual-diff link for a CREATE change request (no target document)", async () => {
  serveDiffDcr({ ...diffBase(), change_type: "CREATE", target_document_id: null });
  const screen = renderWithProviders(<DcrDrawer dcrId={DIFF_DCR_ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0009"); // drawer loaded
  expect(screen.queryByRole("link", { name: /View visual diff/ })).not.toBeInTheDocument();
});

it("hides the visual-diff link for a RETIRE change request (no resulting version)", async () => {
  serveDiffDcr({ ...diffBase(), change_type: "RETIRE", resulting_version_id: null });
  const screen = renderWithProviders(<DcrDrawer dcrId={DIFF_DCR_ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0009");
  expect(screen.queryByRole("link", { name: /View visual diff/ })).not.toBeInTheDocument();
});

// ---- impact-annotation editable gating ----
// Gated on the server-computed, PROCESS-scoped `capabilities.assess` (the ui-2b cockpit precedent —
// DcrAdvancePanel gates on the same flag), NOT a SYSTEM-scoped can(). So the fixture's capability
// flag — not /me/permissions — drives the editable affordance.
const ANNO_ID = "dcr00077-0077-0077-0077-000000000077";
function annoDcr(state: DcrState, assess = true): DcrDetail {
  return {
    id: ANNO_ID,
    identifier: "DCR-2026-0077",
    target_document_id: "11111111-1111-1111-1111-111111111111",
    change_type: "REVISE",
    change_significance: "MAJOR",
    reason_class: "audit_finding",
    reason_text: "Revision.",
    source_link_type: null,
    source_link_id: null,
    proposed_effective_from: null,
    resulting_version_id: null,
    state,
    decision: null,
    created_by: "bbbb1111-1111-1111-1111-111111111111",
    created_at: "2026-05-01T09:00:00+00:00",
    stage_events: [],
    capabilities: { assess, route: false, implement: false, close: false },
  } satisfies DcrDetail;
}
const annoImpact = {
  data: [
    {
      id: "ai1",
      dimension: "affected_processes",
      auto_populated: { applicable: true, processes: ["p1"] },
      requester_annotation: "x",
      created_at: "2026-06-10T10:00:00+00:00",
      updated_at: null,
    },
  ],
} satisfies DcrImpactList;
function serveAnno(state: DcrState, assess = true) {
  // ⚠ /dcrs/:id/impact MUST be registered before /dcrs/:id (the shared-handlers convention — else MSW
  // can match "impact" as the :id and the impact query gets a DCR-detail response instead of {data}).
  server.use(
    http.get("/api/v1/dcrs/:id/impact", () => HttpResponse.json(annoImpact)),
    http.get("/api/v1/dcrs/:id", () => HttpResponse.json(annoDcr(state, assess))),
  );
}

it("shows the editable annotation column for an Assessed DCR with the assess capability", async () => {
  serveAnno("Assessed");
  const screen = renderWithProviders(<DcrDrawer dcrId={ANNO_ID} onClose={() => {}} />);
  expect(await screen.findByLabelText("Annotation for affected_processes")).toBeInTheDocument();
});

it("keeps the annotation column read-only without the assess capability", async () => {
  serveAnno("Assessed", false);
  const screen = renderWithProviders(<DcrDrawer dcrId={ANNO_ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0077");
  expect(screen.queryByLabelText("Annotation for affected_processes")).not.toBeInTheDocument();
});

it("keeps the annotation column read-only in a terminal state even with the capability", async () => {
  serveAnno("Closed");
  const screen = renderWithProviders(<DcrDrawer dcrId={ANNO_ID} onClose={() => {}} />);
  await screen.findByText("DCR-2026-0077");
  expect(screen.queryByLabelText("Annotation for affected_processes")).not.toBeInTheDocument();
});
