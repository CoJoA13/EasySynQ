import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  MeasurementListResponse, Objective, ObjectiveScorecard, ProcessRow,
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
