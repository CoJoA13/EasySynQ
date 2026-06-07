import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DocumentType } from "../../lib/types";

// S-web-2: the document-type catalog, used to resolve document_type_id → friendly name (the library
// Type column + facet). Shell-scoped so the cached map is shared across the page + the detail drawer.
export function useDocumentTypes() {
  const api = useApi();
  return useQuery({
    queryKey: ["document-types"],
    queryFn: () => api.get<DocumentType[]>("/api/v1/document-types"),
  });
}
