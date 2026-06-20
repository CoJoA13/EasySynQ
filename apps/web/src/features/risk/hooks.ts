import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { RiskListResponse, RiskRegisterStatus, RiskRow, RiskSummary } from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

// GET /risks — the live working satellite, per-process row-filtered (filter-not-403; a no-grant
// caller gets an empty list). The register PAGE rolls its scorecard up from THESE rows (the working
// view), distinct from the governing summary (Home/MR read).
export function useRisks() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["risks"],
    queryFn: async () => (await api.get<RiskListResponse>("/api/v1/risks")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /risks/{id} — one risk row (the drawer's fetch; enforced at the row's PROCESS scope → 403 calmly).
export function useRisk(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["risk", id],
    queryFn: () => api.get<RiskRow>(`/api/v1/risks/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /risks/summary — the GOVERNING high-risk read-of-record (S-risk-4a; org-level register.read).
// Home + the doc-13 dashboard read this; the register page does NOT (it rolls up its own live rows).
export function useRiskSummary() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["risks-summary"],
    queryFn: () => api.get<RiskSummary>("/api/v1/risks/summary"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /risks/register — the head lifecycle state (gates the New/edit affordances + the read-only
// banner). Any authenticated org member may read it (org-level, not row-sensitive) → no forbidden flag.
export function useRiskRegisterStatus() {
  const api = useApi();
  return useQuery({
    queryKey: ["risk-register"],
    queryFn: () => api.get<RiskRegisterStatus>("/api/v1/risks/register"),
    retry: false,
  });
}
