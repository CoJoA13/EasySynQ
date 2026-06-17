import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import {
  approvalFixture,
  capaApprovalFixture,
  capaApprovalTask,
  dcrApprovalTask,
  improvementAuthTask,
  initiativeCompletedFixture,
  objectiveVersionWithCommitment,
  objectiveVersionV1Effective,
  objectiveVersionV2WithCommitment,
  periodicReviewTask,
} from "../../test/msw/handlers";
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

test("CAPA review page has no axe violations (heading order)", async () => {
  server.use(
    http.get("/api/v1/tasks/:id", () => HttpResponse.json(capaApprovalTask)),
    http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)),
  );
  const { container } = mount("/tasks/tkca1111-1111-1111-1111-111111111111");
  await screen.findByText(/Schedule supplier re-evaluations/);
  expect(await axe(container)).toHaveNoViolations();
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

describe("ReviewApprovePage — objective (DOCUMENT) subject", () => {
  test("an objective approval renders the frozen commitment card, not the redline", async () => {
    // The subject version carries metadata_snapshot.objective_commitment — detection keys on the
    // SNAPSHOT FIELD, never the document type.
    server.use(
      http.get("/api/v1/documents/:id/versions", () =>
        HttpResponse.json([objectiveVersionWithCommitment]),
      ),
    );
    renderAtTask("task1111-1111-1111-1111-111111111111");
    expect(
      await screen.findByText("The objective commitment you are approving."),
    ).toBeInTheDocument();
    // decimals stay STRINGS rendered verbatim ("95 %", never a reformatted number)
    expect(screen.getByText("95 %")).toBeInTheDocument();
    expect(screen.getByText("Higher is better")).toBeInTheDocument();
    // the DecisionCard is byte-identical — an objective decision IS a document decision
    expect(await screen.findByRole("button", { name: "Submit decision" })).toBeInTheDocument();
    // no redline picker
    expect(screen.queryByText("Compare from")).not.toBeInTheDocument();
  });

  test("an ordinary document (no objective_commitment) keeps the redline path unchanged", async () => {
    // default handlers: versionFixture (2 versions, metadata_snapshot null) → VersionCompare renders
    renderAtTask("task1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("Compare from")).toBeInTheDocument();
    expect(
      screen.queryByText("The objective commitment you are approving."),
    ).not.toBeInTheDocument();
  });

  test("a decided objective task shows the Decided alert beside the commitment card", async () => {
    server.use(
      http.get("/api/v1/documents/:id/versions", () =>
        HttpResponse.json([objectiveVersionWithCommitment]),
      ),
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
    renderAtTask("task1111-1111-1111-1111-111111111111");
    expect(
      await screen.findByText("The objective commitment you are approving."),
    ).toBeInTheDocument();
    expect(screen.getByText("This task has already been decided.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Submit decision" })).not.toBeInTheDocument();
  });
});

describe("ReviewApprovePage — objective revision two-version pin", () => {
  test("a revision approval shows the NEWEST frozen commitment with was→now against the governing one", async () => {
    // versions returned NEWEST-FIRST (version_seq DESC) — exactly what GET /documents/{id}/versions
    // returns. [0] = v2 InReview (target 97, the commitment being signed), [1] = v1 Effective (target 95,
    // the governing one being superseded). Proves the comment-only newest-first pin is a real regression pin.
    server.use(
      http.get("/api/v1/documents/:id/versions", () =>
        HttpResponse.json([objectiveVersionV2WithCommitment, objectiveVersionV1Effective]),
      ),
    );
    renderAtTask("task1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("95 % → 97 %")).toBeInTheDocument();
  });

  test("a changes_requested orphan draft never feeds the was→now (first-release cycle renders plain)", async () => {
    // [0] = v2 InReview (target 97, the commitment being signed)
    // [1] = v1 Draft orphan (target 95) — a changes_requested re-freeze left it behind, version_state Draft
    // There is NO Effective version → previousCommitment must be null → plain render (no was→now).
    server.use(
      http.get("/api/v1/documents/:id/versions", () =>
        HttpResponse.json([
          objectiveVersionV2WithCommitment,
          { ...objectiveVersionWithCommitment, version_state: "Draft" },
        ]),
      ),
    );
    renderAtTask("task1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("97 %")).toBeInTheDocument();
    expect(screen.queryByText("95 % → 97 %")).toBeNull();
    expect(screen.queryByText(/changes shown as was → now/i)).toBeNull();
  });
});

describe("ReviewApprovePage DOC_ACK branch", () => {
  test("a DOC_ACK task renders the attestation card + the doc context, no signature", async () => {
    renderAtTask("tkak1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("Document acknowledgement")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /i have read & understood/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument(); // not a DecisionCard
    // doc context (best-effort) shows the identifier (the attestation copy echoes it too → ≥1 match)
    expect((await screen.findAllByText("SOP-PUR-014")).length).toBeGreaterThan(0);
  });
});

describe("ReviewApprovePage MGMT_REVIEW branch", () => {
  const MR_ID = "mr-0001-0001-0001-000000000001"; // = mgmtReviewDetailFixture.id
  const mrTask = (over: Record<string, unknown>) => ({
    id: "tkmr1111-1111-1111-1111-111111111111",
    instance_id: "wfmr1111-1111-1111-1111-111111111111",
    stage_key: "prepare",
    type: "MR_INPUT",
    state: "PENDING",
    assignee_user_id: "bbbb1111-1111-1111-1111-111111111111",
    candidate_pool: ["bbbb1111-1111-1111-1111-111111111111"],
    action_expected: "prepare_review",
    due_at: null,
    subject_type: "MGMT_REVIEW",
    subject_id: MR_ID,
    ...over,
  });

  test("an MR_INPUT task renders nav-only (review context + open-the-review link, NO decide affordance)", async () => {
    server.use(
      http.get("/api/v1/tasks/:id", () => HttpResponse.json(mrTask({ type: "MR_INPUT" }))),
    );
    renderAtTask("tkmr1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("Prepare management review")).toBeInTheDocument();
    // the MR context (best-effort mgmtReview.read) shows the identifier
    expect((await screen.findAllByText("MR-001")).length).toBeGreaterThan(0);
    // the nav link is present (≥1 — the context card + the prepare card both link to the review)
    expect(screen.getAllByRole("link", { name: /open the review/i }).length).toBeGreaterThan(0);
    // …and there is NO complete/decision affordance (the FE-enforced non-decidability)
    expect(screen.queryByRole("button", { name: /mark action complete/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit decision/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  test("an MR_ACTION pending task renders the one-click complete card (no signature)", async () => {
    server.use(
      http.get("/api/v1/tasks/:id", () => HttpResponse.json(mrTask({ type: "MR_ACTION" }))),
    );
    renderAtTask("tkmr1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("Management review action")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /mark action complete/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument(); // not a DecisionCard
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  test("a decided MR_ACTION task shows the Decided alert instead of the complete card", async () => {
    server.use(
      http.get("/api/v1/tasks/:id", () =>
        HttpResponse.json(mrTask({ type: "MR_ACTION", state: "DONE" })),
      ),
    );
    renderAtTask("tkmr1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("This task has already been decided.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mark action complete/i })).not.toBeInTheDocument();
  });

  test("never resolves a subject document — no workflow-instance read for an MR task", async () => {
    let instanceHit = false;
    server.use(
      http.get("/api/v1/tasks/:id", () => HttpResponse.json(mrTask({ type: "MR_ACTION" }))),
      http.get("/api/v1/workflow-instances/:id", () => {
        instanceHit = true;
        return HttpResponse.json(approvalFixture);
      }),
    );
    renderAtTask("tkmr1111-1111-1111-1111-111111111111");
    expect(await screen.findByText("Management review action")).toBeInTheDocument();
    expect(instanceHit).toBe(false);
  });
});

describe("ReviewApprovePage DCR branch", () => {
  test("a DCR task renders the DCR context + a DCR decision card, no document redline", async () => {
    // dcrApprovalTask (subject_type "DCR", id task-dcr-1) is wired into GET /tasks/:id (Task 3);
    // GET /dcrs/:id defaults to dcrDetailFixture (identifier DCR-2026-0001).
    renderAtTask("task-dcr-1");
    // The DCR context (identifier) + the decision radios appear…
    expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Approve" })).toBeInTheDocument();
    // …and NO document redline (VersionCompare) is rendered.
    expect(screen.queryByText(/redline|version compare|compare from/i)).toBeNull();
  });

  test("never resolves a subject document — no workflow-instance read for a DCR task", async () => {
    let instanceHit = false;
    server.use(
      http.get("/api/v1/workflow-instances/:id", () => {
        instanceHit = true;
        return HttpResponse.json(approvalFixture);
      }),
    );
    renderAtTask("task-dcr-1");
    expect(await screen.findByText("DCR-2026-0001")).toBeInTheDocument();
    expect(instanceHit).toBe(false);
  });

  test("a decided DCR task shows the Decided alert instead of the decision card", async () => {
    server.use(
      http.get("/api/v1/tasks/:id", () => HttpResponse.json({ ...dcrApprovalTask, state: "DONE" })),
    );
    renderAtTask("task-dcr-1");
    expect(await screen.findByText("This task has already been decided.")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Approve" })).toBeNull();
  });

  test("DCR review page has no axe violations", async () => {
    const { container } = mount("/tasks/task-dcr-1");
    await screen.findByText("DCR-2026-0001");
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe("ReviewApprovePage IMPROVEMENT_INITIATIVE branch (S-improvement-4)", () => {
  test("an initiative authorization task renders the initiative context + a verify decision card, no redline", async () => {
    // improvementAuthTask (subject_type IMPROVEMENT_INITIATIVE) is wired into GET /tasks/:id; the
    // context reads the Completed initiative (override so it matches the task's subject_id).
    server.use(
      http.get("/api/v1/improvement-initiatives/:id", () =>
        HttpResponse.json(initiativeCompletedFixture),
      ),
    );
    renderAtTask(improvementAuthTask.id);
    expect(await screen.findByText("IMP-2026-0005")).toBeInTheDocument();
    // the verify sign-off outcome (not "Approve") + NO document redline
    expect(
      await screen.findByRole("radio", { name: "Verify benefit & authorize close" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/redline|version compare|compare from/i)).toBeNull();
  });

  test("never resolves a subject document — no workflow-instance read for an initiative task", async () => {
    let instanceHit = false;
    server.use(
      http.get("/api/v1/improvement-initiatives/:id", () =>
        HttpResponse.json(initiativeCompletedFixture),
      ),
      http.get("/api/v1/workflow-instances/:id", () => {
        instanceHit = true;
        return HttpResponse.json(approvalFixture);
      }),
    );
    renderAtTask(improvementAuthTask.id);
    expect(await screen.findByText("IMP-2026-0005")).toBeInTheDocument();
    expect(instanceHit).toBe(false);
  });

  test("ticking the sign checkbox is required before the verify decision can be submitted", async () => {
    server.use(
      http.get("/api/v1/improvement-initiatives/:id", () =>
        HttpResponse.json(initiativeCompletedFixture),
      ),
    );
    const { findByRole, getByLabelText, getByRole } = renderAtTask(improvementAuthTask.id);
    await userEvent.click(await findByRole("radio", { name: "Verify benefit & authorize close" }));
    // The sign checkbox surfaces (meaning: verify) and gates the submit until ticked.
    const submit = getByRole("button", { name: "Submit decision" });
    expect(submit).toBeDisabled();
    await userEvent.click(getByLabelText(/Signing as .* — meaning: verify/));
    expect(submit).toBeEnabled();
  });

  test("a decided initiative authorization task shows the Decided alert, not the card", async () => {
    server.use(
      http.get("/api/v1/improvement-initiatives/:id", () =>
        HttpResponse.json(initiativeCompletedFixture),
      ),
      http.get("/api/v1/tasks/:id", () =>
        HttpResponse.json({ ...improvementAuthTask, state: "DONE" }),
      ),
    );
    renderAtTask(improvementAuthTask.id);
    expect(await screen.findByText("This task has already been decided.")).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Verify benefit & authorize close" })).toBeNull();
  });
});
