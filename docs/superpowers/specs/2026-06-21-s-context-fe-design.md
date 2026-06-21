# S-context-fe — Context register **front-end** (clause 4.1) + the `can_release`/`can_manage` parity field — design

> The **last** slice in the Context register family (R50). The backend is complete end-to-end
> (S-context-1 core + lifecycle · S-context-2 `GET /context/summary` read consumer). This slice ships
> the **web SPA** the register has lacked, plus the **one carried backend residual**: server-computed
> `can_release`/`can_manage` capability booleans on `GET /context/register` **and** (parity)
> `GET /risks/register`. SPEC-FIRST per CLAUDE.md; the three forks were the owner's calls (§0). The
> risk family's FE spec (`docs/superpowers/specs/2026-06-20-s-risk-4-fe-design.md`) is the verbatim
> template — context drops the process axis + the graded-score axis (matrix/band), keeps the
> register-as-Document head lifecycle.

## 0 · Owner decisions (RESOLVED — ratified 2026-06-21 via AskUserQuestion ×3)

- **D-1 — The visualization analogue: a SWOT 2×2 board.** Context has no graded axis (no 5×5 matrix).
  The board groups the live rows by the SWOT `category` into four quadrants — **Strengths / Weaknesses**
  (Internal, top) and **Opportunities / Threats** (External, bottom); Helpful (S, O) left, Harmful
  (W, T) right — with issues rendered as clickable chips and a null-`category` **"Uncategorized"**
  overflow strip. The ISO-native, recognizable clause-4.1 framing (legibility is the feature, DP-3).
  *Rejected:* a classification × category count grid (the closest structural mirror of the risk matrix,
  but less legible); no-viz (a segmented table only — drops the at-a-glance payoff clause 6.1 got).
- **D-2 — `can_release`/`can_manage` for BOTH registers (parity).** Add the server-computed booleans to
  `GET /context/register` **and** `GET /risks/register` (the two `_register_status` serializers + the
  two openapi schemas are byte-identical twins → kept in lockstep via a shared helper). The faithful
  multi-axis release gate (a single-axis FE `/me/permissions` probe can't replicate
  `_register_release_scope` = artifact + folder_path + document_level + lifecycle_state + SoD-2 — the
  S-risk-5 Codex-r2 / S-context-1 docs-P2 finding). Closes the carried residual for **both**.
  *Rejected:* context-only (the twins drift; the risk residual lingers); FE-only single-axis probe
  (re-incurs the exact gap Codex flagged).
- **D-3 — One slice (the full SPA incl. the steward console).** Context's backend is fully ready
  (CRUD + lifecycle + summary) and the steward lifecycle endpoints already exist (unlike risk, which
  split row-surface S-risk-4b from steward-console S-risk-5 because S-risk-4b predated wanting the
  console). No reason to split. One PR: Part A (BE caps) + Part B (full FE).

## 1 · What the backend already pins (settled — the FE consumes, does not re-decide)

Contracts the FE reads (`apps/api/src/easysynq_api/api/context.py`, `domain/context/summary.py`):

- **`GET /context`** — filter-not-403 list (`{data: [ContextIssue]}`, newest-first). `register.read` @
  SYSTEM, all-or-nothing (a SYSTEM grant returns every row; a no-grant caller → `200`+empty). Each row
  = `_context_issue`: `{ id, register_doc_id, classification(internal|external),
  category(strength|weakness|opportunity|threat|null), status(active|closed), description,
  last_reviewed_at(ISO|null), row_version, created_at, updated_at }`.
- **`GET /context/{id}`** — `register.read` @ SYSTEM enforce (403-on-deny); the single-row serializer.
- **`POST /context`** (201) — `register.manage` @ SYSTEM. Body `ContextCreate`:
  `{ classification(req), description(req, 1..4000), category?(null), last_reviewed_at?(null) }` — **no
  `status`** (a new issue is always `active`). 409 unless the head is Draft/UnderRevision; lazily
  creates the CTX head on the first POST.
- **`PATCH /context/{id}`** — `register.manage` @ SYSTEM. Partial `ContextUpdate`
  `{ classification?, category?, status?, description?, last_reviewed_at? }` — **omitted ≠ null**; an
  explicit null clears `category`/`last_reviewed_at` (a null on `classification`/`status`/`description`
  → 422). 409 unless the head is editable.
- **`GET /context/register`** — the head lifecycle status (any authenticated org member):
  `{ exists, register_doc_id, identifier, state(7-state DocumentCurrentState|null),
  current_effective_version_id, has_governing }` + the new optional `can_release`/`can_manage` (§2). The
  `_NO_REGISTER` all-null/false default before the first issue.
- **`POST /context/register/{start-revision,publish,release}`** — start-revision/publish `register.manage`
  @ SYSTEM; release `document.release` + SoD-2 over `_register_release_scope` (multi-axis). All return
  the register status (the optional caps are GET-only — see §2). publish body `RegisterPublish
  { change_reason?(null, ≤2000) }`.
- **`GET /context/summary`** — `register.read` @ SYSTEM enforce (403-on-deny, **not** a per-row filter —
  context has no process scope). The GOVERNING (current Effective) frozen snapshot (read-of-record,
  never the live satellite): `{ published, total, by_classification{internal,external},
  by_category{strength,weakness,opportunity,threat,uncategorized}, by_status{active,closed}, active,
  never_reviewed }`. `published:false`+all-zero pre-first-release.

## 2 · Part A — `can_release`/`can_manage` on the two register GETs (BE)

The carried residual (S-context-1 docs-P2 / S-risk-5 Codex-r2). The faithful gate is a **server-computed
boolean** — a single-axis FE probe can't replicate the multi-axis release scope.

- **A shared helper** `services/authz/register_caps.py::register_capabilities(session, caller, *,
  release_scope: ResourceContext | None, source_ip) -> {"can_release": bool, "can_manage": bool}` —
  imported by **both** `api/context.py` and `api/risk.py` so the twins stay in lockstep:
  - `can_manage` = `authorize(gather_grants("register.manage"), "register.manage",
    ResourceContext.system(), ctx).allow` — the SYSTEM steward probe (independent of the head).
  - `can_release` = `release_scope is None → False`; else `authorize(gather_grants("document.release"),
    "document.release", release_scope, rel_ctx, sig_hook=True, sod=gather_sod_constraints(org)).allow` —
    over the **same** scope `release_register_endpoint` enforces. `rel_ctx` carries
    `allow_approver_release=get_allow_approver_release(org)` (else the SoD-2 approver block evaluates
    wrong) and `source_ip` (the CX-1 `ip_allow` parity). The `_document_capabilities` pattern verbatim;
    a capability probe writes **no** authz-audit row (pure `gather_grants` + `authorize`, never `enforce`).
- **Wired into only the 2 GET endpoints.** `GET /context/register` + `GET /risks/register` gain `request:
  Request`, compute `release_scope = _register_release_scope(head)` (the per-file helper, head-present)
  and merge the caps into both the head and `_NO_REGISTER` branches. The action endpoints
  (start-revision/publish/release) stay **byte-identical** — the caps are **optional, GET-only**; the FE
  invalidates+refetches `["context-register"]`/`["risk-register"]` after each mutation, so the gating
  always reads fresh caps off the GET.
- **No migration, no new key.** Contract: `can_release`/`can_manage` added as **optional** boolean
  properties on `ContextRegisterStatus` **and** `RiskRegisterStatus` (both `additionalProperties:false`).
- **BE tests (api integration):** for both registers — a no-`register.manage` caller → `can_manage:false`;
  a no-`document.release` caller → `can_release:false`; a SYSTEM-override steward → both `true` (the
  `_document_capabilities` coverage shape). `published`/`exists` branches unaffected.

## 3 · Part B — the web SPA (`apps/web/src/features/context/`)

All under a new `features/context/` dir, mirroring `features/risk/` (which mirrors `features/objectives/`).
New `/context` route + a LeftRail **PLAN** entry + a Home PLAN tile.

### 3.1 — Types (`lib/types.ts`, new `// ---- S-context-fe (Context register, clause 4.1) ----` block,
each pinned to the real `api/context.py` serializer via `satisfies` in fixtures):

```ts
export type ContextClassification = "internal" | "external";
export type ContextCategory = "strength" | "weakness" | "opportunity" | "threat";
export type ContextStatus = "active" | "closed";
export type ContextRegisterState = DocumentCurrentState;   // the CTX head is a kind=DOCUMENT subtype

export interface ContextIssue {                            // api/context.py _context_issue
  id: string; register_doc_id: string;
  classification: ContextClassification;
  category: ContextCategory | null;                        // nullable SWOT axis
  status: ContextStatus;
  description: string;
  last_reviewed_at: string | null;
  row_version: number;
  created_at: string | null; updated_at: string | null;
}
export interface ContextListResponse { data: ContextIssue[]; }
export interface ContextRegisterStatus {                   // _register_status / _NO_REGISTER
  exists: boolean; register_doc_id: string | null; identifier: string | null;
  state: ContextRegisterState | null; current_effective_version_id: string | null;
  has_governing: boolean;
  can_release?: boolean; can_manage?: boolean;             // GET-only optional caps (§2)
}
export interface ContextRegisterSummary {                  // GET /context/summary
  published: boolean; total: number;
  by_classification: Record<ContextClassification, number>;
  by_category: Record<ContextCategory | "uncategorized", number>;
  by_status: Record<ContextStatus, number>;
  active: number; never_reviewed: number;
}
export interface ContextCreateBody {                       // ContextCreate
  classification: ContextClassification; description: string;
  category?: ContextCategory | null; last_reviewed_at?: string | null;
}
export interface ContextUpdateBody {                       // ContextUpdate (partial; omitted ≠ null)
  classification?: ContextClassification; category?: ContextCategory | null;
  status?: ContextStatus; description?: string; last_reviewed_at?: string | null;
}
```
Also add the optional `can_release?`/`can_manage?` to the existing `RiskRegisterStatus`.

### 3.2 — Labels + the SWOT canon (`features/context/labels.ts` + `swot.ts`)

`labels.ts`: meaning labels + `Tone` maps (never colour words). `classification` (Internal ⌂ / External
◇ — the glyph carries the distinction; tone is `info`/`neutral` for grouping only); `status` (Active ●
`info` / Closed ○ `neutral`); `category` → label. `swot.ts` (↔ `matrix.ts`): `SWOT_QUADRANTS` — the four
category→quadrant defs (`category`, label, `classification` canon, helpful/harmful, `Tone`, glyph) in
display order, plus `bucketByCategory(rows)` → `{strength[], weakness[], opportunity[], threat[],
uncategorized[]}`. **No threshold table** (categorical, no graded axis). A small golden unit test pins
the quadrant layout (order + helpful/harmful axis + the canonical classification).

SWOT tones (follow the approved preview; soften harmful→`warning` only if red reads too alarming —
non-blocking): Strengths/Opportunities (helpful) → `success` ✓; Weaknesses/Threats (harmful) → `danger`
✕. Each quadrant + chip carries `StatusBadge` tone+glyph+label (DP-5 / WCAG 2.2 AA).

### 3.3 — `ContextSwotBoard` (↔ `RiskMatrix`, the centerpiece — Mantine cards, NOT SVG)

The risk matrix is a **count heatmap** (SVG `<rect>` density); a SWOT board is an **item board** (lists
issues), so it's a Mantine `SimpleGrid cols={2}` of `Paper withBorder` quadrants, not SVG (more
accessible — real focusable chip buttons + headings). Each quadrant: a heading (`StatusBadge` tone+glyph
+ "{Label}" + a count), then a `Stack` of issue chips (description `lineClamp={1}` + a classification
glyph + a status badge; an `Anchor component="button"` that opens the `?issue=` drawer via the page's
`setSelected`). Closed issues are de-emphasized (the status badge carries it — never colour alone). An
"Uncategorized" full-width `Paper` strip below for null-`category` rows. `role="group"`/`aria-label` per
quadrant ("Strengths, N issues") + a top-level `role="img"`/summary aria-label
("Context SWOT board; N issues across 4 categories"). Built with the **frontend-design skill** (calm,
legible, colour-safe, reduced-motion). Density from the **live `useContextIssues()` rows** (matches the
table the user sees). Distinct accessible names across quadrants/chips/badges (`getByLabelText` is
single-match).

### 3.4 — `ContextScorecardBand` (↔ `RiskScorecardBand`, client-side rollup)

A compact `<Paper>` of `StatusBadge` chips over the **orthogonal** axes the board doesn't emphasize:
classification split (internal/external), status split (active/closed), and a `never_reviewed` count
(computed from `last_reviewed_at == null`). Computed CLIENT-SIDE from the live working rows (the working
view) — distinct **by design** from `GET /context/summary` (the governing read-of-record the Home tile
consumes). Headline: "{active} of {total} active".

### 3.5 — `ContextRegisterPage` (↔ `RisksRegisterPage`)

- **Data:** `useContextIssues()` (filter-not-403, `forbidden` flag) + `useContextRegisterStatus()`. Early
  returns via the `lib/states` primitives (`LoadingState`/`ErrorState`+retry/`NoAccessState`).
- **`headState = status.data?.state ?? null`; `headEditable = headState === null || "Draft" ||
  "UnderRevision"`.**
- **The read-only / lifecycle banner** — `bannerFor(state)`: states the read-only fact + who reopens it
  (the steward console), **never** instructs an action the surface doesn't expose (the S-risk-4b Codex-P1
  copy lesson). Editable states → no banner.
- **"New issue" gating — `status.data?.can_manage ?? false` && headEditable.** Context is org-level — **no
  first-readable-process probe, no `requireProcess`** (the risk process idiom does NOT apply). The
  server's SYSTEM `register.manage` enforce on `POST /context` stays the true boundary.
- **The register-triage toolbar** (`RegisterToolbar` + `SortableTh` + `useDebouncedSearch`/`useTableSort`/
  `sortRows`/`useRowKeyboardNav`/`useUrlParam`) — search over `description`; URL-backed
  `SegmentedControl` filters: classification (All/Internal/External), category (All/S/W/O/T/Uncategorized),
  status (All/Active/Closed), each with a **distinct** `aria-label`. Sort keys: classification · category
  · status · last-reviewed (nulls last).
- **The table:** description (lineClamp 1) · classification (`StatusBadge`) · category (`StatusBadge`/—) ·
  status (`StatusBadge`) · last reviewed (date / "Never"). Row anchor opens the drawer via
  `setSelected(r.id)`.
- **`<AsOf at={dataUpdatedAt} />`** freshness clock.
- **The `?issue=` drawer URL-sync** (the CapaBoardPage/RisksRegisterPage idiom verbatim): `useState`
  seeded from `params.get("issue")`; a `useEffect` keyed on the param **ALONE** (follows removal,
  ignores other-param changes — Codex P3); `closeDrawer` deletes `?issue` `{replace:true}` only if
  present (local opens never touch the URL).
- **`<RegisterLifecyclePanel state={headState} canManage={status.data?.can_manage ?? false}
  canRelease={status.data?.can_release ?? false} />`** mounted unconditionally (self-suppresses for
  non-stewards).
- Conditional-mount `{createOpen && <NewIssueModal .../>}`; `<ContextIssueDrawer issueId={selected}
  onClose={closeDrawer} headEditable={headEditable} canManage={status.data?.can_manage ?? false} />`.

### 3.6 — `ContextIssueDrawer` (↔ `RiskDetailDrawer`, **NO CAPA seam**)

`{ issueId: string | null; onClose; headEditable; canManage }` — prop-driven, opens on `issueId !==
null`. `useContextIssue(issueId)` (enabled, `retry:false`, `forbidden`). Body: classification +
category + status badges, description, last-reviewed. **No process probe, no CAPA-spawn seam** (clause
4.1 has no treatment axis). Edit gated `canManage && headEditable` → `{editOpen && <EditIssueModal
.../>}`; when `!headEditable` a quiet read-only note (no dead button). Title shows the short-id only on
`issue && !isError`.

### 3.7 — `NewIssueModal` / `EditIssueModal` (↔ the risk modals, **NO process picker**)

`NewIssueModal`: classification (required `Select`) + description (`Textarea`, required) + category
(clearable `Select`, nullable SWOT) + last_reviewed_at (optional `DateInput`/date string). Submit gated
`description.trim() !== "" && classification`. `EditIssueModal` adds status (active/closed `Select`) +
the **partial-PATCH idiom** (`buildPatch` sends ONLY changed fields; an explicit null clears
`category`/`last_reviewed_at`; Save disabled until dirty). All `Select`s `comboboxProps={{keepMounted:
false}}`; conditionally mounted by the parent (close discards the draft).

### 3.8 — `RegisterLifecyclePanel` + `PublishRegisterModal` (↔ `features/risk` verbatim)

The steward console — `{ state, canManage, canRelease }`. Self-suppress `if (!canManage && !canRelease)
return null`. `editable = state === "Draft" || "UnderRevision"`. Acts surfaced STATE × permission (no
dead/disabled buttons): Publish (canManage && editable) → `PublishRegisterModal` (optional change-reason;
`onClose` guarded mid-publish); Start revision (canManage && Effective); Release (canRelease &&
Approved) → a `ConfirmDestructive` (stays open on a SoD-2 409); an InReview → "an approver decides in
Tasks" `Alert`. **canRelease/canManage come from the server booleans** (§2 — the faithful multi-axis
gate). The approve/decide step rides the existing `/tasks` DOCUMENT arm (zero new FE). Header:
`StateBadge` (or "Not started"). Never gate Publish on a client row count (a manage-without-read steward
sees 0 rows; the server empty-register 409 is the source of truth — the S-risk-5 Codex lesson).

### 3.9 — Hooks + mutations (`features/context/{hooks,mutations}.ts`)

`useContextIssues()` → `["context"]`, unwraps `.data`, `retry:false`, `forbidden`. `useContextIssue(id)`
→ `["context", id]`, `enabled`. `useContextSummary()` → `["context-summary"]`, `forbidden` (403-on-deny).
`useContextRegisterStatus()` → `["context-register"]` (no forbidden — any member). Mutations:
`useCreateIssue`/`useUpdateIssue(id)` + `useStart/Publish/ReleaseContextRegister` — each invalidates the
relevant keys (create → `["context"]`,`["context-register"]`,`["context-summary"]`; the lifecycle acts
invalidate `["context-register"]`,`["context"]`,`["context-summary"]`).

### 3.10 — Home PLAN line (`features/home/PlanCard.tsx`) + `useHomeAsOf`

Add a context line from `useContextSummary()` (the GOVERNING read-of-record): when `published`, an
**info** `StatLine` "{active} active context issues"; **and** a `warning` `StatLine` "{never_reviewed}
context issues never reviewed" only when `never_reviewed > 0` (the actionable freshness signal — drives
an amber RAG via `countRag(never_reviewed, "amber")`). When `!published` → a neutral "no published
context register yet". Fold `ctx.forbidden` into `allForbidden` (the tile shows `TileNoAccess` only when
EVERY actionable read is forbidden). Add `useContextSummary()` to `useHomeAsOf` (shares the
`["context-summary"]` cache key → no extra fetch).

### 3.11 — Route + nav + the risk-FE parity migration

- `App.tsx`: `import { ContextRegisterPage }` + `<Route path="context" element={<ContextRegisterPage />}
  />` (after `risks`). A single list route — the drawer is `?issue=`.
- `LeftRail.tsx`: a PLAN entry `{ to: "/context", label: "Context", prefix: "/context" }` **UNGATED**
  (the filter-not-403 / risk-CAPA precedent — a no-grant caller gets a calm-empty register, never a
  hidden link).
- **Risk-FE parity migration (minimal):** `RisksRegisterPage` drops the single-axis `releasePerms`
  ARTIFACT probe → `canRelease = status.data?.can_release ?? false` (the faithful multi-axis gate now on
  the server). The New-button gate (process-OR-SYSTEM `register.manage`) is **untouched**. Update
  `riskRegisterStatusFixture` (+ the lifecycle-action returns) with `can_release`/`can_manage` and the
  2–3 risk tests that drove release via the artifact probe to drive it via the fixture.

## 4 · Authz & gating discipline (the load-bearing rules — Codex WILL probe these)

- **Every context gate is SYSTEM-scope** (org-level). The "New issue" + edit affordances gate on
  `status.data.can_manage` (server-computed `register.manage` @ SYSTEM); release on `status.data.can_release`
  (server-computed multi-axis). **No process probe, no process-count, no `requireProcess`** (the risk
  idioms do NOT apply).
- **`GET /context` is filter-not-403** → a no-grant caller renders the page with an empty register (calm),
  never a crash. The LeftRail entry is UNGATED.
- **`GET /context/summary` is org-level `register.read` (SYSTEM), 403-on-deny** — the Home tile degrades
  calmly (`forbidden` → no context line). Distinct **by design** from the page's client-side working
  rollup (read-of-record posture).
- **The FE never re-grades** — context has no graded axis; the SWOT board buckets by the server
  `category` verbatim; status/classification render the server values via `StatusBadge` tone+glyph+label.

## 5 · Test traps (the carried web false-PASS traps — `.claude/rules` "Web SPA testing")

- **`import { expect, it } from "vitest"`** in every test file using a jest-dom matcher (the bare global
  `expect` is jest-typed → a `tsc`-only failure invisible to a per-file vitest pass).
- **A required Mantine `Select`'s label gets an asterisk → `getByLabelText("Classification")` exact
  FAILS** → query by placeholder; the "New issue" open button vs the modal submit get distinct accessible
  names. The three filter `SegmentedControl`s get **distinct** `aria-label`s.
- **Pin every MSW fixture via `satisfies <Type>`** to the real `api/context.py` serializer / the
  `summarize_register` shape — incl. the `_NO_REGISTER` branch, null-`category` + null-`last_reviewed_at`
  rows, and the `by_category` `uncategorized` bucket (5 keys, not 4). Register `/context/summary` and
  `/context/register` handlers **before** `/context/:id` (the route-order pin).
- **No persistently-mounted modal** — conditional-mount `{createOpen && …}` / `{editOpen && …}`; the
  drawer is prop-driven (`issueId !== null`).
- **The global `scrollIntoView`/`matchMedia`/`ResizeObserver` stubs** in `test/setup.ts` cover the
  `Select`s. **MSW `onUnhandledRequest:"error"`** — every `/context*` route the SPA hits needs a handler.
- **Distinct accessible names** across the SWOT quadrants, the scorecard chips, the table badges, and any
  in-drawer badge while mounted.
- **Run the FULL `/check-web`** (eslint + strict `tsc --noEmit` [`noUncheckedIndexedAccess`] + build + the
  whole vitest suite) before the PR — the per-file vitest run misses the `tsc`-only + cross-file traps.

## 6 · Slice plan

One slice, one PR (`feat/s-context-fe`):
1. **Part A — the caps** (BE): the shared `register_caps.py` helper + the 2 GET endpoints + the openapi
   twins + the api integration tests. `/check-api` + `/check-contracts`.
2. **Part B — the SPA** (FE): types + labels + `swot.ts` (+ golden); `ContextRegisterPage` +
   `ContextSwotBoard` + `ContextScorecardBand` + `ContextIssueDrawer` + `NewIssueModal`/`EditIssueModal`
   + `RegisterLifecyclePanel`/`PublishRegisterModal`; the Home PLAN line + `useHomeAsOf`; hooks/mutations;
   the route + LeftRail entry; the risk-FE parity migration; MSW fixtures + component tests. `/check-web`.
3. **Review:** diff-critic + web-test-trap-reviewer + a 3-lens adversarial Workflow (FE-gating/deny-wins ·
   a11y/colour-safe-RAG · read-of-record working-vs-governing) + Codex (trim-don't-chase). Squash-merge on
   green CI + threads resolved + owner go. `/finish-slice` + a `docs(s-context-fe)` follow-up PR.

## 7 · Rejected alternatives & named residuals

- **Rejected — the page scorecard uses `GET /context/summary`.** The page is the working view; its
  scorecard rolls up live rows. The summary endpoint is the governing controlled read for Home (§3.4).
- **Rejected — an SVG SWOT heatmap.** The board lists issues (an item board), so Mantine cards are more
  legible + accessible than forcing the matrix SVG idiom.
- **Rejected — a `/context/:id` detail page.** A `?issue=` drawer (the family's row-list shape).
- **Rejected — wiring the MR 9.3.2(b) context-change input.** It STAYS a sourceless gap
  (`CONTEXT_CHANGES` in `compile.py::_SOURCELESS_GAPS`) → **S-interested-parties-1** (both the 4.1 + 4.2
  halves together, per the owner's S-context-2 D-2).
- **Named residuals (not faked):** S-interested-parties-1 (clause 4.2, a separate register); a per-issue
  clause picker (parity with the risk clause-step residual); a SWOT board click-to-filter interaction
  (read-only in v1).
