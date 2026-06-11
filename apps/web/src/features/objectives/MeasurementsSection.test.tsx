import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { MeasurementsSection } from "./MeasurementsSection";

const ID = "ob000001-0001-0001-0001-000000000001";

it("renders a row per reading with period, value, and target-at-capture", async () => {
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() => expect(screen.getByText("2026-04-01")).toBeInTheDocument());
  expect(screen.getAllByText("Logistics MIS").length).toBeGreaterThan(0);
  // both the live target column and a historic target_at_capture are shown
  expect(screen.getAllByText("95 %").length).toBeGreaterThan(0);
});

it("shows a calm no-access panel when kpi.read is denied", async () => {
  server.use(
    http.get("/api/v1/objectives/:id/measurements", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() =>
    expect(screen.getByText(/don't have access to the measurement history/i)).toBeInTheDocument(),
  );
});

it("shows an empty hint when there are no readings", async () => {
  server.use(
    http.get("/api/v1/objectives/:id/measurements", () => HttpResponse.json({ data: [] })),
  );
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() => expect(screen.getByText(/no measurements recorded yet/i)).toBeInTheDocument());
});
