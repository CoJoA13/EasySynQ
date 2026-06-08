import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { SearchResults, Suggestion } from "../../lib/types";

// GET /search — ranked metadata-plane hits (Effective documents only). Filter-not-403: a caller
// who may read nothing gets results:[] + hidden_by_scope > 0, never an error.
export function useSearch(q: string) {
  const api = useApi();
  const term = q.trim();
  return useQuery({
    queryKey: ["search", term],
    queryFn: () => api.get<SearchResults>(`/api/v1/search?q=${encodeURIComponent(term)}&limit=25`),
    enabled: term.length >= 1,
  });
}

// GET /search/suggest — lightweight identifier/title type-ahead for the ⌘K palette.
export function useSuggest(q: string) {
  const api = useApi();
  const term = q.trim();
  return useQuery({
    queryKey: ["search-suggest", term],
    queryFn: () =>
      api.get<{ suggestions: Suggestion[] }>(
        `/api/v1/search/suggest?q=${encodeURIComponent(term)}&limit=10`,
      ),
    enabled: term.length >= 1,
  });
}
