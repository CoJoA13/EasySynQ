import { List, ThemeIcon } from "@mantine/core";
import type { CapaStage } from "../../lib/types";

export interface GateState {
  rootCause: boolean;
  action: boolean;
  effectiveness: boolean;
}

export function deriveGate(stages: CapaStage[]): GateState {
  const has = (s: CapaStage["stage"]) => stages.some((x) => x.stage === s);
  const effective = stages.some(
    (x) => x.stage === "Verify" && x.content_block?.decision === "effective",
  );
  return {
    rootCause: has("RootCause"),
    action: has("ActionPlan") || has("Implement"),
    effectiveness: effective,
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

export function CloseGateStepper({ stages }: { stages: CapaStage[] }) {
  const gate = deriveGate(stages);
  return (
    <List spacing="xs" size="sm" center>
      <Step done={gate.rootCause} label="Root cause documented" />
      <Step done={gate.action} label="Corrective action defined" />
      <Step done={gate.effectiveness} label="Effectiveness evidence" />
    </List>
  );
}
