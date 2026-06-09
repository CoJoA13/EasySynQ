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

// S-web-4: the default per-document capabilities (detail-only). All-false except read_draft → the
// page renders READ-ONLY (no author actions) but can load history/diff. Author tests override this.
export const detailCapabilities = {
  checkout: false,
  edit: false,
  manage_metadata: false,
  submit: false,
  release: false,
  obsolete: false,
  read_draft: true,
};

// S-web-4: GET /documents/{id}/versions/{vid}/diff?from={vid2} — Rev A → Rev B (doc 05 §8).
export const diffFixture = {
  document_id: "11111111-1111-1111-1111-111111111111",
  from: {
    version_id: "eeee1111-1111-1111-1111-111111111111",
    version_seq: 1,
    revision_label: "Rev A",
    version_state: "Superseded",
    change_significance: "MAJOR",
    change_reason: "Initial release",
    effective_from: "2025-01-01T00:00:00+00:00",
    effective_to: "2026-03-14T00:00:00+00:00",
    author_user_id: "bbbb2222-2222-2222-2222-222222222222",
    created_at: "2025-01-01T09:00:00+00:00",
    signatures: [],
  },
  to: {
    version_id: "dddd1111-1111-1111-1111-111111111111",
    version_seq: 2,
    revision_label: "Rev B",
    version_state: "Effective",
    change_significance: "MAJOR",
    change_reason: "Added weighted scoring & conditional tier after audit AF-204",
    effective_from: "2026-03-14T00:00:00+00:00",
    effective_to: null,
    author_user_id: "bbbb1111-1111-1111-1111-111111111111",
    created_at: "2026-03-14T09:00:00+00:00",
    signatures: [
      {
        meaning: "release",
        signer_user_id: "bbbb1111-1111-1111-1111-111111111111",
        signed_at: "2026-03-14T09:00:00+00:00",
      },
    ],
  },
  metadata_diff: [
    {
      field: "title",
      from: "Supplier Selection",
      to: "Supplier Selection & Evaluation",
      changed: true,
    },
    { field: "classification", from: "Internal", to: "Internal", changed: false },
  ],
  text_diff: {
    status: "ok",
    hunks: [
      { op: "equal", text: "1. Purpose & Scope" },
      { op: "delete", text: "Suppliers are scored on quality history." },
      {
        op: "insert",
        text: "Suppliers are scored on quality history, capacity and certification.",
      },
      { op: "insert", text: "A weighted score >= 70 is required for the Approved tier." },
    ],
  },
};

// S-web-4b: GET/POST …/visual-diff → a Ready status. Page 0 unchanged, 1 & 2 changed (0-based).
export const visualDiffFixture = {
  status: "Ready",
  page_count: 3,
  reason: null,
  pages: [
    { page: 0, changed: false },
    { page: 1, changed: true },
    { page: 2, changed: true },
  ],
};

// A valid 1×1 PNG — the page sub-endpoint streams image/png; tests assert the authed fetch + the
// <img> alt, not the pixels (jsdom can't decode), so any real PNG body suffices.
export const PNG_1x1 = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1f, 0x15, 0xc4,
  0x89, 0x00, 0x00, 0x00, 0x0a, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0x00, 0x01, 0x00, 0x00,
  0x05, 0x00, 0x01, 0x0d, 0x0a, 0x2d, 0xb4, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae,
  0x42, 0x60, 0x82,
]);

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

// ---- S-web-3 authoring fixtures -------------------------------------------------------
export const createdDocFixture = {
  id: "33333333-3333-3333-3333-333333333333",
  identifier: "SOP-GEN-001",
  kind: "DOCUMENT",
  title: "New Draft",
  document_type_id: "aaaa1111-1111-1111-1111-111111111111",
  area_code: "GEN",
  folder_path: null,
  current_state: "Draft",
  classification: "Internal",
  is_singleton: false,
  owner_user_id: "bbbb1111-1111-1111-1111-111111111111",
  framework_id: "cccc1111-1111-1111-1111-111111111111",
  current_effective_version_id: null,
  effective_from: null,
  created_at: "2026-06-07T10:00:00+00:00",
};

function mkVersion(documentId: string) {
  return {
    id: "ver-1",
    document_id: documentId,
    version_seq: 1,
    revision_label: "Rev A",
    version_state: "Draft",
    change_significance: "MAJOR",
    change_reason: "Initial version",
    source_blob_sha256: "sha-new",
    metadata_snapshot: null,
    author_user_id: "bbbb1111-1111-1111-1111-111111111111",
    effective_from: null,
    effective_to: null,
    superseded_by_version_id: null,
    created_at: "2026-06-07T10:05:00+00:00",
    change_detected: true,
  };
}

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

// S-web-5: the document's approval cycle (GET /documents/{id}/approval + GET /workflow-instances/{id}).
// candidate_pool contains TEST_AUTH.sub (bbbb1111…) so the candidate-gated affordances render in tests.
const approveTask = {
  id: "task1111-1111-1111-1111-111111111111",
  instance_id: "wf111111-1111-1111-1111-111111111111",
  stage_key: "quality_approval",
  type: "APPROVE",
  state: "PENDING",
  assignee_user_id: null,
  candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
  action_expected: "approve",
  due_at: null,
};
export const approvalFixture = {
  id: "wf111111-1111-1111-1111-111111111111",
  definition_id: "def11111-1111-1111-1111-111111111111",
  definition_version: 1,
  subject_type: "DOCUMENT",
  subject_id: "11111111-1111-1111-1111-111111111111",
  current_state: "IN_APPROVAL",
  started_at: "2026-06-08T09:00:00+00:00",
  revision: 0,
  tasks: [approveTask],
};
export const taskFixture = approvalFixture.tasks;

// ---- S-web-6 search + compliance fixtures ----
export const searchFixture = {
  query: "supplier",
  results: [
    {
      type: "document",
      id: "11111111-1111-1111-1111-111111111111",
      identifier: "SOP-PUR-014",
      title: "Supplier Selection & Evaluation",
      current_state: "Effective",
      clause_refs: ["8.4"],
      snippet: "…<b>Supplier</b> Selection & Evaluation SOP-PUR-014",
      rank: 0.61,
    },
  ],
  hidden_by_scope: 2,
};

export const suggestFixture = {
  suggestions: [
    {
      id: "11111111-1111-1111-1111-111111111111",
      identifier: "SOP-PUR-014",
      title: "Supplier Selection & Evaluation",
    },
    {
      id: "22222222-2222-2222-2222-222222222222",
      identifier: "SOP-PRD-007",
      title: "Production Control",
    },
  ],
};

export const complianceFixture = {
  framework: "iso9001:2015",
  rollup: { total: 3, covered: 1, partial: 1, gap: 1 },
  rows: [
    { clause_id: "c43", number: "4.3", title: "Scope of the QMS", pdca_phase: "PLAN", mapped_count: 1, effective_count: 1, status: "COVERED" },
    { clause_id: "c62", number: "6.2", title: "Quality objectives", pdca_phase: "PLAN", mapped_count: 1, effective_count: 0, status: "PARTIAL" },
    { clause_id: "c84", number: "8.4", title: "External providers", pdca_phase: "DO", mapped_count: 0, effective_count: 0, status: "GAP" },
  ],
};

// ---- S-ing-4b ingestion fixtures (a tiny Proposed run spanning the row states) ----
export const ingestionRunFixture = {
  id: "10000000-0000-0000-0000-000000000001",
  status: "Proposed",
  source_root: "/srv/import/legacy-qms-share",
  profile: null,
  ocr_enabled: true,
  classifier_version: "rules-heuristic v1.4",
  // The REAL run.counts shape: a flat, top-level-merged bag of per-stage keys (build_summary +
  // classify {classified,by_kind,by_band,extract} + dedup + proposal + commit). There is NO
  // `queues`/`classify`/`review` namespace — the folded review stats live on the checklist endpoint
  // (ingestionChecklistFixture.review).
  counts: {
    total_files: 6,
    total_bytes: 102400,
    included: 4,
    excluded: 0,
    quarantine: 1,
    ext_histogram: { docx: 3, pdf: 1, xlsx: 1, bin: 1 },
    exact_dup_clusters: 0,
    exact_dup_files: 0,
    classified: 4,
    by_kind: { DOCUMENT: 3, RECORD: 0, UNKNOWN: 1 },
    by_band: { HIGH: 2, MEDIUM: 1, LOW: 1, AMBIGUOUS: 0 },
    extract: { extracted: 4, ocr: 0, empty: 0, failed: 0 },
    dedup: { exact_clusters: 0, near_clusters: 1, version_families: 1 },
    proposal: { keep_items: 4, conflicts: 1, needs_identifier: 1 },
  },
  error: null,
  created_by: "bbbb1111-1111-1111-1111-111111111111",
  committed_by: null,
  report_record_id: null,
  created_at: "2026-06-08T10:00:00+00:00",
  scan_started_at: "2026-06-08T10:00:01+00:00",
  completed_at: null,
};

function ingFile(over: Record<string, unknown>) {
  return {
    id: "00000000-0000-0000-0000-000000000000",
    rel_path: "x.docx",
    filename: "x.docx",
    ext: "docx",
    size_bytes: 1024,
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    sha256: "abc",
    staged_blob_uri: "s3://import-staging/abc",
    scan_flags: { disposition: "included" },
    included_candidate: true,
    mtime: null,
    ctime: null,
    classification: null,
    review: null,
    ...over,
  };
}

const HIGH_DOC = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a1",
  rel_path: "SOP-PUR-014 Purchasing.docx",
  filename: "SOP-PUR-014 Purchasing.docx",
  classification: {
    kind: "DOCUMENT", kind_conf: 92, type_code: "SOP", type_conf: 90,
    clause_numbers: ["8.4"], clause_conf: 88, process_names: ["Purchasing"], process_conf: 80,
    pdca_phase: "DO", band: "HIGH", ambiguous: false, top2_margin: 30, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: "SOP-PUR-014",
    identifier_source: "preserved_doc_code", type_code: "SOP", clause_numbers: ["8.4"],
    process_names: ["Purchasing"], owner: null, decided: false, last_action: null,
    commit_ready: false, identifier_collidable: true,
  },
});
const MED_DOC = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a2",
  rel_path: "Final Inspection WI rev1.docx",
  filename: "Final Inspection WI rev1.docx",
  classification: {
    kind: "DOCUMENT", kind_conf: 73, type_code: "WI", type_conf: 70,
    clause_numbers: ["8.6"], clause_conf: 65, process_names: ["Production"], process_conf: 60,
    pdca_phase: "DO", band: "MEDIUM", ambiguous: false, top2_margin: 15, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: "WI-PRD-022",
    identifier_source: "preserved_doc_code", type_code: "WI", clause_numbers: ["8.6"],
    process_names: ["Production"], owner: null, decided: false, last_action: null,
    commit_ready: false, identifier_collidable: true,
  },
});
const LOW_UNKNOWN = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a3",
  rel_path: "scan0421.pdf",
  filename: "scan0421.pdf",
  classification: {
    kind: "UNKNOWN", kind_conf: 22, type_code: null, type_conf: 0,
    clause_numbers: [], clause_conf: 0, process_names: null, process_conf: 0,
    pdca_phase: null, band: "LOW", ambiguous: false, top2_margin: 5, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: null, identifier_source: null,
    type_code: null, clause_numbers: [], process_names: [], owner: null, decided: false,
    last_action: null, commit_ready: false, identifier_collidable: false,
  },
});
const DUP_FILE = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a4",
  rel_path: "SOP-PUR v2 FINAL.docx",
  filename: "SOP-PUR v2 FINAL.docx",
  classification: {
    kind: "DOCUMENT", kind_conf: 90, type_code: "SOP", type_conf: 88,
    clause_numbers: ["8.4"], clause_conf: 85, process_names: ["Purchasing"], process_conf: 78,
    pdca_phase: "DO", band: "HIGH", ambiguous: false, top2_margin: 25, classifier_version: "v1.4",
  },
  review: {
    disposition: "undecided", kind: "UNCONFIRMED", identifier: "SOP-PUR-014",
    identifier_source: "preserved_doc_code", type_code: "SOP", clause_numbers: ["8.4"],
    process_names: ["Purchasing"], owner: null, decided: false, last_action: null,
    commit_ready: false, identifier_collidable: true,
  },
});
const QUARANTINE_FILE = ingFile({
  id: "f0000000-0000-0000-0000-0000000000a5",
  rel_path: "broken.bin",
  filename: "broken.bin",
  sha256: null,
  staged_blob_uri: null,
  scan_flags: { disposition: "quarantine", reason: "sniff_failed", detail: "unrecognized content" },
  included_candidate: false,
});

export const ingestionFilesFixture = [HIGH_DOC, DUP_FILE, MED_DOC, LOW_UNKNOWN, QUARANTINE_FILE];

export const ingestionFileDetailFixture = {
  ...HIGH_DOC,
  run_id: ingestionRunFixture.id,
  // The DETAIL endpoint nests the folded review under `effective` (get_file_review) — unlike the LIST
  // row, whose `review` is the flat ImportFileReview. HIGH_DOC's flat review becomes `effective` here.
  review: { effective: HIGH_DOC.review, decision_history: [] },
  extract: {
    status: "extracted", full_text: "Purchasing procedure…", text_truncated: false,
    header_block: "SOP-PUR-014", language: "en", ocr_used: false, ocr_confidence: null,
    char_count: 4200, page_count: 3, error: null, extractor_version: "tika-2",
  },
  dedup: {
    in_exact_cluster: false, in_near_cluster: true, is_canonical: true, redundant_of_file_id: null,
    in_version_family: true, is_effective: true, superseded_by_file_id: null,
  },
  proposal: {
    proposed_identifier: "SOP-PUR-014", identifier_source: "preserved_doc_code",
    target_ia_path: "DO/08-Operation", proposed_owner: null, owner_source: null,
    conflict_flags: { duplicate_identifier_within_import: ["f0000000-0000-0000-0000-0000000000a4"] },
  },
};

export const ingestionDupeClustersFixture = {
  run_id: ingestionRunFixture.id,
  clusters: [
    {
      id: "c0000000-0000-0000-0000-0000000000c1", method: "near",
      member_file_ids: [HIGH_DOC.id, DUP_FILE.id], canonical_file_id: HIGH_DOC.id,
      jaccard: 0.91, evidence: {},
    },
  ],
};

export const ingestionVersionFamiliesFixture = {
  run_id: ingestionRunFixture.id,
  families: [
    {
      id: "v0000000-0000-0000-0000-0000000000v1", family_key: "SOP-PUR-014",
      base_name: "SOP-PUR-014 Purchasing", doc_code: "SOP-PUR-014",
      ordered_member_file_ids: [HIGH_DOC.id, DUP_FILE.id], effective_file_id: HIGH_DOC.id,
      reconstruct_revision_chain: false, evidence: {},
    },
  ],
};

export const ingestionChecklistFixture = {
  run_id: ingestionRunFixture.id,
  status: "Proposed",
  ready: false,
  blocking: [
    { type: "duplicate_identifier_within_import", identifier: "SOP-PUR-014",
      file_ids: [HIGH_DOC.id, DUP_FILE.id] },
  ],
  advisory: {
    star_coverage: { total: 20, satisfied: 17 },
    unknown_low: 2,
    kind_unconfirmed: 4,
  },
  review: {
    keep_items: 4, decided: 0, accepted: 0, corrected: 0, excluded: 0, deferred: 0,
    undecided: 4, kind_confirmed: 1, commit_ready: 1,
  },
};

export const ingestionDecisionsFixture = { run_id: ingestionRunFixture.id, decisions: [] };

export const handlers = [
  // ---- S-ing-4b ingestion (default happy-path; per-test override for 403/empty/error) ----
  http.get("/api/v1/admin/imports", () => HttpResponse.json([ingestionRunFixture])),
  http.get("/api/v1/admin/imports/:id", () => HttpResponse.json(ingestionRunFixture)),
  http.get("/api/v1/admin/imports/:id/files", ({ request }) => {
    const url = new URL(request.url);
    const band = url.searchParams.get("band");
    const disposition = url.searchParams.get("disposition");
    const reviewStatus = url.searchParams.get("review_status");
    const kind = url.searchParams.get("kind");
    // Cast to a common shape so strict tsc can filter the mixed-literal tuple.
    type FileRow = { classification: { band: string; kind: string } | null; scan_flags: { disposition: string }; review: { disposition: string } | null };
    let files: FileRow[] = ingestionFilesFixture as unknown as FileRow[];
    if (band) files = files.filter((f) => f.classification?.band === band);
    if (disposition) files = files.filter((f) => f.scan_flags.disposition === disposition);
    if (reviewStatus) files = files.filter((f) => f.review?.disposition === reviewStatus);
    if (kind) files = files.filter((f) => f.classification?.kind === kind);
    return HttpResponse.json({ run_id: ingestionRunFixture.id, files });
  }),
  http.get("/api/v1/admin/imports/:id/files/:fid", () =>
    HttpResponse.json(ingestionFileDetailFixture),
  ),
  http.get("/api/v1/admin/imports/:id/dupe-clusters", () =>
    HttpResponse.json(ingestionDupeClustersFixture),
  ),
  http.get("/api/v1/admin/imports/:id/version-families", () =>
    HttpResponse.json(ingestionVersionFamiliesFixture),
  ),
  http.get("/api/v1/admin/imports/:id/checklist", () =>
    HttpResponse.json(ingestionChecklistFixture),
  ),
  http.get("/api/v1/admin/imports/:id/decisions", () =>
    HttpResponse.json(ingestionDecisionsFixture),
  ),
  http.post("/api/v1/admin/imports", () =>
    HttpResponse.json({ ...ingestionRunFixture, status: "Created" }, { status: 202 }),
  ),
  http.post("/api/v1/admin/imports/:id/files/:fid/decision", () => HttpResponse.json({ ok: true })),
  http.post("/api/v1/admin/imports/:id/decisions", () => HttpResponse.json({ applied: 1 })),
  http.post("/api/v1/admin/imports/:id/merge", () => HttpResponse.json({ ok: true })),
  http.post("/api/v1/admin/imports/:id/split", () => HttpResponse.json({ ok: true })),
  http.post("/api/v1/admin/imports/:id/cancel", () =>
    HttpResponse.json({ ...ingestionRunFixture, status: "Cancelled" }),
  ),
  http.post("/api/v1/admin/imports/:id/commit", () =>
    HttpResponse.json({ ...ingestionRunFixture, status: "Committing" }, { status: 202 }),
  ),
  http.get("/api/v1/documents", listDocuments),
  http.get("/api/v1/document-types", () => HttpResponse.json(typeFixture)),
  http.get("/api/v1/directory/users", () => HttpResponse.json(directoryFixture)),
  http.get("/api/v1/documents/:id/versions", () => HttpResponse.json(versionFixture)),
  http.get("/api/v1/documents/:id/where-used", () => HttpResponse.json(whereUsedFixture)),
  // S-web-4: the version diff (text redline + metadata diff) + the controlled-copy download.
  http.get("/api/v1/documents/:id/versions/:vid/diff", () => HttpResponse.json(diffFixture)),
  http.get("/api/v1/documents/:id/download", () =>
    HttpResponse.json({
      download_url: "https://minio.test/cc/sop-pur-014.pdf",
      content_type: "application/pdf",
      rendition: "controlled_copy",
    }),
  ),
  // S-web-4b: the worker-async visual diff. POST = request (idempotent); GET = poll; page = PNG.
  // Defaults are terminal (Ready); per-test overrides drive Pending→Ready / Failed / Unavailable / 403.
  http.post("/api/v1/documents/:id/versions/:vid/visual-diff", () =>
    HttpResponse.json(visualDiffFixture),
  ),
  http.get("/api/v1/documents/:id/versions/:vid/visual-diff", () =>
    HttpResponse.json(visualDiffFixture),
  ),
  http.get(
    "/api/v1/documents/:id/versions/:vid/visual-diff/page/:page",
    () => new HttpResponse(PNG_1x1, { headers: { "Content-Type": "image/png" } }),
  ),
  http.get("/api/v1/documents/:id", ({ params }) => {
    const doc = docFixture.find((d) => d.id === params.id);
    return doc
      ? HttpResponse.json({ ...doc, capabilities: detailCapabilities })
      : HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 });
  }),
  http.get("/api/v1/clauses", () => HttpResponse.json(clauseFixture)),
  // ---- S-web-6 search + compliance (default happy-path; per-test overrides for 403/empty) ----
  http.get("/api/v1/search", () => HttpResponse.json(searchFixture)),
  http.get("/api/v1/search/suggest", () => HttpResponse.json(suggestFixture)),
  http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(complianceFixture)),
  // ---- S-web-5 review/approve (default happy-path; per-test overrides for error cases) ----
  http.get("/api/v1/documents/:id/approval", () => HttpResponse.json(approvalFixture)),
  http.get("/api/v1/tasks", () => HttpResponse.json(taskFixture)),
  http.get("/api/v1/tasks/:id", () => HttpResponse.json(approveTask)),
  http.get("/api/v1/workflow-instances/:id", () => HttpResponse.json(approvalFixture)),
  http.post("/api/v1/tasks/:id/decision", () =>
    HttpResponse.json({
      task_id: approveTask.id,
      instance_id: approvalFixture.id,
      stage_key: "quality_approval",
      outcome: "approve",
      decided_at: "2026-06-08T10:00:00+00:00",
      decided_by: "bbbb1111-1111-1111-1111-111111111111",
      signature_event: null,
      comment: null,
    }),
  ),
  http.post("/api/v1/documents/:id/release", ({ params }) =>
    HttpResponse.json({ ...docFixture[0], id: String(params.id), current_state: "Effective" }),
  ),
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

  // ---- S-web-3 authoring (default happy-path; per-test overrides for error cases) ----
  // Default: the caller holds no coarse affordances (so existing tests render no "New" entry).
  http.get("/api/v1/me/permissions", () =>
    HttpResponse.json({ scope: { level: "SYSTEM", selector: null }, permissions: [] }),
  ),
  http.get("/api/v1/documents/:id/clause-mappings", () => HttpResponse.json([])),
  http.post("/api/v1/documents", () => HttpResponse.json(createdDocFixture, { status: 201 })),
  http.post("/api/v1/documents/:id/checkout", ({ params }) =>
    HttpResponse.json({
      id: "wd-1",
      document_id: String(params.id),
      checked_out_by: "bbbb1111-1111-1111-1111-111111111111",
      checked_out_at: "2026-06-07T10:01:00+00:00",
      source_version_id: null,
      lock_ttl_seconds: 28800,
    }),
  ),
  http.post("/api/v1/documents/:id/break-lock", ({ params }) =>
    HttpResponse.json({ document_id: String(params.id), lock_broken: true }),
  ),
  // versions:init-upload — the colon segment needs a RegExp (path-to-regexp treats ":init" as a param).
  http.post(/\/api\/v1\/documents\/[^/]+\/versions:init-upload$/, () =>
    HttpResponse.json({
      dedup: false,
      object_key: "sha-new",
      upload_url: "https://minio.test/staging/sha-new",
    }),
  ),
  // the presigned MinIO PUT — cross-origin, no bearer.
  http.put(/^https:\/\/minio\.test\//, () => new HttpResponse(null, { status: 200 })),
  http.post("/api/v1/documents/:id/checkin", ({ params }) =>
    HttpResponse.json(mkVersion(String(params.id)), { status: 201 }),
  ),
  http.post("/api/v1/documents/:id/clause-mappings", async ({ params, request }) => {
    const body = (await request.json()) as { clause_id: string };
    return HttpResponse.json(
      {
        id: "cm-1",
        document_id: String(params.id),
        clause_id: body.clause_id,
        clause_number: "8.4",
        clause_title: "Control of external providers",
        is_requirement_level: false,
        framework_id: "f1",
        created_at: "2026-06-07T10:06:00+00:00",
      },
      { status: 201 },
    );
  }),
  http.delete(
    "/api/v1/documents/:id/clause-mappings/:clauseId",
    () => new HttpResponse(null, { status: 204 }),
  ),
  http.post("/api/v1/documents/:id/submit-review", ({ params }) =>
    HttpResponse.json({ ...createdDocFixture, id: String(params.id), current_state: "InReview" }),
  ),
  http.post("/api/v1/documents/:id/start-revision", ({ params }) =>
    HttpResponse.json({
      ...createdDocFixture,
      id: String(params.id),
      current_state: "UnderRevision",
    }),
  ),
  http.get("/api/v1/documents/:id/versions/:vid/download", () =>
    HttpResponse.json({ download_url: "https://minio.test/staging/working-copy" }),
  ),
];
