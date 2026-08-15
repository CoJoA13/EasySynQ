import { getUniqueSearchParam } from "../../lib/effectiveView";

export interface RecordUrlState {
  q?: string;
  record_type?: string;
  disposition_state?: string;
  legal_hold?: string;
  source_document_id?: string;
  captured_by?: string;
  cursor?: string;
}

type RecordCriteria = Omit<RecordUrlState, "cursor">;

const CRITERIA_KEYS = [
  "q",
  "record_type",
  "disposition_state",
  "legal_hold",
  "source_document_id",
  "captured_by",
] as const satisfies readonly (keyof RecordCriteria)[];

const QUERY_KEYS = [
  "cursor",
  "q",
  "record_type",
  "source_document_id",
  "captured_by",
  "disposition_state",
  "legal_hold",
] as const satisfies readonly (keyof RecordUrlState)[];

function readNonblankUnique(
  searchParams: URLSearchParams,
  key: keyof RecordUrlState,
): string | undefined {
  const value = getUniqueSearchParam(searchParams, key);
  return value || undefined;
}

function setNonblank(params: URLSearchParams, key: string, value: string | undefined): void {
  if (value) params.set(key, value);
}

export function parseRecordUrlState(searchParams: URLSearchParams): RecordUrlState {
  const state: RecordUrlState = {};
  for (const key of QUERY_KEYS) {
    const value = readNonblankUnique(searchParams, key);
    if (value !== undefined) state[key] = value;
  }
  return state;
}

export function buildRecordsQuery(request: RecordUrlState & { limit: number }): string {
  const query = new URLSearchParams();
  query.set("limit", String(request.limit));
  for (const key of QUERY_KEYS) setNonblank(query, key, request[key]);
  return query.toString();
}

export function replaceRecordCriteria(
  searchParams: URLSearchParams,
  criteria: RecordCriteria,
): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  next.delete("cursor");
  for (const key of CRITERIA_KEYS) next.delete(key);
  for (const key of CRITERIA_KEYS) setNonblank(next, key, criteria[key]);
  return next;
}

export function pushRecordCursor(searchParams: URLSearchParams, cursor: string): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  next.delete("cursor");
  setNonblank(next, "cursor", cursor);
  return next;
}

export function clearRecordCursor(searchParams: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(searchParams);
  next.delete("cursor");
  return next;
}
