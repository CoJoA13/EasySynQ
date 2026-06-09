import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaStage } from "../../lib/types";
import { CloseGateStepper, deriveGate } from "./CloseGateStepper";

const mk = (stage: CapaStage["stage"], block: Record<string, unknown> = {}): CapaStage => ({
  id: stage, stage, content_block: block, cycle_marker: 0, created_by: "u", created_at: "2026-05-20T09:00:00+00:00",
});

test("deriveGate reflects which requirements are met from stage presence", () => {
  expect(deriveGate([mk("Raised")])).toEqual({ rootCause: false, action: false, effectiveness: false });
  expect(deriveGate([mk("RootCause"), mk("Implement")])).toEqual({
    rootCause: true, action: true, effectiveness: false,
  });
  expect(deriveGate([mk("Verify", { decision: "effective" })])).toMatchObject({ effectiveness: true });
  expect(deriveGate([mk("Verify", { decision: "not_effective" })])).toMatchObject({ effectiveness: false });
});

function wrap(stages: CapaStage[]) {
  return render(
    <MantineProvider theme={theme}>
      <CloseGateStepper stages={stages} />
    </MantineProvider>,
  );
}

test("renders the three close-gate requirements", () => {
  wrap([mk("Raised")]);
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
  expect(screen.getByText(/Corrective action defined/)).toBeInTheDocument();
  expect(screen.getByText(/Effectiveness evidence/)).toBeInTheDocument();
});
