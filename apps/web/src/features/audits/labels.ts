import type { AuditState, FindingType } from "../../lib/types";

export const AUDIT_STATE_ORDER: AuditState[] = [
  "Scheduled", "Planned", "InProgress", "FindingsDraft", "Reported", "Closing", "Closed",
];

export const AUDIT_STATE_LABEL: Record<AuditState, string> = {
  Scheduled: "Scheduled",
  Planned: "Planned",
  InProgress: "In progress",
  FindingsDraft: "Findings draft",
  Reported: "Reported",
  Closing: "Closing",
  Closed: "Closed",
};

// The single legal next transition per state (the backend FSM is a linear forward chain).
// path = the POST sub-resource; gate = the permission key that endpoint requires.
export const NEXT_TRANSITION: Record<
  AuditState,
  { path: string; label: string; gate: "audit.conduct" | "audit.close" } | null
> = {
  Scheduled: { path: "plan", label: "Finalize plan", gate: "audit.conduct" },
  Planned: { path: "conduct", label: "Begin fieldwork", gate: "audit.conduct" },
  InProgress: { path: "draft-findings", label: "Draft findings", gate: "audit.conduct" },
  FindingsDraft: { path: "report", label: "Issue report", gate: "audit.conduct" },
  Reported: { path: "begin-closing", label: "Begin closing", gate: "audit.close" },
  Closing: { path: "close", label: "Close audit", gate: "audit.close" },
  Closed: null,
};

export const FINDING_TYPE_LABEL: Record<FindingType, string> = {
  NC: "NC",
  OBSERVATION: "Observation",
  OFI: "OFI",
};
