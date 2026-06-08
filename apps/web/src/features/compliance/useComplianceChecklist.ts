import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { ComplianceChecklist } from "../../lib/types";

// GET /reports/compliance-checklist is hard-gated (report.compliance_checklist.read). A 403 is a
// first-class non-error outcome (the caller may simply lack the key) → surface a `forbidden` flag so
// the page renders a calm no-access panel instead of a generic error. retry:false (don't hammer a
// permission denial).
export function useComplianceChecklist() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["compliance-checklist"],
    queryFn: () => api.get<ComplianceChecklist>("/api/v1/reports/compliance-checklist"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
