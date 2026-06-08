import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DecisionBody, DecisionResult, Task, TaskState, WorkflowInstance } from "../../lib/types";

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

export function useTask(taskId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["task", taskId],
    queryFn: () => api.get<Task>(`/api/v1/tasks/${taskId}`),
    enabled: taskId !== null,
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
  documentId: string; // for cache invalidation (the task payload has no doc id)
  idempotencyKey: string;
  body: DecisionBody;
}

export function useDecideTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, body, idempotencyKey }: DecideInput) =>
      api.send<DecisionResult>("POST", `/api/v1/tasks/${taskId}/decision`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: (_d, { taskId, documentId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["document", documentId] });
      void qc.invalidateQueries({ queryKey: ["document-approval", documentId] });
      void qc.invalidateQueries({ queryKey: ["document-versions", documentId] });
    },
  });
}
