import type { NcrDisposition, NcrSource } from "../../lib/types";

// The NCR source vocabulary (NcrSource adds `internal` over CapaSource; no `review_output`).
// NCR-specific by design; cf. SOURCE_LABEL in columns.ts for the (different) CAPA source vocabulary.
export const NCR_SOURCES: NcrSource[] = ["audit", "process", "complaint", "internal"];
export const NCR_SOURCE_LABEL: Record<NcrSource, string> = {
  audit: "Audit",
  process: "Process",
  complaint: "Complaint",
  internal: "Internal",
};

// The ISO 9001 §8.7 disposition tokens (R20). The canonical token for the Python `RETURN_` member is `return`.
export const DISPOSITIONS: NcrDisposition[] = [
  "use_as_is", "rework", "scrap", "return", "concession", "regrade",
];
export const DISPOSITION_LABEL: Record<NcrDisposition, string> = {
  use_as_is: "Use as-is",
  rework: "Rework",
  scrap: "Scrap",
  return: "Return to supplier",
  concession: "Concession",
  regrade: "Regrade",
};
