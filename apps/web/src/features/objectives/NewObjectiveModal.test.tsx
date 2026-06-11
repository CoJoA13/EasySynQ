import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { NewObjectiveModal } from "./NewObjectiveModal";

function fill(dialog: HTMLElement, label: RegExp, value: string) {
  fireEvent.change(within(dialog).getByLabelText(label), { target: { value } });
}

function getDialog() {
  return screen.getByRole("dialog");
}

describe("NewObjectiveModal", () => {
  it("creates an objective from the required fields", async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/v1/objectives", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: "new" }, { status: 201 });
      }),
    );
    const onCreated = vi.fn();
    renderWithProviders(<NewObjectiveModal opened onClose={() => {}} onCreated={onCreated} />);

    const dialog = getDialog();
    fill(dialog, /^objective/i, "On-time delivery rate");
    fill(dialog, /^target/i, "95");
    fill(dialog, /^unit/i, "%");
    fill(dialog, /due date/i, "2026-12-31");
    fireEvent.click(within(dialog).getByRole("button", { name: /create objective/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("new"));
    expect(body).toMatchObject({
      title: "On-time delivery rate", target_value: "95", unit: "%",
      direction: "HIGHER_IS_BETTER", due_date: "2026-12-31",
    });
  });

  it("surfaces a 422 inline", async () => {
    server.use(
      http.post("/api/v1/objectives", () =>
        HttpResponse.json({ code: "validation_error", title: "Unknown process_id" }, { status: 422 }),
      ),
    );
    renderWithProviders(<NewObjectiveModal opened onClose={() => {}} onCreated={() => {}} />);
    const dialog = getDialog();
    fill(dialog, /^objective/i, "X");
    fill(dialog, /^target/i, "95");
    fill(dialog, /^unit/i, "%");
    fill(dialog, /due date/i, "2026-12-31");
    fireEvent.click(within(dialog).getByRole("button", { name: /create objective/i }));
    await waitFor(() => expect(screen.getByText(/unknown process_id/i)).toBeInTheDocument());
  });
});
