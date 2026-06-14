import { waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { DcrImpactTable } from "./DcrImpactTable";
import type { DcrImpact, DcrImpactList } from "../../lib/types";

const impact: DcrImpact[] = [
  {
    id: "i1",
    dimension: "affected_processes",
    auto_populated: { applicable: true, processes: ["p1", "p2"] },
    requester_annotation: "Calibration",
    created_at: "2026-06-10T10:00:00+00:00",
    updated_at: null,
  },
  {
    id: "i2",
    dimension: "training_awareness",
    auto_populated: { applicable: false },
    requester_annotation: null,
    created_at: "2026-06-10T10:00:00+00:00",
    updated_at: null,
  },
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

it("editable mode renders a textarea per dimension seeded from the annotation", () => {
  const { getByLabelText } = renderWithProviders(
    <DcrImpactTable impact={impact} editable dcrId="dcr1" />,
  );
  expect(getByLabelText("Annotation for affected_processes")).toHaveValue("Calibration");
  expect(getByLabelText("Annotation for training_awareness")).toHaveValue("");
});

it("Save is disabled until an annotation changes, then PUTs only the changed dimension", async () => {
  let putBody: unknown = null;
  const refreshed = { data: impact } satisfies DcrImpactList;
  server.use(
    http.put("/api/v1/dcrs/:id/impact", async ({ request }) => {
      putBody = await request.json();
      return HttpResponse.json(refreshed);
    }),
  );
  const user = userEvent.setup();
  const { getByRole, getByLabelText } = renderWithProviders(
    <DcrImpactTable impact={impact} editable dcrId="dcr1" />,
  );
  const save = getByRole("button", { name: "Save annotations" });
  expect(save).toBeDisabled();
  await user.type(getByLabelText("Annotation for training_awareness"), "Brief the line leads");
  expect(save).toBeEnabled();
  await user.click(save);
  await waitFor(() =>
    expect(putBody).toEqual({ annotations: { training_awareness: "Brief the line leads" } }),
  );
});

it("editable mode has no axe violations", async () => {
  const { container } = renderWithProviders(
    <DcrImpactTable impact={impact} editable dcrId="dcr1" />,
  );
  expect(await axe(container)).toHaveNoViolations();
});
