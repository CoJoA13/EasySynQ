import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  ImportBulkDecisionRequest,
  ImportChecklist,
  ImportDecisionLog,
  ImportDupeClusterList,
  ImportFileDecisionRequest,
  ImportFileDetail,
  ImportFileList,
  ImportMergeRequest,
  ImportMutationResult,
  ImportRun,
  ImportRunCreate,
  ImportSplitRequest,
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

export function useImportFiles(
  runId: string | null,
  filter: FilesFilter,
  offset: number,
  enabled = true,
) {
  const api = useApi();
  const qs = buildFilesQuery(filter, { limit: FILES_PAGE_SIZE, offset });
  return useQuery({
    queryKey: ["import-files", runId, filter, offset],
    queryFn: () => api.get<ImportFileList>(`/api/v1/admin/imports/${runId}/files?${qs}`),
    enabled: runId !== null && enabled,
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

// Invalidate everything a write can move: the row list, the checklist gate, the run counts, and (for
// structural ops) the cluster/family lists. Merge/split are server-authoritative — never reshape the
// client cache optimistically (D-5); just refetch.
function useRunInvalidator(runId: string | null) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["import-files", runId] });
    void qc.invalidateQueries({ queryKey: ["import-file", runId] });
    void qc.invalidateQueries({ queryKey: ["import-checklist", runId] });
    void qc.invalidateQueries({ queryKey: ["import-run", runId] });
    void qc.invalidateQueries({ queryKey: ["import-decisions", runId] });
    void qc.invalidateQueries({ queryKey: ["import-dupe-clusters", runId] });
    void qc.invalidateQueries({ queryKey: ["import-version-families", runId] });
  };
}

export function useFileDecision(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({
      fileId,
      body,
      idempotencyKey,
    }: {
      fileId: string;
      body: ImportFileDecisionRequest;
      idempotencyKey: string;
    }) =>
      api.send<ImportMutationResult>(
        "POST",
        `/api/v1/admin/imports/${runId}/files/${fileId}/decision`,
        body,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: invalidate,
  });
}

// ONE Idempotency-Key per bulk op (the S-ing-4 "stamp the key on a SINGLE row" rule).
export function useBulkDecision(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({
      body,
      idempotencyKey,
    }: {
      body: ImportBulkDecisionRequest;
      idempotencyKey: string;
    }) =>
      api.send<ImportMutationResult>("POST", `/api/v1/admin/imports/${runId}/decisions`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

export function useMerge(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: ImportMergeRequest; idempotencyKey: string }) =>
      api.send<ImportMutationResult>("POST", `/api/v1/admin/imports/${runId}/merge`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

export function useSplit(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: ImportSplitRequest; idempotencyKey: string }) =>
      api.send<ImportMutationResult>("POST", `/api/v1/admin/imports/${runId}/split`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: invalidate,
  });
}

export function useCreateImportRun() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ImportRunCreate) =>
      api.send<ImportRun>("POST", "/api/v1/admin/imports", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["import-runs"] }),
  });
}

export function useCancelRun(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: () => api.send<ImportRun>("POST", `/api/v1/admin/imports/${runId}/cancel`),
    onSuccess: invalidate,
  });
}

export function useCommitRun(runId: string | null) {
  const api = useApi();
  const invalidate = useRunInvalidator(runId);
  return useMutation({
    mutationFn: () => api.send<ImportRun>("POST", `/api/v1/admin/imports/${runId}/commit`),
    onSuccess: invalidate,
  });
}
