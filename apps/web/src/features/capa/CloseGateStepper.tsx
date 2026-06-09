import { List, ThemeIcon } from "@mantine/core";
import type { CapaCloseState, CapaStage } from "../../lib/types";

export interface GateState {
  rootCause: boolean;
  action: boolean;
  effectiveness: boolean;
}

// `cycleMarker` is the CAPA's current effectiveness-loop counter. Stages carry the marker they were
// appended under, so after a `not_effective` Verify→RootCause loop the PRIOR cycle's RootCause/Action
// stages linger in `stages` with an earlier marker — the server still requires a fresh revised plan in
// the current cycle. Scope the rootCause/action checks to the current cycle so a looped CAPA doesn't
// show a false green for work done in a superseded cycle.
export function deriveGate(
  stages: CapaStage[],
  closeState: CapaCloseState,
  cycleMarker: number,
): GateState {
  const hasCurrent = (s: CapaStage["stage"]) =>
    stages.some((x) => x.stage === s && x.cycle_marker === cycleMarker);
  return {
    rootCause: hasCurrent("RootCause"),
    action: hasCurrent("ActionPlan") || hasCurrent("Implement"),
    // Effectiveness-EVIDENCE completeness isn't in the read payload — the M4 close gate also requires
    // evidence linked to the Implement/Verify stages. The only state where that gate has DEFINITIVELY
    // passed is Closed, so derive this from the lifecycle state, NOT the Verify decision (which can be
    // `effective` while the API still 409s capa_close_incomplete for missing evidence).
    effectiveness: closeState === "Closed",
  };
}

function Step({ done, label }: { done: boolean; label: string }) {
  return (
    <List.Item
      icon={
        <ThemeIcon color={done ? "teal" : "gray"} size={18} radius="xl">
          {done ? "✓" : "•"}
        </ThemeIcon>
      }
    >
      {label} {done ? "" : "— required"}
    </List.Item>
  );
}

export function CloseGateStepper({
  stages,
  closeState,
  cycleMarker,
}: {
  stages: CapaStage[];
  closeState: CapaCloseState;
  cycleMarker: number;
}) {
  const gate = deriveGate(stages, closeState, cycleMarker);
  return (
    <List spacing="xs" size="sm" center>
      <Step done={gate.rootCause} label="Root cause documented" />
      <Step done={gate.action} label="Corrective action defined" />
      <Step done={gate.effectiveness} label="Effectiveness evidence" />
    </List>
  );
}
