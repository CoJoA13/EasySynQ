import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type {
  Capa,
  RiskCreateBody,
  RiskRegisterPublishBody,
  RiskRegisterStatus,
  RiskRow,
  RiskUpdateBody,
} from "../../lib/types";

// POST /risks — create a risk row (lazily mints the RSK head on the first one). Invalidate the list,
// the head status (it may have just been created), and the summary.
export function useCreateRisk() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RiskCreateBody) => api.send<RiskRow>("POST", "/api/v1/risks", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["risks"] });
      void qc.invalidateQueries({ queryKey: ["risk-register"] });
      void qc.invalidateQueries({ queryKey: ["risks-summary"] });
    },
  });
}

// PATCH /risks/{id} — edit a risk row (partial; re-derives risk_rating server-side on a score change).
export function useUpdateRisk(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RiskUpdateBody) => api.send<RiskRow>("PATCH", `/api/v1/risks/${id}`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["risk", id] });
      void qc.invalidateQueries({ queryKey: ["risks"] });
    },
  });
}

// POST /risks/{id}/capa — the one-click idempotent treat-spawn (201 new / 200 replay both land here).
// The new CAPA appears on the board → invalidate ["capas"]; the risk now carries linked_capa_id.
export function useSpawnRiskCapa(id: string) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.send<Capa>("POST", `/api/v1/risks/${id}/capa`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["risk", id] });
      void qc.invalidateQueries({ queryKey: ["risks"] });
      void qc.invalidateQueries({ queryKey: ["capas"] });
    },
  });
}

// S-risk-5 — the register-steward lifecycle. Invalidate every read a lifecycle act changes: the head
// status (gates the page's edit affordances + the read-only banner), the working rows (re-frozen at
// publish / re-graded against new governing criteria at release), and the governing summary (changes
// at release). Mirrors useCreateRisk's invalidation set.
function useInvalidateRiskRegister(): () => void {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["risk-register"] });
    void qc.invalidateQueries({ queryKey: ["risks"] });
    void qc.invalidateQueries({ queryKey: ["risks-summary"] });
  };
}

// POST /risks/register/start-revision — Effective→UnderRevision so the rows become editable again
// (register.manage @ SYSTEM server-side; 409 unless Effective).
export function useStartRiskRegisterRevision() {
  const api = useApi();
  const invalidate = useInvalidateRiskRegister();
  return useMutation({
    mutationFn: () => api.send<RiskRegisterStatus>("POST", "/api/v1/risks/register/start-revision"),
    onSuccess: () => invalidate(),
  });
}

// POST /risks/register/publish — freeze the working rows + scoring criteria into a new version and
// submit it for approval (register.manage @ SYSTEM; 409 unless Draft/UnderRevision; 409 on an empty
// register). The approval/decide step then rides the existing /tasks DOCUMENT arm — no FE here.
export function usePublishRiskRegister() {
  const api = useApi();
  const invalidate = useInvalidateRiskRegister();
  return useMutation({
    mutationFn: (body: RiskRegisterPublishBody) =>
      api.send<RiskRegisterStatus>("POST", "/api/v1/risks/register/publish", body),
    onSuccess: () => invalidate(),
  });
}

// POST /risks/register/release — promote the Approved version to Effective (document.release @ SYSTEM
// + SoD-2 server-side: the releaser must differ from the version's author/approver). Empty body,
// matching useReleaseDocument / useReleaseObjective.
export function useReleaseRiskRegister() {
  const api = useApi();
  const invalidate = useInvalidateRiskRegister();
  return useMutation({
    mutationFn: () => api.send<RiskRegisterStatus>("POST", "/api/v1/risks/register/release", {}),
    onSuccess: () => invalidate(),
  });
}
