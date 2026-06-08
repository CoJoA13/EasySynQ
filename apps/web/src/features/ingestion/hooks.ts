import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  ImportChecklist,
  ImportDecisionLog,
  ImportDupeClusterList,
  ImportFileDetail,
  ImportFileList,
  ImportRun,
  ImportVersionFamilyList,
} from "../../lib/types";
import { FILES_PAGE_SIZE, buildFilesQuery, type FilesFilter } from "./filters";

// A run is "settling" (poll it) while the engine is scanning/classifying/etc OR committing; it RESTS
// at Proposed/Reviewing (human review) and at every terminal status.
const POLLING_STATUSES = new Set([
  "Created", "Scanning", "Scanned", "Extracting", "Classifying", "Classified",
  "Deduping", "Proposing", "Committing",
]);
export function isRunSettling(status: string | undefined): boolean {
  return status !== undefined && POLLING_STATUSES.has(status);
}

export function useImportRuns() {
  const api = useApi();
  return useQuery({
    queryKey: ["import-runs"],
    queryFn: () => api.get<ImportRun[]>("/api/v1/admin/imports"),
  });
}

export function useImportRun(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-run", runId],
    queryFn: () => api.get<ImportRun>(`/api/v1/admin/imports/${runId}`),
    enabled: runId !== null,
    // Poll while the engine is working (scan/commit); halt at a rest/terminal status.
    refetchInterval: (q) => (isRunSettling(q.state.data?.status) ? 2500 : false),
  });
}

export function useImportFiles(runId: string | null, filter: FilesFilter, offset: number) {
  const api = useApi();
  const qs = buildFilesQuery(filter, { limit: FILES_PAGE_SIZE, offset });
  return useQuery({
    queryKey: ["import-files", runId, filter, offset],
    queryFn: () => api.get<ImportFileList>(`/api/v1/admin/imports/${runId}/files?${qs}`),
    enabled: runId !== null,
  });
}

export function useImportFile(runId: string | null, fileId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-file", runId, fileId],
    queryFn: () => api.get<ImportFileDetail>(`/api/v1/admin/imports/${runId}/files/${fileId}`),
    enabled: runId !== null && fileId !== null,
  });
}

export function useDupeClusters(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-dupe-clusters", runId],
    queryFn: () => api.get<ImportDupeClusterList>(`/api/v1/admin/imports/${runId}/dupe-clusters`),
    enabled: runId !== null,
  });
}

export function useVersionFamilies(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-version-families", runId],
    queryFn: () =>
      api.get<ImportVersionFamilyList>(`/api/v1/admin/imports/${runId}/version-families`),
    enabled: runId !== null,
  });
}

export function useChecklist(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-checklist", runId],
    queryFn: () => api.get<ImportChecklist>(`/api/v1/admin/imports/${runId}/checklist`),
    enabled: runId !== null,
  });
}

export function useDecisions(runId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-decisions", runId],
    queryFn: () => api.get<ImportDecisionLog>(`/api/v1/admin/imports/${runId}/decisions`),
    enabled: runId !== null,
  });
}
