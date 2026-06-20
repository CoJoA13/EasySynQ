import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Capa, RiskCreateBody, RiskRow, RiskUpdateBody } from "../../lib/types";

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
