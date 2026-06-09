// apps/web/src/features/capa/mutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Capa, CapaRaiseBody, CapaVerifyBody, StageBlockBody } from "../../lib/types";

// After any CAPA write, invalidate the detail + the board (+ the approval read for the action-plan).
// We never reshape optimistically — the server is the source of truth (close-gate, SoD, signatures).
function useCapaInvalidator(capaId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["capa", capaId] });
    void qc.invalidateQueries({ queryKey: ["capas"] });
    void qc.invalidateQueries({ queryKey: ["capa-approval", capaId] });
  };
}

export function useRaiseCapa() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CapaRaiseBody) => api.send<Capa>("POST", "/api/v1/capas", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["capas"] }),
  });
}

function useStageMutation(capaId: string, path: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: (body: StageBlockBody) =>
      api.send<Capa>("POST", `/api/v1/capas/${capaId}/${path}`, body),
    onSuccess: invalidate,
  });
}

export const useCapaContainment = (id: string) => useStageMutation(id, "containment");
export const useCapaRootCause = (id: string) => useStageMutation(id, "root-cause");
export const useCapaActionPlan = (id: string) => useStageMutation(id, "action-plan");
export const useCapaImplement = (id: string) => useStageMutation(id, "implement");

export function useCapaVerify(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: (body: CapaVerifyBody) =>
      api.send<Capa>("POST", `/api/v1/capas/${capaId}/verify`, body),
    onSuccess: invalidate,
  });
}

export function useCapaClose(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: () => api.send<Capa>("POST", `/api/v1/capas/${capaId}/close`),
    onSuccess: invalidate,
  });
}

export function useLinkEvidence(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: ({
      recordId,
      targetId,
      linkReason,
    }: {
      recordId: string;
      targetId: string;
      linkReason?: string;
    }) =>
      api.send("POST", `/api/v1/records/${recordId}/evidence-links`, {
        target_type: "capa_stage",
        target_id: targetId,
        link_reason: linkReason,
      }),
    onSuccess: invalidate,
  });
}
