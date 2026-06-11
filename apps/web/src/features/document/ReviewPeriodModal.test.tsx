import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { renderWithProviders } from "../../test/render";
import { docFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import type { DocumentSummary } from "../../lib/types";
import { ReviewPeriodModal } from "./ReviewPeriodModal";

const doc = docFixture[0] as unknown as DocumentSummary;

function capturePatch() {
  const bodies: unknown[] = [];
  server.use(
    http.patch("/api/v1/documents/:id", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json({ ...doc, effective_from: null });
    }),
  );
  return bodies;
}

describe("ReviewPeriodModal", () => {
  test("saves a changed period — the body carries the explicit number", async () => {
    const bodies = capturePatch();
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => {}} />);
    const input = screen.getByLabelText("Review period (months)");
    await userEvent.clear(input);
    await userEvent.type(input, "36");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toEqual([{ review_period_months: 36 }]));
  });

  test("clearing sends an EXPLICIT null (an omitted key would inherit server-side)", async () => {
    const bodies = capturePatch();
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => {}} />);
    await userEvent.click(screen.getByLabelText("No scheduled review"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toEqual([{ review_period_months: null }]));
  });

  test("out-of-bounds input disables Save", async () => {
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => {}} />);
    const input = screen.getByLabelText("Review period (months)");
    await userEvent.clear(input);
    await userEvent.type(input, "121");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  test("a PATCH failure surfaces an error and keeps the modal open", async () => {
    server.use(
      http.patch("/api/v1/documents/:id", () =>
        HttpResponse.json({ code: "validation_error", title: "Invalid" }, { status: 422 }),
      ),
    );
    let closed = false;
    renderWithProviders(<ReviewPeriodModal doc={doc} opened onClose={() => (closed = true)} />);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Invalid/)).toBeInTheDocument();
    expect(closed).toBe(false);
  });
});
