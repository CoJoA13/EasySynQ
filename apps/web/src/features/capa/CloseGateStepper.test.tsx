import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaCloseState, CapaStage } from "../../lib/types";
import { CloseGateStepper, deriveGate } from "./CloseGateStepper";

const mk = (stage: CapaStage["stage"], block: Record<string, unknown> = {}): CapaStage => ({
  id: stage, stage, content_block: block, cycle_marker: 0, created_by: "u", created_at: "2026-05-20T09:00:00+00:00",
});

test("deriveGate: rootCause/action from stage presence; effectiveness only when Closed", () => {
  expect(deriveGate([mk("Raised")], "Raised")).toEqual({
    rootCause: false, action: false, effectiveness: false,
  });
  expect(deriveGate([mk("RootCause"), mk("Implement")], "Implement")).toEqual({
    rootCause: true, action: true, effectiveness: false,
  });
  // An effective Verify ALONE does not mark effectiveness done — evidence may still be missing (the
  // API 409s capa_close_incomplete). Only a Closed CAPA has definitively passed the evidence gate.
  expect(deriveGate([mk("Verify", { decision: "effective" })], "Verify")).toMatchObject({
    effectiveness: false,
  });
  expect(
    deriveGate([mk("RootCause"), mk("Implement"), mk("Verify", { decision: "effective" })], "Closed"),
  ).toMatchObject({ effectiveness: true });
});

function wrap(stages: CapaStage[], closeState: CapaCloseState = "Raised") {
  return render(
    <MantineProvider theme={theme}>
      <CloseGateStepper stages={stages} closeState={closeState} />
    </MantineProvider>,
  );
}

test("renders the three close-gate requirements", () => {
  wrap([mk("Raised")]);
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
  expect(screen.getByText(/Corrective action defined/)).toBeInTheDocument();
  expect(screen.getByText(/Effectiveness evidence/)).toBeInTheDocument();
});
