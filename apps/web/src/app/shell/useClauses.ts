import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Clause } from "../../lib/types";

export function useClauses() {
  const api = useApi();
  return useQuery({
    queryKey: ["clauses"],
    queryFn: () => api.get<Clause[]>("/api/v1/clauses"),
  });
}
