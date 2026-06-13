import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Dcr, DcrCancelBody, DcrCreateBody, DcrPatchBody, DcrSpawnBody } from "../../lib/types";

export interface SpawnDcrVars {
  body: DcrSpawnBody;
  idempotencyKey: string;
}

// After any DCR write, re-read the server (NEVER optimistic — FSM/SoD/effectivity truth is server-only).
function useDcrInvalidator(id?: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["dcrs"] });
    if (id) {
      void qc.invalidateQueries({ queryKey: ["dcr", id] });
      void qc.invalidateQueries({ queryKey: ["dcr-impact", id] });
    }
  };
}

// Standalone create (POST /dcrs) — 201, no Idempotency-Key. Returns the new Dcr (caller opens its drawer).
export function useRaiseDcr() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DcrCreateBody) => api.send<Dcr>("POST", "/api/v1/dcrs", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["dcrs"] }),
  });
}

// Edit while Open (PATCH). Invalidate on SETTLE: a 409 dcr_not_editable means a concurrent advance, so the
// drawer must refetch to show the real state behind the calm error (the useNcrDisposition precedent).
export function usePatchDcr(id: string) {
  const api = useApi();
  const invalidate = useDcrInvalidator(id);
  return useMutation({
    mutationFn: (body: DcrPatchBody) => api.send<Dcr>("PATCH", `/api/v1/dcrs/${id}`, body),
    onSettled: invalidate,
  });
}

// Cancel (POST /cancel). Same onSettled rationale (409 dcr_not_cancellable = concurrent advance).
export function useCancelDcr(id: string) {
  const api = useApi();
  const invalidate = useDcrInvalidator(id);
  return useMutation({
    mutationFn: (body: DcrCancelBody) => api.send<Dcr>("POST", `/api/v1/dcrs/${id}/cancel`, body),
    onSettled: invalidate,
  });
}

// CAPA → DCR spawn (1:N idempotent — 201 new / 200 replay both resolve to a Dcr here, NO status branching).
// The modal generates a per-mount Idempotency-Key. reason_class defaults to "capa" server-side.
export function useRaiseDcrFromCapa(capaId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: SpawnDcrVars) =>
      api.send<Dcr>("POST", `/api/v1/capas/${capaId}/raise-dcr`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["dcrs"] }),
  });
}

// MR ACTION-output → DCR spawn (1:N idempotent). reason_class is FORCED to mgmt_review server-side.
export function useRaiseDcrFromMrOutput(reviewId: string, outputId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: SpawnDcrVars) =>
      api.send<Dcr>(
        "POST",
        `/api/v1/management-reviews/${reviewId}/outputs/${outputId}/raise-dcr`,
        body,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["dcrs"] }),
  });
}
