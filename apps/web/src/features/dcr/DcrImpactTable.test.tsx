import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { DcrImpactTable } from "./DcrImpactTable";
import type { DcrImpact } from "../../lib/types";

const impact: DcrImpact[] = [
  { id: "i1", dimension: "affected_processes", auto_populated: { applicable: true, processes: ["p1", "p2"] }, requester_annotation: "Calibration", created_at: "2026-06-10T10:00:00+00:00", updated_at: null },
  { id: "i2", dimension: "training_awareness", auto_populated: { applicable: false }, requester_annotation: null, created_at: "2026-06-10T10:00:00+00:00", updated_at: null },
];

it("renders each dimension with a generic system-facts summary and the annotation or a dash", () => {
  const { getByText } = renderWithProviders(<DcrImpactTable impact={impact} />);
  expect(getByText("affected_processes")).toBeInTheDocument();
  expect(getByText("Applicable · 2 processes")).toBeInTheDocument();
  expect(getByText("Calibration")).toBeInTheDocument();
  expect(getByText("Not applicable")).toBeInTheDocument();
  expect(getByText("—")).toBeInTheDocument();
});

it("shows a not-yet-assessed empty state", () => {
  const { getByText } = renderWithProviders(<DcrImpactTable impact={[]} />);
  expect(getByText("Not yet assessed.")).toBeInTheDocument();
});
