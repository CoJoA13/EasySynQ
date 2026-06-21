import { useQuery } from "@tanstack/react-query";
import { ApiError, useApi } from "../../lib/api";
import type {
  InterestedParty,
  InterestedPartyListResponse,
  InterestedPartyRegisterStatus,
  InterestedPartyRegisterSummary,
} from "../../lib/types";

function forbiddenOf(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

// GET /interested-parties — the live working register rows (filter-not-403: a no-grant caller gets an
// empty list, never 403). The register PAGE rolls its party-type board + scorecard up from THESE rows
// (the working view), distinct from the governing summary (the Home read-of-record). Clause 4.2 is
// org-level — no process filter, all-or-nothing at SYSTEM.
export function useInterestedParties() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["interested-parties"],
    queryFn: async () =>
      (await api.get<InterestedPartyListResponse>("/api/v1/interested-parties")).data,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /interested-parties/{id} — one party (the drawer's fetch; register.read @ SYSTEM enforce → 403
// calmly).
export function useInterestedParty(id: string | null) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["interested-parties", id],
    queryFn: () => api.get<InterestedParty>(`/api/v1/interested-parties/${id!}`),
    enabled: id !== null,
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /interested-parties/summary — the GOVERNING categorical read-of-record (S-interested-parties-2;
// org-level register.read, 403-on-deny). Home reads this; the register page does NOT (it rolls up its
// own live working rows — distinct by design, the read-of-record posture).
export function useInterestedPartySummary() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["interested-parties-summary"],
    queryFn: () => api.get<InterestedPartyRegisterSummary>("/api/v1/interested-parties/summary"),
    retry: false,
  });
  return { ...query, forbidden: forbiddenOf(query.error) };
}

// GET /interested-parties/register — the head lifecycle state + the server-computed
// can_release/can_manage caps (the steward console's faithful gate). Any authenticated org member may
// read it (org-level, not row-sensitive) → no forbidden flag.
export function useInterestedPartyRegisterStatus() {
  const api = useApi();
  return useQuery({
    queryKey: ["interested-parties-register"],
    queryFn: () => api.get<InterestedPartyRegisterStatus>("/api/v1/interested-parties/register"),
    retry: false,
  });
}
