import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Audit, AuditCreateBody, AuditPlan, AuditPlanCreateBody, AuditProgram,
  AuditProgramCreateBody, AuditProgramUpdateBody, Finding, FindingCorrectionBody,
  FindingCreateBody,
} from "../../lib/types";

// Invalidate + refetch, never optimistic — the FSM, the close gate, and the NC→auto-CAPA are
// server truths. No Idempotency-Key: the audits endpoints have no replay latch (forms guard
// double-submit via disabled-while-pending).

export function useCreateProgram() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditProgramCreateBody) =>
      api.send<AuditProgram>("POST", "/api/v1/audit-programs", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audit-programs"] }),
  });
}

export function useUpdateProgram(programId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditProgramUpdateBody) =>
      api.send<AuditProgram>("PATCH", `/api/v1/audit-programs/${programId}`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audit-programs"] }),
  });
}

export function useCreatePlan(programId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditPlanCreateBody) =>
      api.send<AuditPlan>("POST", `/api/v1/audit-programs/${programId}/plans`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audit-plans", programId] }),
  });
}

export function useCreateAudit() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AuditCreateBody) => api.send<Audit>("POST", "/api/v1/audits", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["audits"] }),
  });
}

// One mutation for all six transitions — the variable IS the sub-resource path ("plan" |
// "conduct" | "draft-findings" | "report" | "begin-closing" | "close"). Invalidate on SETTLE:
// a 409 (invalid_audit_transition from a stale tab, or audit_close_blocked) means our cached
// state may be stale — refetch behind the calm error (the 7c disposition-race precedent).
export function useAdvanceAudit(auditId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => api.send<Audit>("POST", `/api/v1/audits/${auditId}/${path}`),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["audit", auditId] });
      void qc.invalidateQueries({ queryKey: ["audits"] });
    },
  });
}

// An NC response carries auto_capa_id → the CAPA board must see the new CAPA (["capas"]).
export function useCreateFinding(auditId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FindingCreateBody) =>
      api.send<Finding>("POST", `/api/v1/audits/${auditId}/findings`, body),
    onSuccess: (created) => {
      void qc.invalidateQueries({ queryKey: ["findings", auditId] });
      if (created.auto_capa_id) void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}

// Correction: settle-invalidate (a 409 finding_already_corrected race means the list is stale —
// refetch flips the original to its superseded render behind the calm error).
export function useCorrectFinding(auditId: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ findingId, body }: { findingId: string; body: FindingCorrectionBody }) =>
      api.send<Finding>("POST", `/api/v1/findings/${findingId}/correction`, body),
    onSettled: (created) => {
      void qc.invalidateQueries({ queryKey: ["findings", auditId] });
      if (created?.auto_capa_id) void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}
