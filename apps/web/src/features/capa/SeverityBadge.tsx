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
  count,
}: {
  severity: NcSeverity;
  size?: MantineSize;
  /**
   * Optional occurrence count, for an AGGREGATE pill ("Critical · 3") rather than one item's
   * severity. Additive: omitting it renders exactly what every existing caller renders today.
   *
   * It lives here rather than in a second grey badge so the severity pill stays single-sourced
   * (S-statusbadge-2) — an aggregate that invented its own colour map is the thing that decision
   * removed. Appending the count also keeps the accessible name distinct from the per-card pills
   * on the same page, so `getByLabelText` stays single-match.
   */
  count?: number;
}) {
  const label = SEVERITY_LABEL[severity];
  return (
    <StatusBadge
      tone={SEVERITY_TONE[severity]}
      label={count === undefined ? label : `${label} · ${count}`}
      kind="Severity"
      size={size}
    />
  );
}
