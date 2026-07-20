import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { DocumentControlRegister } from "../../lib/types";

// GET /reports/document-control is hard-gated (report.read SYSTEM). A 403 is a first-class non-error
// outcome (the caller may lack the key) → surface `forbidden` for a calm no-access panel. retry:false
// (don't hammer a permission denial). The mirror of useComplianceChecklist.
export function useDocumentControlRegister() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["document-control-register"],
    queryFn: () => api.get<DocumentControlRegister>("/api/v1/reports/document-control"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
