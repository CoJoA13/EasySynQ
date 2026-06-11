import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { DriftStatus, SupersededCopies } from "../../lib/types";

// S-web-8: the two drift.read-gated admin reads (S-drift-3, R41). retry:false + the forbidden
// flag — the compliance/audits calm-403 pattern. Pure reads; no scan is ever triggered.

export const SUPERSEDED_PAGE_SIZE = 50;

export function useDriftStatus() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["drift-status"],
    queryFn: () => api.get<DriftStatus>("/api/v1/admin/drift/status"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useSupersededCopies(offset: number) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["drift-superseded", offset],
    queryFn: () =>
      api.get<SupersededCopies>(
        `/api/v1/admin/drift/superseded-copies?limit=${SUPERSEDED_PAGE_SIZE}&offset=${offset}`,
      ),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
