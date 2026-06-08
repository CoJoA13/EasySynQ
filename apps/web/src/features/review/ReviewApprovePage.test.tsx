import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ReviewApprovePage } from "./ReviewApprovePage";

function mount(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="tasks/:id" element={<ReviewApprovePage />} />
    </Routes>,
    { route },
  );
}

test("renders the document context + the decision card for a pending task", async () => {
  const { findByText, findByRole } = mount("/tasks/task1111-1111-1111-1111-111111111111");
  expect(await findByText(/Supplier Selection/)).toBeInTheDocument();
  expect(await findByRole("button", { name: "Submit decision" })).toBeInTheDocument();
});

test("a 404 task is calm with a back link", async () => {
  server.use(
    http.get("/api/v1/tasks/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  const { findByText, findByRole } = mount("/tasks/nope");
  expect(await findByText(/isn't assigned to you/)).toBeInTheDocument();
  expect(await findByRole("link", { name: /Back to your tasks/ })).toBeInTheDocument();
});

test("a decided (non-pending) task shows the read-only summary, not the form", async () => {
  server.use(
    http.get("/api/v1/tasks/:id", () =>
      HttpResponse.json({
        id: "task1111-1111-1111-1111-111111111111",
        instance_id: "wf111111-1111-1111-1111-111111111111",
        stage_key: "quality_approval",
        type: "APPROVE",
        state: "DONE",
        assignee_user_id: "bbbb1111-1111-1111-1111-111111111111",
        candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
        action_expected: "approve",
        due_at: null,
      }),
    ),
  );
  const { findByText, queryByRole } = mount("/tasks/task1111-1111-1111-1111-111111111111");
  expect(await findByText("This task has already been decided.")).toBeInTheDocument();
  expect(queryByRole("button", { name: "Submit decision" })).toBeNull();
});
