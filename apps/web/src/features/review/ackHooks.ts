import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { AckDecisionResult } from "../../lib/types";

// S-ack-2: the DOC_ACK attestation mutations. Separate from useDecideTask (the attestation is
// acknowledge-only, no signature, no DecisionSubjectType) so the shared decision path stays untouched.

function newKey(): string {
  return crypto.randomUUID();
}

export interface AckInput {
  taskId: string;
  documentId?: string; // for the per-doc cache invalidation (known on the single-task page)
}

export function useAcknowledgeTask() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId }: AckInput) =>
      api.send<AckDecisionResult>("POST", `/api/v1/tasks/${taskId}/decision`, { outcome: "acknowledge" }, { "Idempotency-Key": newKey() }),
    onSuccess: (_d, { taskId, documentId }) => {
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["ack-count"] });
      void qc.invalidateQueries({ queryKey: ["documents"] });
      if (documentId) void qc.invalidateQueries({ queryKey: ["document", documentId] });
    },
  });
}

export interface BulkResult {
  ok: string[];
  failed: { taskId: string; code: string }[];
}

// The doc 10 §8.2 sanctioned bulk action — loop the per-task POST (one Idempotency-Key each), report
// per-task. allSettled so a single lapsed/superseded obligation never aborts the rest.
export function useBulkAcknowledge() {
  const api = useApi();
  const qc = useQueryClient();
  async function run(taskIds: string[]): Promise<BulkResult> {
    const settled = await Promise.allSettled(
      taskIds.map((taskId) =>
        api
          .send<AckDecisionResult>("POST", `/api/v1/tasks/${taskId}/decision`, { outcome: "acknowledge" }, { "Idempotency-Key": newKey() })
          .then(() => taskId),
      ),
    );
    const ok: string[] = [];
    const failed: { taskId: string; code: string }[] = [];
    settled.forEach((r, i) => {
      const taskId = taskIds[i]!;
      if (r.status === "fulfilled") ok.push(taskId);
      else failed.push({ taskId, code: r.reason instanceof ApiError ? r.reason.code : "error" });
    });
    void qc.invalidateQueries({ queryKey: ["tasks"] });
    void qc.invalidateQueries({ queryKey: ["ack-count"] });
    void qc.invalidateQueries({ queryKey: ["documents"] });
    return { ok, failed };
  }
  return { run };
}
