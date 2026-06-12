export type DocumentCurrentState =
  | "Draft"
  | "InReview"
  | "Approved"
  | "Effective"
  | "UnderRevision"
  | "Superseded"
  | "Obsolete";

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
  subject_type?: string; // detail-only (GET /tasks/{id}); "DOCUMENT" | "CAPA" | "DCR" | "PERIODIC_REVIEW"
  subject_id?: string;
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

export type DecisionOutcome = "approve" | "changes_requested" | "reject" | "complete";

export type DecisionSubjectType = "DOCUMENT" | "CAPA" | "PERIODIC_REVIEW";

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
  | "Created" | "Scanning" | "Scanned" | "Extracting" | "Classifying" | "Classified"
  | "Deduping" | "Proposing" | "Proposed" | "Reviewing"
  | "Committing" | "Completed" | "PartiallyCommitted" | "Failed" | "Cancelled";

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
export type CapaSource = "audit" | "process" | "complaint" | "review_output";
export type CapaCloseState =
  | "Raised" | "Containment" | "RootCause" | "ActionPlan" | "Implement" | "Verify"
  | "Closed" | "Rejected";

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
export type NcrDisposition =
  | "use_as_is" | "rework" | "scrap" | "return" | "concession" | "regrade";

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
export interface ComplaintList { data: Complaint[]; }

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
export interface NcrList { data: Ncr[]; }

export interface ComplaintCreateBody {
  description: string;
  customer?: string;
  received_at?: string;
  channel?: string;
  severity?: NcSeverity;
}
export interface SpawnCapaBody { severity?: NcSeverity; process_id?: string; }
export interface NcrCreateBody {
  source: NcrSource;
  description: string;
  severity: NcSeverity;
  process_id?: string;
}
export interface NcrDispositionBody { disposition: NcrDisposition; notes?: string; }

// ---- S-web-7d audits & findings (pinned to api/audits.py _program/_plan/_audit/_finding) ----
export type AuditState =
  | "Scheduled" | "Planned" | "InProgress" | "FindingsDraft"
  | "Reported" | "Closing" | "Closed";
export type FindingType = "NC" | "OBSERVATION" | "OFI";

export interface AuditProgram {
  id: string;
  identifier: string;            // AUDPROG-NNN
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
  scheduled_date: string | null;  // date (YYYY-MM-DD)
  checklist_ref: string | null;
  created_at: string;
}
export interface Audit {
  id: string;
  identifier: string | null;      // S-web-7d enrichment (REC-…)
  title: string | null;           // S-web-7d enrichment
  plan_id: string;
  lead_auditor_user_id: string | null;
  state: AuditState;
  started_at: string | null;      // date
  completed_at: string | null;    // date
  result_summary: string | null;  // never written in v1 — not rendered
  created_at: string | null;      // S-web-7d enrichment
}
export interface Finding {
  id: string;
  identifier: string | null;
  title: string | null;           // S-web-7d enrichment (the logged summary / correction reason)
  audit_id: string;
  finding_type: FindingType;
  severity: NcSeverity | null;
  clause_ref: string | null;
  process_ref: string | null;
  auto_capa_id: string | null;
  correction_of: string | null;
  superseded_by_correction: string | null;
}
export interface AuditProgramList { data: AuditProgram[]; }
export interface AuditPlanList { data: AuditPlan[]; }
export interface AuditList { data: Audit[]; }
export interface FindingList { data: Finding[]; }

// request bodies
export interface AuditProgramCreateBody { title: string; period?: string; }
export interface AuditProgramUpdateBody { title?: string; period?: string; archived?: boolean; }
export interface AuditPlanCreateBody {
  auditee_process_id?: string;
  lead_auditor_user_id?: string;
  scheduled_date?: string;
  checklist_ref?: string;
}
export interface AuditCreateBody { plan_id: string; title?: string; lead_auditor_user_id?: string; }
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
export interface ProcessRow { id: string; name: string; }

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
export type ObjectiveState =
  | "Draft" | "InReview" | "Approved" | "Effective"
  | "UnderRevision" | "Superseded" | "Obsolete";

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
  // S-obj-3 (detail-only; absent on list/scorecard rows; effective_from null until Effective):
  capabilities?: { submit: boolean; release: boolean };
  effective_from?: string | null;
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
}

export interface ObjectiveScorecard {
  total: number;
  on_target: number;
  by_rag: { green: number; amber: number; red: number; unmeasured: number };
  objectives: Objective[];
}

export interface ObjectiveListResponse { data: Objective[] }
export interface MeasurementListResponse { data: Measurement[] }

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
