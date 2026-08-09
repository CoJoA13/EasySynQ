---
description: Record a finished slice in the neutral history, execution snapshot, and residual ledger
allowed-tools: Read, Edit, Write, Glob, Grep, Bash
---

Record the slice just completed (named in $ARGUMENTS, e.g. "S-dcr-ui-2b") in the neutral authority homes. Do all of the following consistently:

1. **`docs/slice-history.md`** — append the full shipped narrative under the right family section, preserving its historical-evidence role. Cover what shipped, the migration/key/endpoint/contract delta (or "front-end-only"), load-bearing decisions and traps, honest deferrals, fresh test deltas, and the PR plus squash SHA. Do not revise historical snapshots to make them current.

2. **`docs/current-status.md`** — after fresh evidence, update the dated execution snapshot and its parseable baseline frontmatter. Keep it a current coordination snapshot, not a narrative history; migration truth still comes from `cd apps/api && uv run alembic heads`.

3. **`docs/open-residuals.md`** — close any shipped residual using its existing stable identifier, or add a new stable `RES-*` record for work deliberately deferred. Preserve the required record fields and closure contract.

4. **Recurring traps** — promote a trap only when it recurs and belongs in `.claude/rules/engineering-patterns.md`; do not duplicate a one-off slice lesson into a tool-specific compatibility note.

5. **Test deltas** — record before→after counts from actual commands (for example, the focused API or web suite) in the history narrative and current snapshot. Do not report a delta that was not measured.

Then show a short diff summary of the authority homes touched and stop — do NOT commit unless asked (the slice's PR/branch is usually already merged; these doc edits may belong on `main` via a follow-up or were part of the merged PR). If the slice's PR is still open, note that these belong in it.

Guardrails: pin every claim to what actually shipped (verify against the diff/CI, not memory); name deferrals honestly ("not faked"); don't restate what code or Git already records.
