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

  it("offers the Effective Quality Policy checkbox and sends its id when checked", async () => {
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
    fireEvent.click(within(dialog).getByRole("button", { name: /band & baseline/i }));
    const checkbox = await within(dialog).findByLabelText(/consistent with POL-001/i);
    fireEvent.click(checkbox);

    fill(dialog, /^objective/i, "On-time delivery rate");
    fill(dialog, /^target/i, "95");
    fill(dialog, /^unit/i, "%");
    fill(dialog, /due date/i, "2026-12-31");
    fireEvent.click(within(dialog).getByRole("button", { name: /create objective/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("new"));
    expect(body).toMatchObject({ policy_id: "po000001-0001-0001-0001-000000000001" });
  });

  it("surfaces a stale-policy 422 when the policy was superseded before submit", async () => {
    server.use(
      http.post("/api/v1/objectives", () =>
        HttpResponse.json(
          {
            code: "validation_error",
            title: "policy_id must be the current Effective Quality Policy",
          },
          { status: 422 },
        ),
      ),
    );
    renderWithProviders(<NewObjectiveModal opened onClose={() => {}} onCreated={() => {}} />);
    const dialog = getDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: /band & baseline/i }));
    const checkbox = await within(dialog).findByLabelText(/consistent with POL-001/i);
    fireEvent.click(checkbox);
    fill(dialog, /^objective/i, "X");
    fill(dialog, /^target/i, "95");
    fill(dialog, /^unit/i, "%");
    fill(dialog, /due date/i, "2026-12-31");
    fireEvent.click(within(dialog).getByRole("button", { name: /create objective/i }));
    await waitFor(() =>
      expect(
        screen.getByText(/policy_id must be the current effective quality policy/i),
      ).toBeInTheDocument(),
    );
  });

  it("degrades calmly when there is no Effective policy — no checkbox, creation unblocked", async () => {
    server.use(http.get("/api/v1/objectives/policy", () => HttpResponse.json(null)));
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
    fireEvent.click(within(dialog).getByRole("button", { name: /band & baseline/i }));
    expect(await within(dialog).findByText(/no effective quality policy yet/i)).toBeInTheDocument();

    fill(dialog, /^objective/i, "On-time delivery rate");
    fill(dialog, /^target/i, "95");
    fill(dialog, /^unit/i, "%");
    fill(dialog, /due date/i, "2026-12-31");
    fireEvent.click(within(dialog).getByRole("button", { name: /create objective/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("new"));
    expect(body).toMatchObject({ policy_id: null });
    expect(within(dialog).queryByLabelText(/consistent with/i)).not.toBeInTheDocument();
  });
});
