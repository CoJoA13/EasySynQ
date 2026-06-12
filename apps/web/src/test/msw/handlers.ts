import { http, HttpResponse } from "msw";
import type { AckDecisionResult, AckMatrixRow, AuditList, AuditPlanList, AuditProgramList, Capa, Complaint, DistributionPayload, DocumentVersion, DriftStatus, EffectivePolicy, Finding, FindingList, Measurement, MeasurementListResponse, Ncr, Objective, ObjectiveListResponse, ObjectivePlan, ObjectiveScorecard, SupersededCopies, WorkflowInstance } from "../../lib/types";

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
    review_period_months: 24,
    next_review_due: "2027-03-14",
    last_reviewed_at: null,
    review_state: "current",
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
    review_period_months: null,
    next_review_due: null,
    last_reviewed_at: null,
    review_state: null,
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
  review_period_months: null,
  next_review_due: null,
  last_reviewed_at: null,
  review_state: null,
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
  subject_type: "DOCUMENT",
  subject_id: "11111111-1111-1111-1111-111111111111",
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
  rollup: { total: 3, covered: 1, partial: 1, gap: 1, overdue_review: 1 },
  rows: [
    { clause_id: "c43", number: "4.3", title: "Scope of the QMS", pdca_phase: "PLAN", mapped_count: 1, effective_count: 1, status: "COVERED", overdue_review: true },
    { clause_id: "c62", number: "6.2", title: "Quality objectives", pdca_phase: "PLAN", mapped_count: 1, effective_count: 0, status: "PARTIAL", overdue_review: false },
    { clause_id: "c84", number: "8.4", title: "External providers", pdca_phase: "DO", mapped_count: 0, effective_count: 0, status: "GAP", overdue_review: false },
  ],
};

// ---- S-web-7 CAPA fixtures -------------------------------------------------------
export const capaListFixture = {
  data: [
    { id: "ca000001-0001-0001-0001-000000000001", identifier: "REC-000031", title: "Supplier re-evaluation overdue for 2 vendors", source: "audit", severity: "Major", process_id: "pr000001-0001-0001-0001-000000000001", close_state: "RootCause", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-05-20T09:00:00+00:00" },
    { id: "ca000002-0002-0002-0002-000000000002", identifier: "REC-000034", title: "Delivered batch missing CoA documents", source: "complaint", severity: "Critical", process_id: null, close_state: "Containment", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-05-28T09:00:00+00:00" },
    { id: "ca000003-0003-0003-0003-000000000003", identifier: "REC-000035", title: "Calibration label missing on torque wrench", source: "process", severity: "Minor", process_id: null, close_state: "Raised", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-06-01T09:00:00+00:00" },
    { id: "ca000004-0004-0004-0004-000000000004", identifier: "REC-000028", title: "Scrap-rate spike on Line 2", source: "process", severity: "Major", process_id: null, close_state: "Implement", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-05-15T09:00:00+00:00" },
    { id: "ca000005-0005-0005-0005-000000000005", identifier: "REC-000025", title: "Recurring late deliveries", source: "audit", severity: "Major", process_id: null, close_state: "Verify", cycle_marker: 1, origin_finding_id: null, raised_by: null, created_at: "2026-05-10T09:00:00+00:00" },
    { id: "ca000006-0006-0006-0006-000000000006", identifier: "REC-000019", title: "Document control numbering gap", source: "audit", severity: "Minor", process_id: null, close_state: "Closed", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-04-30T09:00:00+00:00" },
    { id: "ca000007-0007-0007-0007-000000000007", identifier: "REC-000012", title: "Duplicate complaint — withdrawn", source: "complaint", severity: "Minor", process_id: null, close_state: "Rejected", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: "2026-04-20T09:00:00+00:00" },
  ],
} satisfies { data: Capa[] };

export const capaDetailFixture = {
  ...capaListFixture.data[0]!,
  raised_by: "bbbb1111-1111-1111-1111-111111111111",
  stages: [
    { id: "st000001-0001-0001-0001-000000000001", stage: "Raised", content_block: { problem: "Two approved vendors past their re-evaluation date.", source: "audit", severity: "Major" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-20T09:00:00+00:00", evidence_links: [] },
    { id: "st000002-0002-0002-0002-000000000002", stage: "Containment", content_block: { correction: "Froze new POs to both vendors pending review." }, cycle_marker: 0, created_by: "bbbb9999-9999-9999-9999-999999999999", created_at: "2026-05-21T09:00:00+00:00", evidence_links: [] },
    { id: "st000003-0003-0003-0003-000000000003", stage: "RootCause", content_block: { root_cause: "Re-eval reminders never scheduled.", method: "5-whys" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-22T09:00:00+00:00", evidence_links: [] },
  ],
} satisfies Capa;

// A cycle_marker>0 detail: a not_effective Verify looped the CAPA once. The loop bumps cycle_marker
// WITHOUT appending a new RootCause stage (the established RCA carries forward; the FSM offers no path
// to re-record one) — the current cycle re-plans + re-verifies. Matches the backend close-gate model.
export const capaLoopDetailFixture = {
  ...capaListFixture.data[4]!,
  raised_by: "bbbb1111-1111-1111-1111-111111111111",
  stages: [
    { id: "lp000001-0001-0001-0001-000000000001", stage: "RootCause", content_block: { root_cause: "Planning hand-off undefined." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-11T09:00:00+00:00", evidence_links: [] },
    { id: "lp000002-0002-0002-0002-000000000002", stage: "ActionPlan", content_block: { action_items: ["Add a planning hand-off checklist"] }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-13T09:00:00+00:00", evidence_links: [] },
    { id: "lp000003-0003-0003-0003-000000000003", stage: "Verify", content_block: { decision: "not_effective", narrative: "Late deliveries recurred." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-18T09:00:00+00:00", evidence_links: [] },
    { id: "lp000004-0004-0004-0004-000000000004", stage: "ActionPlan", content_block: { action_items: ["Re-baseline the capacity model"] }, cycle_marker: 1, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-19T09:00:00+00:00", evidence_links: [] },
    { id: "lp000005-0005-0005-0005-000000000005", stage: "Verify", content_block: { decision: "effective", narrative: "On-time rate recovered." }, cycle_marker: 1, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-21T09:00:00+00:00", evidence_links: [] },
  ],
} satisfies Capa;

// A close-READY CAPA: at Verify (cycle 0) with a current-cycle Implement + an effective Verify, BOTH
// carrying an evidence link → the honest close gate is satisfied (close succeeds).
export const capaCloseReadyFixture = {
  id: "ca000008-0008-0008-0008-000000000008",
  identifier: "REC-000040",
  title: "Press guard interlock bypass",
  source: "audit",
  severity: "Major",
  process_id: "pr000001-0001-0001-0001-000000000001",
  close_state: "Verify",
  cycle_marker: 0,
  origin_finding_id: null,
  raised_by: "bbbb1111-1111-1111-1111-111111111111",
  created_at: "2026-05-25T09:00:00+00:00",
  stages: [
    { id: "cr000001-0001-0001-0001-000000000001", stage: "RootCause", content_block: { root_cause: "Interlock unmaintained." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-26T09:00:00+00:00", evidence_links: [] },
    { id: "cr000002-0002-0002-0002-000000000002", stage: "ActionPlan", content_block: { action_items: ["Replace the interlock", "Add a PM task"] }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-26T12:00:00+00:00", evidence_links: [] },
    { id: "cr000003-0003-0003-0003-000000000003", stage: "Implement", content_block: { actions_done: "Replaced + scheduled PM." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-27T09:00:00+00:00", evidence_links: [{ id: "el1", record_id: "re000001-0001-0001-0001-000000000001", record_identifier: "REC-000041", link_reason: "PM schedule", created_at: "2026-05-27T09:10:00+00:00" }] },
    { id: "cr000004-0004-0004-0004-000000000004", stage: "Verify", content_block: { decision: "effective", narrative: "No recurrence in 30 days." }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-28T09:00:00+00:00", evidence_links: [{ id: "el2", record_id: "re000002-0002-0002-0002-000000000002", record_identifier: "REC-000042", link_reason: "audit re-check", created_at: "2026-05-28T09:10:00+00:00" }] },
  ],
} satisfies Capa;

// GET /capas/{id}/approval — a pending action-plan approval (the proposer's drawer + the approver's page).
// Per-test override (the default /approval handler returns null): server.use(http.get(".../approval", () =>
// HttpResponse.json(capaApprovalFixture))). capaApprovalTask is the matching CAPA-subject GET /tasks/{id}.
export const capaApprovalFixture = {
  instance: {
    id: "wfca1111-1111-1111-1111-111111111111",
    current_state: "qm_approval",
    definition_version: 1,
    subject_type: "CAPA",
    subject_id: "ca000001-0001-0001-0001-000000000001",
    tasks: [
      { id: "tkca1111-1111-1111-1111-111111111111", stage_key: "qm_approval", type: "APPROVE", state: "PENDING", assignee_user_id: null, candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"], action_expected: "approve_capa_action_plan", due_at: null },
    ],
  },
  proposed_action_plan: { action_items: ["Schedule supplier re-evaluations", "Add a calendar reminder"] },
};

// A CAPA-subject task detail (GET /tasks/{id}) — the approver routes through ReviewApprovePage's CAPA branch.
export const capaApprovalTask = {
  id: "tkca1111-1111-1111-1111-111111111111",
  instance_id: "wfca1111-1111-1111-1111-111111111111",
  stage_key: "qm_approval",
  type: "APPROVE",
  state: "PENDING",
  assignee_user_id: null,
  candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
  action_expected: "approve_capa_action_plan",
  due_at: null,
  subject_type: "CAPA",
  subject_id: "ca000001-0001-0001-0001-000000000001",
};

// A PERIODIC_REVIEW task detail (GET /tasks/{id}) — S-web-8 routes it via ReviewApprovePage's
// periodic branch. due_at = org-midnight of the THEN-current next_review_due (the sweep's anchor);
// it deliberately predates docFixture[0].next_review_due, which reads as the post-reset clock.
export const periodicReviewTask = {
  id: "tkpr1111-1111-1111-1111-111111111111",
  instance_id: "wfpr1111-1111-1111-1111-111111111111",
  stage_key: "review",
  type: "PERIODIC_REVIEW",
  state: "PENDING",
  assignee_user_id: null,
  candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
  action_expected: "periodic_review",
  due_at: "2026-06-10T00:00:00-05:00",
  subject_type: "PERIODIC_REVIEW",
  subject_id: "11111111-1111-1111-1111-111111111111",
};

// ---- S-web-8 drift fixtures (pinned to drift_report.py + the openapi getDriftStatus example) ----

export const driftStatusFixture = {
  scans: {
    MIRROR: {
      status: "CLEAN",
      started_at: "2026-06-10T03:00:00+00:00",
      finished_at: "2026-06-10T03:00:04+00:00",
      counts: { scanned: 41, ok: 41, stale: 0, tampered: 0, rebuild_triggered: false },
      triggered_by: "beat",
    },
    BLOB_REHASH: {
      status: "DIVERGENT",
      started_at: "2026-06-10T04:00:00+00:00",
      finished_at: "2026-06-10T04:01:10+00:00",
      counts: { scanned: 500, ok: 498, mismatched: 1, missing: 1, read_errors: 0, stamped: 498, full: false, sample_limit: 500, total_blobs: 1240 },
      triggered_by: "beat",
    },
  },
  blob_coverage: { total: 1240, never_verified: 612, failing: 2, oldest_verified_at: "2026-06-01T04:00:00+00:00" },
  superseded_copies: { versions: 2, copies: 5 },
} satisfies DriftStatus;

export const supersededCopiesFixture = {
  total: { versions: 2, copies: 5 },
  items: [
    { document_id: "11111111-1111-1111-1111-111111111111", identifier: "SOP-PUR-014", version_id: "eeee1111-1111-1111-1111-111111111111", revision_label: "Rev A", version_state: "Superseded", current_revision_label: "Rev B", exported: 2, printed: 1, last_copy_at: "2026-05-30T14:22:00+00:00" },
    { document_id: "99999999-9999-9999-9999-999999999999", identifier: "SOP-OBS-001", version_id: "ffff1111-1111-1111-1111-111111111111", revision_label: "Rev C", version_state: "Obsolete", current_revision_label: null, exported: 0, printed: 2, last_copy_at: "2026-05-12T08:00:00+00:00" },
  ],
} satisfies SupersededCopies;

// GET /records — the evidence picker source (a bare array).
export const recordsFixture = [
  { id: "re000001-0001-0001-0001-000000000001", identifier: "REC-000041", title: "Preventive-maintenance schedule", record_type: "EVIDENCE" },
  { id: "re000002-0002-0002-0002-000000000002", identifier: "REC-000042", title: "Audit re-check checklist", record_type: "EVIDENCE" },
];

// ---- S-web-7c complaint + NCR fixtures (pinned to the _complaint / _ncr serializers) ----
export const complaintListFixture = {
  data: [
    { id: "cm000001-0001-0001-0001-000000000001", identifier: "CMP-000007", customer: "Northwind Foods Ltd.", received_at: "2026-06-02T09:00:00+00:00", channel: "email", description: "Delivered batch missing CoA documents.", severity: "Critical", spawned_capa_id: null },
    { id: "cm000002-0002-0002-0002-000000000002", identifier: "CMP-000006", customer: "Acme Pharma", received_at: "2026-05-30T09:00:00+00:00", channel: "phone", description: "Late delivery on PO-44821.", severity: "Minor", spawned_capa_id: "ca000002-0002-0002-0002-000000000002" },
  ],
} satisfies { data: Complaint[] };

export const ncrListFixture = {
  data: [
    { id: "nc000001-0001-0001-0001-000000000001", identifier: "NCR-000052", source: "process", description: "Nonconforming output: torque out of spec on Line 2.", severity: "Major", process_id: null, disposition: null, disposition_authorized_by: null, disposition_notes: null, disposed_at: null, created_at: "2026-06-03T09:00:00+00:00" },
    { id: "nc000002-0002-0002-0002-000000000002", identifier: "NCR-000049", source: "audit", description: "Mislabelled retain samples.", severity: "Minor", process_id: null, disposition: "rework", disposition_authorized_by: "bbbb1111-1111-1111-1111-111111111111", disposition_notes: "Re-labelled + re-inspected.", disposed_at: "2026-06-04T09:00:00+00:00", created_at: "2026-06-01T09:00:00+00:00" },
  ],
} satisfies { data: Ncr[] };

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

// ---- S-web-7d audit fixtures (pinned to api/audits.py _program/_plan/_audit/_finding + the
// S-web-7d read-enrichment: _audit carries identifier/title/created_at, _finding carries title) ----
export const auditProgramsFixture = {
  data: [
    { id: "ap000001-0001-0001-0001-000000000001", identifier: "AUDPROG-000001", title: "2026 Internal Audit Programme", period: "2026", coverage: null, archived: false, created_at: "2026-01-05T09:00:00+00:00" },
    { id: "ap000002-0002-0002-0002-000000000002", identifier: "AUDPROG-000002", title: "2025 Programme", period: "2025", coverage: null, archived: true, created_at: "2025-01-06T09:00:00+00:00" },
  ],
} satisfies AuditProgramList;

export const auditPlansFixture = {
  data: [
    { id: "pl000001-0001-0001-0001-000000000001", program_id: "ap000001-0001-0001-0001-000000000001", auditee_process_id: "pr000001-0001-0001-0001-000000000001", lead_auditor_user_id: "bbbb1111-1111-1111-1111-111111111111", scheduled_date: "2026-05-28", checklist_ref: "FRM-AUD-002", created_at: "2026-01-10T09:00:00+00:00" },
    { id: "pl000002-0002-0002-0002-000000000002", program_id: "ap000001-0001-0001-0001-000000000001", auditee_process_id: null, lead_auditor_user_id: null, scheduled_date: "2026-09-01", checklist_ref: null, created_at: "2026-01-11T09:00:00+00:00" },
  ],
} satisfies AuditPlanList;

export const auditListFixture = {
  data: [
    { id: "au000001-0001-0001-0001-000000000001", identifier: "REC-000061", title: "Purchasing & Suppliers audit", plan_id: "pl000001-0001-0001-0001-000000000001", lead_auditor_user_id: "bbbb1111-1111-1111-1111-111111111111", state: "InProgress", started_at: "2026-05-28", completed_at: null, result_summary: null, created_at: "2026-05-20T09:00:00+00:00" },
    { id: "au000002-0002-0002-0002-000000000002", identifier: "REC-000055", title: "Document Control audit", plan_id: "pl000002-0002-0002-0002-000000000002", lead_auditor_user_id: null, state: "Closed", started_at: "2026-04-01", completed_at: "2026-04-30", result_summary: null, created_at: "2026-03-25T09:00:00+00:00" },
    { id: "au000003-0003-0003-0003-000000000003", identifier: "REC-000066", title: "Competence & Training audit", plan_id: "pl000001-0001-0001-0001-000000000001", lead_auditor_user_id: "bbbb1111-1111-1111-1111-111111111111", state: "Closing", started_at: "2026-05-01", completed_at: null, result_summary: null, created_at: "2026-04-25T09:00:00+00:00" },
  ],
} satisfies AuditList;

// Findings of au000001: a live Major NC (its CAPA ca000001 is at RootCause → BLOCKS close), an OFI,
// and a corrected pair (fd000003 NC superseded by fd000004 OBSERVATION → does NOT block).
export const findingsFixture = {
  data: [
    { id: "fd000001-0001-0001-0001-000000000001", identifier: "REC-000062", title: "Supplier re-evaluation overdue for 2 vendors", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Major", clause_ref: "8.4", process_ref: "Purchasing", auto_capa_id: "ca000001-0001-0001-0001-000000000001", correction_of: null, superseded_by_correction: null },
    { id: "fd000002-0002-0002-0002-000000000002", identifier: "REC-000063", title: "Consider automating the supplier scorecard", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "OFI", severity: null, clause_ref: "8.4", process_ref: null, auto_capa_id: null, correction_of: null, superseded_by_correction: null },
    { id: "fd000003-0003-0003-0003-000000000003", identifier: "REC-000064", title: "Mis-typed as an NC at first triage", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Minor", clause_ref: null, process_ref: null, auto_capa_id: "ca000006-0006-0006-0006-000000000006", correction_of: null, superseded_by_correction: "fd000004-0004-0004-0004-000000000004" },
    { id: "fd000004-0004-0004-0004-000000000004", identifier: "REC-000065", title: "Vendor file index stored outside the library", audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "OBSERVATION", severity: null, clause_ref: null, process_ref: null, auto_capa_id: null, correction_of: "fd000003-0003-0003-0003-000000000003", superseded_by_correction: null },
  ],
} satisfies FindingList;

// A created NC finding (the POST /audits/{id}/findings default response) — auto_capa_id SET.
export const createdNcFindingFixture = {
  id: "fd-new-00-0000-0000-0000-000000000000",
  identifier: "REC-000070",
  title: "New NC finding",
  audit_id: "au000001-0001-0001-0001-000000000001",
  finding_type: "NC",
  severity: "Major",
  clause_ref: null,
  process_ref: null,
  auto_capa_id: "ca-auto-00-0000-0000-0000-000000000000",
  correction_of: null,
  superseded_by_correction: null,
} satisfies Finding;

// GET /processes returns a BARE ARRAY (api/processes.py list_processes_endpoint) — pin the full
// _process row shape (the SPA reads id+name only, but the fixture mirrors the serializer).
export const processesFixture = [
  { id: "pr000001-0001-0001-0001-000000000001", org_id: "or000001-0001-0001-0001-000000000001", name: "Purchasing", parent_id: null, owner_org_role_id: null, pdca_phase: "DO", criteria: null, state: "ACTIVE", excluded: false, is_outsourced: false, outsourced_supplier_id: null, created_at: "2026-01-01T09:00:00+00:00" },
  { id: "pr000002-0002-0002-0002-000000000002", org_id: "or000001-0001-0001-0001-000000000001", name: "Production", parent_id: null, owner_org_role_id: null, pdca_phase: "DO", criteria: null, state: "ACTIVE", excluded: false, is_outsourced: false, outsourced_supplier_id: null, created_at: "2026-01-01T09:00:00+00:00" },
];

// ---- S-ack-2 acknowledgements fixtures (pinned to the S-ack-1 serializers) ----
// The doc-detail document (docFixture[0], SOP-PUR-014) is flag-on with a fuller audience.
export const distributionFixture = {
  acknowledgement_required: true,
  entries: [
    { id: "de000001-0001-0001-0001-000000000001", target_type: "user", target_id: "bbbb1111-1111-1111-1111-111111111111", ack_required: true, created_at: "2026-03-15T09:00:00+00:00" },
    { id: "de000002-0002-0002-0002-000000000002", target_type: "org_role", target_id: "ro000001-0001-0001-0001-000000000001", ack_required: true, created_at: "2026-03-15T09:05:00+00:00" },
  ],
  coverage: { required: 47, acknowledged: 41, pending: 6, overdue: 2 },
} satisfies DistributionPayload;

// Flag ON but no Effective version → coverage null (queries.coverage_counts boundary None).
export const distributionNoEffectiveFixture = {
  acknowledgement_required: true,
  entries: [],
  coverage: null,
} satisfies DistributionPayload;

// Flag OFF but an Effective version exists → honest zeros, not null.
export const distributionFlagOffFixture = {
  acknowledgement_required: false,
  entries: [],
  coverage: { required: 0, acknowledged: 0, pending: 0, overdue: 0 },
} satisfies DistributionPayload;

export const ackMatrixFixture = [
  { user_id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Mara Quality", status: "acknowledged", acknowledged_at: "2026-03-16T10:00:00+00:00", acknowledged_revision_label: "Rev B", due_at: null },
  { user_id: "bbbb2222-2222-2222-2222-222222222222", display_name: "Diego Owner", status: "pending", acknowledged_at: null, acknowledged_revision_label: null, due_at: "2026-03-30T00:00:00+00:00" },
  { user_id: "bbbb3333-3333-3333-3333-333333333333", display_name: "Sam Patel", status: "overdue", acknowledged_at: null, acknowledged_revision_label: null, due_at: "2026-03-20T00:00:00+00:00" },
] satisfies AckMatrixRow[];

// A DOC_ACK task detail (GET /tasks/{id}) — subject_type/subject_id are DETAIL-ONLY (the list omits them).
export const docAckTask = {
  id: "tkak1111-1111-1111-1111-111111111111",
  instance_id: "wfak1111-1111-1111-1111-111111111111",
  stage_key: "acknowledge",
  type: "DOC_ACK",
  state: "PENDING",
  assignee_user_id: "bbbb1111-1111-1111-1111-111111111111",
  candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
  action_expected: "acknowledge",
  due_at: "2026-03-30T00:00:00+00:00",
  subject_type: "DOC_ACK",
  subject_id: "11111111-1111-1111-1111-111111111111",
};
// The list row (GET /tasks?type=DOC_ACK) — subject_type/subject_id STRIPPED (matches _task without them).
export const docAckListRow = {
  id: docAckTask.id, instance_id: docAckTask.instance_id, stage_key: docAckTask.stage_key,
  type: "DOC_ACK", state: "PENDING", assignee_user_id: docAckTask.assignee_user_id,
  candidate_pool: docAckTask.candidate_pool, action_expected: "acknowledge", due_at: docAckTask.due_at,
};

export const ackDecisionResultFixture = {
  task_id: docAckTask.id,
  instance_id: docAckTask.instance_id,
  stage_key: "acknowledge",
  outcome: "acknowledge",
  decided_at: "2026-06-11T10:00:00+00:00",
  decided_by: "bbbb1111-1111-1111-1111-111111111111",
  stage_state: "COMPLETED",
  current_state: "ACKNOWLEDGED",
  signature_spec: null,
  comment: null,
  replayed: false,
  document_id: "11111111-1111-1111-1111-111111111111",
  document_version_id: "dddd1111-1111-1111-1111-111111111111",
  acknowledgement_id: "ack00001-0001-0001-0001-000000000001",
} satisfies AckDecisionResult;

export const rolesFixture = [
  { id: "ro000001-0001-0001-0001-000000000001", name: "Employee", description: "All staff", is_reserved: true },
  { id: "ro000002-0002-0002-0002-000000000002", name: "Process Owner", description: null, is_reserved: true },
];

const OBJ_DETAIL_ID = "ob000001-0001-0001-0001-000000000001";

const objectiveFixtures: Objective[] = [
  {
    id: OBJ_DETAIL_ID,
    identifier: "OBJ-001",
    title: "On-time delivery rate",
    current_state: "Draft",
    target_value: "95",
    unit: "%",
    baseline_value: "80",
    current_value: "92",
    direction: "HIGHER_IS_BETTER",
    at_risk_threshold: "90",
    due_date: "2026-12-31",
    process_id: "70000000-0000-0000-0000-000000000001",
    policy_id: null,
    rag: "amber",
    pct_toward_target: 0.8,
    attainment: "in_progress",
    plans: [],
  },
  {
    id: "ob000002-0002-0002-0002-000000000002",
    identifier: "OBJ-002",
    title: "Customer complaints per quarter",
    current_state: "Draft",
    target_value: "5",
    unit: "complaints",
    baseline_value: null,
    current_value: "7",
    direction: "LOWER_IS_BETTER",
    at_risk_threshold: null,
    due_date: "2026-12-31",
    process_id: null,
    policy_id: null,
    rag: "red",
    pct_toward_target: null,
    attainment: "in_progress",
    plans: [],
  },
  {
    id: "ob000003-0003-0003-0003-000000000003",
    identifier: "OBJ-003",
    title: "First-pass yield",
    current_state: "Draft",
    target_value: "98",
    unit: "%",
    baseline_value: "90",
    current_value: "99",
    direction: "HIGHER_IS_BETTER",
    at_risk_threshold: "95",
    due_date: "2026-12-31",
    process_id: null,
    policy_id: null,
    rag: "green",
    pct_toward_target: 1.125,
    attainment: "in_progress",
    plans: [],
  },
  {
    id: "ob000004-0004-0004-0004-000000000004",
    identifier: "OBJ-004",
    title: "Supplier defect rate",
    current_state: "Draft",
    target_value: "2",
    unit: "%",
    baseline_value: null,
    current_value: null,
    direction: "LOWER_IS_BETTER",
    at_risk_threshold: null,
    due_date: "2026-12-31",
    process_id: null,
    policy_id: null,
    rag: "unmeasured",
    pct_toward_target: null,
    attainment: "in_progress",
    plans: [],
  },
] satisfies Objective[];

const objectivePlanFixtures: ObjectivePlan[] = [
  {
    id: "pl000001-0001-0001-0001-000000000001",
    objective_id: OBJ_DETAIL_ID,
    action: "Add a second carrier to the south region",
    resource: "Logistics budget",
    responsible_user_id: "bbbb1111-1111-1111-1111-111111111111",
    due_date: "2026-09-30",
  },
] satisfies ObjectivePlan[];

export const objectiveDetailFixture: Objective = {
  ...objectiveFixtures[0]!,
  plans: objectivePlanFixtures,
  // S-obj-3 detail-only keys (api/objectives.py _objective with capabilities= — the detail GET
  // ALWAYS carries both; LIST/scorecard/submit/release responses carry neither).
  capabilities: { submit: true, release: false },
  effective_from: null,
} satisfies Objective;

// domain/objectives/commitment.py build_commitment — decimal STRINGS, direction .value, ISO date.
// Mirrors objectiveFixtures[0] (the commitment the submit would freeze for OBJ-001).
const objectiveCommitment = {
  target_value: "95",
  unit: "%",
  direction: "HIGHER_IS_BETTER",
  due_date: "2026-12-31",
  at_risk_threshold: "90",
  baseline_value: "80",
  policy_id: null,
};

// A version row carrying the frozen commitment (api/documents.py _version, with
// metadata_snapshot trimmed to the objective_commitment fold the FE reads) — consumed by the
// ReviewApprovePage objective-context leg (Task 14).
export const objectiveVersionWithCommitment = {
  id: "veob1111-1111-1111-1111-111111111111",
  document_id: OBJ_DETAIL_ID,
  version_seq: 1,
  revision_label: "Rev A",
  version_state: "InReview",
  change_significance: "MAJOR",
  change_reason: "Objective commitment submitted for review",
  source_blob_sha256: "6f1ed002ab5595859014ebf0951522d9a8b65a42c2e9a47b6b1f5d7e9c3a1b42",
  metadata_snapshot: { objective_commitment: objectiveCommitment },
  author_user_id: "aaaa1111-1111-1111-1111-111111111111",
  effective_from: null,
  effective_to: null,
  superseded_by_version_id: null,
  created_at: "2026-06-11T09:00:00+00:00",
} satisfies DocumentVersion;

// api/objectives.py _approval_instance/_approval_task (field-equivalent to workflow.py's
// _instance/_task — no subject_type/subject_id on the tasks; subject_type=DOCUMENT on the
// instance, instantiate_approval hardcodes it).
export const objectiveApprovalInstance = {
  id: "wfob1111-1111-1111-1111-111111111111",
  definition_id: "df000001-0001-0001-0001-000000000001",
  definition_version: 1,
  subject_type: "DOCUMENT",
  subject_id: OBJ_DETAIL_ID,
  current_state: "IN_APPROVAL",
  started_at: "2026-06-11T09:00:00+00:00",
  revision: 0,
  tasks: [
    {
      id: "tkob1111-1111-1111-1111-111111111111",
      instance_id: "wfob1111-1111-1111-1111-111111111111",
      stage_key: "quality_approval",
      type: "APPROVE",
      state: "PENDING",
      assignee_user_id: null,
      candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
      action_expected: "approve",
      due_at: null,
    },
  ],
} satisfies WorkflowInstance;

// GET /objectives/policy (api/objectives.py get_objective_policy_endpoint — {id,identifier,title}).
export const effectivePolicyFixture = {
  id: "po000001-0001-0001-0001-000000000001",
  identifier: "POL-001",
  title: "Quality Policy",
} satisfies EffectivePolicy;

const measurementFixtures: Measurement[] = [
  {
    id: "me000002-0002-0002-0002-000000000002",
    objective_id: OBJ_DETAIL_ID,
    record_id: "re000002-0002-0002-0002-000000000002",
    period: "2026-04-01",
    value: "92",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-06-02T09:00:00+00:00",
  },
  {
    id: "me000001-0001-0001-0001-000000000001",
    objective_id: OBJ_DETAIL_ID,
    record_id: "re000001-0001-0001-0001-000000000001",
    period: "2026-01-01",
    value: "88",
    target_at_capture: "95",
    unit: "%",
    source: "Logistics MIS",
    created_at: "2026-04-04T09:00:00+00:00",
  },
] satisfies Measurement[];

export const handlers = [
  // ---- S-obj-2 Quality Objectives (default happy-path; per-test overrides for 403/empty/error) ----
  http.get("/api/v1/objectives/scorecard", ({ request }) => {
    const pid = new URL(request.url).searchParams.get("process_id");
    const rows = pid ? objectiveFixtures.filter((o) => o.process_id === pid) : objectiveFixtures;
    const by_rag = { green: 0, amber: 0, red: 0, unmeasured: 0 };
    for (const o of rows) by_rag[o.rag] += 1;
    return HttpResponse.json({
      total: rows.length,
      on_target: by_rag.green,
      by_rag,
      objectives: rows,
    } satisfies ObjectiveScorecard);
  }),
  http.get("/api/v1/objectives", ({ request }) => {
    const pid = new URL(request.url).searchParams.get("process_id");
    const rows = pid ? objectiveFixtures.filter((o) => o.process_id === pid) : objectiveFixtures;
    return HttpResponse.json({ data: rows } satisfies ObjectiveListResponse);
  }),
  // The bare `policy` literal MUST register BEFORE the `:id` route or it resolves as :id
  // (the scorecard precedent above; routes with a literal tail like /:id/approval are safe).
  http.get("/api/v1/objectives/policy", () => HttpResponse.json(effectivePolicyFixture)),
  http.get("/api/v1/objectives/:id", () => HttpResponse.json(objectiveDetailFixture)),
  http.get("/api/v1/objectives/:id/approval", () => HttpResponse.json(objectiveApprovalInstance)),
  http.get("/api/v1/objectives/:id/measurements", () =>
    HttpResponse.json({ data: measurementFixtures } satisfies MeasurementListResponse),
  ),
  // submit/release return the bare objective (no capabilities/effective_from, plans []) — the
  // api/objectives.py call-sites pass neither to _objective.
  http.post("/api/v1/objectives/:id/submit-review", () =>
    HttpResponse.json({ ...objectiveFixtures[0]!, current_state: "InReview" } satisfies Objective),
  ),
  http.post("/api/v1/objectives/:id/release", () =>
    HttpResponse.json({ ...objectiveFixtures[0]!, current_state: "Effective" } satisfies Objective),
  ),
  http.post("/api/v1/objectives", () => HttpResponse.json(objectiveDetailFixture, { status: 201 })),
  http.post("/api/v1/objectives/:id/measurements", () =>
    HttpResponse.json(measurementFixtures[0]!, { status: 201 }),
  ),
  http.post("/api/v1/objectives/:id/plans", () =>
    HttpResponse.json(objectivePlanFixtures[0]!, { status: 201 }),
  ),
  http.delete("/api/v1/objectives/:id/plans/:planId", () => new HttpResponse(null, { status: 204 })),
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
  // ---- S-web-7 CAPA (default happy-path; per-test overrides for 403/empty/error) ----
  http.get("/api/v1/capas", () => HttpResponse.json(capaListFixture)),
  http.get("/api/v1/capas/:id", ({ params }) => {
    if (params.id === "ca000005-0005-0005-0005-000000000005") return HttpResponse.json(capaLoopDetailFixture);
    if (params.id === "ca000008-0008-0008-0008-000000000008") return HttpResponse.json(capaCloseReadyFixture);
    return HttpResponse.json({ ...capaDetailFixture, id: String(params.id) });
  }),
  // S-web-7b writes (default happy-path; per-test overrides for the 409s). Each returns a CAPA-ish body
  // the UI ignores (it invalidates + refetches).
  http.post("/api/v1/capas", () => HttpResponse.json({ ...capaDetailFixture, id: "ca-new-0000-0000-0000-000000000000" }, { status: 201 })),
  http.post("/api/v1/capas/:id/containment", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/root-cause", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/action-plan", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id), approval_instance: { id: "wfca1111-1111-1111-1111-111111111111", current_state: "qm_approval", definition_version: 1 } })),
  http.post("/api/v1/capas/:id/implement", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/verify", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id) })),
  http.post("/api/v1/capas/:id/close", ({ params }) => HttpResponse.json({ ...capaDetailFixture, id: String(params.id), close_state: "Closed" })),
  http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(null)),
  http.get("/api/v1/records", () => HttpResponse.json(recordsFixture)),
  // ---- S-web-7c complaints + NCRs (default happy-path; per-test overrides for 403/empty/error) ----
  http.get("/api/v1/complaints", () => HttpResponse.json(complaintListFixture)),
  http.post("/api/v1/complaints", () =>
    HttpResponse.json({ ...complaintListFixture.data[0]!, id: "cm-new-0000-0000-0000-000000000000", spawned_capa_id: null }, { status: 201 }),
  ),
  http.post("/api/v1/complaints/:id/spawn-capa", () =>
    HttpResponse.json({ ...capaDetailFixture, id: "ca-spawn-0000-0000-0000-000000000000", source: "complaint" }, { status: 201 }),
  ),
  http.get("/api/v1/ncrs", () => HttpResponse.json(ncrListFixture)),
  http.post("/api/v1/ncrs", () =>
    HttpResponse.json({ ...ncrListFixture.data[0]!, id: "nc-new-0000-0000-0000-000000000000" }, { status: 201 }),
  ),
  http.patch("/api/v1/ncrs/:id/disposition", ({ params }) =>
    HttpResponse.json({ ...ncrListFixture.data[0]!, id: String(params.id), disposition: "rework", disposition_authorized_by: "bbbb1111-1111-1111-1111-111111111111", disposed_at: "2026-06-09T09:00:00+00:00" }),
  ),
  // ---- S-web-7d audits & findings (default happy-path; per-test overrides for 403/409/422) ----
  http.get("/api/v1/audit-programs", () => HttpResponse.json(auditProgramsFixture)),
  http.get("/api/v1/audit-programs/:id/plans", () => HttpResponse.json(auditPlansFixture)),
  http.get("/api/v1/audit-plans/:id", ({ params }) =>
    HttpResponse.json(
      auditPlansFixture.data.find((p) => p.id === params.id) ?? auditPlansFixture.data[0]!,
    ),
  ),
  http.post("/api/v1/audit-programs", () =>
    HttpResponse.json(
      { ...auditProgramsFixture.data[0]!, id: "ap-new-00-0000-0000-0000-000000000000", identifier: "AUDPROG-000003" },
      { status: 201 },
    ),
  ),
  http.patch("/api/v1/audit-programs/:id", ({ params }) =>
    HttpResponse.json({ ...auditProgramsFixture.data[0]!, id: String(params.id) }),
  ),
  http.post("/api/v1/audit-programs/:id/plans", () =>
    HttpResponse.json(
      { ...auditPlansFixture.data[0]!, id: "pl-new-00-0000-0000-0000-000000000000" },
      { status: 201 },
    ),
  ),
  http.get("/api/v1/audits", () => HttpResponse.json(auditListFixture)),
  http.get("/api/v1/audits/:id", ({ params }) => {
    const audit = auditListFixture.data.find((a) => a.id === params.id);
    return audit
      ? HttpResponse.json(audit)
      : HttpResponse.json({ code: "not_found", title: "Audit not found" }, { status: 404 });
  }),
  http.post("/api/v1/audits", () =>
    HttpResponse.json(
      { ...auditListFixture.data[0]!, id: "au-new-00-0000-0000-0000-000000000000", identifier: "REC-000069", state: "Scheduled", started_at: null },
      { status: 201 },
    ),
  ),
  // The 6 FSM transitions — each returns the audit advanced to its target state.
  http.post("/api/v1/audits/:id/plan", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Planned" })),
  http.post("/api/v1/audits/:id/conduct", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "InProgress" })),
  http.post("/api/v1/audits/:id/draft-findings", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "FindingsDraft" })),
  http.post("/api/v1/audits/:id/report", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Reported" })),
  http.post("/api/v1/audits/:id/begin-closing", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Closing" })),
  http.post("/api/v1/audits/:id/close", ({ params }) => HttpResponse.json({ ...auditListFixture.data[0]!, id: String(params.id), state: "Closed", completed_at: "2026-06-09" })),
  http.get("/api/v1/audits/:id/findings", () => HttpResponse.json(findingsFixture)),
  http.post("/api/v1/audits/:id/findings", () => HttpResponse.json(createdNcFindingFixture, { status: 201 })),
  // The successor's title is the correction REASON (the record title), not the original's text.
  http.post("/api/v1/findings/:id/correction", ({ params }) =>
    HttpResponse.json(
      { ...findingsFixture.data[1]!, id: "fd-corr-0-0000-0000-0000-000000000000", title: "Reclassified as an improvement", correction_of: String(params.id) },
      { status: 201 },
    ),
  ),
  http.get("/api/v1/processes", () => HttpResponse.json(processesFixture)),
  // Pinned to the real _evidence_link serializer (api/records.py): {id, record_id, target_type, target_id,
  // link_reason, created_at} — NOT a record_identifier (that field only exists on the per-stage projection).
  // The UI ignores this body (it invalidates + refetches), but the fixture must match the real shape.
  http.post("/api/v1/records/:id/evidence-links", () => HttpResponse.json({ id: "el-new", record_id: "re000001-0001-0001-0001-000000000001", target_type: "capa_stage", target_id: "cr000003-0003-0003-0003-000000000003", link_reason: null, created_at: "2026-06-09T09:00:00+00:00" }, { status: 201 })),
  // ---- S-web-8 drift surface (default happy-path; per-test overrides for 403/null-scans) ----
  http.get("/api/v1/admin/drift/status", () => HttpResponse.json(driftStatusFixture)),
  http.get("/api/v1/admin/drift/superseded-copies", ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const limit = Number(sp.get("limit") ?? "50");
    const offset = Number(sp.get("offset") ?? "0");
    return HttpResponse.json({
      total: supersededCopiesFixture.total,
      items: supersededCopiesFixture.items.slice(offset, offset + limit),
    });
  }),
  // ---- S-web-6 search + compliance (default happy-path; per-test overrides for 403/empty) ----
  http.get("/api/v1/search", () => HttpResponse.json(searchFixture)),
  http.get("/api/v1/search/suggest", () => HttpResponse.json(suggestFixture)),
  http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(complianceFixture)),
  // ---- S-ack-2 acknowledgements (default happy-path; per-test overrides for 403/409/null-coverage) ----
  http.get("/api/v1/documents/:id/distribution", () => HttpResponse.json(distributionFixture)),
  http.post("/api/v1/documents/:id/distribution", () => HttpResponse.json(distributionFixture)),
  http.delete(
    "/api/v1/documents/:id/distribution/:entryId",
    () => new HttpResponse(null, { status: 204 }),
  ),
  http.get("/api/v1/documents/:id/acknowledgements", () => HttpResponse.json(ackMatrixFixture)),
  http.get("/api/v1/roles", () => HttpResponse.json(rolesFixture)),
  // ---- S-web-5 review/approve (default happy-path; per-test overrides for error cases) ----
  http.get("/api/v1/documents/:id/approval", () => HttpResponse.json(approvalFixture)),
  http.get("/api/v1/tasks", ({ request }) => {
    const type = new URL(request.url).searchParams.get("type");
    if (type === "DOC_ACK") return HttpResponse.json([docAckListRow]);
    return HttpResponse.json(taskFixture);
  }),
  http.get("/api/v1/tasks/:id", ({ params }) => {
    if (params.id === periodicReviewTask.id) return HttpResponse.json(periodicReviewTask);
    if (params.id === docAckTask.id) return HttpResponse.json(docAckTask);
    return HttpResponse.json(approveTask);
  }),
  http.get("/api/v1/workflow-instances/:id", () => HttpResponse.json(approvalFixture)),
  http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
    const body = (await request.json()) as { outcome?: string };
    if (body.outcome === "acknowledge") return HttpResponse.json(ackDecisionResultFixture);
    return HttpResponse.json({
      task_id: approveTask.id, instance_id: approvalFixture.id, stage_key: "quality_approval",
      outcome: "approve", decided_at: "2026-06-08T10:00:00+00:00",
      decided_by: "bbbb1111-1111-1111-1111-111111111111", signature_event: null, comment: null,
    });
  }),
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
  http.patch("/api/v1/documents/:id", async ({ params, request }) => {
    const body = (await request.json()) as { review_period_months?: number | null };
    const doc = docFixture.find((d) => d.id === params.id) ?? docFixture[0]!;
    const months = body.review_period_months ?? null;
    // The real PATCH response is bare _document(doc): NO clause_refs/capabilities (handler-supplied
    // read-path extras) and a null effective_from — the UI must invalidate, never consume this body.
    const bare: Record<string, unknown> = { ...doc };
    delete bare.clause_refs;
    return HttpResponse.json({
      ...bare,
      effective_from: null,
      review_period_months: months,
      next_review_due: months === null ? null : "2028-03-14",
      review_state: months === null ? null : "current",
    });
  }),
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
