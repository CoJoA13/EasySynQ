import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  Audit, AuditList, AuditPlan, AuditPlanList, AuditProgramList, FindingList, ProcessRow,
} from "../../lib/types";

// Every audit-family read is gated (audit.read / finding.read) and the demo admin holds none —
// the S-web-6 calm-403 case. retry:false + a `forbidden` flag (the capa hooks idiom).
function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function useAuditPrograms() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audit-programs"],
    queryFn: async () => (await api.get<AuditProgramList>("/api/v1/audit-programs")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useAuditPlans(programId: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audit-plans", programId],
    queryFn: async () =>
      (await api.get<AuditPlanList>(`/api/v1/audit-programs/${programId!}/plans`)).data,
    enabled: programId !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useAuditPlan(planId: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["audit-plan", planId],
    queryFn: () => api.get<AuditPlan>(`/api/v1/audit-plans/${planId!}`),
    enabled: planId !== null,
    retry: false,
  });
}

export function useAudits() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audits"],
    queryFn: async () => (await api.get<AuditList>("/api/v1/audits")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useAudit(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["audit", id],
    queryFn: () => api.get<Audit>(`/api/v1/audits/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

export function useFindings(auditId: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["findings", auditId],
    queryFn: async () => (await api.get<FindingList>(`/api/v1/audits/${auditId!}/findings`)).data,
    enabled: auditId !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /processes returns a BARE array (not {data}). Auxiliary read for the plan form's process
// picker + name resolution — degrade gracefully (omit the picker) when process.read is missing.
export function useProcesses() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["processes"],
    queryFn: () => api.get<ProcessRow[]>("/api/v1/processes"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}
