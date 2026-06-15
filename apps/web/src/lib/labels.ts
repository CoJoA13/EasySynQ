// Shared humanisers for critique #2b — de-leaking raw backend tokens (snake_case keys, machine
// stage_keys) on the most-scrutinised surfaces. Used by the open-bag generic renderers (MR
// SummaryTable, drift counts) and as the fallback for the closed label maps (DCR dimensions).

// A snake_case / lower token → sentence-case words. The generic fallback when no curated label exists;
// turns "verify_failed_at" into "Verify failed at" rather than leaking the raw key.
export function humanizeToken(token: string): string {
  const spaced = token.replace(/_/g, " ").trim();
  if (!spaced) return token;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

// A workflow stage_key → a human label. The MR action task's key is "action:<output_uuid>" (spawn.py),
// so drop any ":<suffix>" before humanising — "action:9f2c…" reads as "Action", never the raw uuid.
export function humanizeStageKey(stageKey: string): string {
  const head = stageKey.split(":")[0] ?? stageKey;
  return humanizeToken(head);
}
