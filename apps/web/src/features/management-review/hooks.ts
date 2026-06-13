import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  MgmtReviewDetail,
  MgmtReviewListResponse,
  MgmtReviewNextDue,
  WorkflowInstance,
} from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function useMgmtReviews() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-reviews"],
    queryFn: () => api.get<MgmtReviewListResponse>("/api/v1/management-reviews"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useMgmtReview(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-review", id],
    queryFn: () => api.get<MgmtReviewDetail>(`/api/v1/management-reviews/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useMgmtReviewApproval(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-review-approval", id],
    queryFn: () =>
      api.get<WorkflowInstance | null>(`/api/v1/management-reviews/${id!}/approval`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useMgmtReviewNextDue() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["management-review-next-due"],
    queryFn: () => api.get<MgmtReviewNextDue>("/api/v1/management-reviews/next-due"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
