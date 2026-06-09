import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaStage, EvidenceLink } from "../../lib/types";
import { CloseGateStepper, deriveGate } from "./CloseGateStepper";

const ev = (): EvidenceLink => ({
  id: "e", record_id: "r", record_identifier: "REC-1", link_reason: null, created_at: null,
});

const mk = (
  stage: CapaStage["stage"],
  block: Record<string, unknown> = {},
  cycle = 0,
  evidence = 0,
): CapaStage => ({
  id: `${stage}-${cycle}`, stage, content_block: block, cycle_marker: cycle, created_by: "u",
  created_at: "2026-05-20T09:00:00+00:00", evidence_links: Array.from({ length: evidence }, ev),
});

test("deriveGate: root cause is cycle-agnostic; action/effectiveness need current-cycle evidence", () => {
  expect(deriveGate([mk("Raised")], 0)).toEqual({
    rootCause: false, action: false, effectiveness: false,
  });
  // an Implement WITHOUT evidence does NOT satisfy the action step (the M4 gate needs a linked record)
  expect(deriveGate([mk("RootCause"), mk("Implement")], 0)).toEqual({
    rootCause: true, action: false, effectiveness: false,
  });
  // with a current-cycle Implement + an effective Verify, BOTH carrying evidence → all met
  expect(
    deriveGate(
      [mk("RootCause"), mk("Implement", {}, 0, 1), mk("Verify", { decision: "effective" }, 0, 1)],
      0,
    ),
  ).toEqual({ rootCause: true, action: true, effectiveness: true });
  // a not_effective Verify (even with evidence) does NOT mark effectiveness
  expect(
    deriveGate([mk("Verify", { decision: "not_effective" }, 0, 1)], 0).effectiveness,
  ).toBe(false);
});

test("after a not_effective loop: root-cause carries forward, a prior-cycle action does not", () => {
  expect(
    deriveGate(
      [mk("RootCause", {}, 0), mk("Implement", {}, 0, 1), mk("Verify", { decision: "not_effective" }, 0, 1)],
      1,
    ),
  ).toEqual({ rootCause: true, action: false, effectiveness: false });
});

function wrap(stages: CapaStage[], cycleMarker = 0) {
  return render(
    <MantineProvider theme={theme}>
      <CloseGateStepper stages={stages} cycleMarker={cycleMarker} />
    </MantineProvider>,
  );
}

test("renders the three close-gate requirements", () => {
  wrap([mk("Raised")]);
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
  expect(screen.getByText(/Corrective action defined/)).toBeInTheDocument();
  expect(screen.getByText(/Effectiveness evidence/)).toBeInTheDocument();
});
