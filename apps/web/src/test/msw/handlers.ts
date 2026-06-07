import { http, HttpResponse } from "msw";

export const docFixture = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    identifier: "SOP-PUR-014",
    kind: "DOCUMENT",
    title: "Supplier Selection & Evaluation",
    document_type_id: "aaaa1111-1111-1111-1111-111111111111",
    area_code: "PUR",
    folder_path: "/SOPs/Purchasing",
    current_state: "Effective",
    classification: "Internal",
    is_singleton: false,
    owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
    framework_id: "cccc1111-1111-1111-1111-111111111111",
    current_effective_version_id: "dddd1111-1111-1111-1111-111111111111",
    effective_from: "2026-03-14T00:00:00+00:00",
    created_at: "2026-03-14T09:12:00+00:00",
    clause_refs: ["8.4"],
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    identifier: "SOP-PRD-007",
    kind: "DOCUMENT",
    title: "Production Control",
    document_type_id: "aaaa2222-2222-2222-2222-222222222222",
    area_code: "PRD",
    folder_path: "/SOPs/Production",
    current_state: "Draft",
    classification: "Internal",
    is_singleton: false,
    owner_user_id: "bbbb2222-2222-2222-2222-222222222222",
    framework_id: "cccc1111-1111-1111-1111-111111111111",
    current_effective_version_id: null,
    effective_from: null,
    created_at: "2026-05-29T11:00:00+00:00",
    clause_refs: ["8.5"],
  },
];

export const typeFixture = [
  {
    id: "aaaa1111-1111-1111-1111-111111111111",
    code: "SOP",
    name: "Procedure",
    document_level: "L2_PROCEDURE",
    is_singleton: false,
  },
  {
    id: "aaaa2222-2222-2222-2222-222222222222",
    code: "WI",
    name: "Work Instruction",
    document_level: "L3_WORK_INSTRUCTION",
    is_singleton: false,
  },
];

export const directoryFixture = [
  { id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Mara Quality" },
  { id: "bbbb2222-2222-2222-2222-222222222222", display_name: "Diego Owner" },
];

export const versionFixture = [
  {
    id: "dddd1111-1111-1111-1111-111111111111",
    document_id: "11111111-1111-1111-1111-111111111111",
    version_seq: 2,
    revision_label: "Rev B",
    version_state: "Effective",
    change_significance: "MAJOR",
    change_reason: "Added weighted scoring & conditional tier after audit AF-204",
    source_blob_sha256: "sha-b",
    metadata_snapshot: null,
    author_user_id: "bbbb1111-1111-1111-1111-111111111111",
    effective_from: "2026-03-14T00:00:00+00:00",
    effective_to: null,
    superseded_by_version_id: null,
    created_at: "2026-03-14T09:00:00+00:00",
  },
  {
    id: "eeee1111-1111-1111-1111-111111111111",
    document_id: "11111111-1111-1111-1111-111111111111",
    version_seq: 1,
    revision_label: "Rev A",
    version_state: "Superseded",
    change_significance: "MAJOR",
    change_reason: "Initial release",
    source_blob_sha256: "sha-a",
    metadata_snapshot: null,
    author_user_id: "bbbb2222-2222-2222-2222-222222222222",
    effective_from: "2025-01-01T00:00:00+00:00",
    effective_to: "2026-03-14T00:00:00+00:00",
    superseded_by_version_id: "dddd1111-1111-1111-1111-111111111111",
    created_at: "2025-01-01T09:00:00+00:00",
  },
];

export const whereUsedFixture = {
  document_id: "11111111-1111-1111-1111-111111111111",
  processes: [{ id: "p1", name: "Purchasing", state: "ACTIVE", is_active: true }],
  child_documents: [
    {
      link_id: "l1",
      link_type: "parent_of",
      direction: "outbound",
      document_id: "wi1",
      identifier: "WI-PUR-008",
      title: "Supplier Onboarding Work Instruction",
      current_state: "Effective",
      document_level: "L3_WORK_INSTRUCTION",
    },
  ],
  parent_documents: [],
  referenced_by: [],
  references_out: [],
  forms_templates: [
    {
      link_id: "l2",
      link_type: "references",
      direction: "outbound",
      document_id: "frm1",
      identifier: "FRM-PUR-009",
      title: "Supplier Evaluation Form",
      current_state: "Effective",
      document_level: "L4_FORM",
    },
  ],
  supersedes: [],
  superseded_by: [],
  records_produced_under: { count: 3, sample: [{ id: "r1", identifier: "REC-PUR-2026-001" }] },
  clauses: [{ number: "8.4", title: "Control of external providers", is_mandatory_star: true }],
  related_capas_findings: [],
  obsoletion_safety: { blocked: false, reasons: [] },
};

export const clauseFixture = [
  {
    id: "c4",
    framework_id: "f1",
    number: "4",
    parent_id: null,
    title: "Context of the organization",
    intent_text: "…",
    is_mandatory_star: false,
    pdca_phase: "PLAN",
    requirement_node: false,
  },
  {
    id: "c8",
    framework_id: "f1",
    number: "8",
    parent_id: null,
    title: "Operation",
    intent_text: "…",
    is_mandatory_star: false,
    pdca_phase: "DO",
    requirement_node: false,
  },
  {
    id: "c84",
    framework_id: "f1",
    number: "8.4",
    parent_id: "c8",
    title: "Control of external providers",
    intent_text: "…",
    is_mandatory_star: true,
    pdca_phase: "DO",
    requirement_node: true,
  },
  {
    id: "c9",
    framework_id: "f1",
    number: "9",
    parent_id: null,
    title: "Performance evaluation",
    intent_text: "…",
    is_mandatory_star: false,
    pdca_phase: "CHECK",
    requirement_node: false,
  },
  {
    id: "c10",
    framework_id: "f1",
    number: "10",
    parent_id: null,
    title: "Improvement",
    intent_text: "…",
    is_mandatory_star: false,
    pdca_phase: "ACT",
    requirement_node: false,
  },
];

// A filter + pagination-aware GET /documents so the library facet/pager tests are realistic.
function listDocuments({ request }: { request: Request }) {
  const sp = new URL(request.url).searchParams;
  let rows = docFixture;
  const state = sp.get("filter[current_state][eq]");
  if (state) rows = rows.filter((d) => d.current_state === state);
  const type = sp.get("filter[document_type][eq]");
  if (type) rows = rows.filter((d) => d.document_type_id === type);
  const owner = sp.get("filter[owner_user_id][eq]");
  if (owner) rows = rows.filter((d) => d.owner_user_id === owner);
  const clause = sp.get("filter[clause_refs][has]");
  if (clause) rows = rows.filter((d) => (d.clause_refs ?? []).includes(clause));
  const gte = sp.get("filter[effective_from][gte]");
  if (gte) rows = rows.filter((d) => d.effective_from !== null && d.effective_from >= gte);
  const limit = Number(sp.get("limit") ?? "50");
  const offset = Number(sp.get("offset") ?? "0");
  const pageRows = rows.slice(offset, offset + limit);
  return HttpResponse.json({
    data: pageRows,
    page: {
      limit,
      offset,
      returned: pageRows.length,
      has_more: rows.length > offset + pageRows.length,
    },
  });
}

export const handlers = [
  http.get("/api/v1/documents", listDocuments),
  http.get("/api/v1/document-types", () => HttpResponse.json(typeFixture)),
  http.get("/api/v1/directory/users", () => HttpResponse.json(directoryFixture)),
  http.get("/api/v1/documents/:id/versions", () => HttpResponse.json(versionFixture)),
  http.get("/api/v1/documents/:id/where-used", () => HttpResponse.json(whereUsedFixture)),
  http.get("/api/v1/documents/:id", ({ params }) => {
    const doc = docFixture.find((d) => d.id === params.id);
    return doc
      ? HttpResponse.json(doc)
      : HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 });
  }),
  http.get("/api/v1/clauses", () => HttpResponse.json(clauseFixture)),
  http.get("/api/v1/me", () =>
    HttpResponse.json({
      id: "bbbb1111-1111-1111-1111-111111111111",
      display_name: "Mara Quality",
      email: "mara@example.com",
      status: "ACTIVE",
    }),
  ),
  http.get("/api/v1/setup/state", () => HttpResponse.json({ setup_state: "OPERATIONAL" })),
  http.get("/api/v1/auth/config", () =>
    HttpResponse.json({
      issuer: "http://localhost/realms/easysynq",
      client_id: "easysynq-web",
      audience: "easysynq-api",
    }),
  ),
  http.get("/readyz", () => HttpResponse.json({ ready: true, dependencies: [] })),
];
