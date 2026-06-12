import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type { Task } from "../../lib/types";

// The My-Tasks rail source — the caller's open tasks across all types (self-scoped server-side, no
// permission key). The TopBar bell's useAckCount is the DOC_ACK-only sibling. retry:false + a forbidden
// flag for symmetry (self-scoped → 403 is not expected, but never crash if policy changes).
export function useMyTasks() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["my-tasks"],
    queryFn: () => api.get<Task[]>("/api/v1/tasks?assignee=me&state=PENDING"),
    retry: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
