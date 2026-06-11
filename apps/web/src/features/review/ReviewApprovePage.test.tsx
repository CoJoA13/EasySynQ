import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { screen } from "@testing-library/react";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { approvalFixture, capaApprovalFixture, capaApprovalTask, periodicReviewTask } from "../../test/msw/handlers";
import { ReviewApprovePage } from "./ReviewApprovePage";

function mount(route: string) {
  return renderWithProviders(
    <Routes>
      <Route path="tasks/:id" element={<ReviewApprovePage />} />
    </Routes>,
    { route },
  );
}

function renderAtTask(id: string) {
  return mount(`/tasks/${id}`);
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

test("a CAPA action-plan task renders the proposed plan + a working decision card", async () => {
  server.use(
    http.get("/api/v1/tasks/:id", () => HttpResponse.json(capaApprovalTask)),
    http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)),
  );
  const { findByText, findByRole } = mount("/tasks/tkca1111-1111-1111-1111-111111111111");
  expect(await findByText(/Schedule supplier re-evaluations/)).toBeInTheDocument();
  expect(await findByRole("button", { name: "Submit decision" })).toBeInTheDocument();
});

test("a CAPA task whose action plan can't load does NOT offer the decision form (no blind signing)", async () => {
  server.use(
    http.get("/api/v1/tasks/:id", () => HttpResponse.json(capaApprovalTask)),
    // approval read returns null (no plan) → the approver must not be able to sign blind
    http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(null)),
  );
  const { findByText, queryByRole } = mount("/tasks/tkca1111-1111-1111-1111-111111111111");
  expect(await findByText(/Action plan unavailable/)).toBeInTheDocument();
  expect(queryByRole("button", { name: "Submit decision" })).toBeNull();
});

test("a decided CAPA task shows the read-only summary, not the decision form", async () => {
  server.use(
    http.get("/api/v1/tasks/:id", () => HttpResponse.json({ ...capaApprovalTask, state: "DONE" })),
    http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)),
  );
  const { findByText, queryByRole } = mount("/tasks/tkca1111-1111-1111-1111-111111111111");
  expect(await findByText("This task has already been decided.")).toBeInTheDocument();
  expect(queryByRole("button", { name: "Submit decision" })).toBeNull();
});

describe("ReviewApprovePage — PERIODIC_REVIEW", () => {
  test("renders the doc context + the periodic decision card, and never reads the workflow instance", async () => {
    let instanceHit = false;
    server.use(
      http.get("/api/v1/workflow-instances/:id", () => {
        instanceHit = true;
        return HttpResponse.json(approvalFixture);
      }),
    );
    renderAtTask(periodicReviewTask.id);
    expect(await screen.findByText("Periodic review")).toBeInTheDocument();
    expect(await screen.findByText("SOP-PUR-014")).toBeInTheDocument();
    expect(screen.getByText("Supplier Selection & Evaluation")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm — no change needed")).toBeInTheDocument();
    // the obsolete path is a LINK to the doc page, not a task outcome
    expect(screen.getByText(/Obsolete it from the document page/)).toBeInTheDocument();
    expect(instanceHit).toBe(false);
  });

  test("a document-read 403 degrades calmly — the decision card still renders", async () => {
    server.use(
      http.get("/api/v1/documents/:id", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderAtTask(periodicReviewTask.id);
    expect(await screen.findByText("Document details not visible to you")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm — no change needed")).toBeInTheDocument();
  });

  test("a decided task shows the Decided alert instead of the card", async () => {
    server.use(
      http.get("/api/v1/tasks/:id", () =>
        HttpResponse.json({ ...periodicReviewTask, state: "DONE" }),
      ),
    );
    renderAtTask(periodicReviewTask.id);
    expect(await screen.findByText("This task has already been decided.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Confirm — no change needed")).not.toBeInTheDocument();
  });
});
