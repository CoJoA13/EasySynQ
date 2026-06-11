import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";

// S-ack-2: the TopBar ack-bell count — the caller's open DOC_ACK tasks. Kept in app/shell (not
// features/review) so the shell never depends on a feature module. Self-scoped server-side.
export function useAckCount() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["ack-count"],
    queryFn: () => api.get<{ id: string }[]>("/api/v1/tasks?assignee=me&state=PENDING&type=DOC_ACK"),
    retry: false,
  });
  return query.data?.length ?? 0;
}
