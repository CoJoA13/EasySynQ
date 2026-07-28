import { LifecycleStepper, type LifecycleStepStatus } from "../../lib/LifecycleStepper";
import type { DocumentCurrentState, Task, WorkflowInstance } from "../../lib/types";

export interface StepNode {
  key: string;
  title: string;
  sub: string;
  status: LifecycleStepStatus;
}

function approvalNode(
  instance: WorkflowInstance,
  task: Task | null,
  nameOf: (id: string | null) => string,
): StepNode {
  if (instance.current_state === "REJECTED_TO_DRAFT") {
    return {
      key: "approval",
      title: "Changes requested",
      sub: task?.assignee_user_id
        ? `By ${nameOf(task.assignee_user_id)}`
        : "Returned to the author",
      status: "rejected",
    };
  }
  if (task && task.state === "DONE") {
    return {
      key: "approval",
      title: "Approved",
      sub: task.assignee_user_id ? `By ${nameOf(task.assignee_user_id)}` : "Approved",
      status: "done",
    };
  }
  const pool = task?.candidate_pool ?? [];
  const sub =
    instance.current_state === "NEEDS_ATTENTION"
      ? "Awaiting an approver — none assigned"
      : pool.length
        ? `Awaiting ${pool.map(nameOf).join(", ")}`
        : "Awaiting approval";
  return { key: "approval", title: "Quality approval", sub, status: "current" };
}

// Derive the ordered stepper nodes from the instance + tasks + the document's own state. The DOCUMENT
// path is single-stage today (one approval node); the release/effective node reads the document state
// (authoritative — release never mutates the workflow instance). Pure function for easy testing.
export function buildApprovalNodes(
  instance: WorkflowInstance,
  docState: DocumentCurrentState,
  effectiveFrom: string | null,
  nameOf: (id: string | null) => string,
): StepNode[] {
  const started = instance.started_at ? instance.started_at.slice(0, 10) : "";
  const submitted: StepNode = {
    key: "submitted",
    title: "Submitted for review",
    sub: started ? `Submitted · ${started}` : "Submitted",
    status: "done",
  };
  const approveTask = (instance.tasks ?? []).find((t) => t.type === "APPROVE") ?? null;
  const release: StepNode = {
    key: "release",
    title: "Released to effective",
    sub:
      docState === "Effective"
        ? effectiveFrom
          ? `Effective · ${effectiveFrom.slice(0, 10)}`
          : "Effective"
        : docState === "Approved"
          ? "Awaiting release"
          : "Not yet released",
    status: docState === "Effective" ? "done" : docState === "Approved" ? "current" : "pending",
  };
  return [submitted, approvalNode(instance, approveTask, nameOf), release];
}

export function ApprovalStepper(props: {
  instance: WorkflowInstance;
  docState: DocumentCurrentState;
  effectiveFrom: string | null;
  nameOf: (id: string | null) => string;
}) {
  const nodes = buildApprovalNodes(
    props.instance,
    props.docState,
    props.effectiveFrom,
    props.nameOf,
  );
  return (
    <LifecycleStepper
      ariaLabel="Approval progress"
      steps={nodes.map((node) => ({
        key: node.key,
        label: node.title,
        description: node.sub,
        status: node.status,
      }))}
    />
  );
}
