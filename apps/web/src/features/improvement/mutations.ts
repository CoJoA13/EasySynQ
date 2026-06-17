import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Initiative,
  InitiativeAuthorization,
  InitiativeAuthorizationRequestBody,
  InitiativeCreateBody,
  InitiativePatchBody,
  InitiativeSpawnBody,
  InitiativeTransitionBody,
} from "../../lib/types";

export interface SpawnInitiativeVars {
  body: InitiativeSpawnBody;
  idempotencyKey: string;
}

// After any initiative write, re-read the server (NEVER optimistic — the FSM/gate truth is server-only).
function useInitiativeInvalidator(id?: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["initiatives"] });
    if (id) {
      void qc.invalidateQueries({ queryKey: ["initiative", id] });
      void qc.invalidateQueries({ queryKey: ["initiative-stage-events", id] });
    }
  };
}

// Manual create (POST, source=manual) — 201, no Idempotency-Key. Returns the new Initiative (the caller
// opens its drawer). onSuccess (not onSettled): a fresh create has no FSM race to self-heal.
export function useCreateInitiative() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: InitiativeCreateBody) =>
      api.send<Initiative>("POST", "/api/v1/improvement-initiatives", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["initiatives"] }),
  });
}

// OFI/OBSERVATION finding → initiative (1:N idempotent — 201 new / 200 replay both resolve to an
// Initiative, NO status branching). onSuccess (not onSettled): a 200 replay IS a success, there is no
// 409 race to self-heal. The modal mints a per-mount Idempotency-Key. source=OFI is derived server-side.
export function useRaiseInitiativeFromFinding(findingId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: SpawnInitiativeVars) =>
      api.send<Initiative>("POST", `/api/v1/findings/${findingId}/raise-initiative`, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["initiatives"] }),
  });
}

// ACTION/IMPROVEMENT MR output → initiative (1:N idempotent). The optional body.process_id homes the
// initiative (an MR has no process); source=review is derived server-side.
export function useRaiseInitiativeFromMrOutput(reviewId: string, outputId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: SpawnInitiativeVars) =>
      api.send<Initiative>(
        "POST",
        `/api/v1/management-reviews/${reviewId}/outputs/${outputId}/raise-initiative`,
        body,
        { "Idempotency-Key": idempotencyKey },
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["initiatives"] }),
  });
}

// Edit mutable metadata (PATCH; never the stage). onSettled: a concurrent change means the drawer
// must refetch to show the real state behind the calm error (the usePatchDcr precedent).
export function usePatchInitiative(id: string) {
  const api = useApi();
  const invalidate = useInitiativeInvalidator(id);
  return useMutation({
    mutationFn: (body: InitiativePatchBody) =>
      api.send<Initiative>("PATCH", `/api/v1/improvement-initiatives/${id}`, body),
    onSettled: invalidate,
  });
}

// FSM move (POST /transition). onSettled: a 409 improvement_transition_invalid means a concurrent
// advance, so refetch on error too to self-heal the drawer (the DCR lifecycle precedent).
export function useTransitionInitiative(id: string) {
  const api = useApi();
  const invalidate = useInitiativeInvalidator(id);
  return useMutation({
    mutationFn: (body: InitiativeTransitionBody) =>
      api.send<Initiative>("POST", `/api/v1/improvement-initiatives/${id}/transition`, body),
    onSettled: invalidate,
  });
}

// S-improvement-4: request a Top-Management authorization (POST /request-authorization). onSettled:
// a 409 (not-Completed / already-in-flight) means a concurrent change → refetch the authorization
// cycle to self-heal the cockpit. Busts the initiative invalidator + the authorization query.
export function useRequestInitiativeAuthorization(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  const invalidate = useInitiativeInvalidator(id);
  return useMutation({
    mutationFn: (body: InitiativeAuthorizationRequestBody) =>
      api.send<InitiativeAuthorization>(
        "POST",
        `/api/v1/improvement-initiatives/${id}/request-authorization`,
        body,
      ),
    onSettled: () => {
      invalidate();
      void qc.invalidateQueries({ queryKey: ["initiative-authorization", id] });
    },
  });
}
