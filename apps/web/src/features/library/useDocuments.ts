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
  // S-doc-filters: emit each only when DEFINED — `!== undefined`, NEVER `if (filters.x)`, because
  // `false` is falsy and the CREATE picker sends false. The other useDocuments callers (Library, the
  // ui-2a DCR target picker) never set these → undefined → not emitted → their query stays identical.
  if (filters.has_effective_version !== undefined)
    p.set("filter[has_effective_version][eq]", String(filters.has_effective_version));
  if (filters.managed_subtype !== undefined)
    p.set("filter[managed_subtype][eq]", String(filters.managed_subtype));
  // s-dcr-target-typeahead: a top-level free-text param (NOT bracketed). Emitted only when non-empty,
  // so the Library + the other useDocuments callers (which never set `q`) keep an identical query.
  if (filters.q) p.set("q", filters.q);
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
