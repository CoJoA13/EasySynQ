import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { VisualDiffStatus } from "../../lib/types";

// S-web-4b: the worker-async visual page-image diff of `fromVid` → `toVid`. Honours the S-dcr-3b
// contract: POST to REQUEST (idempotent — UPSERTs the cache row + enqueues only while Pending),
// then GET to POLL (a pure read — never enqueues, and never runs before THIS pair's POST, so it
// can't hit the 404-before-request). The POLL owns its own fetch: it is enabled once the POST has
// SETTLED FOR THIS PAIR (request.isSuccess AND request.variables === url — see below), and its
// first GET runs against an EMPTY cache (data === undefined), so it always fetches and populates the
// cache itself. We deliberately do NOT seed the cache from the POST's onSuccess: a `{status:"Pending"}`
// seed stamps dataUpdatedAt=now at the same instant `enabled` flips true, which React Query v5 reads
// as "fresh" (default staleTime:0) and SKIPS the enable-triggered initial GET — the viewer then
// hangs on "Rendering…" with zero GET traffic (the first-mount stall). Letting the poll fetch off an
// empty cache avoids that race. refetchInterval then sustains the poll while Pending and halts at
// any terminal status (Ready/Failed/Unavailable) — the SetupWizard refetchInterval precedent. Gated
// document.read_draft server-side: a 403 surfaces as an ApiError, which the viewer renders quietly
// (DP-6). `enabled` lets the caller withhold the request until the visual mode is actually active (a
// Text-mode view never triggers a render).
//
// PAIR-SCOPED enable gate (Codex P2 — 404-before-request): `request.isSuccess` is the SINGLE
// mutation's state, NOT pair-scoped. When this hook stays mounted and the pair CHANGES after a prior
// POST succeeded (navigating between …/diff?mode=visual pages, or the Compare picker switching
// pairs), on the first render with the NEW pair `request.isSuccess` is still `true` (stale, from the
// OLD pair) while the useEffect has not yet fired the new pair's POST — so a bare `isSuccess` gate
// would enable the poll for the new (empty) queryKey and GET a row that doesn't exist yet → a
// 404-before-request. We therefore pass the pair-identifying `url` as the mutate VARIABLE and gate
// the poll on `request.variables === url`: it is only true once THIS pair's POST has settled, so a
// newly-changed pair can never GET an unrequested row.
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

  // TVariables is the pair-identifying `url` so `request.variables` tracks WHICH pair the POST was
  // for (the enable gate below reads it). The mutationFn ignores it — `url` is closed over — but a
  // zero-arg fn is assignable to `(v: string) => …`.
  const request = useMutation<VisualDiffStatus, Error, string>({
    mutationFn: () => api.send<VisualDiffStatus>("POST", url),
  });

  // Fire the POST once per active, distinct version pair (it re-fires when the pair changes — the
  // ids are in the dep list). `request.mutate` is referentially stable, so this is not a loop; the
  // mutation object itself is deliberately excluded to avoid re-POSTing on unrelated re-renders.
  // `url` is derived from the ids (changes exactly when the pair changes), and is the mutate
  // variable so `request.variables` tracks the requested pair; it's in the dep list so eslint's
  // exhaustive-deps stays clean.
  useEffect(() => {
    if (active) request.mutate(url);
  }, [active, documentId, toVid, fromVid, url]);

  const poll = useQuery({
    queryKey: key,
    queryFn: () => api.get<VisualDiffStatus>(url),
    // Enabled ONLY once THIS pair's POST has settled (request.isSuccess AND request.variables ===
    // url — the pair-scoped gate). request.isSuccess alone is the single mutation's state and stays
    // true (stale) across a pair change before the new pair's POST fires; checking variables === url
    // holds the GET until THIS pair's POST has settled, so the settled POST proves the cache row
    // exists and the first GET can't hit the 404-before-request. The POST is idempotent server-side
    // (S-dcr-3b) so the GET never double-enqueues. The poll's first fetch runs against an empty cache
    // (no seed), so it always fetches and populates the status itself — never skipped by an enable-
    // time staleness read. refetchInterval then polls until a poll returns a terminal status, and halts.
    enabled: active && request.isSuccess && request.variables === url,
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
    request.mutate(url);
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
