import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DocumentVersion } from "../../lib/types";

// S-web-2: the version timeline (History tab). Gated document.read_draft server-side — a reader
// without it gets a 403 (ApiError), which the tab renders as quiet "no access" (DP-6). Lazy: only
// fetched when the History tab is active.
// S-dcr-ui-3: opts.retry lets a calm-403 caller (the DCR diff page) skip the default 3 retries on a
// deterministic deny (the S-web-8 useTask conditional-spread precedent); existing callers untouched.
export function useDocumentVersions(
  documentId: string | null,
  enabled: boolean,
  opts?: { retry?: boolean },
) {
  const api = useApi();
  return useQuery({
    queryKey: ["document-versions", documentId],
    queryFn: () => api.get<DocumentVersion[]>(`/api/v1/documents/${documentId}/versions`),
    enabled: enabled && documentId !== null,
    ...(opts?.retry === false ? { retry: false } : {}),
  });
}
