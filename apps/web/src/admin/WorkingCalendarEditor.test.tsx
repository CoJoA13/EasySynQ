import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { WorkingCalendar } from "../lib/types";
import { server } from "../test/msw/server";
import { renderWithProviders } from "../test/render";
import { workingCalendarFixture } from "../test/msw/handlers";
import { WorkingCalendarEditor } from "./WorkingCalendarEditor";

function statefulCal(initial: WorkingCalendar = workingCalendarFixture) {
  let current: WorkingCalendar = { ...initial };
  return [
    http.get("/api/v1/admin/notifications/working-calendar", () =>
      HttpResponse.json(current as unknown as Record<string, unknown>),
    ),
    http.put("/api/v1/admin/notifications/working-calendar", async ({ request }) => {
      const b = (await request.json()) as Partial<WorkingCalendar>;
      current = { ...current, ...b, exists: true };
      return HttpResponse.json(current as unknown as Record<string, unknown>);
    }),
  ];
}

describe("WorkingCalendarEditor", () => {
  it("renders the loaded calendar and is accessible", async () => {
    server.use(...statefulCal());
    const { container } = renderWithProviders(<WorkingCalendarEditor />);
    expect(await screen.findByRole("checkbox", { name: "Monday" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Saturday" })).not.toBeChecked();
    expect(screen.getByText("2026-12-25")).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();
  });

  it("Save is gated by dirty and returns to disabled + Saved after a round-trip", async () => {
    server.use(...statefulCal());
    const user = userEvent.setup();
    renderWithProviders(<WorkingCalendarEditor />);
    const sat = await screen.findByRole("checkbox", { name: "Saturday" });
    const save = screen.getByRole("button", { name: "Save calendar" });
    expect(save).toBeDisabled();
    await user.click(sat); // dirty
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(save).toBeDisabled()); // value-equality dirty reset
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("enforces at least one working day (mutation-verify, not tautology)", async () => {
    server.use(...statefulCal());
    const user = userEvent.setup();
    renderWithProviders(<WorkingCalendarEditor />);
    for (const day of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]) {
      await user.click(await screen.findByRole("checkbox", { name: day }));
    }
    const save = screen.getByRole("button", { name: "Save calendar" });
    expect(save).toBeDisabled(); // dirty + invalid
    expect(screen.getByText(/at least one working day/i)).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Monday" }));
    expect(save).toBeEnabled(); // re-valid
  });

  it("adds a holiday via the date input and removes it; blank Add is a no-op", async () => {
    server.use(...statefulCal({ ...workingCalendarFixture, holidays: [] }));
    const user = userEvent.setup();
    renderWithProviders(<WorkingCalendarEditor />);
    const addBtn = await screen.findByRole("button", { name: "Add holiday" });
    await user.click(addBtn); // blank input → no-op
    expect(screen.queryByText(/^2026-/)).not.toBeInTheDocument();
    const input = screen.getByLabelText("Holiday date");
    fireEvent.change(input, { target: { value: "2026-12-25" } });
    await user.click(addBtn);
    expect(await screen.findByText("2026-12-25")).toBeInTheDocument();
    const remove = screen.getByRole("button", { name: "Remove holiday 2026-12-25" });
    await user.click(remove);
    expect(screen.queryByText("2026-12-25")).not.toBeInTheDocument();
  });
});
