export type DocumentCurrentState =
  | "Draft"
  | "InReview"
  | "Approved"
  | "Effective"
  | "UnderRevision"
  | "Superseded"
  | "Obsolete";

export interface DocumentSummary {
  id: string;
  identifier: string;
  kind: string;
  title: string;
  document_type_id: string | null;
  area_code: string | null;
  folder_path: string | null;
  current_state: DocumentCurrentState;
  classification: string;
  is_singleton: boolean;
  owner_user_id: string;
  framework_id: string;
  current_effective_version_id: string | null;
  // S-web-2: the governing effective version's effective_from (null when no effective version).
  effective_from: string | null;
  created_at: string | null;
  clause_refs?: string[];
  // S-web-3: per-document authoring affordances (DP-6) — present only on detail reads.
  capabilities?: DocumentCapabilities;
}

// S-web-3: GET /documents/{id}.capabilities — the authz answer per authoring key (detail-only). The
// UI combines these with lifecycle state + lock state for the final affordance (DP-6, no dead button).
export interface DocumentCapabilities {
  checkout: boolean;
  edit: boolean; // check-in + start-revision
  manage_metadata: boolean; // clause mapping
  submit: boolean;
  release: boolean; // reflects the version-relative SoD-2
  obsolete: boolean;
  read_draft: boolean;
}

// S-web-2: the GET /documents pagination envelope.
export interface PageMeta {
  limit: number;
  offset: number;
  returned: number;
  has_more: boolean;
}

export interface DocumentsPage {
  data: DocumentSummary[];
  page: PageMeta;
}

// S-web-2: GET /document-types (the friendly Type column / facet).
export interface DocumentType {
  id: string;
  code: string;
  name: string;
  document_level: string;
  is_singleton: boolean;
}

// S-web-2: GET /directory/users — display name only (the friendly Owner column / facet).
export interface DirectoryUser {
  id: string;
  display_name: string | null;
}

// S-web-2: GET /documents/{id}/versions (the History timeline; gated document.read_draft).
export interface DocumentVersion {
  id: string;
  document_id: string;
  version_seq: number;
  revision_label: string;
  version_state: string;
  change_significance: string;
  change_reason: string;
  source_blob_sha256: string;
  metadata_snapshot: Record<string, unknown> | null;
  author_user_id: string;
  effective_from: string | null;
  effective_to: string | null;
  superseded_by_version_id: string | null;
  created_at: string | null;
}

// S-web-2: GET /documents/{id}/where-used (the §7.2 categories).
export interface WhereUsedLink {
  link_id: string;
  link_type: string;
  direction: "outbound" | "inbound";
  document_id: string;
  identifier: string;
  title: string;
  current_state: DocumentCurrentState;
  document_level: string | null;
}

export interface WhereUsedProcess {
  id: string;
  name: string;
  state: string;
  is_active: boolean;
}

export interface WhereUsedClause {
  number: string;
  title: string;
  is_mandatory_star: boolean;
}

export interface WhereUsed {
  document_id: string;
  processes: WhereUsedProcess[];
  child_documents: WhereUsedLink[];
  parent_documents: WhereUsedLink[];
  referenced_by: WhereUsedLink[];
  references_out: WhereUsedLink[];
  forms_templates: WhereUsedLink[];
  supersedes: WhereUsedLink[];
  superseded_by: WhereUsedLink[];
  records_produced_under: { count: number; sample: { id: string; identifier: string }[] };
  clauses: WhereUsedClause[];
  related_capas_findings: unknown[];
  obsoletion_safety: { blocked: boolean; reasons: { code: string; detail: string }[] };
}

// S-web-2: the library's typed facet state (URL-driven). All optional — an absent facet = "All".
export interface DocumentFilters {
  current_state?: DocumentCurrentState;
  document_type?: string; // a document_type_id
  owner_user_id?: string;
  clause?: string; // a clause number, e.g. "8.4"
  effective_from_gte?: string; // ISO timestamp (the relative date bucket's lower bound)
}

export type PdcaPhase = "PLAN" | "DO" | "CHECK" | "ACT";

export interface Clause {
  id: string;
  framework_id: string;
  number: string;
  parent_id: string | null;
  title: string;
  intent_text: string | null;
  is_mandatory_star: boolean;
  pdca_phase: PdcaPhase;
  requirement_node: boolean;
}

// ---- S-web-3 (Document Authoring) -------------------------------------------------------

// GET /me/permissions — the caller's own effective grants (DP-6 affordance gating).
export interface MePermissionEntry {
  key: string;
  effect: "ALLOW" | "DENY";
  source: string | null;
}

export interface MePermissions {
  scope: { level: string; selector: Record<string, unknown> | null };
  permissions: MePermissionEntry[];
}

export type ChangeSignificance = "MAJOR" | "MINOR";

// POST /documents body.
export interface DocumentCreate {
  title: string;
  document_type_id: string;
  area_code?: string;
  folder_path?: string;
  classification?: string;
}

// POST /documents/{id}/checkout response (also the working-draft mirror row).
export interface WorkingDraft {
  id: string;
  document_id: string;
  checked_out_by: string;
  checked_out_at: string;
  source_version_id: string | null;
  lock_ttl_seconds: number;
}

// POST /documents/{id}/versions:init-upload response.
export interface InitUploadResult {
  dedup: boolean;
  object_key: string;
  upload_url: string | null;
}

// POST /documents/{id}/checkin response (the new version + a no-op-detection flag).
export interface CheckinResult extends DocumentVersion {
  change_detected: boolean;
}

// GET/POST /documents/{id}/clause-mappings item.
export interface ClauseMapping {
  id: string;
  document_id: string;
  clause_id: string;
  clause_number: string;
  clause_title: string;
  is_requirement_level: boolean;
  framework_id: string;
  created_at: string;
}

// ---- S-web-4 (Document detail page + the redline) ---------------------------------------

// GET /documents/{id}/download — the controlled-copy presign (rendition card; gate document.read).
// rendition: controlled_copy = the watermarked PDF rendition; source = no controlled rendition yet
// (still rendering, or a non-renderable R26 format).
export interface DocumentDownload {
  download_url: string;
  content_type?: string;
  rendition: "controlled_copy" | "source";
}

// GET /documents/{id}/versions/{vid}/diff?from={vid2} — doc 05 §8 (gate document.read_draft).
// One version's signature event (PII-projected — signer_user_id only).
export interface DiffSignature {
  meaning: "approval" | "release" | "obsolete";
  signer_user_id: string | null;
  signed_at: string | null;
}

// The §8.1 provenance header for one version (listed, not diffed).
export interface DiffProvenance {
  version_id: string;
  version_seq: number;
  revision_label: string;
  version_state: string;
  change_significance: ChangeSignificance;
  change_reason: string;
  effective_from: string | null;
  effective_to: string | null;
  author_user_id: string;
  created_at: string | null;
  signatures: DiffSignature[];
}

// A field-by-field delta over the frozen metadata snapshots (§8.2).
export interface MetadataDiffEntry {
  field: string;
  from: unknown;
  to: unknown;
  changed: boolean;
}

// One inline text-redline hunk (line-level LCS; §8.3).
export interface TextDiffHunk {
  op: "equal" | "insert" | "delete";
  text: string;
}

// status="ok" with hunks, or "unavailable" (Tika down / non-extractable) with a reason.
export interface TextDiff {
  status: "ok" | "unavailable";
  reason?: string;
  hunks?: TextDiffHunk[];
}

export interface VersionDiff {
  document_id: string;
  from: DiffProvenance;
  to: DiffProvenance;
  metadata_diff: MetadataDiffEntry[];
  text_diff: TextDiff;
}

// ---- S-web-4b (the worker-async visual page-image diff) ---------------------------------
// POST/GET /documents/{id}/versions/{vid}/visual-diff?from={vid2} → VisualDiffStatus (gate
// document.read_draft). The page PNGs stream from …/visual-diff/page/{n}?layer=from|to|diff.
export type VisualDiffLayer = "from" | "to" | "diff";

// One page of the diff. `page` is a 0-BASED index (label it 1-based in the UI). `changed` drives
// the changed-page rail's non-color marker + the n/p nav targets.
export interface VisualDiffPage {
  page: number;
  changed: boolean;
}

// Pending → render in progress (a dev renderer outage leaves it Pending, NOT Failed — re-POST
// re-drives). Ready → page_count + pages populated. Unavailable → a version is non-renderable
// (R26) — terminal, NOT an error (fall back to source download). Failed → defensive (a version
// row vanished) — recoverable via re-POST. page_count/pages are null until Ready.
export interface VisualDiffStatus {
  status: "Pending" | "Ready" | "Failed" | "Unavailable";
  page_count: number | null;
  reason: string | null;
  pages: VisualDiffPage[] | null;
}

// ---- S-web-5 (Review & Approve) ---------------------------------------------------------
export type TaskState = "PENDING" | "CLAIMED" | "DONE" | "SKIPPED" | "ESCALATED" | "EXPIRED";
export type TaskType =
  | "APPROVE"
  | "REVIEW"
  | "PERIODIC_REVIEW"
  | "AUDIT_TASK"
  | "FINDING_ACK"
  | "CAPA_STAGE"
  | "CAPA_ACTION"
  | "VERIFY"
  | "MR_INPUT"
  | "MR_ACTION"
  | "DCR_TRIAGE";

// GET /tasks · GET /tasks/{id} · the tasks[] of GET /workflow-instances/{id}?expand=tasks.
export interface Task {
  id: string;
  instance_id: string;
  stage_key: string;
  type: TaskType;
  state: TaskState;
  assignee_user_id: string | null;
  candidate_pool: string[] | null;
  action_expected: string | null;
  due_at: string | null;
}

// current_state is free-form Text server-side — keep it an open string, do NOT enum-validate.
export type WorkflowInstanceState =
  | "IN_APPROVAL"
  | "APPROVED"
  | "REJECTED_TO_DRAFT"
  | "NEEDS_ATTENTION"
  | (string & {});

// GET /documents/{id}/approval · GET /workflow-instances/{id}.
export interface WorkflowInstance {
  id: string;
  definition_id: string;
  definition_version: number;
  subject_type: string;
  subject_id: string;
  current_state: WorkflowInstanceState;
  started_at: string | null;
  revision: number;
  tasks?: Task[];
}

export type DecisionOutcome = "approve" | "changes_requested" | "reject";

// POST /tasks/{id}/decision body.
export interface DecisionBody {
  outcome: DecisionOutcome;
  comment?: string;
  effective_from?: string;
}

export interface SignatureEventSummary {
  id: string;
  meaning: string;
  method: string;
  content_digest: string | null;
  auth_context: Record<string, unknown> | null;
  reauth_at: string | null;
  crypto_signature: string | null;
}

// POST /tasks/{id}/decision response.
export interface DecisionResult {
  task_id: string;
  instance_id: string;
  stage_key: string;
  outcome: DecisionOutcome;
  decided_at: string | null;
  decided_by: string;
  signature_event: SignatureEventSummary | null;
  comment: string | null;
}

// ---- S-web-6 (Global Search + Compliance Checklist) -------------------------------------

// GET /search → ranked metadata-plane hits (Effective documents only). `snippet` is PostgreSQL
// ts_headline output: matched terms wrapped in literal <b>…</b> (rendered safely, never as HTML).
export interface SearchHit {
  type: string; // "document" (the only indexed type in v1)
  id: string;
  identifier: string;
  title: string;
  current_state: DocumentCurrentState;
  clause_refs: string[];
  snippet: string;
  rank: number;
}

export interface SearchResults {
  query: string;
  results: SearchHit[];
  hidden_by_scope: number; // count of candidate hits the caller's access scope hid
}

// GET /search/suggest → lightweight identifier/title type-ahead.
export interface Suggestion {
  id: string;
  identifier: string;
  title: string;
}

// GET /reports/compliance-checklist — ★ mandatory-clause coverage (hard-gated
// report.compliance_checklist.read; 403 for callers without the key).
export type CoverageStatus = "COVERED" | "PARTIAL" | "GAP";

export interface ChecklistRollup {
  total: number;
  covered: number;
  partial: number;
  gap: number;
}

export interface ChecklistRow {
  clause_id: string;
  number: string;
  title: string;
  pdca_phase: PdcaPhase;
  mapped_count: number;
  effective_count: number;
  status: CoverageStatus;
}

export interface ComplianceChecklist {
  framework: string;
  rollup: ChecklistRollup;
  rows: ChecklistRow[];
}
