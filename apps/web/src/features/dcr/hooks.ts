import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { Dcr, DcrDetail, DcrImpact, DcrImpactList, DcrList } from "../../lib/types";

// List — client-side filtering happens in the page (the CAPA precedent), so this takes no args.
export function useDcrs() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["dcrs"],
    queryFn: async (): Promise<Dcr[]> => (await api.get<DcrList>("/api/v1/dcrs")).data,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useDcr(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["dcr", id],
    queryFn: () => api.get<DcrDetail>(`/api/v1/dcrs/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useDcrImpact(id: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["dcr-impact", id],
    queryFn: async (): Promise<DcrImpact[]> => (await api.get<DcrImpactList>(`/api/v1/dcrs/${id!}/impact`)).data,
    enabled: id !== null,
    retry: false,
  });
}
