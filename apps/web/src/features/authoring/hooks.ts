import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import { sha256Hex } from "../../lib/hash";
import type {
  ChangeSignificance,
  CheckinResult,
  ClauseMapping,
  DocumentCreate,
  DocumentSummary,
  InitUploadResult,
  WorkingDraft,
} from "../../lib/types";
import { putToPresigned } from "../../lib/upload";

// S-web-3 authoring mutations — all on useApi().send (the token is implicit, so authoring components
// inside the AppShell never thread a token). Each onSuccess invalidates the reads it changes. The
// dependency direction is one-way: features/authoring → features/document / lib (no cycle).

// Invalidate every read a per-document mutation can change (state, versions, where-used, the list).
function useInvalidateDocument(): (documentId: string) => void {
  const qc = useQueryClient();
  return (documentId: string) => {
    void qc.invalidateQueries({ queryKey: ["document", documentId] });
    void qc.invalidateQueries({ queryKey: ["document-versions", documentId] });
    void qc.invalidateQueries({ queryKey: ["clause-mappings", documentId] });
    void qc.invalidateQueries({ queryKey: ["where-used", documentId] });
    void qc.invalidateQueries({ queryKey: ["documents"] });
  };
}

export function useCreateDocument() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DocumentCreate) =>
      api.send<DocumentSummary>("POST", "/api/v1/documents", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useCheckout() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.send<WorkingDraft>("POST", `/api/v1/documents/${documentId}/checkout`),
    onSuccess: (_d, documentId) => invalidate(documentId),
  });
}

export function useBreakLock() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.send<{ lock_broken: boolean }>("POST", `/api/v1/documents/${documentId}/break-lock`),
    onSuccess: (_d, documentId) => invalidate(documentId),
  });
}

export function useStartRevision() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.send<DocumentSummary>("POST", `/api/v1/documents/${documentId}/start-revision`),
    onSuccess: (_d, documentId) => invalidate(documentId),
  });
}

export interface CheckinInput {
  documentId: string;
  file: File;
  changeReason: string;
  changeSignificance: ChangeSignificance;
}

// The 3-call upload orchestration: hash → init-upload → (PUT to MinIO unless dedup) → check-in.
export function useUploadAndCheckin() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: async ({
      documentId,
      file,
      changeReason,
      changeSignificance,
    }: CheckinInput): Promise<CheckinResult> => {
      const sha256 = await sha256Hex(file);
      const contentType = file.type || "application/octet-stream";
      const init = await api.send<InitUploadResult>(
        "POST",
        `/api/v1/documents/${documentId}/versions:init-upload`,
        { sha256, content_type: contentType },
      );
      // Skip the PUT when the bytes are already vaulted (content-addressed dedup).
      if (!init.dedup && init.upload_url) {
        await putToPresigned(init.upload_url, file, contentType);
      }
      return api.send<CheckinResult>("POST", `/api/v1/documents/${documentId}/checkin`, {
        sha256,
        change_reason: changeReason,
        change_significance: changeSignificance,
        mime_type: contentType,
      });
    },
    onSuccess: (_d, vars) => invalidate(vars.documentId),
  });
}

export function useMapClause() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: ({ documentId, clauseId }: { documentId: string; clauseId: string }) =>
      api.send<ClauseMapping>("POST", `/api/v1/documents/${documentId}/clause-mappings`, {
        clause_id: clauseId,
      }),
    onSuccess: (_d, vars) => invalidate(vars.documentId),
  });
}

export function useUnmapClause() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: ({ documentId, clauseId }: { documentId: string; clauseId: string }) =>
      api.send<void>("DELETE", `/api/v1/documents/${documentId}/clause-mappings/${clauseId}`),
    onSuccess: (_d, vars) => invalidate(vars.documentId),
  });
}

export function useSubmitReview() {
  const api = useApi();
  const invalidate = useInvalidateDocument();
  return useMutation({
    mutationFn: (documentId: string) =>
      api.send<DocumentSummary>("POST", `/api/v1/documents/${documentId}/submit-review`),
    onSuccess: (_d, documentId) => invalidate(documentId),
  });
}

// The current clause mappings (drives the ClauseMapper chips + the ≥1-before-submit gate).
export function useClauseMappings(documentId: string | null, enabled: boolean) {
  const api = useApi();
  return useQuery({
    queryKey: ["clause-mappings", documentId],
    queryFn: () => api.get<ClauseMapping[]>(`/api/v1/documents/${documentId}/clause-mappings`),
    enabled: enabled && documentId !== null,
  });
}
