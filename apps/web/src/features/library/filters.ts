import type { DocumentCurrentState, DocumentFilters } from "../../lib/types";

// The library's raw URL facet state (one short key per facet). `eff` is a relative-date BUCKET key
// (not an ISO timestamp) — it is translated to effective_from_gte at query time so the value stays
// stable within a day (no refetch loop).
export interface UrlFilters {
  state?: string;
  type?: string;
  owner?: string;
  clause?: string;
  eff?: string;
  // S-report-doc-control fix wave: the register's process facet (a process id). Library ignores it
  // (not in its FILTER_KEYS) — parsing it here is harmless for callers that never render it.
  process?: string;
}

export const STATES: DocumentCurrentState[] = [
  "Draft",
  "InReview",
  "Approved",
  "Effective",
  "UnderRevision",
  "Superseded",
  "Obsolete",
];

export const EFFECTIVE_BUCKETS: { value: string; label: string; days: number }[] = [
  { value: "30d", label: "Last 30 days", days: 30 },
  { value: "90d", label: "Last 90 days", days: 90 },
  { value: "365d", label: "Last 12 months", days: 365 },
];

export const PAGE_SIZES = [25, 50, 100];
export const DEFAULT_PAGE_SIZE = 25;

export function parseUrlFilters(p: URLSearchParams): UrlFilters {
  const out: UrlFilters = {};
  const state = p.get("state");
  if (state) out.state = state;
  const type = p.get("type");
  if (type) out.type = type;
  const owner = p.get("owner");
  if (owner) out.owner = owner;
  const clause = p.get("clause");
  if (clause) out.clause = clause;
  const eff = p.get("eff");
  if (eff) out.eff = eff;
  const process = p.get("process");
  if (process) out.process = process;
  return out;
}

function bucketToGte(bucket: string | undefined): string | undefined {
  const b = EFFECTIVE_BUCKETS.find((x) => x.value === bucket);
  if (!b) return undefined;
  // YYYY-MM-DD only — stable within the day, so the query key (and cache) don't churn each render.
  return new Date(Date.now() - b.days * 86_400_000).toISOString().slice(0, 10);
}

export function toDocumentFilters(uf: UrlFilters): DocumentFilters {
  const f: DocumentFilters = {};
  if (uf.state) f.current_state = uf.state as DocumentCurrentState;
  if (uf.type) f.document_type = uf.type;
  if (uf.owner) f.owner_user_id = uf.owner;
  if (uf.clause) f.clause = uf.clause;
  const gte = bucketToGte(uf.eff);
  if (gte) f.effective_from_gte = gte;
  if (uf.process) f.process_id = uf.process;
  return f;
}

export function parseOffset(p: URLSearchParams): number {
  const n = Number(p.get("offset") ?? "0");
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

export function parsePageSize(p: URLSearchParams): number {
  const n = Number(p.get("size") ?? String(DEFAULT_PAGE_SIZE));
  return PAGE_SIZES.includes(n) ? n : DEFAULT_PAGE_SIZE;
}
