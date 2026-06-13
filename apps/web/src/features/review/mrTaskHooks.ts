import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";

// S-mr-2: the MR_ACTION decision mutation. Separate from useDecideTask (an MR action is a one-click
// `complete`, no signature, no DecisionSubjectType) so the shared decision path stays untouched.
// The MR_INPUT leg never decides (the FE enforces non-decidability — `decide_mr_task` doesn't gate on
// task.type), so only the action needs a mutation. Invalidates the review DETAIL (its close-readiness
// changes when an action completes — the same keys useInvalidateReview hits) + the list, and my-tasks
// so the task leaves the inbox.
export function useDecideMrTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      taskId,
      idempotencyKey,
    }: {
      taskId: string;
      reviewId: string;
      idempotencyKey: string;
    }) =>
      api.send<{ current_state: string }>(
        "POST",
        `/api/v1/tasks/${taskId}/decision`,
        { outcome: "complete" },
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: (_d, { taskId, reviewId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["my-tasks"] });
      void qc.invalidateQueries({ queryKey: ["management-review", reviewId] });
      void qc.invalidateQueries({ queryKey: ["management-reviews"] });
    },
  });
}
