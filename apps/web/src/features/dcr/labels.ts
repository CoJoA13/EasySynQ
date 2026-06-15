import type {
  ChangeSignificance,
  DcrChangeType,
  DcrReasonClass,
  DcrSourceLinkType,
} from "../../lib/types";
import { humanizeToken } from "../../lib/labels";

// Total Records over the union types so a new enum member breaks the build (exhaustiveness) — the
// single source of human labels shared by the register columns/filters and the drawer.
export const CHANGE_TYPE_LABEL: Record<DcrChangeType, string> = {
  REVISE: "Revise",
  CREATE: "Create",
  RETIRE: "Retire",
};

export const REASON_LABEL: Record<DcrReasonClass, string> = {
  regulatory: "Regulatory",
  audit_finding: "Audit finding",
  capa: "CAPA",
  process_improvement: "Process improvement",
  error_correction: "Error correction",
  periodic_review: "Periodic review",
  customer_requirement: "Customer requirement",
  mgmt_review: "Management review",
  other: "Other",
};

export const SOURCE_LABEL: Record<DcrSourceLinkType, string> = {
  capa: "CAPA",
  finding: "Audit finding",
  mgmt_review: "Management-review output",
  risk: "Risk",
};

// #2b: humanise the raw uppercase change_significance (MAJOR/MINOR leaked verbatim to the approver).
export const SIGNIFICANCE_LABEL: Record<ChangeSignificance, string> = {
  MAJOR: "Major",
  MINOR: "Minor",
};

// The 7 structured impact dimensions (ImpactDimension, doc 05 §5.3). DcrImpact.dimension is typed as a
// bare string, so this is a curated map WITH a humanising fallback — an unknown/added dimension reads
// cleanly ("Effectivity transition") instead of leaking the snake_case key.
const DIMENSION_LABEL: Record<string, string> = {
  affected_processes: "Affected processes",
  dependent_documents: "Dependent documents",
  records_produced_under: "Records produced under",
  training_awareness: "Training & awareness",
  clause_coverage: "Clause coverage",
  effectivity_transition: "Effectivity transition",
  risk: "Risk",
};

export function dimensionLabel(dimension: string): string {
  return DIMENSION_LABEL[dimension] ?? humanizeToken(dimension);
}
