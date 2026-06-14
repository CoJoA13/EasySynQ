import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  DecisionBody,
  DecisionResult,
  DecisionSubjectType,
  PeriodicReviewDecisionResult,
  Task,
  TaskState,
  WorkflowInstance,
} from "../../lib/types";

// S-web-5 review/approve reads + the decision mutation. All on useApi (token implicit). The inbox is
// self-scoped server-side (GET /tasks returns the caller's own/candidate tasks — no permission key).

export function useTasks(filters: { state?: TaskState; type?: string } = {}) {
  const api = useApi();
  return useQuery({
    queryKey: ["tasks", filters],
    queryFn: () => {
      const qs = new URLSearchParams({ assignee: "me" });
      if (filters.state) qs.set("state", filters.state);
      if (filters.type) qs.set("type", filters.type);
      return api.get<Task[]>(`/api/v1/tasks?${qs.toString()}`);
    },
  });
}

// `opts.retry` lets a best-effort caller (e.g. the MR outputs section reading a spawned action task
// it may not own) opt out of retries — a 404 is the EXPECTED outcome there, not a transient.
// ⚠ Spread `retry` CONDITIONALLY: a bare `retry: undefined` clobbers the QueryClient default (S-web-8).
export function useTask(taskId: string | null, opts?: { retry?: boolean }) {
  const api = useApi();
  return useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.get<Task>(`/api/v1/tasks/${taskId}`),
    enabled: taskId !== null,
    ...(opts?.retry !== undefined ? { retry: opts.retry } : {}),
  });
}

// Resolve a task's instance → its subject document (the review page needs the doc id for the redline).
export function useWorkflowInstance(instanceId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["workflow-instance", instanceId],
    queryFn: () =>
      api.get<WorkflowInstance>(`/api/v1/workflow-instances/${instanceId}?expand=tasks`),
    enabled: instanceId !== null,
  });
}

export interface DecideInput {
  taskId: string;
  subjectType: DecisionSubjectType;
  subjectId: string; // the document id or capa id — for cache invalidation
  idempotencyKey: string;
  body: DecisionBody;
}

export function useDecideTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, body, idempotencyKey }: DecideInput) =>
      api.send<DecisionResult | PeriodicReviewDecisionResult>(
        "POST",
        `/api/v1/tasks/${taskId}/decision`,
        body,
        {
          "Idempotency-Key": idempotencyKey,
        },
      ),
    onSuccess: (_d, { taskId, subjectType, subjectId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      if (subjectType === "DOCUMENT") {
        void qc.invalidateQueries({ queryKey: ["document", subjectId] });
        void qc.invalidateQueries({ queryKey: ["document-approval", subjectId] });
        void qc.invalidateQueries({ queryKey: ["document-versions", subjectId] });
      } else if (subjectType === "PERIODIC_REVIEW") {
        // subjectId IS the document id — the clock reset must show on the doc page + library.
        void qc.invalidateQueries({ queryKey: ["document", subjectId] });
        void qc.invalidateQueries({ queryKey: ["documents"] });
      } else if (subjectType === "DCR") {
        // subjectId IS the dcr id — refresh the drawer/register + the impact + the Home rail.
        void qc.invalidateQueries({ queryKey: ["dcr", subjectId] });
        void qc.invalidateQueries({ queryKey: ["dcrs"] });
        void qc.invalidateQueries({ queryKey: ["dcr-impact", subjectId] });
        void qc.invalidateQueries({ queryKey: ["my-tasks"] });
      } else {
        void qc.invalidateQueries({ queryKey: ["capa", subjectId] });
        void qc.invalidateQueries({ queryKey: ["capas"] });
        void qc.invalidateQueries({ queryKey: ["capa-approval", subjectId] });
      }
    },
  });
}
