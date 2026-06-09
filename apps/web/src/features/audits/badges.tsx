import { Badge } from "@mantine/core";
import type { AuditState, FindingType, NcSeverity } from "../../lib/types";
import { SEVERITY_COLOR, SEVERITY_LABEL } from "../capa/columns";
import { AUDIT_STATE_LABEL } from "./labels";

// DP-7: glyph + label, never color-only. Text content is the accessible name (no aria-label —
// looped rows would duplicate it; tests scope within(row)).
const STATE_GLYPH: Record<AuditState, string> = {
  Scheduled: "◷",
  Planned: "◷",
  InProgress: "●",
  FindingsDraft: "✎",
  Reported: "▤",
  Closing: "◔",
  Closed: "✓",
};

const STATE_COLOR: Record<AuditState, string> = {
  Scheduled: "gray",
  Planned: "gray",
  InProgress: "blue",
  FindingsDraft: "yellow",
  Reported: "violet",
  Closing: "orange",
  Closed: "green",
};

export function AuditStateBadge({ state }: { state: AuditState }) {
  return (
    <Badge variant="light" color={STATE_COLOR[state]}>
      {STATE_GLYPH[state]} {AUDIT_STATE_LABEL[state]}
    </Badge>
  );
}

export function FindingTypeBadge({
  type,
  severity,
}: {
  type: FindingType;
  severity: NcSeverity | null;
}) {
  if (type === "NC") {
    const sev = severity ? `${SEVERITY_LABEL[severity]} ` : "";
    return (
      <Badge variant="light" color={severity ? SEVERITY_COLOR[severity] : "red"}>
        ⚑ {sev}NC
      </Badge>
    );
  }
  if (type === "OBSERVATION") {
    return (
      <Badge variant="light" color="gray">
        ◆ Observation
      </Badge>
    );
  }
  return (
    <Badge variant="light" color="blue">
      ➚ OFI
    </Badge>
  );
}
