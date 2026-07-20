import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { DocumentControlRegister, DocumentFilters } from "../../lib/types";
import { buildFilterParams } from "../library/useDocuments";

// GET /reports/document-control is hard-gated (report.read SYSTEM ‖ PROCESS) + row-filtered by
// document.read. A 403 is a first-class non-error outcome (the caller may lack the surface key) →
// surface `forbidden` for a calm no-access panel. retry:false (don't hammer a permission denial).
// The mirror of useComplianceChecklist.
//
// S-report-doc-control fix wave (FIX 4): accepts the SAME typed `DocumentFilters` the Library's
// useDocuments takes, and serializes it with the EXACT same `filter[field][op]` grammar
// (buildFilterParams, factored out of useDocuments so both stay byte-identical) — the register has
// no pagination, so only the filter params are appended. `filters` rides the query key so a facet
// change triggers a refetch (React Query hashes the key by value, not by reference).
export function useDocumentControlRegister(filters: DocumentFilters = {}) {
  const api = useApi();
  const qs = buildFilterParams(filters).toString();
  const query = useQuery({
    queryKey: ["document-control-register", filters],
    queryFn: () =>
      api.get<DocumentControlRegister>(
        qs ? `/api/v1/reports/document-control?${qs}` : "/api/v1/reports/document-control",
      ),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
