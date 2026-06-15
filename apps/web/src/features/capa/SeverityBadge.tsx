import type { MantineSize } from "@mantine/core";
import { StatusBadge } from "../../lib/StatusBadge";
import type { NcSeverity } from "../../lib/types";
import { SEVERITY_LABEL, SEVERITY_TONE } from "./columns";

// The one severity pill for the CAPA/NCR/complaint/finding-severity domains, on the canonical status
// system (S-statusbadge-2). Tone carries the AA-tuned colour pair AND the non-colour glyph; the label
// (Critical/Major/Minor) carries the precise meaning. Replaces the per-consumer
// `color={SEVERITY_COLOR[…]} variant="light"` ad-hoc colour map.
export function SeverityBadge({
  severity,
  size = "sm",
}: {
  severity: NcSeverity;
  size?: MantineSize;
}) {
  return (
    <StatusBadge
      tone={SEVERITY_TONE[severity]}
      label={SEVERITY_LABEL[severity]}
      kind="Severity"
      size={size}
    />
  );
}
