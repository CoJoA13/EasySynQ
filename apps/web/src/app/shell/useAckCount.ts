import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";

// S-ack-2: the TopBar ack-bell count — the caller's open DOC_ACK tasks. Kept in app/shell (not
// features/review) so the shell never depends on a feature module. Self-scoped server-side.
//
// The count is reported ALONGSIDE the query's error/loading flags so the bell can tell "you have zero
// acks" apart from "the count couldn't be loaded". The previous `data?.length ?? 0` collapsed a 403 /
// network failure to a confident `0` — the bell read "no acks" identically whether you had none or the
// call failed (a silent-zero). The caller renders an indeterminate bell on error, never a fake 0.
export function useAckCount(): { count: number; isError: boolean; isLoading: boolean } {
  const api = useApi();
  const query = useQuery({
    queryKey: ["ack-count"],
    queryFn: () =>
      api.get<{ id: string }[]>("/api/v1/tasks?assignee=me&state=PENDING&type=DOC_ACK"),
    retry: false,
  });
  return {
    count: query.data?.length ?? 0,
    isError: query.isError,
    isLoading: query.isLoading,
  };
}
