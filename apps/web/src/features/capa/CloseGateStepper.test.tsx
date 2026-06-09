import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaCloseState, CapaStage } from "../../lib/types";
import { CloseGateStepper, deriveGate } from "./CloseGateStepper";

const mk = (
  stage: CapaStage["stage"],
  block: Record<string, unknown> = {},
  cycle = 0,
): CapaStage => ({
  id: `${stage}-${cycle}`, stage, content_block: block, cycle_marker: cycle, created_by: "u",
  created_at: "2026-05-20T09:00:00+00:00",
});

test("deriveGate: rootCause/action from current-cycle stages; effectiveness only when Closed", () => {
  expect(deriveGate([mk("Raised")], "Raised", 0)).toEqual({
    rootCause: false, action: false, effectiveness: false,
  });
  expect(deriveGate([mk("RootCause"), mk("Implement")], "Implement", 0)).toEqual({
    rootCause: true, action: true, effectiveness: false,
  });
  // An effective Verify ALONE does not mark effectiveness done — evidence may still be missing (the
  // API 409s capa_close_incomplete). Only a Closed CAPA has definitively passed the evidence gate.
  expect(deriveGate([mk("Verify", { decision: "effective" })], "Verify", 0)).toMatchObject({
    effectiveness: false,
  });
  expect(
    deriveGate([mk("RootCause"), mk("Implement"), mk("Verify", { decision: "effective" })], "Closed", 0),
  ).toMatchObject({ effectiveness: true });
});

test("a prior-cycle action does NOT satisfy the current cycle after a not_effective loop", () => {
  // CAPA looped to cycle 1 (RootCause): the cycle-0 ActionPlan lingers but a fresh plan is required.
  expect(
    deriveGate([mk("ActionPlan", {}, 0), mk("Verify", { decision: "not_effective" }, 0), mk("RootCause", {}, 1)], "RootCause", 1),
  ).toEqual({ rootCause: true, action: false, effectiveness: false });
});

function wrap(stages: CapaStage[], closeState: CapaCloseState = "Raised", cycleMarker = 0) {
  return render(
    <MantineProvider theme={theme}>
      <CloseGateStepper stages={stages} closeState={closeState} cycleMarker={cycleMarker} />
    </MantineProvider>,
  );
}

test("renders the three close-gate requirements", () => {
  wrap([mk("Raised")]);
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
  expect(screen.getByText(/Corrective action defined/)).toBeInTheDocument();
  expect(screen.getByText(/Effectiveness evidence/)).toBeInTheDocument();
});
