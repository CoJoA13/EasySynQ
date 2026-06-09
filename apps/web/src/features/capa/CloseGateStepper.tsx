import { List, ThemeIcon } from "@mantine/core";
import type { CapaCloseState, CapaStage } from "../../lib/types";

export interface GateState {
  rootCause: boolean;
  action: boolean;
  effectiveness: boolean;
}

// Mirrors the server's M4 close gate (`close_capa`): `has_root_cause` is CYCLE-AGNOSTIC (the
// established RCA carries across loop iterations — a `not_effective` loop bumps `cycle_marker` without
// appending a new RootCause stage, and the FSM offers no path to re-record one), while the implemented
// action is CURRENT-cycle (after a loop the prior cycle's ActionPlan/Implement no longer counts — a
// fresh revised plan is required). So root-cause = any RootCause stage; action = a current-cycle one.
export function deriveGate(
  stages: CapaStage[],
  closeState: CapaCloseState,
  cycleMarker: number,
): GateState {
  const hasAny = (s: CapaStage["stage"]) => stages.some((x) => x.stage === s);
  const hasCurrent = (s: CapaStage["stage"]) =>
    stages.some((x) => x.stage === s && x.cycle_marker === cycleMarker);
  return {
    rootCause: hasAny("RootCause"),
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
