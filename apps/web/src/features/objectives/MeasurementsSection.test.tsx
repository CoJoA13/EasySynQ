import { expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { MeasurementsSection } from "./MeasurementsSection";

const ID = "ob000001-0001-0001-0001-000000000001";

it("renders a row per reading with period, value, and target-at-capture", async () => {
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  // the period appears in both the table cell and the chart x-axis → scope to the table.
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  const table = within(screen.getByRole("table"));
  expect(table.getByText("2026-04-01")).toBeInTheDocument();
  expect(table.getAllByText("Logistics MIS").length).toBeGreaterThan(0);
  // both the live target column and a historic target_at_capture are shown
  expect(table.getAllByText("95 %").length).toBeGreaterThan(0);
});

it("renders the trend chart ABOVE the table when readings exist", async () => {
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  const chart = screen.getByRole("img", { name: /KPI trend/i });
  expect(chart).toBeInTheDocument();
  const table = screen.getByRole("table");
  // DOM order: the chart precedes the table.
  expect(chart.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it("keeps the append-only note and drops the 'arrive in a later release' placeholder", async () => {
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() => expect(screen.getByText("Readings are append-only.")).toBeInTheDocument());
  expect(screen.queryByText(/trend charts arrive in a later release/i)).not.toBeInTheDocument();
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
  await waitFor(() =>
    expect(screen.getByText(/no measurements recorded yet/i)).toBeInTheDocument(),
  );
});

it("shows a calm error (not the empty hint) on a non-403 failure", async () => {
  server.use(
    http.get("/api/v1/objectives/:id/measurements", () =>
      HttpResponse.json({ code: "internal_error", title: "boom" }, { status: 500 }),
    ),
  );
  renderWithProviders(<MeasurementsSection objectiveId={ID} unit="%" />);
  await waitFor(() => expect(screen.getByText(/couldn't load measurements/i)).toBeInTheDocument());
  expect(screen.queryByText(/no measurements recorded yet/i)).not.toBeInTheDocument();
});
