import type { CapaCloseState, CapaSource, NcSeverity } from "../../lib/types";

export type CapaColumnKey = "open" | "correction" | "rootcause" | "action" | "verify" | "closed";

export const CAPA_COLUMNS: { key: CapaColumnKey; label: string }[] = [
  { key: "open", label: "Open / NC" },
  { key: "correction", label: "Correction" },
  { key: "rootcause", label: "Root Cause" },
  { key: "action", label: "Action" },
  { key: "verify", label: "Verify" },
  { key: "closed", label: "Closed" },
];

const STATE_TO_COLUMN: Record<CapaCloseState, CapaColumnKey> = {
  Raised: "open",
  Containment: "correction",
  RootCause: "rootcause",
  ActionPlan: "action",
  Implement: "action",
  Verify: "verify",
  Closed: "closed",
  Rejected: "closed",
};

export function columnKeyFor(state: CapaCloseState): CapaColumnKey {
  return STATE_TO_COLUMN[state];
}

export const SEVERITY_LABEL: Record<NcSeverity, string> = {
  Critical: "Critical",
  Major: "Major",
  Minor: "Minor",
};

// Mantine badge color per severity (Critical red, Major orange, Minor gray).
export const SEVERITY_COLOR: Record<NcSeverity, string> = {
  Critical: "red",
  Major: "orange",
  Minor: "gray",
};

export const SOURCE_LABEL: Record<CapaSource, string> = {
  audit: "Audit",
  process: "Process",
  complaint: "Complaint",
  review_output: "Mgmt review",
};

// #2b: humanise the CAPA close_state so the raw backend casing ('RootCause'/'ActionPlan') never reaches
// the Quality Manager or auditor. Total over CapaCloseState — a new state breaks the build (the
// SEVERITY_LABEL exhaustiveness precedent).
export const CLOSE_STATE_LABEL: Record<CapaCloseState, string> = {
  Raised: "Raised",
  Containment: "Containment",
  RootCause: "Root cause",
  ActionPlan: "Action plan",
  Implement: "Implementation",
  Verify: "Verification",
  Closed: "Closed",
  Rejected: "Rejected",
};
