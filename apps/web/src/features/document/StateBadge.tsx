import type { MantineSize } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { DocumentCurrentState } from "../../lib/types";

// The 7 lifecycle states, each mapped to a label + a canonical status tone. The tone supplies both the
// AA-tuned colour pair AND the non-colour glyph via StatusBadge (status is NEVER colour-only, DP-7).
// Effective is `emphasisSuccess` (★) so a released document reads as a milestone over a plain ✓.
const META: Record<DocumentCurrentState, { label: string; tone: Tone }> = {
  Draft: { label: "Draft", tone: "neutral" },
  InReview: { label: "In review", tone: "warning" },
  Approved: { label: "Approved", tone: "info" },
  Effective: { label: "Effective", tone: "emphasisSuccess" },
  UnderRevision: { label: "Under revision", tone: "warning" },
  Superseded: { label: "Superseded", tone: "neutral" },
  Obsolete: { label: "Obsolete", tone: "neutral" },
};

export function StateBadge({
  state,
  size = "sm",
}: {
  state: DocumentCurrentState;
  size?: MantineSize;
}) {
  const { label, tone } = META[state];
  return <StatusBadge tone={tone} label={label} kind="State" size={size} />;
}
