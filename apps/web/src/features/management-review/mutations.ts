import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  MgmtReview,
  MgmtReviewCreateBody,
  MgmtReviewDetail,
  MgmtReviewMetaBody,
  NcSeverity,
  ReviewOutput,
  ReviewOutputCreateBody,
  ReviewOutputUpdateBody,
} from "../../lib/types";

// Invalidate every read a lifecycle mutation can change: the detail, the approval cycle, the list,
// the my-tasks rail on the Home page, AND the Home next-due tile (release moves the cadence anchor —
// `_last_released_effective_from` — so the derived due/overdue status changes; Codex P2).
function useInvalidateReview(): (id: string) => void {
  const qc = useQueryClient();
  return (id: string) => {
    void qc.invalidateQueries({ queryKey: ["management-review", id] });
    void qc.invalidateQueries({ queryKey: ["management-review-approval", id] });
    void qc.invalidateQueries({ queryKey: ["management-reviews"] });
    void qc.invalidateQueries({ queryKey: ["my-tasks"] });
    void qc.invalidateQueries({ queryKey: ["management-review-next-due"] });
  };
}

export function useCreateReview() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: MgmtReviewCreateBody) =>
      api.send<MgmtReview>("POST", "/api/v1/management-reviews", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["management-reviews"] }),
  });
}

export function useCompileInputs() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReviewDetail>("POST", `/api/v1/management-reviews/${id}/compile-inputs`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useAddOutput() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ReviewOutputCreateBody }) =>
      api.send<ReviewOutput>("POST", `/api/v1/management-reviews/${id}/outputs`, body),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function usePatchOutput() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({
      id,
      oid,
      body,
    }: {
      id: string;
      oid: string;
      body: ReviewOutputUpdateBody;
    }) =>
      api.send<ReviewOutput>("PATCH", `/api/v1/management-reviews/${id}/outputs/${oid}`, body),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function useDeleteOutput() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({ id, oid }: { id: string; oid: string }) =>
      api.send<void>("DELETE", `/api/v1/management-reviews/${id}/outputs/${oid}`),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function usePatchMeta() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: MgmtReviewMetaBody }) =>
      api.send<MgmtReview>("PATCH", `/api/v1/management-reviews/${id}`, body),
    onSuccess: (_d, { id }) => invalidate(id),
  });
}

export function useSubmitReview() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReview>("POST", `/api/v1/management-reviews/${id}/submit-review`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useReleaseReview() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReview>("POST", `/api/v1/management-reviews/${id}/release`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useCloseReview() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<MgmtReview>("POST", `/api/v1/management-reviews/${id}/close`, {}),
    onSuccess: (_d, id) => invalidate(id),
  });
}

export function useRaiseMrCapa() {
  const api = useApi();
  const invalidate = useInvalidateReview();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, oid, severity }: { id: string; oid: string; severity: NcSeverity }) =>
      api.send<ReviewOutput>(
        "POST",
        `/api/v1/management-reviews/${id}/outputs/${oid}/raise-capa`,
        { severity },
      ),
    onSuccess: (_d, { id }) => {
      invalidate(id);
      void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}
