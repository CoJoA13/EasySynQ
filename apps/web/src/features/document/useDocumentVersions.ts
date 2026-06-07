import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DocumentVersion } from "../../lib/types";

// S-web-2: the version timeline (History tab). Gated document.read_draft server-side — a reader
// without it gets a 403 (ApiError), which the tab renders as quiet "no access" (DP-6). Lazy: only
// fetched when the History tab is active.
export function useDocumentVersions(documentId: string | null, enabled: boolean) {
  const api = useApi();
  return useQuery({
    queryKey: ["document-versions", documentId],
    queryFn: () => api.get<DocumentVersion[]>(`/api/v1/documents/${documentId}/versions`),
    enabled: enabled && documentId !== null,
  });
}
