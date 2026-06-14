import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { VisualDiffStatus } from "../../lib/types";

// S-web-4b: the worker-async visual page-image diff of `fromVid` → `toVid`. Honours the S-dcr-3b
// contract: POST to REQUEST (idempotent — UPSERTs the cache row + enqueues only while Pending),
// then GET to POLL (a pure read — never enqueues, and never runs before the POST, so it can't hit
// the 404-before-request). The POLL owns its own fetch: it is enabled once the POST has SETTLED
// (request.isSuccess — a reactive flag), and its first GET runs against an EMPTY cache (data ===
// undefined), so it always fetches and populates the cache itself. We deliberately do NOT seed the
// cache from the POST's onSuccess: a `{status:"Pending"}` seed stamps dataUpdatedAt=now at the same
// instant `enabled` flips true, which React Query v5 reads as "fresh" (default staleTime:0) and
// SKIPS the enable-triggered initial GET — the viewer then hangs on "Rendering…" with zero GET
// traffic (the first-mount stall). Letting the poll fetch off an empty cache avoids that race.
// refetchInterval then sustains the poll while Pending and halts at any terminal status
// (Ready/Failed/Unavailable) — the SetupWizard refetchInterval precedent. Gated document.read_draft
// server-side: a 403 surfaces as an ApiError, which the viewer renders quietly (DP-6). `enabled`
// lets the caller withhold the request until the visual mode is actually active (a Text-mode view
// never triggers a render).
//
// Everything the caller renders is read from the POLL CACHE, which is KEYED by (documentId, toVid,
// fromVid). So when the version pair changes while this hook stays mounted, it never falls back to
// the previous pair's result (no stale Ready/Failed pages, no page-image requests for a diff that
// hasn't been requested yet) and the poll is enabled only once THIS pair's POST has settled.
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
    // Enabled ONLY once THIS pair's POST has settled (request.isSuccess) — the settled POST proves
    // the cache row exists, so the first GET can't hit the 404-before-request, and the POST is
    // idempotent server-side (S-dcr-3b) so the GET never double-enqueues. The poll's first fetch
    // runs against an empty cache (no seed), so it always fetches and populates the status itself —
    // never skipped by an enable-time staleness read. refetchInterval then polls until a poll
    // returns a terminal status, and halts.
    enabled: active && request.isSuccess,
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
