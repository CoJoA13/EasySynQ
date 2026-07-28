import { LifecycleStepper, type LifecycleStep } from "../../lib/LifecycleStepper";
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
    effectiveness: currentWithEvidence(
      "Verify",
      (s) => s.content_block["decision"] === "effective",
    ),
  };
}

export function CloseGateStepper({
  stages,
  cycleMarker,
}: {
  stages: CapaStage[];
  cycleMarker: number;
}) {
  const gate = deriveGate(stages, cycleMarker);
  const steps: LifecycleStep[] = [
    {
      key: "root-cause",
      label: "Root cause documented",
      description: gate.rootCause ? "Requirement met" : "Required",
      status: gate.rootCause ? "done" : "pending",
    },
    {
      key: "corrective-action",
      label: "Corrective action defined",
      description: gate.action ? "Requirement met" : "Required",
      status: gate.action ? "done" : "pending",
    },
    {
      key: "effectiveness",
      label: "Effectiveness evidence",
      description: gate.effectiveness ? "Requirement met" : "Required",
      status: gate.effectiveness ? "done" : "pending",
    },
  ];
  return <LifecycleStepper ariaLabel="CAPA close requirements" steps={steps} />;
}
