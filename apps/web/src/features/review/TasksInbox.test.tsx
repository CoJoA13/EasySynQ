import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { TasksInbox } from "./TasksInbox";

test("lists pending tasks with a link to the review page", async () => {
  const { findByRole } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  const link = await findByRole("link", { name: /approve/i });
  expect(link).toHaveAttribute("href", "/tasks/task1111-1111-1111-1111-111111111111");
});

test("shows a calm empty state", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
  const { findByText } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  expect(await findByText("No tasks in your queue.")).toBeInTheDocument();
});

test("surfaces a 403 quietly", async () => {
  server.use(
    http.get("/api/v1/tasks", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const { findByText } = renderWithProviders(<TasksInbox />, { route: "/tasks" });
  expect(await findByText(/don't have access/i)).toBeInTheDocument();
});
