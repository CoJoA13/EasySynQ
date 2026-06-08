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
//
// Everything the caller renders is read from the POLL CACHE, which is KEYED by (documentId, toVid,
// fromVid). So when the version pair changes while this hook stays mounted, it never falls back to
// the previous pair's result (no stale Ready/Failed pages, no page-image requests for a diff that
// hasn't been requested yet) and the poll is enabled only once THIS pair's row exists and is
// Pending.
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

  // The cache entry for THIS pair (seeded by the POST's onSuccess). Reading it here is keyed by the
  // pair and reactive via the useQuery subscription below, so `enabled` flips correctly when the
  // POST resolves — and a different pair reads its own (initially empty) cache, never this one's.
  const seeded = qc.getQueryData<VisualDiffStatus>(key);

  const poll = useQuery({
    queryKey: key,
    queryFn: () => api.get<VisualDiffStatus>(url),
    // Enabled ONLY once THIS pair's row is seeded AND Pending — never before the POST (no
    // 404-before-request) and never on a terminal row (no redundant GET). refetchInterval then
    // polls until a poll returns a terminal status, and halts.
    enabled: active && seeded?.status === "Pending",
    refetchInterval: (q) => (q.state.data?.status === "Pending" ? 2500 : false),
  });

  const status: VisualDiffStatus | undefined = poll.data;
  const isError = request.isError || poll.isError;
  const error: Error | null = request.error ?? poll.error;

  // Re-request a STALLED (Pending) render — e.g. the dev renderer was off when the row was created.
  // (A terminal Failed/Unavailable row is NOT re-drivable: get_or_create_visual_diff only re-enqueues
  // a Pending row, so the viewer offers this only from the Pending state, never from Failed.)
  function retry() {
    qc.removeQueries({ queryKey: key });
    request.reset();
    request.mutate();
  }

  return {
    status,
    // The POST is in flight and nothing has been seeded yet — the viewer shows the phased skeleton
    // (as it also does for status==="Pending").
    isLoading: active && status === undefined && !isError,
    isError,
    error,
    retry,
  };
}
