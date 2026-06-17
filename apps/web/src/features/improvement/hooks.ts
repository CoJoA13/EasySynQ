import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  Initiative,
  InitiativeAuthorization,
  InitiativeList,
  InitiativeStageEvent,
  InitiativeStageEventList,
} from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

// List — the endpoint row-filters to the caller's grant scope (never a hard 403; api/improvement.py),
// and client-side filtering happens in the page (the DCR/CAPA precedent), so this takes no args. The
// bare ["initiatives"] key is shared with the dashboard ActCard read (react-query dedups).
export function useInitiatives() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["initiatives"],
    queryFn: async (): Promise<Initiative[]> =>
      (await api.get<InitiativeList>("/api/v1/improvement-initiatives")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useInitiative(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["initiative", id],
    queryFn: () => api.get<Initiative>(`/api/v1/improvement-initiatives/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// S-improvement-4: the current management-authorization cycle (latest workflow instance + tasks), or
// null when never requested. Gated improvement.read; a 403 degrades calmly (forbidden) — never crash.
export function useInitiativeAuthorization(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["initiative-authorization", id],
    queryFn: () =>
      api.get<InitiativeAuthorization | null>(
        `/api/v1/improvement-initiatives/${id!}/authorization`,
      ),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// The append-only stage-event trail (oldest→newest) — a SEPARATE endpoint, not embedded in the detail.
export function useInitiativeStageEvents(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["initiative-stage-events", id],
    queryFn: async (): Promise<InitiativeStageEvent[]> =>
      (
        await api.get<InitiativeStageEventList>(
          `/api/v1/improvement-initiatives/${id!}/stage-events`,
        )
      ).data,
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
