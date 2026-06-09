import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { Capa, CapaApproval, CapaList, RecordSummary } from "../../lib/types";

// GET /capas is gated capa.read; the demo admin holds NO capa.* (S-web-6 calm-403 case, NOT S-ing-4b).
// Surface a `forbidden` flag so the page renders a calm no-access panel. retry:false — don't hammer a
// permission denial.
export function useCapas() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["capas"],
    queryFn: async () => (await api.get<CapaList>("/api/v1/capas")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

// GET /capas/{id} — the detail (+ stages[]). Disabled until a card is selected; the `id!` makes the
// non-null intent explicit (the `enabled` guard means the queryFn never fires with a null id).
export function useCapa(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["capa", id],
    queryFn: () => api.get<Capa>(`/api/v1/capas/${id!}`),
    enabled: id !== null,
    retry: false,
  });
}

// GET /capas/{id}/approval — the action-plan approval cycle (or null). Gated capa.read (the Top-Mgmt
// approver holds only capa.read). Enabled only when we want it (e.g. a RootCause CAPA, or the approval page).
export function useCapaApproval(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["capa-approval", id],
    queryFn: () => api.get<CapaApproval | null>(`/api/v1/capas/${id!}/approval`),
    enabled: id !== null,
    retry: false,
  });
}

// GET /records — the evidence picker source (filter-not-403; a bare array). limit 100.
export function useRecords() {
  const api = useApi();
  return useQuery({
    queryKey: ["records", "for-evidence"],
    queryFn: () => api.get<RecordSummary[]>("/api/v1/records?limit=100"),
    retry: false,
  });
}
