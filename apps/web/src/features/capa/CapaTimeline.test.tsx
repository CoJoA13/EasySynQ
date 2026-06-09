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

function wrap(stages: CapaStage[], cycleMarker = 0) {
  return render(
    <MantineProvider theme={theme}>
      <CapaTimeline
        stages={stages}
        directory={directory}
        capaId="ca1"
        cycleMarker={cycleMarker}
        closeState="RootCause"
      />
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
      cycleMarker={0}
      closeState="Implement"
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

test("a looped CAPA renders the linker only on the CURRENT cycle's Verify (no duplicate label)", async () => {
  // Two Verify stages (cycle 0 + cycle 1); at cycleMarker=1 only the current-cycle one gets a linker, so
  // "Record (Verify)" resolves to a SINGLE element (the S-web-6 duplicate-accessible-name trap is avoided),
  // and the superseded-cycle stage shows its historical evidence as a read-only chip but no linker.
  renderWithProviders(
    <CapaTimeline
      capaId="ca1"
      directory={directory}
      cycleMarker={1}
      closeState="Verify"
      stages={[
        { id: "v0", stage: "Verify", content_block: { decision: "not_effective" }, cycle_marker: 0, created_by: "u", created_at: "2026-05-18T09:00:00+00:00", evidence_links: [{ id: "e0", record_id: "r0", record_identifier: "REC-OLD", link_reason: null, created_at: null }] },
        { id: "v1", stage: "Verify", content_block: { decision: "effective" }, cycle_marker: 1, created_by: "u", created_at: "2026-05-21T09:00:00+00:00", evidence_links: [] },
      ]}
    />,
  );
  // exactly one current-cycle linker → getByLabelText (single-match) does not throw
  expect(await screen.findByLabelText("Record (Verify)")).toBeInTheDocument();
  // the superseded cycle's historical evidence still shows as a chip
  expect(screen.getByText("REC-OLD")).toBeInTheDocument();
});

test("a terminal (Closed) CAPA shows linked evidence read-only but offers NO evidence linker", () => {
  // Post-closure the evidence trail is frozen — the Implement/Verify stages must not expose a linker.
  renderWithProviders(
    <CapaTimeline
      capaId="ca1"
      directory={directory}
      cycleMarker={0}
      closeState="Closed"
      stages={[
        { id: "im", stage: "Implement", content_block: {}, cycle_marker: 0, created_by: "u", created_at: "2026-05-27T09:00:00+00:00", evidence_links: [{ id: "e1", record_id: "r1", record_identifier: "REC-000041", link_reason: null, created_at: null }] },
        { id: "vf", stage: "Verify", content_block: { decision: "effective" }, cycle_marker: 0, created_by: "u", created_at: "2026-05-28T09:00:00+00:00", evidence_links: [{ id: "e2", record_id: "r2", record_identifier: "REC-000042", link_reason: null, created_at: null }] },
      ]}
    />,
  );
  expect(screen.getByText("REC-000041")).toBeInTheDocument(); // historical evidence still shown
  expect(screen.queryByLabelText(/^Record/)).toBeNull(); // but no linker on a closed CAPA
});
