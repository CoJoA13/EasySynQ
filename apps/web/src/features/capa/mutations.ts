// apps/web/src/features/capa/mutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Capa,
  CapaRaiseBody,
  CapaVerifyBody,
  Complaint,
  ComplaintCreateBody,
  Ncr,
  NcrCreateBody,
  NcrDispositionBody,
  NcSeverity,
  SpawnCapaBody,
  StageBlockBody,
} from "../../lib/types";

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

// S-capa-overdue: set (or clear) the target completion date. Gated on capa.update in the UI.
export function useCapaSetTargetDate(capaId: string) {
  const api = useApi();
  const invalidate = useCapaInvalidator(capaId);
  return useMutation({
    mutationFn: (target_completion_date: string | null) =>
      api.send<Capa>("PATCH", `/api/v1/capas/${capaId}`, { target_completion_date }),
    onSuccess: invalidate,
  });
}

// --- S-web-7c complaint + NCR intake writes -------------------------------------------------

export function useCreateComplaint() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ComplaintCreateBody) =>
      api.send<Complaint>("POST", "/api/v1/complaints", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["complaints"] }),
  });
}

// Idempotent server-side (201 new / 200 replay both resolve here). We invalidate the complaint list
// (its spawned_capa_id flips) + the CAPA board (the new CAPA appears).
export function useSpawnCapa() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ complaintId, severity }: { complaintId: string; severity?: NcSeverity }) =>
      api.send<Capa>("POST", `/api/v1/complaints/${complaintId}/spawn-capa`, {
        severity,
      } satisfies SpawnCapaBody),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["complaints"] });
      void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}

export function useCreateNcr() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NcrCreateBody) => api.send<Ncr>("POST", "/api/v1/ncrs", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["ncrs"] }),
  });
}

// One-shot ISO 8.7 disposition (409 ncr_already_dispositioned if already set — the caller surfaces it
// calmly). Invalidate on SETTLE (success OR error), not just success: a 409 race means another user
// already disposed this NCR, so the list is stale (still shows the "Record disposition" action) — the
// refetch flips the row to its read-only disposed state behind the calm error.
export function useNcrDisposition(ncrId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NcrDispositionBody) =>
      api.send<Ncr>("PATCH", `/api/v1/ncrs/${ncrId}/disposition`, body),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["ncrs"] }),
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
