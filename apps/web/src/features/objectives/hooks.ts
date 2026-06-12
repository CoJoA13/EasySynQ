import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  EffectivePolicy, MeasurementListResponse, Objective, ObjectiveScorecard, ProcessRow,
  WorkflowInstance,
} from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function useObjectiveScorecard(processId: string | null = null) {
  const api = useApi();
  const qs = processId ? `?process_id=${encodeURIComponent(processId)}` : "";
  const query = useQuery({
    queryKey: ["objectives-scorecard", processId],
    queryFn: () => api.get<ObjectiveScorecard>(`/api/v1/objectives/scorecard${qs}`),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useObjective(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["objective", id],
    queryFn: () => api.get<Objective>(`/api/v1/objectives/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useObjectiveMeasurements(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["objective-measurements", id],
    queryFn: async () =>
      (await api.get<MeasurementListResponse>(`/api/v1/objectives/${id!}/measurements`)).data,
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /objectives/{id}/approval — the approval cycle for the detail-page stepper (objective.read).
// null before submit; the OBJ instance carries subject_type=DOCUMENT (instantiate_approval).
export function useObjectiveApproval(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["objective-approval", id],
    queryFn: () => api.get<WorkflowInstance | null>(`/api/v1/objectives/${id!}/approval`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /objectives/policy — the Effective Quality Policy for the create modal (or null).
export function useEffectivePolicy() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["effective-policy"],
    queryFn: () => api.get<EffectivePolicy | null>("/api/v1/objectives/policy"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /processes (bare array) — the optional process picker/filter source. Degrade (omit) on a 403.
export function useProcesses() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["processes"],
    queryFn: () => api.get<ProcessRow[]>("/api/v1/processes"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
