import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";

// S-web-2: fetch a single document (for a cold drawer deep-link where the row is not in the loaded
// list). `seed` (the clicked list row) is used as initialData to avoid a fetch/flash on the click path.
export function useDocument(
  documentId: string | null,
  // retry: pass false where a 403 is an EXPECTED outcome (the periodic-review context) so the
  // calm panel appears without re-hammering a deterministic deny; undefined keeps the
  // react-query default for the existing detail/drawer call sites.
  opts: { enabled: boolean; seed?: DocumentSummary; retry?: boolean | number },
) {
  const api = useApi();
  const seed = opts.seed && opts.seed.id === documentId ? opts.seed : undefined;
  return useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.get<DocumentSummary>(`/api/v1/documents/${documentId}`),
    enabled: opts.enabled && documentId !== null,
    initialData: seed,
    // Spread, not `retry: opts.retry` — an explicit `retry: undefined` key would OVERRIDE the
    // QueryClient defaultOptions (options spread wins), silently re-enabling retries where a
    // test/app default disabled them.
    ...(opts.retry !== undefined ? { retry: opts.retry } : {}),
  });
}
