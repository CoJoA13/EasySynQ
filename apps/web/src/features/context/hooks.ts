import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  ContextIssue,
  ContextListResponse,
  ContextRegisterStatus,
  ContextRegisterSummary,
} from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

// GET /context — the live working register rows (filter-not-403: a no-grant caller gets an empty list,
// never 403). The register PAGE rolls its SWOT board + scorecard up from THESE rows (the working view),
// distinct from the governing summary (the Home read-of-record). Clause 4.1 is org-level — no process
// filter, all-or-nothing at SYSTEM.
export function useContextIssues() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["context"],
    queryFn: async () => (await api.get<ContextListResponse>("/api/v1/context")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /context/{id} — one issue (the drawer's fetch; register.read @ SYSTEM enforce → 403 calmly).
export function useContextIssue(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["context", id],
    queryFn: () => api.get<ContextIssue>(`/api/v1/context/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /context/summary — the GOVERNING categorical read-of-record (S-context-2; org-level
// register.read, 403-on-deny). Home reads this; the register page does NOT (it rolls up its own live
// working rows — distinct by design, the read-of-record posture).
export function useContextSummary() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["context-summary"],
    queryFn: () => api.get<ContextRegisterSummary>("/api/v1/context/summary"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /context/register — the head lifecycle state + the server-computed can_release/can_manage caps
// (the steward console's faithful gate). Any authenticated org member may read it (org-level, not
// row-sensitive) → no forbidden flag.
export function useContextRegisterStatus() {
  const api = useApi();
  return useQuery({
    queryKey: ["context-register"],
    queryFn: () => api.get<ContextRegisterStatus>("/api/v1/context/register"),
    retry: false,
  });
}
