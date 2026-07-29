import type { DocumentCurrentState, DocumentFilters } from "../../lib/types";
import { formatDateInTimeZone } from "../../lib/time";

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

function subtractCalendarDays(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (year === undefined || month === undefined || day === undefined) return isoDate;
  const cursor = new Date(Date.UTC(year, month - 1, day));
  cursor.setUTCDate(cursor.getUTCDate() - days);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${cursor.getUTCFullYear()}-${pad(cursor.getUTCMonth() + 1)}-${pad(cursor.getUTCDate())}`;
}

function bucketToGte(
  bucket: string | undefined,
  orgTimezone: string | null | undefined,
): string | undefined {
  const b = EFFECTIVE_BUCKETS.find((x) => x.value === bucket);
  if (!b || !orgTimezone) return undefined;
  // Start from TODAY'S calendar date in the organization timezone, then subtract calendar days.
  // Subtracting milliseconds before slicing a UTC ISO string shifts the facet by a day whenever
  // the organization and UTC are on different dates (and also models DST days as always 24h).
  const today = formatDateInTimeZone(new Date(Date.now()).toISOString(), orgTimezone);
  return subtractCalendarDays(today, b.days);
}

export function toDocumentFilters(uf: UrlFilters, orgTimezone?: string | null): DocumentFilters {
  const f: DocumentFilters = {};
  if (uf.state) f.current_state = uf.state as DocumentCurrentState;
  if (uf.type) f.document_type = uf.type;
  if (uf.owner) f.owner_user_id = uf.owner;
  if (uf.clause) f.clause = uf.clause;
  const gte = bucketToGte(uf.eff, orgTimezone);
  if (gte) f.effective_from_gte = gte;
  // The `process` facet is register-only (S-report-doc-control fix wave R3-1) — the Library's
  // FILTER_KEYS/FacetBar/hasFilters/clearFilters don't know about it, so mapping it here would
  // silently narrow the Library by a hidden, uncleared filter. The register maps it itself.
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
