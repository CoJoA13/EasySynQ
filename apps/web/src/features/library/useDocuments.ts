import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";

export function useDocuments() {
  const api = useApi();
  return useQuery({
    queryKey: ["documents"],
    queryFn: () => api.get<DocumentSummary[]>("/api/v1/documents"),
  });
}
