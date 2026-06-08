import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { VisualDiffStatus } from "../../lib/types";

// S-web-4b: the worker-async visual page-image diff of `fromVid` → `toVid`. Honours the S-dcr-3b
// contract: POST to REQUEST (idempotent — UPSERTs the cache row + enqueues only while Pending),
// then GET to POLL (a pure read — never enqueues, and never runs before the POST, so it can't hit
// the 404-before-request). The POST seeds the poll cache so the GET never races that 404. The poll
// runs ONLY while status==="Pending" and stops at any terminal status (Ready/Failed/Unavailable) —
// the SetupWizard refetchInterval precedent. Gated document.read_draft server-side: a 403 surfaces
// as an ApiError, which the viewer renders quietly (DP-6). `enabled` lets the caller withhold the
// request until the visual mode is actually active (a Text-mode view never triggers a render).
export function useVisualDiff(
  documentId: string | null,
  toVid: string | null,
  fromVid: string | null,
  enabled: boolean,
) {
  const api = useApi();
  const qc = useQueryClient();

  const active =
    enabled && documentId !== null && toVid !== null && fromVid !== null && toVid !== fromVid;
  const key = ["visual-diff", documentId, toVid, fromVid] as const;
  const url = `/api/v1/documents/${documentId}/versions/${toVid}/visual-diff?from=${fromVid}`;

  const request = useMutation({
    mutationFn: () => api.send<VisualDiffStatus>("POST", url),
    onSuccess: (data) => qc.setQueryData(key, data),
  });

  // Fire the POST once per active, distinct version pair (it re-fires when the pair changes — the
  // ids are in the dep list). `request.mutate` is referentially stable, so this is not a loop; the
  // mutation object itself is deliberately excluded to avoid re-POSTing on unrelated re-renders.
  useEffect(() => {
    if (active) request.mutate();
  }, [active, documentId, toVid, fromVid]);

  const poll = useQuery({
    queryKey: key,
    queryFn: () => api.get<VisualDiffStatus>(url),
    // Enabled ONLY when the POST came back Pending — a terminal POST result never triggers a
    // redundant GET. Once enabled, refetchInterval keeps polling until a poll returns a terminal
    // status, then halts. (Gating on request.data, not poll.data, keeps this non-circular.)
    enabled: active && request.data?.status === "Pending",
    refetchInterval: (q) => (q.state.data?.status === "Pending" ? 2500 : false),
  });

  const status: VisualDiffStatus | undefined = poll.data ?? request.data;
  const error: Error | null = request.error ?? poll.error;
  const isError = request.isError || poll.isError;

  // Re-request a stalled/failed render: clear the cached status, reset the mutation, POST again.
  function retry() {
    qc.removeQueries({ queryKey: key });
    request.reset();
    request.mutate();
  }

  return {
    status,
    // The POST is in flight and nothing has come back yet — the viewer shows the phased skeleton
    // (as it also does for status==="Pending").
    isLoading: active && status === undefined && !isError,
    isError,
    error,
    retry,
  };
}
