import type { MantineSize } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { DcrState } from "../../lib/types";

// Maps the 9-state DCR change-control lifecycle to a label + a canonical status tone. The tone supplies
// both the AA-tuned colour pair AND the non-colour glyph via StatusBadge (status is NEVER colour-only,
// DP-7): the text label always carries the meaning, and the tone glyph adds a second non-colour channel.
// Implemented is `emphasisSuccess` (★) so a change that has landed reads as a milestone over a plain ✓
// (preserving the prior ★ read); Closed is the plain success done-ok ✓. Approved is `info` (approved but
// not yet implemented). Cancelled is inert (neutral), Rejected is a hard ✕ (danger).
const META: Record<DcrState, { label: string; tone: Tone }> = {
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

export function DcrStateBadge({ state, size = "sm" }: { state: DcrState; size?: MantineSize }) {
  const { label, tone } = META[state];
  return <StatusBadge tone={tone} label={label} kind="State" size={size} />;
}
