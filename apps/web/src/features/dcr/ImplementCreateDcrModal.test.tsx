import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DocumentsPage, DocumentVersion } from "../../lib/types";
import { ImplementCreateDcrModal } from "./ImplementCreateDcrModal";

const DCR_ID = "dcr00001-0001-0001-0001-000000000001";
const DOC_ID = "doc00001-0001-0001-0001-000000000001";
const VER_ID = "ver00001-0001-0001-0001-000000000001";

const docsPage: DocumentsPage = {
  data: [
    {
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
    },
  ],
  page: { limit: 200, offset: 0, returned: 1, has_more: false },
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

it("excludes an approved REVISION of an existing document (current_effective_version_id set)", async () => {
  // Codex P1: an approved revision sits at current_state Approved but carries an effective version —
  // a CREATE DCR must only release the INITIAL version of a NEW document.
  server.use(
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        ...docsPage,
        data: [
          {
            ...docsPage.data[0]!,
            current_effective_version_id: "eff00001-0001-0001-0001-000000000001",
          },
        ],
      } satisfies DocumentsPage),
    ),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  expect(
    await screen.findByText(/Author the new document in the workspace first/),
  ).toBeInTheDocument();
  expect(screen.queryByText("SOP-NEW-001 — New procedure")).not.toBeInTheDocument();
});

it("excludes managed subtypes (Quality Objective / Management Review) from the picker", async () => {
  // Codex P2: OBJ/MR are managed subtypes with their own create flows — never CREATE-DCR targets.
  const OBJ_TYPE = "objtype1-0001-0001-0001-000000000001";
  server.use(
    http.get("/api/v1/document-types", () =>
      HttpResponse.json([
        {
          id: OBJ_TYPE,
          code: "OBJ",
          name: "Quality Objective",
          document_level: "L3",
          is_singleton: false,
        },
      ]),
    ),
    http.get("/api/v1/documents", () =>
      HttpResponse.json({
        ...docsPage,
        data: [{ ...docsPage.data[0]!, document_type_id: OBJ_TYPE }],
      } satisfies DocumentsPage),
    ),
  );
  renderWithProviders(<ImplementCreateDcrModal dcrId={DCR_ID} onClose={() => {}} />);
  expect(
    await screen.findByText(/Author the new document in the workspace first/),
  ).toBeInTheDocument();
  expect(screen.queryByText("SOP-NEW-001 — New procedure")).not.toBeInTheDocument();
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
