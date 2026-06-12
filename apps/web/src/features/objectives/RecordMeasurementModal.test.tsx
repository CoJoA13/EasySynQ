import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RecordMeasurementModal } from "./RecordMeasurementModal";

const ID = "ob000001-0001-0001-0001-000000000001";

describe("RecordMeasurementModal", () => {
  it("sends the objective's unit verbatim (locked) with the value and period", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/v1/objectives/:id/measurements", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: "m" }, { status: 201 });
      }),
    );
    const onDone = vi.fn();
    renderWithProviders(
      <RecordMeasurementModal opened objectiveId={ID} unit="%" onClose={() => {}} onRecorded={onDone} />,
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/period/i), { target: { value: "2026-07-01" } });
    fireEvent.change(within(dialog).getByLabelText(/value/i), { target: { value: "94" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /record/i }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(body).toMatchObject({ period: "2026-07-01", value: "94", unit: "%" });
  });

  it("surfaces a 422 unit-mismatch inline", async () => {
    server.use(
      http.post("/api/v1/objectives/:id/measurements", () =>
        HttpResponse.json({ code: "validation_error", title: "unit must match" }, { status: 422 }),
      ),
    );
    renderWithProviders(
      <RecordMeasurementModal opened objectiveId={ID} unit="%" onClose={() => {}} onRecorded={() => {}} />,
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/period/i), { target: { value: "2026-07-01" } });
    fireEvent.change(within(dialog).getByLabelText(/value/i), { target: { value: "94" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /record/i }));
    await waitFor(() => expect(screen.getByText(/unit must match/i)).toBeInTheDocument());
  });
});
