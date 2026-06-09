import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { Capa, CapaList } from "../../lib/types";

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
