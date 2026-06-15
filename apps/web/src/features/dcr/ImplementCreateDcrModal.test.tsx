import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DocumentsPage, DocumentSummary, DocumentVersion } from "../../lib/types";
import { ImplementCreateDcrModal } from "./ImplementCreateDcrModal";

const DCR_ID = "dcr00001-0001-0001-0001-000000000001";
const DOC_ID = "doc00001-0001-0001-0001-000000000001";
const VER_ID = "ver00001-0001-0001-0001-000000000001";

// Pinned to the real _document serializer (apps/api/.../api/documents.py::_document) via
// `satisfies DocumentSummary` so strict tsc enforces the shape.
const candidate = {
  id: DOC_ID,
  identifier: "SOP-NEW-001",
  kind: "DOCUMENT",
  title: "New procedure",
  document_type_id: null,
  area_code: null,
  folder_path: null,
  current_state: "Approved",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "u1",
  framework_id: "f1",
  current_effective_version_id: null,
  effective_from: null,
  created_at: null,
  review_period_months: null,
  next_review_due: null,
  last_reviewed_at: null,
  review_state: null,
} satisfies DocumentSummary;

const docsPage: DocumentsPage = {
  data: [candidate],
  page: { limit: 100, offset: 0, returned: 1, has_more: false },
};

const versions: DocumentVersion[] = [
  {
    id: VER_ID,
    document_id: DOC_ID,
    version_seq: 1,
    revision_label: "1.0",
    version_state: "Approved",
    change_significance: "MINOR",
    change_reason: "initial",
    source_blob_sha256: "x",
    metadata_snapshot: null,
    author_user_id: "u1",
    effective_from: null,
    effective_to: null,
    superseded_by_version_id: null,
    created_at: null,
  },
];

function mockReads() {
  server.use(
    http.get("/api/v1/documents", () => HttpResponse.json(docsPage)),
    http.get(`/api/v1/documents/${DOC_ID}/versions`, () => HttpResponse.json(versions)),
  );
}

it("picks an approved document, resolves its Approved version, and POSTs resulting_version_id", async () => {
  mockReads();
  let body: unknown;
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/implement`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ id: DCR_ID, state: "Implemented" });
    }),
  );
  let closed = false;
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => (closed = true)} />);
  // Mantine v7 injects an aria-hidden " *" into a required-field label's textContent → match by regex
  // (the DcrRaiseFields/RaiseDcrModal precedent), not the exact "New document".
  await userEvent.click(await screen.findByLabelText(/New document/));
  await userEvent.click(await screen.findByText("SOP-NEW-001 — New procedure"));
  const submit = await screen.findByRole("button", { name: "Implement" });
  await waitFor(() => expect(submit).toBeEnabled());
  await userEvent.click(submit);
  await waitFor(() => expect(closed).toBe(true));
  expect(body).toEqual({ resulting_version_id: VER_ID });
});

it("shows an empty-candidate state when no approved documents exist", async () => {
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({ ...docsPage, data: [] } satisfies DocumentsPage),
    ),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  expect(
    await screen.findByText(/Author the new document in the workspace first/),
  ).toBeInTheDocument();
});

it("surfaces a release/SoD-2 403 calmly (submit-and-show)", async () => {
  mockReads();
  server.use(
    http.post(`/api/v1/dcrs/${DCR_ID}/implement`, () =>
      HttpResponse.json(
        {
          code: "sod_violation",
          detail: "You authored this version; another approver must release it.",
        },
        { status: 403 },
      ),
    ),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  await userEvent.click(await screen.findByLabelText(/New document/));
  await userEvent.click(await screen.findByText("SOP-NEW-001 — New procedure"));
  const submit = await screen.findByRole("button", { name: "Implement" });
  await waitFor(() => expect(submit).toBeEnabled());
  await userEvent.click(submit);
  expect(await screen.findByText(/another approver must release it/)).toBeInTheDocument();
});

it("has no axe violations", async () => {
  mockReads();
  const { container } = renderWithProviders(
    <ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />,
  );
  // Let the documents query settle (the Select renders) before auditing — drains the pending state
  // inside act (the DcrsRegisterPage axe precedent).
  await screen.findByLabelText(/New document/);
  expect(await axe(container)).toHaveNoViolations();
});

it("sends the two narrowing filters to GET /documents", async () => {
  // S-doc-filters: the candidate narrowing is server-side now — the modal must ASK for it. ⚠ the
  // false-emit case (filter[...][eq]=false) is the load-bearing one (the picker sends false).
  let url: URL | undefined;
  server.use(
    http.get("/api/v1/documents", ({ request }) => {
      url = new URL(request.url);
      return HttpResponse.json(docsPage);
    }),
    http.get(`/api/v1/documents/${DOC_ID}/versions`, () => HttpResponse.json(versions)),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  await screen.findByLabelText(/New document/);
  expect(url).toBeDefined();
  expect(url!.searchParams.get("filter[current_state][eq]")).toBe("Approved");
  expect(url!.searchParams.get("filter[has_effective_version][eq]")).toBe("false");
  expect(url!.searchParams.get("filter[managed_subtype][eq]")).toBe("false");
});

it("renders every server-returned candidate with NO client filtering", async () => {
  // S-doc-filters: the client-side .filter (current_effective_version_id===null + OBJ/MR exclusion)
  // is GONE — the server is trusted to have narrowed. Include a doc that the OLD client filter would
  // have dropped (current_effective_version_id non-null) and assert it APPEARS now, proving the
  // client no longer filters. (In production the server would not return such a row; here it stands
  // in for "the server decides, the client trusts".)
  const wouldHaveBeenFiltered = {
    ...candidate,
    id: "doc00002-0002-0002-0002-000000000002",
    identifier: "SOP-OLD-002",
    title: "Previously filtered",
    current_effective_version_id: "eff00001-0001-0001-0001-000000000001",
  } satisfies DocumentSummary;
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        ...docsPage,
        data: [candidate, wouldHaveBeenFiltered],
        page: { limit: 100, offset: 0, returned: 2, has_more: false },
      } satisfies DocumentsPage),
    ),
    http.get(`/api/v1/documents/${DOC_ID}/versions`, () => HttpResponse.json(versions)),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  await userEvent.click(await screen.findByLabelText(/New document/));
  expect(await screen.findByText("SOP-NEW-001 — New procedure")).toBeInTheDocument();
  expect(screen.getByText("SOP-OLD-002 — Previously filtered")).toBeInTheDocument();
});

it("surfaces a calm error when the version fetch is forbidden (releaser lacks draft access)", async () => {
  // Codex P2: a release-capable user without document.read_draft 403s on the version fetch; surface it
  // instead of leaving a silently-disabled button.
  server.use(
    http.get("/api/v1/documents", () => HttpResponse.json(docsPage)),
    http.get(`/api/v1/documents/${DOC_ID}/versions`, () => new HttpResponse(null, { status: 403 })),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  await userEvent.click(await screen.findByLabelText(/New document/));
  await userEvent.click(await screen.findByText("SOP-NEW-001 — New procedure"));
  expect(await screen.findByText(/needs draft-read access/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Implement" })).toBeDisabled();
});
