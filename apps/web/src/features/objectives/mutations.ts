import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Measurement, MeasurementCreateBody, Objective, ObjectiveCreateBody, ObjectivePlan, PlanCreateBody,
} from "../../lib/types";

export function useCreateObjective() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ObjectiveCreateBody) => api.send<Objective>("POST", "/api/v1/objectives", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["objectives-scorecard"] }),
  });
}

export function useRecordMeasurement(objectiveId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MeasurementCreateBody) =>
      api.send<Measurement>("POST", `/api/v1/objectives/${objectiveId}/measurements`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["objective", objectiveId] });
      void qc.invalidateQueries({ queryKey: ["objective-measurements", objectiveId] });
      void qc.invalidateQueries({ queryKey: ["objectives-scorecard"] });
    },
  });
}

export function useAddPlan(objectiveId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlanCreateBody) =>
      api.send<ObjectivePlan>("POST", `/api/v1/objectives/${objectiveId}/plans`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["objective", objectiveId] }),
  });
}

export function useRemovePlan(objectiveId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (planId: string) =>
      api.send<void>("DELETE", `/api/v1/objectives/${objectiveId}/plans/${planId}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["objective", objectiveId] }),
  });
}

// Invalidate every read a lifecycle mutation can change (detail, approval cycle, the scorecard —
// the scorecard prefix also catches the per-process ["objectives-scorecard", pid] keys; no plain
// ["objectives"] list key exists in hooks.ts).
function useInvalidateObjective(): (id: string) => void {
  const qc = useQueryClient();
  return (id: string) => {
    void qc.invalidateQueries({ queryKey: ["objective", id] });
    void qc.invalidateQueries({ queryKey: ["objective-approval", id] });
    void qc.invalidateQueries({ queryKey: ["objectives-scorecard"] });
  };
}

// S-obj-3: freeze the commitment + instantiate approval (objective.manage; Draft/UnderRevision only).
export function useSubmitObjectiveForReview() {
  const api = useApi();
  const invalidate = useInvalidateObjective();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<Objective>("POST", `/api/v1/objectives/${id}/submit-review`),
    onSuccess: (_d, id) => invalidate(id),
  });
}

// S-obj-3: release the latest Approved version → Effective (document.release + SoD-2 server-side;
// the UI only shows the button when capabilities.release is true — the useReleaseDocument posture).
export function useReleaseObjective() {
  const api = useApi();
  const invalidate = useInvalidateObjective();
  return useMutation({
    mutationFn: (id: string) => api.send<Objective>("POST", `/api/v1/objectives/${id}/release`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}
