export type DocumentCurrentState =
  "Draft" | "InReview" | "Approved" | "Effective" | "UnderRevision" | "Superseded" | "Obsolete";

export type ReviewState = "current" | "due_soon" | "overdue";

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
  // S-drift-1 review scheduling (always emitted). review_state is server-derived (org tz, 30-day
  // due_soon window) — the client never recomputes it.
  review_period_months: number | null;
  next_review_due: string | null; // a DATE — "YYYY-MM-DD"
  last_reviewed_at: string | null; // an ISO DATETIME with offset (unlike next_review_due)
  review_state: ReviewState | null;
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
  // S-doc-filters (CREATE-picker): two opt-in server-side narrowing filters. false → never-released /
  // non-managed-subtype. Omitted (undefined) by default — only the CREATE picker sets them.
  has_effective_version?: boolean;
  managed_subtype?: boolean;
  // s-dcr-target-typeahead: free-text substring over identifier/title (server-side). Emitted as a
  // top-level `q=` param (not bracketed). Omitted when blank — other callers stay byte-identical.
  q?: string;
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
  // S-process-scope-1: optionally link the new document to processes at creation. A bound Process
  // Owner (PROCESS-scoped document.create) must declare ≥1 owned process; omitting it = unlinked.
  process_ids?: string[];
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
  | "DOC_ACK"
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
  // S-optimize-1: subject identity now on the LIST + detail (no N+1) so the inbox/rail triage in place.
  subject_type?: string; // "DOCUMENT" | "CAPA" | "DCR" | "MGMT_REVIEW" | "PERIODIC_REVIEW" | "DOC_ACK"
  subject_id?: string;
  subject_identifier?: string | null; // the subject's human id (doc/CAPA/MR/OBJ identifier, or the DCR id)
  subject_title?: string | null; // short subject title (DCR = reason_text, truncated); null if none
}

// current_state is free-form Text server-side — keep it an open string, do NOT enum-validate.
export type WorkflowInstanceState =
  "IN_APPROVAL" | "APPROVED" | "REJECTED_TO_DRAFT" | "NEEDS_ATTENTION" | (string & {});

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

export type DecisionOutcome = "approve" | "changes_requested" | "reject" | "complete" | "verify";

export type DecisionSubjectType =
  | "DOCUMENT"
  | "CAPA"
  | "PERIODIC_REVIEW"
  | "DCR"
  | "IMPROVEMENT_INITIATIVE"
  | "LEADERSHIP_AUTHORIZATION";

// POST /tasks/{id}/decision for a PERIODIC_REVIEW subject returns the wf-engine dict, NOT
// DecisionResult (services/vault/review.py:245-380). The UI ignores the body (invalidate+refetch).
export interface PeriodicReviewDecisionResult {
  current_state: string;
  replayed: boolean;
  document_id?: string;
  next_review_due?: string | null;
  signature_event_id?: string | null;
}

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

// ---- S-leadership-1 FE: document-backed Top-Management RELEASE authorization (POL/OBJ/MR) ----
// Pinned to the runtime serializer (api/documents.py::_leadership_authorization +
// services/vault/leadership_authorization.release_authorization_status). POL/OBJ/MR all share the
// documented_information id, so these endpoints take a document id (objective.id / mr.id work too).
export interface LeadershipAuthorizationTask {
  id: string;
  stage_key: string;
  state: string;
  assignee_user_id: string | null;
  candidate_pool: string[] | null;
  // Task.action_expected is a nullable Text column (the action label), echoed raw by the serializer —
  // string | null (matches openapi + the inbox Task type; NOT a bool).
  action_expected: string | null;
}

export interface LeadershipAuthorizationCycle {
  instance_id: string;
  subject_id: string;
  // the pending stage key ("leadership_authorization") | "COMPLETED" | "REJECTED" | "NEEDS_ATTENTION".
  current_state: string;
  started_at: string | null;
  tasks: LeadershipAuthorizationTask[];
}

export interface LeadershipAuthorizationStatus {
  is_leadership_artifact: boolean;
  // the org flag is ON *and* it is a leadership type → release is gated.
  required: boolean;
  version_id: string | null;
  // the current Approved version already carries a Top-Management verify signature.
  authorized: boolean;
  // CX-1: the caller holds document.approve at THIS document's scope (server-computed, ABAC-aware) →
  // may start an authorization cycle. The FE ANDs it with required/Approved/in-flight state.
  can_request: boolean;
  instance: LeadershipAuthorizationCycle | null;
}

// POST /documents/{id}/request-leadership-authorization body (optional).
export interface LeadershipAuthorizationRequest {
  comment?: string | null;
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
  overdue_review: number; // S-drift-1: count of rows with overdue_review=true
}

export interface ChecklistRow {
  clause_id: string;
  number: string;
  title: string;
  pdca_phase: PdcaPhase;
  mapped_count: number;
  effective_count: number;
  status: CoverageStatus;
  overdue_review: boolean; // orthogonal to status — never a fourth coverage state
}

export interface ComplianceChecklist {
  framework: string;
  rollup: ChecklistRollup;
  rows: ChecklistRow[];
}

// ---- S-ing-4b (Ingestion Review UI) — types for the /admin/imports/* surface ----------------
// Several response bodies are `object`/additionalProperties:true in openapi.yaml; the shapes below
// are pinned from the backend (apps/api/.../services/ingestion/review.py) and typed here WITHOUT a
// contract change. The UI must tolerate `status` strings beyond ImportRunStatus (additive stages).

export type ImportRunStatus =
  | "Created"
  | "Scanning"
  | "Scanned"
  | "Extracting"
  | "Classifying"
  | "Classified"
  | "Deduping"
  | "Proposing"
  | "Proposed"
  | "Reviewing"
  | "Committing"
  | "Completed"
  | "PartiallyCommitted"
  | "Failed"
  | "Cancelled";

export type ImportKind = "DOCUMENT" | "RECORD" | "UNKNOWN";
export type ConfirmedKind = "DOCUMENT" | "RECORD"; // R10: confirmable kind, never UNKNOWN
export type ImportConfidenceBand = "HIGH" | "MEDIUM" | "LOW" | "AMBIGUOUS";
export type ImportDisposition = "included" | "excluded" | "quarantine"; // scan_flags.disposition
export type ImportReviewStatus = "included" | "excluded" | "deferred" | "undecided"; // folded
export type ImportDecisionAction = "accept" | "correct" | "exclude" | "defer";

export interface ImportRun {
  id: string;
  status: ImportRunStatus | string; // tolerate additive stages
  source_root: string;
  profile: string | null;
  ocr_enabled: boolean;
  classifier_version: string | null;
  counts: Record<string, unknown> | null; // stage-namespaced; read via narrow accessors
  error: string | null;
  created_by: string;
  committed_by: string | null;
  report_record_id: string | null;
  created_at: string | null;
  scan_started_at: string | null;
  completed_at: string | null;
}

export interface ImportClassificationEvidence {
  dimension: string;
  candidate: string;
  signal_type: string;
  weight: number;
  explanation: string;
}

export interface ImportClassification {
  kind: ImportKind;
  kind_conf: number;
  type_code: string | null;
  type_conf: number;
  clause_numbers: string[];
  clause_conf: number;
  process_names: string[] | null;
  process_conf: number;
  pdca_phase: "PLAN" | "DO" | "CHECK" | "ACT" | null;
  band: ImportConfidenceBand;
  ambiguous: boolean;
  top2_margin: number;
  classifier_version: string;
  evidence?: ImportClassificationEvidence[]; // detail endpoint only
}

// The S-ing-4 folded effective state (EffectiveFileState.as_dict()). `kind === "UNCONFIRMED"` until
// a human confirms (R10); `commit_ready === (disposition === "included" && kind in DOCUMENT|RECORD)`.
export interface ImportFileReview {
  disposition: ImportReviewStatus;
  kind: ImportKind | "UNCONFIRMED";
  identifier: string | null;
  identifier_source: string | null;
  type_code: string | null;
  clause_numbers: string[];
  process_names: string[] | null;
  owner: string | null;
  decided: boolean;
  last_action: ImportDecisionAction | null;
  commit_ready: boolean;
  identifier_collidable: boolean;
}

export interface ImportFileScanFlags {
  disposition: ImportDisposition;
  reason?: string | null;
  detail?: string | null;
}

export interface ImportFile {
  id: string;
  rel_path: string;
  filename: string;
  ext: string | null;
  size_bytes: number;
  mime_type: string | null;
  sha256: string | null;
  staged_blob_uri: string | null;
  scan_flags: ImportFileScanFlags;
  included_candidate: boolean;
  mtime: string | null;
  ctime: string | null;
  classification: ImportClassification | null;
  review: ImportFileReview | null;
}

export interface ImportFileList {
  run_id: string;
  files: ImportFile[];
}

export interface ImportDedupMembership {
  in_exact_cluster: boolean;
  in_near_cluster: boolean;
  is_canonical: boolean | null;
  redundant_of_file_id: string | null;
  in_version_family: boolean;
  is_effective: boolean | null;
  superseded_by_file_id: string | null;
}

export interface ImportProposalNode {
  proposed_identifier: string | null;
  identifier_source: string | null;
  target_ia_path: string | null;
  proposed_owner: string | null;
  owner_source: string | null;
  conflict_flags: Record<string, unknown>;
}

export interface ImportExtract {
  status: "extracted" | "ocr" | "empty" | "failed";
  full_text: string | null;
  text_truncated: boolean;
  header_block: string | null;
  language: string | null;
  ocr_used: boolean;
  ocr_confidence: number | null;
  char_count: number | null;
  page_count: number | null;
  error: string | null;
  extractor_version: string | null;
}

// The DETAIL endpoint (GET /admin/imports/{id}/files/{fid}) nests the review under `effective`
// (get_file_review → { effective: <flat ImportFileReview>, decision_history: [...] }) — UNLIKE the LIST
// endpoint, whose `review` IS the flat ImportFileReview. Override the inherited flat `review` here.
export interface ImportFileReviewDetail {
  effective: ImportFileReview;
  decision_history: ImportDecision[];
}

export interface ImportFileDetail extends Omit<ImportFile, "review"> {
  run_id: string;
  extract: ImportExtract | null;
  dedup: ImportDedupMembership;
  proposal: ImportProposalNode | null;
  review: ImportFileReviewDetail | null;
}

export interface ImportDupeCluster {
  id: string;
  method: "exact" | "near";
  member_file_ids: string[];
  canonical_file_id: string;
  jaccard: number | null;
  evidence: Record<string, unknown>;
}
export interface ImportDupeClusterList {
  run_id: string;
  clusters: ImportDupeCluster[];
}

export interface ImportVersionFamily {
  id: string;
  family_key: string;
  base_name: string;
  doc_code: string | null;
  ordered_member_file_ids: string[];
  effective_file_id: string;
  reconstruct_revision_chain: boolean;
  evidence: Record<string, unknown>;
}
export interface ImportVersionFamilyList {
  run_id: string;
  families: ImportVersionFamily[];
}

// GET /admin/imports/{id}/checklist (review.py:983-994). `ready === blocking.length === 0`; advisory
// never affects ready. A blocker carries a `type` + type-specific members (kept loose) — e.g. an
// `identifier` + a `file_ids` list of the offending files.
export interface ImportChecklistBlocker {
  type: string;
  identifier?: string;
  file_ids?: string[];
  [k: string]: unknown;
}
export interface ImportChecklistReviewStats {
  keep_items: number;
  decided: number;
  accepted: number;
  corrected: number;
  excluded: number;
  deferred: number;
  undecided: number;
  kind_confirmed: number;
  commit_ready: number;
}
export interface ImportChecklist {
  run_id: string;
  status: string;
  ready: boolean;
  blocking: ImportChecklistBlocker[];
  advisory: {
    star_coverage?: { total?: number; satisfied?: number; [k: string]: unknown } | null;
    unknown_low?: number;
    kind_unconfirmed?: number;
  };
  review: ImportChecklistReviewStats;
}

export interface ImportDecision {
  id: string;
  action: string; // accept|correct|merge|split|exclude|defer
  file_id: string | null;
  cluster_id: string | null;
  target_kind: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  decided_by: string;
  decided_at: string;
}
export interface ImportDecisionLog {
  run_id: string;
  decisions: ImportDecision[];
}

// ---- request bodies ----
export interface ImportDecisionAfter {
  kind?: ConfirmedKind;
  type_code?: string;
  clause_numbers?: string[];
  process_names?: string[];
  identifier?: string;
  owner?: string;
}
export interface ImportFileDecisionRequest {
  action: ImportDecisionAction;
  after?: ImportDecisionAfter;
  reason?: string | null;
}
export interface ImportBulkSelector {
  kind?: string | null;
  band?: string | null;
  disposition?: string | null;
}
export interface ImportBulkDecisionRequest {
  action: ImportDecisionAction;
  file_ids?: string[] | null;
  selector?: ImportBulkSelector | null;
  after?: ImportDecisionAfter;
  reason?: string | null;
}
export interface ImportMergeRequest {
  file_ids: string[];
  effective_file_id?: string | null;
  reconstruct_revision_chain?: boolean | null;
  reason?: string | null;
}
export interface ImportSplitRequest {
  target_kind: "dupe_cluster" | "version_family";
  target_id: string;
  separate_file_ids: string[];
  reason?: string | null;
}
export interface ImportRunCreate {
  source_root: string;
  profile?: string | null;
  ocr_enabled?: boolean;
  classifier_version?: string | null;
}
// Decision/merge/split results are loosely typed — the UI invalidates + refetches rather than reading
// the body; keep a permissive shape so a handler can return e.g. {applied: 3} or the family/split row.
export type ImportMutationResult = Record<string, unknown>;

// ---- S-web-7 (Nonconformity & CAPA) -----------------------------------------------------
export type NcSeverity = "Critical" | "Major" | "Minor";
export type CapaSource = "audit" | "process" | "complaint" | "review_output" | "risk";
export type CapaCloseState =
  | "Raised"
  | "Containment"
  | "RootCause"
  | "ActionPlan"
  | "Implement"
  | "Verify"
  | "Closed"
  | "Rejected";

export interface Capa {
  id: string;
  identifier: string | null; // the record identifier, e.g. "REC-000031"
  title: string | null;
  source: CapaSource;
  severity: NcSeverity;
  process_id: string | null;
  close_state: CapaCloseState;
  cycle_marker: number; // effectiveness-loop counter; >0 => the Verify→RootCause loop ran
  origin_finding_id: string | null; // NULL in v1
  raised_by: string | null; // detail-only (the Raised stage's actor); null on list rows
  created_at: string | null;
  target_completion_date: string | null; // S-capa-overdue: operator-set target; null if unset
  overdue: boolean; // S-capa-overdue: server-computed (target_completion_date < today)
  stages?: CapaStage[]; // detail-only
}

export interface CapaStage {
  id: string;
  stage: CapaCloseState;
  content_block: Record<string, unknown>; // free-form
  cycle_marker: number;
  created_by: string; // an app_user id; resolve via the user directory
  created_at: string;
  evidence_links?: EvidenceLink[]; // detail-only; links pointing AT this stage (target_type=capa_stage)
}

export interface CapaList {
  data: Capa[];
}

// The per-stage PROJECTION of an evidence-for link, as returned inside CapaStage.evidence_links by the
// CAPA detail (list_stage_evidence) — it omits the general link's target_type/target_id (the stage IS the
// target). The write uses EvidenceLinkBody; 7b never lists a record's full evidence links.
export interface EvidenceLink {
  id: string;
  record_id: string;
  record_identifier: string | null;
  link_reason: string | null;
  created_at: string | null;
}

// GET /capas/{id}/approval — the latest action-plan approval cycle, or null (no cycle opened).
export interface CapaApproval {
  instance: {
    id: string;
    current_state: string; // a stage key while running; COMPLETED | REJECTED | NEEDS_ATTENTION terminal
    definition_version: number;
    subject_type: string;
    subject_id: string;
    // the _approval_task serializer omits instance_id (the instance is the parent) — narrow the type so a
    // consumer can't read a field the API never sends.
    tasks: Omit<Task, "instance_id">[];
  };
  proposed_action_plan: Record<string, unknown> | null;
}

// GET /records (a bare array; filter-not-403) — the evidence picker's source. Minimal shape.
export interface RecordSummary {
  id: string;
  identifier: string | null;
  title: string;
  record_type: string;
}

// ---- request bodies (CAPA writes) ----
export interface CapaRaiseBody {
  title: string;
  severity: NcSeverity;
  source?: CapaSource;
  process_id?: string;
  problem?: string;
}
export interface StageBlockBody {
  content_block: Record<string, unknown>;
}
export interface CapaVerifyBody {
  decision: "effective" | "not_effective";
  content_block: Record<string, unknown>;
}
export interface EvidenceLinkBody {
  target_type: "capa_stage";
  target_id: string;
  link_reason?: string;
}

// ---- S-web-7c (Complaint + NCR intake) ----
export type NcrSource = "audit" | "process" | "complaint" | "internal";
export type NcrDisposition = "use_as_is" | "rework" | "scrap" | "return" | "concession" | "regrade";

// Pinned to the _complaint serializer (api/capa.py:217). identifier may be null (get_identifier).
export interface Complaint {
  id: string;
  identifier: string | null;
  customer: string | null;
  received_at: string | null;
  channel: string | null;
  description: string;
  severity: NcSeverity | null;
  spawned_capa_id: string | null; // set once a CAPA has been spawned (idempotency latch)
}
export interface ComplaintList {
  data: Complaint[];
}

// Pinned to the _ncr serializer (api/capa.py:230). identifier is NCR-NNN, non-null (ncr.identifier nullable=False).
export interface Ncr {
  id: string;
  identifier: string;
  source: NcrSource;
  description: string;
  severity: NcSeverity;
  process_id: string | null;
  disposition: NcrDisposition | null;
  disposition_authorized_by: string | null;
  disposition_notes: string | null;
  disposed_at: string | null;
  created_at: string;
}
export interface NcrList {
  data: Ncr[];
}

export interface ComplaintCreateBody {
  description: string;
  customer?: string;
  received_at?: string;
  channel?: string;
  severity?: NcSeverity;
}
export interface SpawnCapaBody {
  severity?: NcSeverity;
  process_id?: string;
}
export interface NcrCreateBody {
  source: NcrSource;
  description: string;
  severity: NcSeverity;
  process_id?: string;
}
export interface NcrDispositionBody {
  disposition: NcrDisposition;
  notes?: string;
}

// ---- S-web-7d audits & findings (pinned to api/audits.py _program/_plan/_audit/_finding) ----
export type AuditState =
  "Scheduled" | "Planned" | "InProgress" | "FindingsDraft" | "Reported" | "Closing" | "Closed";
export type FindingType = "NC" | "OBSERVATION" | "OFI";

export interface AuditProgram {
  id: string;
  identifier: string; // AUDPROG-NNN
  title: string;
  period: string | null;
  coverage: Record<string, unknown> | null;
  archived: boolean;
  created_at: string;
}
export interface AuditPlan {
  id: string;
  program_id: string;
  auditee_process_id: string | null;
  lead_auditor_user_id: string | null;
  scheduled_date: string | null; // date (YYYY-MM-DD)
  checklist_ref: string | null;
  created_at: string;
}
export interface Audit {
  id: string;
  identifier: string | null; // S-web-7d enrichment (REC-…)
  title: string | null; // S-web-7d enrichment
  plan_id: string;
  lead_auditor_user_id: string | null;
  state: AuditState;
  started_at: string | null; // date
  completed_at: string | null; // date
  result_summary: string | null; // never written in v1 — not rendered
  created_at: string | null; // S-web-7d enrichment
}
export interface Finding {
  id: string;
  identifier: string | null;
  title: string | null; // S-web-7d enrichment (the logged summary / correction reason)
  audit_id: string;
  finding_type: FindingType;
  severity: NcSeverity | null;
  clause_ref: string | null;
  process_ref: string | null;
  auto_capa_id: string | null;
  correction_of: string | null;
  superseded_by_correction: string | null;
}
export interface AuditProgramList {
  data: AuditProgram[];
}
export interface AuditPlanList {
  data: AuditPlan[];
}
export interface AuditList {
  data: Audit[];
}
export interface FindingList {
  data: Finding[];
}

// request bodies
export interface AuditProgramCreateBody {
  title: string;
  period?: string;
}
export interface AuditProgramUpdateBody {
  title?: string;
  period?: string;
  archived?: boolean;
}
export interface AuditPlanCreateBody {
  auditee_process_id?: string;
  lead_auditor_user_id?: string;
  scheduled_date?: string;
  checklist_ref?: string;
}
export interface AuditCreateBody {
  plan_id: string;
  title?: string;
  lead_auditor_user_id?: string;
}
export interface FindingCreateBody {
  finding_type: FindingType;
  severity?: NcSeverity;
  clause_ref?: string;
  process_ref?: string;
  summary?: string;
}
export interface FindingCorrectionBody {
  finding_type: FindingType;
  severity?: NcSeverity;
  clause_ref?: string;
  process_ref?: string;
  reason?: string;
}

// GET /processes (bare array; _process in api/processes.py) — the SPA reads id + name only,
// but extra fields arrive (structural subset typing).
export interface ProcessRow {
  id: string;
  name: string;
}

// ---- S-web-8 drift surface ----

export type DriftScanStatusValue = "CLEAN" | "DIVERGENT" | "FAILED";

// One drift_scan row (openapi DriftScanSummary). counts is an OPEN bag — unknown keys are
// additive (S-drift-3 §10a); render generically, never destructure a closed set.
export interface DriftScanSummary {
  status: DriftScanStatusValue;
  started_at: string;
  finished_at: string | null;
  counts: Record<string, unknown>;
  triggered_by: "beat" | "sync" | "cli";
}

export interface DriftStatus {
  scans: { MIRROR: DriftScanSummary | null; BLOB_REHASH: DriftScanSummary | null };
  blob_coverage: {
    total: number;
    never_verified: number;
    failing: number; // unresolved verify_failed_at pins — the live alarm count
    oldest_verified_at: string | null;
  };
  superseded_copies: { versions: number; copies: number };
}

export interface SupersededCopyRow {
  document_id: string;
  identifier: string;
  version_id: string;
  revision_label: string;
  version_state: "Superseded" | "Obsolete";
  current_revision_label: string | null; // null when the document is obsoleted
  exported: number;
  printed: number;
  last_copy_at: string;
}

export interface SupersededCopies {
  total: { versions: number; copies: number }; // FULL-set totals, not the page
  items: SupersededCopyRow[];
}

// ---- S-ack-2 (Acknowledgements UI) ------------------------------------------------------
// All shapes pinned to S-ack-1: api/documents.py (_distribution_payload, DistributionUpdate),
// services/ack/queries.py (coverage_counts/coverage_matrix), services/ack/decide.py.

export type DistributionTargetType = "user" | "org_role" | "process" | "folder";

export interface DistributionEntry {
  id: string;
  target_type: DistributionTargetType;
  target_id: string;
  ack_required: boolean;
  created_at: string;
}

// coverage is null when the doc has no Effective version (queries.coverage_counts).
export interface Coverage {
  required: number;
  acknowledged: number;
  pending: number;
  overdue: number;
}

export interface DistributionPayload {
  acknowledgement_required: boolean;
  entries: DistributionEntry[];
  coverage: Coverage | null;
}

export type AckStatus = "acknowledged" | "pending" | "overdue";

export interface AckMatrixRow {
  user_id: string;
  display_name: string | null;
  status: AckStatus;
  acknowledged_at: string | null;
  acknowledged_revision_label: string | null;
  due_at: string | null;
}

// POST /documents/{id}/distribution body. add_entries items: ack_required defaults true server-side.
export interface DistributionEntryCreate {
  target_type: "user" | "org_role"; // process/folder are 422 (target_kind_deferred) — never sent
  target_id: string;
  ack_required?: boolean;
}
export interface DistributionUpdateBody {
  acknowledgement_required?: boolean | null;
  add_entries?: DistributionEntryCreate[];
}

// POST /tasks/{id}/decision (DOC_ACK) → the engine result + the three ack fields (services/ack/decide.py).
export interface AckDecisionResult {
  task_id: string;
  instance_id: string;
  stage_key: string;
  outcome: string | null;
  decided_at: string | null;
  decided_by: string;
  stage_state: string;
  current_state: string;
  signature_spec: Record<string, unknown> | null;
  comment: string | null;
  replayed: boolean;
  document_id: string;
  document_version_id: string | null;
  acknowledgement_id: string | null;
}

// GET /roles (authz.py; role.read — QMS Owner + admin hold it). The editor's org_role picker source.
export interface RoleSummary {
  id: string;
  name: string;
  description: string | null;
  is_reserved: boolean;
}

// ---- S-obj-2 Quality Objectives (clause 6.2) — pinned to api/objectives.py serializers ----
export type ObjectiveDirection = "HIGHER_IS_BETTER" | "LOWER_IS_BETTER";
export type ObjectiveRag = "green" | "amber" | "red" | "unmeasured";
export type ObjectiveAttainment = "in_progress" | "met" | "missed";
// The 7-state document lifecycle — an objective IS a kind=DOCUMENT subtype (R44), so its state
// union is the document one (S-obj-4 unified the alias so StateBadge renders both).
export type ObjectiveState = DocumentCurrentState;

// Pinned to the api build_commitment serializer (domain/objectives/commitment.py) — all decimals
// are STRINGS, direction is the enum .value, dates are ISO strings.
export interface ObjectiveCommitment {
  target_value: string;
  unit: string;
  direction: ObjectiveDirection;
  due_date: string;
  at_risk_threshold: string | null;
  baseline_value: string | null;
  policy_id: string | null;
}

export interface ObjectivePlan {
  id: string;
  objective_id: string;
  action: string;
  resource: string | null;
  responsible_user_id: string | null;
  due_date: string | null;
}

export interface Objective {
  id: string;
  identifier: string;
  title: string;
  current_state: ObjectiveState;
  target_value: string; // decimal string
  unit: string;
  baseline_value: string | null;
  current_value: string | null;
  direction: ObjectiveDirection;
  at_risk_threshold: string | null;
  due_date: string; // ISO date
  process_id: string | null;
  policy_id: string | null;
  rag: ObjectiveRag;
  pct_toward_target: number | null; // JSON number | null — NOT a string
  attainment: ObjectiveAttainment;
  plans: ObjectivePlan[]; // [] in list/scorecard rows; populated on detail GET
  // S-obj-3/4 (detail-only; absent on list/scorecard rows; effective_from null until Effective;
  // pending_commitment = the in-edit working commitment when it diverges from governing, else null):
  capabilities?: { submit: boolean; release: boolean; edit: boolean; start_revision: boolean };
  effective_from?: string | null;
  pending_commitment?: ObjectiveCommitment | null;
}

// GET /objectives/policy — the Effective Quality Policy singleton, or null.
export interface EffectivePolicy {
  id: string;
  identifier: string;
  title: string;
}

export interface Measurement {
  id: string;
  objective_id: string | null;
  record_id: string;
  period: string; // ISO date
  value: string; // decimal string
  target_at_capture: string; // decimal string
  unit: string;
  source: string | null;
  created_at: string; // ISO date-time
  rag: ObjectiveRag; // S-obj-charts — per-reading RAG (never "unmeasured" in practice)
}

export interface ObjectiveScorecard {
  total: number;
  on_target: number;
  by_rag: { green: number; amber: number; red: number; unmeasured: number };
  objectives: Objective[];
}

export interface ObjectiveListResponse {
  data: Objective[];
}
export interface MeasurementListResponse {
  data: Measurement[];
}

export interface ObjectiveCreateBody {
  title: string;
  target_value: string;
  unit: string;
  direction: ObjectiveDirection;
  due_date: string;
  baseline_value?: string | null;
  at_risk_threshold?: string | null;
  process_id?: string | null;
  policy_id?: string | null;
}
// PATCH /objectives/{id} (S-obj-4) — the SPA sends the full commitment (explicit null clears
// the nullable fields), EXCEPT policy_id which is omitted (server-inherits) whenever no current
// Effective Policy loaded — sending null would silently unlink, sending a lapsed seed would 422.
// The API also accepts partials.
export interface ObjectiveUpdateBody {
  target_value: string;
  unit: string;
  direction: ObjectiveDirection;
  due_date: string;
  at_risk_threshold: string | null;
  baseline_value: string | null;
  policy_id?: string | null;
}
export interface MeasurementCreateBody {
  period: string;
  value: string;
  unit: string;
  source?: string | null;
}
export interface PlanCreateBody {
  action: string;
  resource?: string | null;
  responsible_user_id?: string | null;
  due_date?: string | null;
}

// ---- S-mr-2 Management Reviews (clause 9.3) — pinned to api/mgmt_review.py serializers ----
export type MgmtReviewCloseState = "ActionsTracked" | "Closed";
export type ReviewInputType =
  | "PRIOR_ACTIONS"
  | "CONTEXT_CHANGES"
  | "CUSTOMER_SATISFACTION"
  | "OBJECTIVES_STATUS"
  | "PROCESS_PERFORMANCE"
  | "NONCONFORMITIES_CAPA"
  | "MONITORING_RESULTS"
  | "AUDIT_RESULTS"
  | "SUPPLIER_PERFORMANCE"
  | "RESOURCE_ADEQUACY"
  | "RISK_OPPORTUNITY_ACTIONS"
  | "IMPROVEMENT_OPPORTUNITIES";
export type ReviewOutputType = "DECISION" | "ACTION" | "IMPROVEMENT";
export type MgmtReviewState = "current" | "due_soon" | "overdue";

export interface AttendeeRow {
  name: string;
  role?: string;
  user_id?: string;
}

// source_ref is free-form per input_type: an available row carries `summary`, a gap row `reason`.
export interface ReviewInputSourceRef {
  available: boolean;
  generated_at: string;
  summary?: Record<string, unknown>;
  reason?: string;
}
export interface ReviewInput {
  id: string;
  management_review_id: string;
  input_type: ReviewInputType;
  available: boolean;
  source_ref: ReviewInputSourceRef;
  position: number;
}
export interface ReviewOutput {
  id: string;
  management_review_id: string;
  output_type: ReviewOutputType;
  description: string;
  owner_user_id: string | null;
  due_date: string | null;
  spawned_task_id: string | null;
  spawned_capa_id: string | null;
}
export interface MgmtReview {
  id: string;
  identifier: string;
  title: string;
  current_state: DocumentCurrentState;
  period_label: string | null;
  review_date: string | null;
  attendees: AttendeeRow[] | null;
  close_state: MgmtReviewCloseState | null;
  closed_at: string | null;
  created_at: string;
}
export interface MgmtReviewDetail extends MgmtReview {
  inputs: ReviewInput[];
  outputs: ReviewOutput[];
  capabilities?: { release: boolean };
}
export interface MgmtReviewListResponse {
  data: MgmtReview[];
}
export interface MgmtReviewNextDue {
  cadence_months: number;
  last_review_effective_from: string | null;
  next_review_due: string | null;
  review_state: MgmtReviewState | null;
  owner_configured: boolean;
}
export interface MgmtReviewCreateBody {
  title: string;
  period_label?: string;
  review_date?: string;
}
export interface MgmtReviewMetaBody {
  period_label?: string | null;
  review_date?: string | null;
  attendees?: AttendeeRow[] | null;
}
export interface ReviewOutputCreateBody {
  output_type: ReviewOutputType;
  description: string;
  owner_user_id?: string | null;
  due_date?: string | null;
}
export interface ReviewOutputUpdateBody {
  output_type?: ReviewOutputType;
  description?: string;
  owner_user_id?: string | null;
  due_date?: string | null;
}

// ---- S-dcr-ui-1 (Document Change Request — read spine) — pinned to api/dcr.py serializers ----
// _dcr (api/dcr.py:118-137), _stage_event (:151-160), _impact (:140-148).
// ⚠ The plan's header said "16 fields" but the live _dcr serializer (lines 120-136) has exactly 15.
// ChangeSignificance ("MAJOR"|"MINOR") is already declared above — reused here, not re-declared.
export type DcrChangeType = "REVISE" | "CREATE" | "RETIRE";
export type DcrReasonClass =
  | "regulatory"
  | "audit_finding"
  | "capa"
  | "process_improvement"
  | "error_correction"
  | "periodic_review"
  | "customer_requirement"
  | "mgmt_review"
  | "other";
export type DcrSourceLinkType = "capa" | "finding" | "mgmt_review" | "risk";
export type DcrState =
  | "Open"
  | "Assessed"
  | "Routed"
  | "InApproval"
  | "Approved"
  | "Implemented"
  | "Closed"
  | "Cancelled"
  | "Rejected";

export interface Dcr {
  id: string;
  identifier: string; // DCR-{YYYY}-{NNNN}
  target_document_id: string | null; // null for CREATE
  target_identifier?: string | null; // S-optimize-1: the target document's identifier (list + detail); null for CREATE
  target_title?: string | null; // S-optimize-1: the target document's title; null for CREATE / unresolved
  change_type: DcrChangeType;
  change_significance: ChangeSignificance; // reuses the existing "MAJOR"|"MINOR" type
  reason_class: DcrReasonClass;
  reason_text: string;
  source_link_type: DcrSourceLinkType | null;
  source_link_id: string | null; // polymorphic, no FK
  proposed_effective_from: string | null; // ISO datetime
  resulting_version_id: string | null; // set at implement (REVISE/CREATE); null for RETIRE / pre-implement
  resulting_document_id?: string | null; // ui-4: detail-only (GET /dcrs/{id}); the document the resulting version belongs to
  state: DcrState;
  decision: string | null; // null until approval/rejection
  created_by: string; // an app_user.id
  created_at: string; // ISO datetime
}

export interface DcrStageEvent {
  id: string;
  from_state: DcrState | null; // null on genesis
  to_state: DcrState;
  actor_id: string | null; // null for system/Beat
  comment: string | null;
  payload: Record<string, unknown> | null; // free JSONB — not rendered in the read spine
  occurred_at: string;
}

export interface DcrCapabilities {
  assess: boolean;
  route: boolean;
  implement: boolean;
  close: boolean;
}

export interface DcrDetail extends Dcr {
  stage_events: DcrStageEvent[]; // GET /dcrs/{id} augments _dcr with this
  capabilities?: DcrCapabilities; // detail-only; the FE derives Edit←assess, Cancel←close
}

export interface DcrList {
  data: Dcr[];
}

export interface DcrImpact {
  id: string;
  dimension: string; // one of 7
  auto_populated: Record<string, unknown> | null;
  requester_annotation: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface DcrImpactList {
  data: DcrImpact[];
}

// ---- S-dcr-ui-2a write bodies (pinned to api/dcr.py DcrCreate/DcrPatch/DcrCancel + the two spawn bodies) ----
export interface DcrCreateBody {
  change_type: DcrChangeType;
  change_significance: ChangeSignificance;
  reason_class: DcrReasonClass;
  reason_text: string;
  target_document_id?: string | null;
  source_link_type?: DcrSourceLinkType | null;
  source_link_id?: string | null;
  proposed_effective_from?: string | null;
}
// PATCH while Open — every field optional; null/absent = unchanged (cannot clear a field, mirrors the backend).
export interface DcrPatchBody {
  reason_text?: string;
  reason_class?: DcrReasonClass;
  change_significance?: ChangeSignificance;
  proposed_effective_from?: string | null;
}
export interface DcrCancelBody {
  comment?: string;
}
// Shared by both spawn endpoints (CAPA defaults reason_class=capa, MR forces mgmt_review — neither carries it).
export interface DcrSpawnBody {
  change_type: DcrChangeType;
  change_significance: ChangeSignificance;
  reason_text: string;
  target_document_id?: string | null;
  proposed_effective_from?: string | null;
}
export interface DcrImplementBody {
  resulting_version_id?: string | null; // CREATE only (deferred in the SPA)
  force_retire?: boolean; // RETIRE only
  override_justification?: string | null;
}

// ---- S-improvement-3 Improvement Initiatives (clause 10.3, R46) ----
// Pinned to api/improvement.py `_initiative` / `_stage_event` serializers. An initiative is an
// own-table mutable-state workflow object (the DCR doctrine, NON-★); the append-only stage trail is a
// SEPARATE endpoint (GET /improvement-initiatives/{id}/stage-events), NOT embedded in the detail
// (unlike DcrDetail). There is NO server-computed `capabilities` block — write affordances gate on
// usePermissions().can("improvement.manage") + the FSM stage (the management-review cockpit precedent).
export type InitiativeStage = "Open" | "InProgress" | "Completed" | "Closed" | "Cancelled";
export type InitiativeSource = "OFI" | "review" | "manual";

export interface Initiative {
  id: string;
  identifier: string; // IMP-{YYYY}-{NNNN}
  title: string;
  description: string | null;
  target_outcome: string | null;
  source: InitiativeSource;
  source_link_id: string | null; // finding.id (OFI) / review_output.id (review) / null (manual)
  process_id: string | null;
  owner_user_id: string | null; // an app_user.id
  stage: InitiativeStage;
  opened_at: string; // ISO datetime
  closed_at: string | null; // set on Closed/Cancelled
  created_by: string; // an app_user.id
  created_at: string; // ISO datetime
  updated_at: string | null; // null until first edit/transition
}

export interface InitiativeList {
  data: Initiative[];
}

export interface InitiativeStageEvent {
  id: string;
  from_state: InitiativeStage | null; // null on the genesis (Open) event
  to_state: InitiativeStage;
  actor_id: string | null; // an app_user.id; null reserved for future system moves
  comment: string | null;
  payload: Record<string, unknown> | null; // genesis {source}; Closed-with-outcome {outcome}; else null
  // S-improvement-4: null for every unsigned move; the FK to the leadership `verify` signature on the
  // signed authorized-close event (the timeline's "verified by leadership" marker).
  signed_event_id: string | null;
  occurred_at: string;
}

export interface InitiativeStageEventList {
  data: InitiativeStageEvent[];
}

// ---- S-improvement-4: the engine-routed Top-Management authorization cycle (pinned to api/improvement.py
// _authorization). The latest workflow instance + its tasks, or null when never requested. ----
export interface InitiativeAuthorizationTask {
  id: string;
  stage_key: string;
  state: string;
  assignee_user_id: string | null;
  candidate_pool: string[] | null;
  action_expected: string | null;
}
export interface InitiativeAuthorization {
  instance_id: string;
  subject_id: string;
  // The pending stage key, COMPLETED (granted → the initiative is Closed), REJECTED, or
  // NEEDS_ATTENTION (no Top-Management member assigned).
  current_state: string;
  started_at: string | null;
  tasks: InitiativeAuthorizationTask[];
}
export interface InitiativeAuthorizationRequestBody {
  comment?: string | null;
}

// ---- write bodies (pinned to api/improvement.py InitiativeCreate / InitiativePatch / InitiativeTransition) ----
export interface InitiativeCreateBody {
  title: string;
  description?: string | null;
  target_outcome?: string | null;
  process_id?: string | null;
  owner_user_id?: string | null;
}
// PATCH — every field optional; null/absent = unchanged (cannot clear a field, mirrors the backend).
export interface InitiativePatchBody {
  title?: string;
  description?: string | null;
  target_outcome?: string | null;
  owner_user_id?: string | null;
  process_id?: string | null;
}
export interface InitiativeTransitionBody {
  to_state: InitiativeStage;
  comment?: string | null; // required server-side for a Closed/Cancelled move (422 otherwise)
  outcome?: string | null; // folded into the sealed stage_event.payload on a Closed move only
}

// ---- S-improvement-3b spawn body (pinned to FindingInitiativeCreate (api/audits.py) +
// OutputInitiativeCreate (api/mgmt_review.py)). Raise an initiative FROM an ISO origin — a 1:N idempotent
// recording act (Idempotency-Key header, 201 new / 200 replay). `source`/`source_link_id` derive
// server-side. `process_id` is present ONLY for the MR-output spawn (SYSTEM fallback when null/absent);
// the finding spawn derives the process from the audit's auditee process. ----
export interface InitiativeSpawnBody {
  title: string;
  description: string | null;
  target_outcome: string | null;
  owner_user_id: string | null;
  process_id?: string | null; // MR-output spawn only
}

// ---- S-risk-4 (Risk & Opportunity register, clause 6.1) — pinned to api/risk.py serializers ----
export type RiskType = "risk" | "opportunity";
// The RAG band, server-derived from risk_rating against the governing version's FROZEN criteria
// (never re-graded client-side). unscored is forward-compat (v1 always derives a rating ≥1 → low+).
export type RiskBand = "critical" | "high" | "medium" | "low" | "unscored";
export type RiskScoringMethod = "5x5_matrix";
// The RSK head is a kind=DOCUMENT subtype → its lifecycle state is the 7-state document one.
export type RiskRegisterState = DocumentCurrentState;

// One risk row — api/risk.py `_risk(...)`. band/band_tone/band_rank are server-graded; the FE renders
// them verbatim (R49 L2). band_tone is the StatusBadge Tone subset for the four bands.
export interface RiskRow {
  id: string;
  register_doc_id: string;
  type: RiskType;
  description: string;
  process_id: string | null;
  clause_id: string | null;
  likelihood: number;
  severity: number;
  risk_rating: number;
  scoring_method: RiskScoringMethod;
  band: RiskBand;
  band_tone: "danger" | "warning" | "success" | "neutral";
  band_rank: number;
  treatment: string | null;
  effectiveness: string | null;
  linked_capa_id: string | null;
  row_version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface RiskListResponse {
  data: RiskRow[];
}

// The RSK register head lifecycle status — api/risk.py `_register_status` / `_NO_REGISTER`.
// can_release/can_manage are server-computed (S-context-fe) and present on GET /risks/register ONLY
// (optional — the lifecycle-action responses omit them; the FE refetches the GET after each mutation).
// can_release is the faithful multi-axis release gate (a single-axis FE probe can't replicate it).
export interface RiskRegisterStatus {
  exists: boolean;
  register_doc_id: string | null;
  identifier: string | null;
  state: RiskRegisterState | null;
  current_effective_version_id: string | null;
  has_governing: boolean;
  can_release?: boolean;
  can_manage?: boolean;
}

// POST /risks/register/publish body — api/risk.py `RegisterPublish`. The change reason is optional
// (the server defaults a system reason when omitted/empty; ignored on a no-freeze re-publish).
export interface RiskRegisterPublishBody {
  change_reason: string | null;
}

// GET /risks/summary — the governing high-risk read-of-record (S-risk-4a). published:false + all-zero
// counts before the first publish/release. high_risk = critical + high (the danger-tone set).
export interface RiskSummary {
  published: boolean;
  total: number;
  by_band: Record<RiskBand, number>;
  high_risk: number;
  by_type: Record<RiskType, number>;
  effectiveness: { treated: number; recorded: number; pending: number };
}

export interface RiskCreateBody {
  type: RiskType;
  description: string;
  likelihood: number;
  severity: number;
  scoring_method?: RiskScoringMethod;
  process_id?: string;
  clause_id?: string;
  treatment?: string;
}

// Partial PATCH — omitted ≠ null; an explicit null clears a nullable field. scoring_method is
// write-once (server-rejected on change); risk_rating is server-derived (not settable).
export interface RiskUpdateBody {
  type?: RiskType;
  description?: string;
  likelihood?: number;
  severity?: number;
  process_id?: string | null;
  clause_id?: string | null;
  treatment?: string | null;
  effectiveness?: string | null;
}

// ---- S-context-fe (Context register, clause 4.1) — pinned to api/context.py serializers ----
// Clause 4.1 context is ORG-LEVEL (no process axis) and PURELY CATEGORICAL (no graded band) — so,
// unlike risk, there is no process_id / band / tone / score; classification is the ISO spine and
// category is the optional (nullable) SWOT axis.
export type ContextClassification = "internal" | "external";
export type ContextCategory = "strength" | "weakness" | "opportunity" | "threat";
export type ContextStatus = "active" | "closed";
// The CTX head is a kind=DOCUMENT subtype → its lifecycle state is the 7-state document one.
export type ContextRegisterState = DocumentCurrentState;

// One context issue — api/context.py `_context_issue`. category is the nullable SWOT axis; status is
// never null (a new issue is always "active"; retire by closing).
export interface ContextIssue {
  id: string;
  register_doc_id: string;
  classification: ContextClassification;
  category: ContextCategory | null;
  status: ContextStatus;
  description: string;
  last_reviewed_at: string | null;
  row_version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ContextListResponse {
  data: ContextIssue[];
}

// The CTX register head lifecycle status — api/context.py `_register_status` / `_NO_REGISTER`. The
// optional can_release/can_manage are server-computed (GET /context/register only) — the faithful
// steward gate (see RiskRegisterStatus).
export interface ContextRegisterStatus {
  exists: boolean;
  register_doc_id: string | null;
  identifier: string | null;
  state: ContextRegisterState | null;
  current_effective_version_id: string | null;
  has_governing: boolean;
  can_release?: boolean;
  can_manage?: boolean;
}

// POST /context/register/publish body — api/context.py `RegisterPublish` (optional change reason).
export interface ContextRegisterPublishBody {
  change_reason: string | null;
}

// GET /context/summary — the GOVERNING (Effective) categorical read-of-record (S-context-2).
// published:false + all-zero counts before the first publish/release. by_category carries an
// "uncategorized" bucket for NULL-category rows; active == by_status.active; never_reviewed counts
// rows with no last_reviewed_at.
export interface ContextRegisterSummary {
  published: boolean;
  total: number;
  by_classification: Record<ContextClassification, number>;
  by_category: Record<ContextCategory | "uncategorized", number>;
  by_status: Record<ContextStatus, number>;
  active: number;
  never_reviewed: number;
}

export interface ContextCreateBody {
  classification: ContextClassification;
  description: string;
  category?: ContextCategory | null;
  last_reviewed_at?: string | null;
}

// Partial PATCH — omitted ≠ null; an explicit null clears category/last_reviewed_at. A new issue has
// no status field on create (always "active"); status is settable here (active/closed).
export interface ContextUpdateBody {
  classification?: ContextClassification;
  category?: ContextCategory | null;
  status?: ContextStatus;
  description?: string;
  last_reviewed_at?: string | null;
}

// ---- S-interested-parties-fe (Interested Parties register, clause 4.2) — pinned to
// api/interested_parties.py serializers + the openapi InterestedParty* schemas (R51) ----
// Clause 4.2 is ORG-LEVEL (no process axis) and PURELY CATEGORICAL (no graded band) — like context,
// unlike risk. party_type is the 7-way ISO-4.2 spine (NOT NULL); influence is the optional (nullable)
// ORDERED relevance axis; party_name is the anchor identifier + needs_expectations the body (two text
// fields, vs context's single description).
export type InterestedPartyType =
  "customer" | "regulator" | "supplier" | "employee" | "owner" | "community" | "partner";
export type InterestedPartyInfluence = "low" | "medium" | "high";
export type InterestedPartyStatus = "active" | "closed";
// The IPR head is a kind=DOCUMENT subtype → its lifecycle state is the 7-state document one.
export type InterestedPartyRegisterState = DocumentCurrentState;

// One interested party — api/interested_parties.py `_interested_party`. influence is the nullable
// ordered axis; status is never null (a new party is always "active"; retire by closing).
export interface InterestedParty {
  id: string;
  register_doc_id: string;
  party_type: InterestedPartyType;
  party_name: string;
  needs_expectations: string;
  influence: InterestedPartyInfluence | null;
  status: InterestedPartyStatus;
  last_reviewed_at: string | null;
  row_version: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface InterestedPartyListResponse {
  data: InterestedParty[];
}

// The IPR register head lifecycle status — api/interested_parties.py `_register_status` /
// `_NO_REGISTER`. The optional can_release/can_manage are server-computed (GET
// /interested-parties/register only) — the faithful steward gate (see RiskRegisterStatus).
export interface InterestedPartyRegisterStatus {
  exists: boolean;
  register_doc_id: string | null;
  identifier: string | null;
  state: InterestedPartyRegisterState | null;
  current_effective_version_id: string | null;
  has_governing: boolean;
  can_release?: boolean;
  can_manage?: boolean;
}

// POST /interested-parties/register/publish body — `RegisterPublish` (optional change reason).
export interface InterestedPartyRegisterPublishBody {
  change_reason: string | null;
}

// GET /interested-parties/summary — the GOVERNING (Effective) categorical read-of-record
// (S-interested-parties-2). published:false + all-zero counts before the first publish/release.
// by_influence carries an "unspecified" bucket for NULL-influence rows; active == by_status.active;
// never_reviewed counts rows with no last_reviewed_at.
export interface InterestedPartyRegisterSummary {
  published: boolean;
  total: number;
  by_party_type: Record<InterestedPartyType, number>;
  by_influence: Record<InterestedPartyInfluence | "unspecified", number>;
  by_status: Record<InterestedPartyStatus, number>;
  active: number;
  never_reviewed: number;
}

export interface InterestedPartyCreateBody {
  party_type: InterestedPartyType;
  party_name: string;
  needs_expectations: string;
  influence?: InterestedPartyInfluence | null;
  last_reviewed_at?: string | null;
}

// Partial PATCH — omitted ≠ null; an explicit null clears influence/last_reviewed_at. A new party has
// no status field on create (always "active"); status is settable here (active/closed).
export interface InterestedPartyUpdateBody {
  party_type?: InterestedPartyType;
  party_name?: string;
  needs_expectations?: string;
  influence?: InterestedPartyInfluence | null;
  status?: InterestedPartyStatus;
  last_reviewed_at?: string | null;
}

// S-notify-fe: the in-app notification + per-user preference shapes (pinned to api/notifications.py::_view).
export interface Notification {
  id: string;
  event_key: string;
  subject_type: string;
  subject_id: string | null;
  title: string;
  body: string;
  deep_link: string;
  created_at: string;
  read_at: string | null;
}

export type NotificationDigestMode = "immediate" | "daily" | "off";

export type NotificationClass = "action_required" | "awareness" | "critical" | "admin_ops";

export interface NotificationPreferences {
  email_enabled: boolean;
  digest_modes: Record<NotificationClass, NotificationDigestMode>;
  digest_hour: number;
  timezone: string;
  quiet_start: string | null;
  quiet_end: string | null;
}

export interface NotificationPreferencesUpdate {
  email_enabled?: boolean;
  digest_modes?: Partial<Record<NotificationClass, NotificationDigestMode>>;
  digest_hour?: number;
  timezone?: string;
  quiet_start?: string | null;
  quiet_end?: string | null;
}

// S-notify-5b: the org-config + notification delivery-health admin shapes
// (pinned to api/config.py::_config_view + services/notifications/health.py::get_delivery_health).
export interface OrgConfig {
  org_id: string;
  capture_pre_release_templates: boolean;
  allow_self_disposition: boolean;
  allow_capa_self_verify: boolean;
  leadership_release_requires_top_management_authorization: boolean;
  notifications_email_enabled: boolean;
  notifications_escalation_pierce_quiet_hours: boolean;
}

export interface OrgConfigUpdate {
  capture_pre_release_templates?: boolean;
  allow_self_disposition?: boolean;
  allow_capa_self_verify?: boolean;
  leadership_release_requires_top_management_authorization?: boolean;
  notifications_email_enabled?: boolean;
  notifications_escalation_pierce_quiet_hours?: boolean;
}

export interface WorkingCalendar {
  name: string;
  working_days: number[];
  holidays: string[];
  timezone: string;
  exists: boolean;
}

export interface WorkingCalendarUpdate {
  name: string;
  working_days: number[];
  holidays: string[];
  timezone: string;
}

export interface NotificationEmailFailure {
  recipient_email: string;
  last_error: string | null;
  attempts: number;
  failed_at: string | null;
  email_kind: "single" | "digest";
}

export interface NotificationDeliveryHealth {
  org_email_enabled: boolean;
  email: {
    failed: number;
    pending_now: number;
    pending_scheduled: number;
    suppressed: number;
    oldest_pending_at: string | null;
  };
  recent_failures: NotificationEmailFailure[];
  awareness: {
    pending: number;
    oldest_pending_at: string | null;
  };
}

// GET /reports/document-control — the Controlled Document Register (hard-gated report.read SYSTEM;
// 403 for callers without the key).
export interface ClauseRef {
  clause: string;
  starred: boolean;
}

export interface RegisterRow {
  id: string;
  identifier: string;
  title: string;
  document_type_id: string | null;
  document_type: string | null;
  current_state: DocumentCurrentState;
  owner_user_id: string;
  owner_display: string | null;
  effective_revision_label: string | null;
  effective_from: string | null;
  blob_sha256: string | null;
  clause_refs: ClauseRef[];
  process_links: string[];
  approved_by: string | null;
  approved_on: string | null;
  next_review_due: string | null;
  review_state: ReviewState | null;
}

export interface RegisterProvenance {
  report_name: string;
  generated_by: string;
  generated_at: string;
  as_of: string;
  scope: string;
  app_version: string;
  filters: Record<string, string>;
  row_count: number;
  content_hash: string;
}

export interface DocumentControlRegister {
  provenance: RegisterProvenance;
  rows: RegisterRow[];
}
