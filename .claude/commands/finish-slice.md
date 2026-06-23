---
description: Record a finished slice — slice-history narrative + CLAUDE.md learning + Current-status pointer + the memory resume note + test deltas, in one consistent pass
allowed-tools: Read, Edit, Write, Glob, Grep, Bash
---

Record the slice just completed (named in $ARGUMENTS, e.g. "S-dcr-ui-2b"). Keep CLAUDE.md lean — the deep narrative lives in `docs/slice-history.md`; CLAUDE.md holds only the learning line + the Current-status pointer. Do all of the following consistently:

1. **`docs/slice-history.md`** — add the full per-slice narrative under the right family section, **newest-first** (match the dense house style of the existing entries). Cover: what shipped, the migration/key/endpoint/contract delta (or "front-end-only"), the load-bearing decisions + traps, the named-not-faked deferrals, the test deltas, and the PR + squash SHA. Update the file's top "Migration head" line if it moved.

2. **`CLAUDE.md` → "Recent learnings"** — prepend ONE dense bullet (the section is **capped ~12, newest-first**; demote/drop the oldest if over cap, per the section's own comment). Convert relative dates to absolute. Lead with the slice's thesis + the ⚠ traps a future session must carry.

3. **`CLAUDE.md` → "Current status"** — append a concise pointer for the slice (✅, the one-line what-it-did, PR link), and update the **migration-head note** (which slices added a migration vs none; the next free number).

4. **The memory resume note** (`~/.claude/projects/<key>/memory/<track>-resume-point.md`) — if this slice belongs to a track with a resume note (e.g. `s-dcr-ui-resume-point`), UPDATE it (don't duplicate): mark this slice merged (PR + SHA), refresh the as-built anchors, and set the NEXT slice as the resume point with its named scope + any review insights to fold in. Update the one-line pointer in `MEMORY.md`.

5. **Test deltas** — state the before→after counts (e.g. web 779→805; +N api). If you can, confirm them: `cd apps/web && npx vitest run --pool=forks --maxWorkers=1 2>&1 | tail -3` (the clean-signal full run; vitest 4 — the old `--poolOptions.forks.singleFork=true` was removed) and/or the api unit count.

Then show a short diff summary of the doc files touched and stop — do NOT commit unless asked (the slice's PR/branch is usually already merged; these doc edits may belong on `main` via a follow-up or were part of the merged PR). If the slice's PR is still open, note that these belong in it.

Guardrails: pin every claim to what actually shipped (verify vs the diff/CI, not memory); name deferrals honestly ("not faked"); don't restate what the code/git already records.
