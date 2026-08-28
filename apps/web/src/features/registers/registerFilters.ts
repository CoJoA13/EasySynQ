// Server-side facets for the CAPA / Risk / Audit registers.
//
// Those listings took no query parameters at all: they loaded a fixed newest-first window and
// reported `truncated`, so once an org passed the cap its OLDEST rows were unreachable — from the
// SPA as well as the API. Narrowing is what reaches them, because the conditions run in SQL BEFORE
// the window (see services/common/register_filters.py).

export interface RegisterFilterState {
  /** ISO date (YYYY-MM-DD) lower bound on creation. */
  createdFrom?: string;
  /** ISO date upper bound on creation. */
  createdTo?: string;
}

/**
 * Serialize to the bracketed `filter[field][op]` grammar the API accepts (doc 15 §3.2).
 *
 * An empty value is OMITTED rather than sent blank: the API refuses an unparseable value with a
 * 422, so a cleared control must remove its parameter, not send an empty one.
 */
export function buildRegisterParams(state: RegisterFilterState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.createdFrom) params.set("filter[created_at][gte]", state.createdFrom);
  if (state.createdTo) {
    // The user means "up to and including this day", but a bare date parses as midnight, which
    // would exclude everything created during it.
    params.set("filter[created_at][lte]", `${state.createdTo}T23:59:59`);
  }
  return params;
}

/** The query-string suffix, or "" when nothing is selected. */
export function registerQuerySuffix(state: RegisterFilterState): string {
  const query = buildRegisterParams(state).toString();
  return query ? `?${query}` : "";
}

/** A stable, order-independent cache key for a filter state. */
export function registerFilterKey(state: RegisterFilterState): string {
  const params = buildRegisterParams(state);
  return [...params.entries()]
    .map(([k, v]) => `${k}=${v}`)
    .sort()
    .join("&");
}

export function hasActiveRegisterFilters(state: RegisterFilterState): boolean {
  return registerFilterKey(state) !== "";
}
