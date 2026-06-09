import { List, ThemeIcon } from "@mantine/core";
import type { CapaCloseState, CapaStage } from "../../lib/types";

export interface GateState {
  rootCause: boolean;
  action: boolean;
  effectiveness: boolean;
}

export function deriveGate(stages: CapaStage[], closeState: CapaCloseState): GateState {
  const has = (s: CapaStage["stage"]) => stages.some((x) => x.stage === s);
  return {
    rootCause: has("RootCause"),
    action: has("ActionPlan") || has("Implement"),
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
}: {
  stages: CapaStage[];
  closeState: CapaCloseState;
}) {
  const gate = deriveGate(stages, closeState);
  return (
    <List spacing="xs" size="sm" center>
      <Step done={gate.rootCause} label="Root cause documented" />
      <Step done={gate.action} label="Corrective action defined" />
      <Step done={gate.effectiveness} label="Effectiveness evidence" />
    </List>
  );
}
