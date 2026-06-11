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
