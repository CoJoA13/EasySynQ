import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { MePermissions } from "../../lib/types";

// S-web-3 (DP-6): the caller's OWN effective permissions, cached at app load, for COARSE affordance
// gating (e.g. the Library "New Document" entry). Default SYSTEM scope; pass a scope to ask a refined
// question (e.g. document.create at a DOC_CLASS). Per-document write buttons gate on the document's
// `capabilities` block instead (GET /documents/{id}) — a global key set can't express ABAC scope.
export function usePermissions(scope?: { level: string; id?: string }) {
  const api = useApi();
  const qs = scope
    ? `?scope_level=${scope.level}${scope.id ? `&scope_id=${encodeURIComponent(scope.id)}` : ""}`
    : "";
  const query = useQuery({
    queryKey: ["me-permissions", scope?.level ?? "SYSTEM", scope?.id ?? null],
    queryFn: () => api.get<MePermissions>(`/api/v1/me/permissions${qs}`),
  });
  const allowed = new Set(
    (query.data?.permissions ?? []).filter((p) => p.effect === "ALLOW").map((p) => p.key),
  );
  return { ...query, can: (key: string) => allowed.has(key) };
}
