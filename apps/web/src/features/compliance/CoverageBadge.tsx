import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { CoverageStatus } from "../../lib/types";

// The 3 mandatory-clause coverage states, each mapped to a label + a canonical status tone. The tone
// supplies both the AA-tuned colour pair AND the non-colour glyph via StatusBadge (status is NEVER
// colour-only, DP-5 / doc-11 DP-7). `overdue_review` is orthogonal — never a fourth coverage state.
const META: Record<CoverageStatus, { label: string; tone: Tone }> = {
  COVERED: { label: "Covered", tone: "success" },
  PARTIAL: { label: "Partial", tone: "warning" },
  GAP: { label: "Gap", tone: "danger" },
};

export function CoverageBadge({ status }: { status: CoverageStatus }) {
  const { label, tone } = META[status];
  return <StatusBadge tone={tone} label={label} kind="Coverage" />;
}
