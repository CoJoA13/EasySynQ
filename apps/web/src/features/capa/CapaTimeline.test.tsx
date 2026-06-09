import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaStage } from "../../lib/types";
import { CapaTimeline } from "./CapaTimeline";

const directory = [
  { id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Mara Quality" },
];

function wrap(stages: CapaStage[]) {
  return render(
    <MantineProvider theme={theme}>
      <CapaTimeline stages={stages} directory={directory} />
    </MantineProvider>,
  );
}

const baseStages: CapaStage[] = [
  { id: "s1", stage: "Raised", content_block: { problem: "X" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-20T09:00:00+00:00" },
  { id: "s2", stage: "Containment", content_block: { correction: "Y" }, cycle_marker: 0, created_by: "bbbb9999-9999-9999-9999-999999999999", created_at: "2026-05-21T09:00:00+00:00" },
];

test("renders one timeline item per stage with its label + actor", () => {
  wrap(baseStages);
  expect(screen.getByText("Raised")).toBeInTheDocument();
  expect(screen.getByText("Containment")).toBeInTheDocument();
  expect(screen.getByText(/Mara Quality/)).toBeInTheDocument();
});

test("degrades to the raw id when the actor is not in the directory", () => {
  wrap(baseStages);
  expect(screen.getByText(/bbbb9999/)).toBeInTheDocument();
});

test("marks the effectiveness loop when a stage has cycle_marker > 0", () => {
  wrap([
    ...baseStages,
    { id: "s3", stage: "RootCause", content_block: { root_cause: "Z" }, cycle_marker: 1, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-22T09:00:00+00:00" },
  ]);
  expect(screen.getByText(/Cycle 2/)).toBeInTheDocument();
});

test("renders an empty stage list calmly", () => {
  wrap([]);
  expect(screen.getByText(/no stages/i)).toBeInTheDocument();
});
