export type DocumentCurrentState =
  | "Draft" | "InReview" | "Approved" | "Effective"
  | "UnderRevision" | "Superseded" | "Obsolete";

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
