import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { WhereUsed } from "../../lib/types";

// S-web-2: the where-used graph (Where-used tab; gate document.read, neighbour titles resolved
// server-side). Lazy: only fetched when the Where-used tab is active.
export function useWhereUsed(documentId: string | null, enabled: boolean) {
  const api = useApi();
  return useQuery({
    queryKey: ["where-used", documentId],
    queryFn: () => api.get<WhereUsed>(`/api/v1/documents/${documentId}/where-used`),
    enabled: enabled && documentId !== null,
  });
}
