import type { Tone } from "../../lib/status";
import type { DcrState } from "../../lib/types";

export const DCR_STATES: DcrState[] = [
  "Open",
  "Assessed",
  "Routed",
  "InApproval",
  "Approved",
  "Implemented",
  "Closed",
  "Cancelled",
  "Rejected",
];

// One exhaustive mapping drives badges, filter options, and timeline copy. Adding a backend state
// therefore requires one deliberate user-facing label and tone instead of leaking its machine token.
export const DCR_STATE_META: Record<DcrState, { label: string; tone: Tone }> = {
  Open: { label: "Open", tone: "info" },
  Assessed: { label: "Assessed", tone: "info" },
  Routed: { label: "Routed", tone: "info" },
  InApproval: { label: "In approval", tone: "warning" },
  Approved: { label: "Approved", tone: "info" },
  Implemented: { label: "Implemented", tone: "emphasisSuccess" },
  Closed: { label: "Closed", tone: "success" },
  Cancelled: { label: "Cancelled", tone: "neutral" },
  Rejected: { label: "Rejected", tone: "danger" },
};

export function dcrStateLabel(state: DcrState): string {
  return DCR_STATE_META[state].label;
}
