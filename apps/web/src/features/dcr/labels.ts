import type { DcrChangeType, DcrReasonClass, DcrSourceLinkType } from "../../lib/types";

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
