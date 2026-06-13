import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { DocumentSummary } from "../../lib/types";
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
  server.use(
    http.get("/api/v1/documents/:id", () => new HttpResponse(null, { status: 403 })),
  );

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
