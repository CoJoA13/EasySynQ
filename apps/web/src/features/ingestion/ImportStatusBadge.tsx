import type { MantineSize } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { ImportRunStatus } from "../../lib/types";

// Maps an import-run status to a label + a canonical status tone. The tone supplies both the AA-tuned
// colour pair AND the non-colour glyph via StatusBadge (status is NEVER colour-only, DP-7): the text
// label carries the meaning, the glyph adds a second channel. Mirrors StateBadge. The `status` prop is
// a plain string (not the enum) so the badge tolerates additive commit stages beyond ImportRunStatus —
// an unknown status degrades to a neutral `?? fallback`, never crashes. Machine-active stages read as
// `info` (neutral-active), human-paced / needs-attention stages as `warning`, a completed run as
// `success`, a failure as `danger`, and the inert (created/cancelled) states as `neutral` — a faithful
// 1:1 carry-over of the prior token-colour map onto the shared tones.
const META: Record<ImportRunStatus, { label: string; tone: Tone }> = {
  Created: { label: "Created", tone: "neutral" },
  Scanning: { label: "Scanning", tone: "info" },
  Scanned: { label: "Scanned", tone: "info" },
  Extracting: { label: "Extracting", tone: "info" },
  Classifying: { label: "Classifying", tone: "info" },
  Classified: { label: "Classified", tone: "info" },
  Deduping: { label: "Deduping", tone: "info" },
  Proposing: { label: "Proposing", tone: "info" },
  Proposed: { label: "Proposed", tone: "warning" },
  Reviewing: { label: "Reviewing", tone: "warning" },
  Committing: { label: "Committing", tone: "info" },
  Completed: { label: "Completed", tone: "success" },
  PartiallyCommitted: { label: "Partially committed", tone: "warning" },
  Failed: { label: "Failed", tone: "danger" },
  Cancelled: { label: "Cancelled", tone: "neutral" },
};

export function ImportStatusBadge({ status, size = "sm" }: { status: string; size?: MantineSize }) {
  const meta = META[status as ImportRunStatus] ?? { label: status, tone: "neutral" as const };
  return <StatusBadge tone={meta.tone} label={meta.label} kind="Run status" size={size} />;
}
