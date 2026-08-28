import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { Capa, CapaApproval, CapaList, ComplaintList, NcrList } from "../../lib/types";
import type { RegisterFilterState } from "../registers/registerFilters";
import { registerFilterKey, registerQuerySuffix } from "../registers/registerFilters";

// GET /capas is gated capa.read; the demo admin holds NO capa.* (S-web-6 calm-403 case, NOT S-ing-4b).
// Surface a `forbidden` flag so the page renders a calm no-access panel. retry:false — don't hammer a
// permission denial.
export function useCapas(filters: RegisterFilterState = {}) {
  const api = useApi();
  // The filter state is part of the cache key, and the request carries it as the bracketed
  // filter[field][op] grammar the API narrows on in SQL — which is how entries older than the
  // server's scan window are reached at all.
  const filterKey = registerFilterKey(filters);
  const query = useQuery({
    queryKey: ["capas", filterKey],
    queryFn: () => api.get<CapaList>(`/api/v1/capas${registerQuerySuffix(filters)}`),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  // U14: keep `data` the bare array (every consumer reads it that way) and surface the
  // register's scan-window truncation alongside `forbidden`.
  return {
    ...query,
    data: query.data?.data,
    truncated: query.data?.truncated ?? false,
    forbidden,
  };
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

// GET /complaints — gated record.read; the demo admin holds none of these keys (calm-403). retry:false.
export function useComplaints() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["complaints"],
    queryFn: async () => (await api.get<ComplaintList>("/api/v1/complaints")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

// GET /ncrs — gated ncr.read (QMS-Owner / Internal-Auditor); the demo admin holds none → calm-403
// (a SYSTEM override is granted only for the live smoke). retry:false.
export function useNcrs() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["ncrs"],
    queryFn: async () => (await api.get<NcrList>("/api/v1/ncrs")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
