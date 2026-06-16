import { StatusBadge } from "../../lib/StatusBadge";
import type { Tone } from "../../lib/status";
import type { AuditState, FindingType, NcSeverity } from "../../lib/types";
import { SEVERITY_LABEL, SEVERITY_TONE } from "../capa/columns";
import { AUDIT_STATE_LABEL } from "./labels";

// Audit lifecycle state → canonical tone (FSM: Scheduled → Planned → InProgress → FindingsDraft →
// Reported → Closing → Closed). The tone supplies both the AA-tuned colour pair AND the non-colour
// glyph via StatusBadge (status is NEVER colour-only, DP-7). Labels are reused verbatim from
// AUDIT_STATE_LABEL. Map stays feature-local (only Tone + glyphs are shared).
const STATE_TONE: Record<AuditState, Tone> = {
  Scheduled: "neutral", // not yet started (was gray)
  Planned: "neutral", // plan finalized, not yet started (was gray)
  InProgress: "info", // fieldwork active (was blue ●)
  FindingsDraft: "warning", // drafting findings — in progress (was yellow)
  Reported: "info", // report issued, not yet closed (was violet)
  Closing: "warning", // closing in progress (was orange)
  Closed: "success", // done / closed-ok (was green ✓)
};

export function AuditStateBadge({ state }: { state: AuditState }) {
  return (
    <StatusBadge tone={STATE_TONE[state]} label={AUDIT_STATE_LABEL[state]} kind="Audit state" />
  );
}

// Finding severity → canonical tone is the ONE app-wide severity convention — reused from
// capa/columns.SEVERITY_TONE (Critical → danger, Major → warning, Minor → neutral) rather than
// re-declared here, so the two surfaces can never drift. An NC with no recorded severity still
// reads as danger (the prior bare-NC red).
export function FindingTypeBadge({
  type,
  severity,
}: {
  type: FindingType;
  severity: NcSeverity | null;
}) {
  if (type === "NC") {
    const label = severity ? `${SEVERITY_LABEL[severity]} NC` : "NC";
    const tone: Tone = severity ? SEVERITY_TONE[severity] : "danger";
    return <StatusBadge tone={tone} label={label} kind="Finding type" />;
  }
  if (type === "OBSERVATION") {
    return <StatusBadge tone="neutral" label="Observation" kind="Finding type" />;
  }
  return <StatusBadge tone="info" label="OFI" kind="Finding type" />;
}
