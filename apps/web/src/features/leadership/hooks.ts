import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { LeadershipAuthorizationCycle, LeadershipAuthorizationStatus } from "../../lib/types";

// S-leadership-1 FE: the document-backed Top-Management RELEASE authorization (POL/OBJ/MR). The status
// read is safe to call on ANY document — it returns is_leadership_artifact=false / required=false for an
// ordinary doc — so the release-gate panel self-suppresses without the caller knowing the doc type.
// POL/OBJ/MR share the documented_information id, so `documentId` is `objective.id` / `mr.id` directly.

export function useLeadershipAuthorization(documentId: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["leadership-authorization", documentId],
    queryFn: () =>
      api.get<LeadershipAuthorizationStatus>(
        `/api/v1/documents/${documentId}/leadership-authorization`,
      ),
    enabled: documentId !== null,
    // document.read-gated; the caller already holds read on the detail they're viewing. A deny/error is
    // not a transient → no retry.
    retry: false,
  });
  // Release is blocked when the gate is required AND the current Approved version isn't yet authorized.
  // False while loading (the common non-leadership case stays instant); a click during that window is a
  // calm surfaced 409 from the cutover, never a broken flow.
  const blocksRelease = query.data?.required === true && query.data.authorized !== true;
  return { ...query, blocksRelease };
}

export function useRequestLeadershipAuthorization(documentId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    // No Idempotency-Key: the backend already guards single-active (authorization_in_progress) and
    // already-authorized (both 409), so a double-submit is a calm surfaced 409, not a duplicate cycle.
    mutationFn: (comment?: string) =>
      api.send<LeadershipAuthorizationCycle>(
        "POST",
        `/api/v1/documents/${documentId}/request-leadership-authorization`,
        comment ? { comment } : undefined,
      ),
    onSuccess: () => {
      // A NEW cycle was opened → the caller's task list may have changed.
      void qc.invalidateQueries({ queryKey: ["my-tasks"] });
    },
    // CR-1 / CX-5: refetch the status on BOTH success and the expected 409s
    // (already_authorized / authorization_in_progress) — a concurrent approver may have advanced the
    // cycle, so a conflict means the cached "no cycle" status is stale and the panel must re-derive.
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["leadership-authorization", documentId] });
    },
  });
}
