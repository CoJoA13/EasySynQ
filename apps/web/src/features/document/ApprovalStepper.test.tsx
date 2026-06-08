import { expect, test } from "vitest";
import type { Task, WorkflowInstance } from "../../lib/types";
import { renderWithProviders } from "../../test/render";
import { ApprovalStepper, buildApprovalNodes } from "./ApprovalStepper";

const baseTask: Task = {
  id: "t",
  instance_id: "wf",
  stage_key: "quality_approval",
  type: "APPROVE",
  state: "PENDING",
  assignee_user_id: null,
  candidate_pool: ["u1"],
  action_expected: "approve",
  due_at: null,
};
const base: WorkflowInstance = {
  id: "wf",
  definition_id: "d",
  definition_version: 1,
  subject_type: "DOCUMENT",
  subject_id: "doc",
  current_state: "IN_APPROVAL",
  started_at: "2026-06-08T09:00:00+00:00",
  revision: 0,
  tasks: [baseTask],
};
const nameOf = (id: string | null) => (id === "u1" ? "Ken" : (id ?? "—"));

test("in-approval → approval current, release pending", () => {
  const nodes = buildApprovalNodes(base, "InReview", null, nameOf);
  expect(nodes.map((n) => n.status)).toEqual(["done", "current", "pending"]);
  expect(nodes[1]?.sub).toContain("Ken");
});

test("effective → all done with the approver named", () => {
  const inst: WorkflowInstance = {
    ...base,
    current_state: "APPROVED",
    tasks: [{ ...baseTask, state: "DONE", assignee_user_id: "u1" }],
  };
  const nodes = buildApprovalNodes(inst, "Effective", "2026-06-09T00:00:00+00:00", nameOf);
  expect(nodes.map((n) => n.status)).toEqual(["done", "done", "done"]);
  expect(nodes[1]?.sub).toContain("Ken");
  expect(nodes[2]?.sub).toContain("2026-06-09");
});

test("rejected → approval node is rejected", () => {
  const inst: WorkflowInstance = {
    ...base,
    current_state: "REJECTED_TO_DRAFT",
    tasks: [{ ...baseTask, state: "DONE", assignee_user_id: "u1" }],
  };
  const nodes = buildApprovalNodes(inst, "Draft", null, nameOf);
  expect(nodes[1]?.status).toBe("rejected");
});

test("needs-attention → honest 'none assigned' label", () => {
  const inst: WorkflowInstance = {
    ...base,
    current_state: "NEEDS_ATTENTION",
    tasks: [{ ...baseTask, candidate_pool: null }],
  };
  const nodes = buildApprovalNodes(inst, "InReview", null, nameOf);
  expect(nodes[1]?.sub).toContain("none assigned");
});

test("renders a labeled list and marks the current step", () => {
  const { getByLabelText, container } = renderWithProviders(
    <ApprovalStepper instance={base} docState="InReview" effectiveFrom={null} nameOf={nameOf} />,
  );
  expect(getByLabelText("Approval progress")).toBeInTheDocument();
  expect(container.querySelector('[aria-current="step"]')).not.toBeNull();
});
