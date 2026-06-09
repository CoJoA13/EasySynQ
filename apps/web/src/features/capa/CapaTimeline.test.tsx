import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { expect, test } from "vitest";
import { theme } from "../../theme/mantine";
import type { CapaStage } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { CapaTimeline } from "./CapaTimeline";

const directory = [
  { id: "bbbb1111-1111-1111-1111-111111111111", display_name: "Mara Quality" },
];

function wrap(stages: CapaStage[]) {
  return render(
    <MantineProvider theme={theme}>
      <CapaTimeline stages={stages} directory={directory} capaId="ca1" />
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

test("an Implement stage shows its linked records (as labels) + a stage-scoped evidence linker", async () => {
  // EvidenceLinker (mounted on Implement/Verify) uses react-query, so render with the full providers.
  renderWithProviders(
    <CapaTimeline
      capaId="ca1"
      directory={directory}
      stages={[
        {
          id: "im",
          stage: "Implement",
          content_block: { actions_done: "done" },
          cycle_marker: 0,
          created_by: "bbbb1111-1111-1111-1111-111111111111",
          created_at: "2026-05-27T09:00:00+00:00",
          evidence_links: [
            { id: "el1", record_id: "r1", record_identifier: "REC-000041", link_reason: null, created_at: null },
          ],
        },
      ]}
    />,
  );
  expect(screen.getByText("Linked records:")).toBeInTheDocument();
  expect(screen.getByText("REC-000041")).toBeInTheDocument();
  // the linker's label is suffixed by the stage so two linkers never collide
  expect(await screen.findByLabelText("Record (Implement)")).toBeInTheDocument();
});
