import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { Task } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { MyTasksRail } from "./MyTasksRail";

const task = (id: string, type: Task["type"], due: string | null): Task => ({
  id, instance_id: `i-${id}`, stage_key: "s", type, state: "PENDING",
  assignee_user_id: null, candidate_pool: null, action_expected: null, due_at: due,
});

it("shows the count, the soonest-due first, and a see-all link", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([
    task("a", "REVIEW", "2026-06-20T00:00:00+00:00"),
    task("b", "DOC_ACK", "2026-06-12T00:00:00+00:00"),
    task("c", "CAPA_ACTION", null),
    task("d", "APPROVE", "2026-06-15T00:00:00+00:00"),
  ])));
  renderWithProviders(<MyTasksRail />);
  await waitFor(() => expect(screen.getByText(/my tasks \(4\)/i)).toBeInTheDocument());
  // top 3 by due date asc (b 12th, d 15th, a 20th); the null-due "c" is pushed out of the top 3
  const rows = screen.getAllByText(/due 2026-/);
  expect(rows[0]).toHaveTextContent("due 2026-06-12");
  expect(screen.getByRole("link", { name: /see all my tasks/i })).toHaveAttribute("href", "/tasks");
});

it("shows a calm caught-up state when there are no tasks", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
  renderWithProviders(<MyTasksRail />);
  await waitFor(() => expect(screen.getByText(/you're all caught up/i)).toBeInTheDocument());
});

it("shows a calm error state when the read fails", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json({ code: "error" }, { status: 500 })));
  renderWithProviders(<MyTasksRail />);
  await waitFor(() => expect(screen.getByText(/couldn't load your tasks/i)).toBeInTheDocument());
});
