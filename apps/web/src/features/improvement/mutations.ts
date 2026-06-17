import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Initiative,
  InitiativeCreateBody,
  InitiativePatchBody,
  InitiativeTransitionBody,
} from "../../lib/types";

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
