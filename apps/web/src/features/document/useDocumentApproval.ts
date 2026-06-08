import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { WorkflowInstance } from "../../lib/types";

// S-web-5: the document's current approval cycle (the latest workflow instance + its tasks), or null
// when the document was never submitted. Gated document.read server-side; a 403 surfaces as an
// ApiError → the consumer renders it quietly (DP-6), like useVersionDiff.
export function useDocumentApproval(documentId: string | null, enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: ["document-approval", documentId],
    queryFn: () => api.get<WorkflowInstance | null>(`/api/v1/documents/${documentId}/approval`),
    enabled: enabled && documentId !== null,
  });
}
