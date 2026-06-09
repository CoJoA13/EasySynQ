import { List, ThemeIcon } from "@mantine/core";
import type { CapaStage } from "../../lib/types";

export interface GateState {
  rootCause: boolean;
  action: boolean;
  effectiveness: boolean;
}

export function deriveGate(stages: CapaStage[], cycleMarker: number): GateState {
  const hasAnyRootCause = stages.some((s) => s.stage === "RootCause");
  const currentWithEvidence = (stage: CapaStage["stage"], extra?: (s: CapaStage) => boolean) =>
    stages.some(
      (s) =>
        s.stage === stage &&
        s.cycle_marker === cycleMarker &&
        (s.evidence_links?.length ?? 0) > 0 &&
        (extra ? extra(s) : true),
    );
  return {
    rootCause: hasAnyRootCause,
    action: currentWithEvidence("Implement"),
    effectiveness: currentWithEvidence("Verify", (s) => s.content_block["decision"] === "effective"),
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
  cycleMarker,
}: {
  stages: CapaStage[];
  cycleMarker: number;
}) {
  const gate = deriveGate(stages, cycleMarker);
  return (
    <List spacing="xs" size="sm" center>
      <Step done={gate.rootCause} label="Root cause documented" />
      <Step done={gate.action} label="Corrective action defined" />
      <Step done={gate.effectiveness} label="Effectiveness evidence" />
    </List>
  );
}
