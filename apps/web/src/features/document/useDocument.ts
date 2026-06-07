import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";

// S-web-2: fetch a single document (for a cold drawer deep-link where the row is not in the loaded
// list). `seed` (the clicked list row) is used as initialData to avoid a fetch/flash on the click path.
export function useDocument(
  documentId: string | null,
  opts: { enabled: boolean; seed?: DocumentSummary },
) {
  const api = useApi();
  const seed = opts.seed && opts.seed.id === documentId ? opts.seed : undefined;
  return useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api.get<DocumentSummary>(`/api/v1/documents/${documentId}`),
    enabled: opts.enabled && documentId !== null,
    initialData: seed,
  });
}
