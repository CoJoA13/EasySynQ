# S-dcr-impact-annotation — DCR impact-dimension annotation (clause §10→§7.5) — design

- **Date:** 2026-06-14
- **Slice:** a small follow-up closing a named residual of the (now-complete) DCR-UI track: the SPA can *view* a DCR's 7 impact dimensions but can't *annotate* them. This wires the requester annotation through the already-built `PUT /dcrs/{id}/impact`.
- **Status:** owner-approved design (brainstorm 2026-06-14; F1/F2 settled WITH the owner after verifying the backend).
- **Scope decision — 100% FRONT-END-ONLY.** The backend endpoint + service already exist (S-dcr-3b/-5). No migration, no permission key, no new endpoint, no contract change. Gate: **`/check-web` only** (head stays `0051`).
- **Doc grounding:** doc-05 (the change-control loop) · the impact-assessment model (`ImpactDimension`, `impact_assessment.requester_annotation`) · the web SPA testing rules (`.claude/rules/engineering-patterns.md` "Web SPA testing").

## s0 · As-built anchors (verified — pin to these, NOT narrative)
- **`PUT /dcrs/{dcr_id}/impact`** ([api/dcr.py:555-566]) — gate **`changeRequest.assess`** (`_dcr_assess`); body `ImpactAnnotate { annotations: dict[str, str] }` keyed by `ImpactDimension` value; returns the refreshed `{"data": [_impact…]}`.
- **`annotate_impact`** ([services/dcr/service.py:390-]) — a **partial merge**: it sets `requester_annotation` only on the dimensions present in the dict (`auto_populated` untouched); an unknown dimension key → **422** `unknown_dimension`; a dimension with no existing `impact_assessment` row → **409** `impact_not_assessed`; emits `DCR_UPDATED`. ⚠ It has **no FSM-state gate** — it only requires the impact rows to exist (the DCR has been assessed). So the backend permits annotation in any post-assess state.
- **`_impact` serializer** — `{id, dimension (one of 7 `ImpactDimension` values), auto_populated: object|null, requester_annotation: string|null, created_at, updated_at}` (the `DcrImpact` TS type in `lib/types.ts`).
- **`features/dcr/DcrImpactTable.tsx`** — read-only; 3 columns (Dimension · System facts · Annotation); empty-state "Not yet assessed."; rendered in `DcrDrawer`'s "Impact assessment" section as `<DcrImpactTable impact={impact ?? []} />`.
- **`features/dcr/hooks.ts`** — `useDcrImpact(id)` → `GET /dcrs/{id}/impact` (`{data}` unwrap), key `["dcr-impact", id]`. **`features/dcr/mutations.ts`** — the existing DCR write hooks + `useDcrInvalidator`.
- **`lib/api.ts`** — `apiSend`/`useApi().send` method union is `"POST" | "PATCH" | "DELETE"` (no `PUT`).
- **`usePermissions().can(key)`** — SYSTEM-scoped; `_dcr` carries no `process_id` so DCR write gating is SYSTEM-scoped (the read-spine precedent).

## s1 · Owner decisions (2026-06-14)
- **F1 — editable window:** the Annotation column is editable when **`impact.length > 0 && can("changeRequest.assess") && dcr.state ∉ {Closed, Cancelled, Rejected}`**. Rationale: matches the backend's permissiveness (no state gate) so a requester/approver can refine annotations right up through approval (where they inform the decision); only the three terminal states hide it (an edit there is pointless). No client gate the backend doesn't honor.
- **F2 — UX:** **inline-editable cells + one batch Save.** The Annotation cells become `Textarea`s; a single "Save annotations" button PUTs only the changed dimensions (the partial-merge dict). Not a modal.

## s2 · Changes
### `lib/api.ts`
- Widen the method union to `"POST" | "PUT" | "PATCH" | "DELETE"` in BOTH `apiSend` (line ~78) and `useApi().send` (line ~93). Additive, backward-compatible — existing callers are byte-unaffected.

### `features/dcr/mutations.ts` — `useAnnotateImpact(dcrId)`
- `useMutation({ mutationFn: (annotations: Record<string,string>) => api.send<{data: DcrImpact[]}>("PUT", `/api/v1/dcrs/${dcrId}/impact`, { annotations }) })`.
- `onSuccess` → invalidate `["dcr-impact", dcrId]` + `["dcr", dcrId]` (annotate emits `DCR_UPDATED`). Non-optimistic (the server returns the refreshed rows).

### `features/dcr/DcrImpactTable.tsx` — add an editable mode
- Props: `{ impact: DcrImpact[]; editable?: boolean; dcrId?: string }`.
- Read-only path (default / `!editable`): **byte-unchanged** (the current render + empty-state).
- Editable path (`editable && dcrId`):
  - Local draft state: `Record<dimension, string>` seeded from each row's `requester_annotation ?? ""`. Re-seed when the `impact` prop identity/content changes (so a post-save refetch resets the draft to the saved values) — keyed on the rows.
  - The Annotation cell renders a `Textarea` (autosize, small) bound to the draft for that dimension, with an `aria-label` like `Annotation for {dimension}` (distinct per row — no duplicate-aria-label trap).
  - Below the table: a **"Save annotations"** button. On click → compute the **changed** subset (`draft[dim] !== (row.requester_annotation ?? "")`) → `useAnnotateImpact(dcrId).mutate(changed)`. The button is **disabled when there are no changes** and shows a loading state while saving. A calm inline error (Mantine `Text c="red"` / `Alert`) on failure; the table self-heals from the refetch on success.
  - `useAnnotateImpact(dcrId)` is called unconditionally (hooks rules) but only `.mutate`d on Save; harmless in read-only mode (never fired).

### `features/dcr/DcrDrawer.tsx`
- Compute `const canAnnotate = (impact?.length ?? 0) > 0 && can("changeRequest.assess") && !["Closed","Cancelled","Rejected"].includes(dcr.state);` (via `usePermissions()`), and pass `editable={canAnnotate} dcrId={dcr.id}` to `DcrImpactTable`. (The "Impact assessment" section heading + placement unchanged.)

## s3 · Error handling & data flow
- The UI only ever edits dimensions that already have rows, so 409 `impact_not_assessed` / 422 `unknown_dimension` are unreachable from it — but surface calmly (inline error) if they occur.
- 403 can't appear (the affordance is gated by `can("changeRequest.assess")`); a stale-gate 403 falls to the calm error.
- Save sends ONLY changed dimensions (the partial merge) — an untouched dimension is never PUT.
- After a successful save the impact query refetches; the draft re-seeds from the new rows (no stale-draft).

## s4 · Testing
- `DcrImpactTable.test.tsx` (extend): editable mode renders a `Textarea` per row seeded from `requester_annotation`; editing one + Save calls the mutation with **only the changed dimension(s)** (assert the PUT body); Save is disabled when nothing changed; the read-only mode + empty-state render unchanged; a jest-axe smoke on the editable table.
- `mutations.test.tsx` / co-located: `useAnnotateImpact` PUTs the right URL+body and invalidates `["dcr-impact",id]` + `["dcr",id]`.
- `DcrDrawer.test.tsx` (extend): the Annotation column is editable for an Assessed DCR with `changeRequest.assess` granted; read-only when the permission is absent OR the state is terminal (Closed/Cancelled/Rejected) OR no impact rows.
- Conventions: `import { expect, it } from "vitest"`; MSW fixtures `satisfies DcrImpact`/`DcrImpactList`; per-test `server.use` overrides for the PUT; `waitFor`/`findBy` first assertion; the Mantine v7 `Textarea` `aria-label` per row (distinct). Run the full `/check-web` (`--pool=forks --poolOptions.forks.singleFork=true`) before the PR.
- Estimated delta: ~+8–12 web tests (842 → ~850–854).

## s5 · Live smoke (Chrome MCP, pre-merge; owner does the login)
- Reuse the S-dcr-ui-3 data (an Assessed/Implemented REVISE DCR exists with impact rows; `changeRequest.assess` granted to org-AHT). Open the DCR drawer → edit an Annotation cell → Save → confirm the value persists (re-open the drawer / the refetch shows it) and that only the changed dimension was PUT (`read_network_requests` on `/impact`). Confirm a terminal-state DCR shows the read-only column (no textareas).

## s6 · Out of scope (named, not faked)
- CREATE deep-link/implement (residual C — needs a backend `resulting_document_id` enrichment).
- Annotating before assess (no rows exist yet — the assess step auto-populates them; annotation is a post-assess refinement).
- Editing the `auto_populated` system facts (they're system-derived, never user-edited).
