// The rail's open-task count, as a pure decision separated from rendering (the drawerWidth.ts
// precedent in this folder). Concentrating the rule here means every state can be exercised without
// a DOM, a query client or MSW — including the one that is awkward to stage through the network: a
// REFETCH that fails after an earlier success, where `data` is still populated AND `isError` is true.
//
// The rule it enforces is the house never-a-confident-zero contract (the ack-bell / NotificationBell
// pattern): a failed count must never be rendered as "0", and must never be rendered as a stale
// number presented as current.

export type TaskCountState =
  /** First load still in flight — say nothing rather than pre-announce a count we do not have. */
  | { kind: "pending" }
  /** The count could not be established. NEVER collapse this to zero. */
  | { kind: "unavailable" }
  /** Known to be zero. Nothing to flag. */
  | { kind: "none" }
  | { kind: "count"; count: number };

export interface TaskCountQuery {
  data?: unknown[] | undefined;
  isError: boolean;
  isLoading: boolean;
  forbidden: boolean;
}

export function resolveTaskCount(q: TaskCountQuery): TaskCountState {
  // Checked FIRST, and deliberately ahead of `data`. After a successful fetch followed by a failed
  // refetch, TanStack keeps the previous `data` while setting isError — reading the length there
  // would present a stale number as if it were current.
  if (q.isError || q.forbidden) return { kind: "unavailable" };
  // Absence of DATA is the pending test, not `isLoading`. TanStack reports isLoading === false for a
  // PAUSED query (offline, fetchStatus "paused"), so keying on isLoading let an offline first load
  // fall through to `data?.length ?? 0` and render a confident zero — precisely the silent-zero this
  // module exists to prevent. `isLoading` remains in the type because callers pass the whole query
  // result, but the decision no longer depends on it.
  if (q.data === undefined) return { kind: "pending" };
  return q.data.length === 0 ? { kind: "none" } : { kind: "count", count: q.data.length };
}

/** The badge caption. Capped, because the rail cannot widen for a four-digit number. */
export function taskCountBadge(state: TaskCountState): string | null {
  if (state.kind === "unavailable") return "·";
  if (state.kind === "count") return state.count > 99 ? "99+" : String(state.count);
  return null;
}

/**
 * The link's accessible name. The count is information a sighted user receives from the badge, so
 * assistive tech has to receive it too — an aria-hidden badge alone would make it visual-only.
 */
export function taskCountLabel(base: string, state: TaskCountState): string {
  switch (state.kind) {
    case "unavailable":
      return `${base}, task count unavailable`;
    case "count":
      return `${base}, ${state.count} open ${state.count === 1 ? "task" : "tasks"}`;
    case "pending":
    case "none":
      return base;
  }
}
