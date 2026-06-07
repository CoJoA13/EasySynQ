import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DocumentFilters, DocumentsPage } from "../../lib/types";

export interface PageParams {
  limit: number;
  offset: number;
}

// Build the GET /documents query string from the typed facet state + pagination. URLSearchParams
// percent-encodes values (so an ISO timestamp's "+00:00" becomes "%2B00:00", not a space).
export function buildDocumentsQuery(filters: DocumentFilters, page: PageParams): string {
  const p = new URLSearchParams();
  p.set("limit", String(page.limit));
  p.set("offset", String(page.offset));
  if (filters.current_state) p.set("filter[current_state][eq]", filters.current_state);
  if (filters.document_type) p.set("filter[document_type][eq]", filters.document_type);
  if (filters.owner_user_id) p.set("filter[owner_user_id][eq]", filters.owner_user_id);
  if (filters.clause) p.set("filter[clause_refs][has]", filters.clause);
  if (filters.effective_from_gte) p.set("filter[effective_from][gte]", filters.effective_from_gte);
  return p.toString();
}

export function useDocuments(filters: DocumentFilters, page: PageParams) {
  const api = useApi();
  const qs = buildDocumentsQuery(filters, page);
  return useQuery({
    queryKey: ["documents", filters, page],
    queryFn: () => api.get<DocumentsPage>(`/api/v1/documents?${qs}`),
  });
}
