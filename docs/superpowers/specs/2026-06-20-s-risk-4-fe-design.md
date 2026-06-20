# S-risk-4 — Risk & Opportunity register **front-end** + the high-risk read endpoint (clause 6.1) — design

> The **last** slice in the Risk & Opportunity register family (R49). The backend is complete
> end-to-end (S-risk-1 core · S-risk-1b lifecycle · S-risk-2 MR read-consumer · S-risk-3 CAPA-spawn).
> This slice ships the **web SPA** the register has lacked, plus the **one deferred backend piece**:
> the doc-13/Home "high-risk" read endpoint (`GET /risks/summary`). SPEC-FIRST per CLAUDE.md; the three
> architectural forks were the owner's calls (§0). Parent family spec:
> `docs/superpowers/specs/2026-06-19-s-risk-register-design.md` (§9 is the FE surface this expands).

## 0 · Owner decisions (RESOLVED — ratified 2026-06-20 via AskUserQuestion ×3)

- **F-1 — Scope: row surface only; defer the steward lifecycle UI.** **Ratified.** S-risk-4 ships the
  **row surface** (view / create / edit / treat), the **5×5 matrix**, the **`RiskDetailDrawer`**, the
  **Home PLAN card**, and the **`GET /risks/summary`** endpoint. The FE **reads `GET /risks/register`**
  to gate the "New risk" / edit affordances on the head being **editable** (Draft/UnderRevision) and
  shows a calm **read-only banner** ("This register is Effective — start a revision to edit risks.")
  when the head is Effective. The **steward publish / start-revision / release UI** stays the
  already-NAMED *register-steward role/UI* deferral — it rides SYSTEM overrides in v1 and is a distinct
  privileged-actor surface (the backend `/risks/register/{start-revision,publish,release}` endpoints
  already exist; no FE buttons this slice).
- **F-2 — A dedicated `GET /risks/summary` endpoint** returning `summarize_register` over the
  **GOVERNING** (Effective) frozen snapshot — `{published, total, by_band, high_risk, by_type,
  effectiveness}`. Rides the seeded **`register.read`** at org level (SYSTEM-gated, like the S-risk-2 MR
  consumer); reads the **read-of-record** (governing), never the live satellite; **no migration, no new
  key** (catalog stays 102); openapi in-PR. Mirrors the objectives `scorecard` read the `PlanCard`
  already consumes — the clean doc-13/Home seam. *Rejected:* folding `high_risk` into
  `GET /risks/register` (conflates lifecycle state with a content rollup; doesn't mirror the scorecard
  read).
- **F-3 — Split the slice** into **S-risk-4a** (the `GET /risks/summary` endpoint — BE, openapi + api
  integration tests, its own small PR) then **S-risk-4b** (the SPA — register page, matrix, drawer, Home
  card, route, rail entry — consuming the now-landed endpoint with MSW fixtures pinned to the real
  serializer). Each PR is reviewable; the endpoint lands tested before the FE depends on it.

## 1 · What the backend already pins (settled — the FE consumes, does not re-decide)

The FE is a thin, gated view over an already-complete, already-adversarially-reviewed backend. The
contracts it reads (`apps/api/src/easysynq_api/api/risk.py`, `domain/risk/{rules,summary}.py`,
`services/risk/queries.py`):

- **`GET /risks`** — filter-not-403 list; each row is the `_risk` serializer (api/risk.py:148):
  `{ id, register_doc_id, type, description, process_id, clause_id, likelihood, severity, risk_rating,
  scoring_method, band, band_tone, band_rank, treatment, effectiveness, linked_capa_id, row_version,
  created_at, updated_at }`. `risk_rating` is server-derived (read-only). `band` ∈ `{critical, high,
  medium, low, unscored}`; `band_tone` ∈ `{danger, warning, success, neutral}`; `band_rank` is the
  danger-first sort rank (`critical:0, high:1, medium:2, low:3, unscored:4`) — all graded against the
  **governing** version's frozen criteria server-side. **The FE never re-grades a row's band** — it
  renders the server `band`/`band_tone`/`band_rank` verbatim (R49 L2).
- **`GET /risks/{id}`** — scoped-`require` enforce (403-on-deny); the single-row serializer.
- **`POST /risks`** — `register.manage` enforced over the body `process_id` (SYSTEM for a null process).
  Body: `RiskCreate` `{ type, description, likelihood(1..5), severity(1..5), scoring_method?,
  process_id?, clause_id?, treatment? }`. **409 unless the head is Draft/UnderRevision** (the working
  register is open). Lazily creates the RSK head on the first POST.
- **`PATCH /risks/{id}`** — `register.manage` at the row's process; re-enforces the NEW target on a
  `process_id` reassign. Partial body `RiskUpdate` (omitted ≠ null; `scoring_method` write-once;
  `effectiveness` settable). `likelihood`/`severity` edits re-derive `risk_rating` server-side. **409
  unless the head is editable.**
- **`POST /risks/{risk_id}/capa`** — the one-click idempotent treat-spawn (S-risk-3). **201** new /
  **200** replay; returns the spawned **CAPA** (the `_capa_full` shape — see §3.6). Gated `capa.create`
  at the risk's OWN process scope. **Only `type=risk` rows** (422 on an `opportunity`). Latches
  `linked_capa_id`. **Works at ANY head state** — `linked_capa_id` is operational metadata, decoupled
  from the publish freeze (S-risk-3 owner fork). The spawn is **NOT** gated on head editability.
- **`GET /risks/register`** — the head lifecycle status: `{ exists, register_doc_id, identifier, state,
  current_effective_version_id, has_governing }` (or the `_NO_REGISTER` all-null/false default before
  the first risk). `state` is the 7-state `DocumentCurrentState`. Any authenticated org member may read
  it (lifecycle state is org-level; row contents gated separately by `GET /risks`).
- **`summarize_register(governing)`** (`domain/risk/summary.py`, pure) → `{ total, by_band(every
  RiskBand value keyed), high_risk(critical+high), by_type{risk,opportunity}, effectiveness{treated,
  recorded, pending} }`. `governing_register(session, org_id)` resolves the head's current Effective
  version's frozen `{rows, criteria}` snapshot, or `None` pre-first-release. **This is the seam
  S-risk-4a surfaces.**

## 2 · S-risk-4a — the `GET /risks/summary` endpoint (BE)

**The doc-13/Home high-risk read, the controlled read-of-record.** A new route in `api/risk.py`,
mounted **with the other `/risks/register*` static routes BEFORE `/risks/{risk_id}`** (the S-pack-2
ordering invariant — `/risks/summary` would otherwise match `{risk_id}` and 422 on the UUID parse).

```
GET /api/v1/risks/summary        (register.read, org-level / SYSTEM)
→ 200
{
  "published": true,                       // false pre-first-release (governing is None)
  "total": 12,
  "by_band":  {"critical":1,"high":2,"medium":4,"low":5,"unscored":0},
  "high_risk": 3,                          // danger-tone = High ∪ Critical (doc-13 set)
  "by_type":  {"risk":9,"opportunity":3},
  "effectiveness": {"treated":5,"recorded":3,"pending":2}
}
```

- **Authority — `register.read` at org level (SYSTEM), the S-risk-2 MR-consumer posture.** Use the
  bare `require("register.read")` dependency (default SYSTEM scope) — **NOT** a per-row filter. *Why
  org-level, not per-process filter:* this is a cross-surface controlled summary (the same read the MR
  input-(e) compiles), org-wide by design (the F3 MR-summary posture); a SYSTEM `register.read` matches
  (QMS Owner / Internal Auditor). A no-grant caller gets **403** (an enforced single read, not a
  filtered list — the `GET /risks/{id}` posture). This is the **already-NAMED cross-cutting deferral**
  (should MR/dashboard sourced summaries honor per-process denies across ALL inputs) — **do not
  re-open it here**; keep it org-wide for consistency with every other sourced summary.
- **Read-of-record = the GOVERNING snapshot, never the live satellite.** `governing = await
  governing_register(session, caller.org_id)`. When `governing is None` (no published register yet) →
  return `{ "published": False, ...all-zero summarize_register({"rows": [], "criteria": …}) }` — i.e.
  the helper's empty-register zeros wrapped with `published: False`. When non-`None` → `{ "published":
  True, **summarize_register(governing) }`. **The live working satellite (UnderRevision edits) is never
  read here** — exactly the MR controlled-consumer discipline (S-risk-2). During UnderRevision this
  returns the PRIOR Effective counts (the governing pointer only moves at the next cutover).
- **`published` is the new wrapper key** (the only thing the endpoint adds over `summarize_register`).
  Keep every leaf a JSON primitive (the `summarize_register` contract is already `rfc8785`-safe; the
  bool wrapper is too).
- **No migration, no new key, no model change.** Pure read; reuses `governing_register` +
  `summarize_register`. Contract: a new `GET /risks/summary` path + a `RiskRegisterSummary` schema
  component (`additionalProperties: false`), `tags: [risk]`, in-PR (redocly-lint only).
- **Tests (api integration, CI-authoritative on this Windows box).** Mirror `test_risk_lifecycle.py` /
  `test_risk_capa.py` scaffolding (`_drive_to_editable` → `_create_risk` → publish → `_approve_and_release`):
  1. **pre-first-release → `published:false` + all-zeros** (no published register; the `None` governing
     branch). Use the `restore_register_head` teardown + normalize-at-start discipline (the shared
     per-org singleton head).
  2. **after publish+release → `published:true`** with the governing counts (a `L=4,S=5`→rating 20 →
     `critical`, `high_risk≥1`); assert `by_band`/`high_risk`/`by_type`/`effectiveness`.
  3. **a no-grant caller → 403** (the enforced-read boundary; not a filtered 200-empty).
  4. **read-of-record: an UnderRevision edit does NOT change the summary** — release v1, `start-revision`,
     edit a row's band-moving score, assert `GET /risks/summary` still reflects the **governing** (v1)
     counts (the working edit is invisible until re-published). The strongest read-of-record proof.

## 3 · S-risk-4b — the web SPA surface

All under `apps/web/src/features/risk/` (new feature dir), mirroring `features/objectives/`. New
`/risks` route + a LeftRail **PLAN** entry + Home PLAN tile.

### 3.1 — Types (`lib/types.ts`, a new `// ---- S-risk-4 (Risk & Opportunity register, clause 6.1) ----`
banner at the end), each pinned to the real serializer (`satisfies` in fixtures):

```ts
export type RiskType = "risk" | "opportunity";
export type RiskBand = "critical" | "high" | "medium" | "low" | "unscored";
export type RiskScoringMethod = "5x5_matrix";
export type RiskRegisterState = DocumentCurrentState;     // the RSK head is a kind=DOCUMENT subtype

export interface RiskRow {                                 // api/risk.py _risk(...)
  id: string; register_doc_id: string;
  type: RiskType; description: string;
  process_id: string | null; clause_id: string | null;
  likelihood: number; severity: number; risk_rating: number;
  scoring_method: RiskScoringMethod;
  band: RiskBand; band_tone: Tone; band_rank: number;      // server-graded (governing criteria)
  treatment: string | null; effectiveness: string | null;
  linked_capa_id: string | null; row_version: number;
  created_at: string | null; updated_at: string | null;
}
export interface RiskListResponse { data: RiskRow[]; }
export interface RiskRegisterStatus {                      // api/risk.py _register_status / _NO_REGISTER
  exists: boolean; register_doc_id: string | null; identifier: string | null;
  state: RiskRegisterState | null; current_effective_version_id: string | null;
  has_governing: boolean;
}
export interface RiskSummary {                             // GET /risks/summary
  published: boolean; total: number;
  by_band: Record<RiskBand, number>;
  high_risk: number;
  by_type: Record<RiskType, number>;
  effectiveness: { treated: number; recorded: number; pending: number };
}
export interface RiskCreateBody {                          // RiskCreate (api/risk.py)
  type: RiskType; description: string; likelihood: number; severity: number;
  scoring_method?: RiskScoringMethod; process_id?: string; clause_id?: string; treatment?: string;
}
export interface RiskUpdateBody {                          // RiskUpdate (partial; omitted ≠ null)
  type?: RiskType; description?: string; likelihood?: number; severity?: number;
  process_id?: string | null; clause_id?: string | null; treatment?: string | null;
  effectiveness?: string | null;
}
```
Plus **extend `CapaSource`** to include `"risk"` (the backend added it in migration 0059; the spawn
returns a `source:"risk"` CAPA and the fixture must `satisfies Capa`).

### 3.2 — Labels + the band canon (`features/risk/labels.ts`), reusing the objectives glyph canon:

```ts
export const RISK_BAND_LABEL: Record<RiskBand, string> = {
  critical: "Critical", high: "High", medium: "Medium", low: "Low",
  unscored: "Not yet measured",
};
// Mirrors the server BAND_TONE (domain/risk/rules.py) — used for FE-computed matrix cells. Rows
// themselves carry server `band_tone` verbatim (no FE re-grade); this map agrees with it.
export const RISK_BAND_TONE: Record<RiskBand, Tone> = {
  critical: "danger", high: "danger", medium: "warning", low: "success", unscored: "neutral",
};
export const RISK_TYPE_LABEL: Record<RiskType, string> = { risk: "Risk", opportunity: "Opportunity" };
```
Status is carried by **`StatusBadge` tone + glyph + label, never colour alone** (DP-5 / WCAG 2.2 AA):
each row's band → `<StatusBadge tone={row.band_tone} label={RISK_BAND_LABEL[row.band]} kind="Risk" />`.
The danger-tone glyph is `✕`, warning `◔`, success `✓`, neutral `○` (the `TONE_GLYPH` canon). Sort the
status column by **`band_rank`** (danger-first; the server's `RAG_SEVERITY` precedent), `unscored` last.

### 3.3 — `RisksRegisterPage` (mirrors `ObjectivesRegisterPage.tsx`)

- **Data:** `useRisks()` (GET /risks — live satellite, per-process filtered, `forbidden` flag) +
  `useRiskRegisterStatus()` (GET /risks/register — head state). Loading/error/forbidden via the
  `lib/states` primitives (`LoadingState` / `ErrorState`+retry / `NoAccessState`).
- **Scorecard band rollup — computed CLIENT-SIDE from the live rows the page shows** (NOT the summary
  endpoint). *Why client-side, not `GET /risks/summary`:* the page's scorecard must agree with its own
  table, and both must respect the per-process row-filter (a bound owner sees only their rows → their
  scorecard). The page is the **working view** (live satellite, what the steward edits); the summary
  endpoint is the **controlled read** (governing) for Home/MR/doc-13 — they differ **by design** (the
  read-of-record posture). Roll up `by_band` from `row.band` + `high_risk = critical+high`. A small
  `RiskScorecardBand` component (mirrors `ObjectiveScorecardBand`): one `StatusBadge` chip per band
  ("{n} {label-lowercased}"), plus an emphasis "{high} high / critical" headline.
- **The read-only / lifecycle banner.** From `useRiskRegisterStatus()`: when `state === "Effective"`,
  a calm `InlineState`/`Alert` "This register is Effective (read-only). Start a revision to edit
  risks." (no steward buttons — F-1). When `state` is Draft/UnderRevision, the register is editable (the
  "New risk" + edit affordances are live). When `!exists` (no register yet), the create flow is the
  bootstrap (first POST lazily creates the head).
- **`headEditable = status?.state === "Draft" || status?.state === "UnderRevision" || !status?.exists`.**
  Gates the "New risk" button and the drawer's edit affordances.
- **"New risk" button gating — `register.manage` at PROCESS scope, the CapaBoardPage first-readable-
  process probe**, AND `headEditable`:
  ```ts
  const perms = usePermissions();
  const { data: readableProcesses } = useProcesses();
  const firstProcessId = readableProcesses?.[0]?.id;
  const processPerms = usePermissions(firstProcessId ? { level: "PROCESS", id: firstProcessId } : undefined);
  const systemCanManage = perms.can("register.manage");
  const canCreateRisk = systemCanManage || (!!firstProcessId && processPerms.can("register.manage"));
  // Button shown iff: headEditable && canCreateRisk
  ```
  **Gate on `register.manage`, never on process-count** — an Internal Auditor holds `register.read` +
  `process.read` (a non-empty process list) but no `register.manage`; gating on count would leak the
  button (the S-capa-raise-process MAJOR). The server's PROCESS-scoped `POST /risks` enforce stays the
  true boundary.
- **The register-triage toolbar** (`RegisterToolbar` + `SortableTh` + `useDebouncedSearch` /
  `useTableSort` / `sortRows` / `useRowKeyboardNav` / `useUrlParam`), exactly the objectives wiring:
  - search over `description` (+ identifier if shown);
  - sort keys `["ref"/risk_rating, "type", "rating", "band", "treated"]` — `band` sorts by `band_rank`
    (danger-first, `unscored` → null → last); `rating` sorts numeric `risk_rating`;
  - URL-backed filters: a band `SegmentedControl` (All / Critical / High / Medium / Low) **and** a type
    `SegmentedControl` (All / Risks / Opportunities) in the toolbar `children` (distinct `aria-label`s).
- **The table** (StatusBadge-per-band; the row opens the drawer locally via `setSelected(r.id)`):
  columns ref/short-id · description (lineClamp 1) · type · likelihood×severity (e.g. "4 × 5 = 20") ·
  **Band** (`StatusBadge`) · treatment indicator (a `✓` treated / dimmed "—" untreated) · the linked
  CAPA marker when `linked_capa_id`.
- **`<AsOf at={dataUpdatedAt} />`** freshness clock (the register-page convention).

### 3.4 — The 5×5 matrix as hand-rolled SVG (D4 — the `ObjectiveTrendChart`/`BandPreview` precedent)

A `RiskMatrix` component: a 5×5 grid of `<rect>`s, **Likelihood on the X-axis (1→5 left→right),
Severity on the Y-axis (1→5 bottom→top)**, so the top-right cell (L5×S5=25) is Critical and bottom-left
(1×1=1) is Low — the standard heatmap orientation.

- **Cell band — FE-computed from a v1-mirrored threshold table** (`bandForCell(likelihood, severity)`
  in `features/risk/matrix.ts`, mirroring `default_criteria`: critical≥20, high≥12, medium≥6, low≥1).
  *Why a FE mirror is safe for v1:* there is exactly ONE `scoring_method` (`5x5_matrix`), golden-pinned
  server-side; the matrix is a **reference heatmap of the band structure**, not a per-row verdict (rows
  carry their server-graded band). **A FE unit test pins `bandForCell` to the same thresholds** (mirrors
  the backend golden test). **Named residual:** if a future `scoring_method` ever mints new thresholds,
  the matrix must read criteria from the server — out of v1 scope.
- **Each cell:** filled with the band's tone colour (via `RISK_BAND_TONE` → the `--mantine-color-*`
  fills, the `ObjectiveTrendChart` `RAG_FILL` idiom), carries the band's **glyph** (`TONE_GLYPH`) in a
  dark inset chip (the `BandPreview` non-colour channel — so the matrix reads in greyscale), a count of
  the org's rows at that (L,S), and a `<title>` ("Likelihood 4 × Severity 5 = 20 — High · 2 risks"
  — `<title>` first child of its `<g>`, the Codex-P3 SVG-tooltip rule). The **selected** row's cell
  (drawer open via `?risk=`) gets a highlight ring.
- **`role="img"` + an aria-label summary** ("Risk matrix, 5×5; N risks plotted; M high or critical").
  Axis tick labels 1–5 on both axes + axis titles "Likelihood" / "Severity". Density (counts) computed
  from the **live `useRisks()` rows** (so it matches the table the user sees).

### 3.5 — `RiskDetailDrawer` (the `CapaDrawer` / `InitiativeDrawer` precedent; `?risk=` URL param)

- **Container:** the shared `app/shell/DetailDrawer` (right Mantine Drawer); prop-driven `riskId` /
  `onClose`, `opened={riskId !== null}`. URL wiring lives in `RisksRegisterPage` (the CapaBoardPage
  idiom verbatim): seed `selected` from `params.get("risk")`, a `useEffect([params])` re-opens on a
  deep-link, `closeDrawer` deletes `?risk` with `{ replace: true }` **only if present** (local opens
  never touch the URL). Title shows identifier/short-id **only on `risk && !isError`** (no stale id over
  an error body — the drawer title-gating idiom).
- **Data:** `useRisk(riskId)` (GET /risks/{id}, `enabled`, `retry:false`, `forbidden`).
- **Body sections:**
  1. **Header** — type chip + the band `StatusBadge` + the description.
  2. **Score** — likelihood × severity = risk_rating, the band. A mini single-cell matrix marker
     (reuse `RiskMatrix` highlighting this row's cell, or a compact band readout).
  3. **Treatment** — the `treatment` text + `effectiveness` text.
  4. **The risk → CAPA spawn seam** (the operational treat action):
     - **Shown only for `type === "risk"`** (an `opportunity` row has no spawn affordance — the server
       422s it; mirror that in the UI).
     - **When `linked_capa_id` is set** → render the linked CAPA reference + an `Anchor` to
       `/capa?capa={linked_capa_id}` (deep-links the CAPA board drawer). No spawn button (already
       treated; a replay is idempotent but the UI shows the link, not a re-spawn).
     - **When unlinked** → a one-click **"Treat → spawn CAPA"** button (no form; severity is
       band-derived server-side). On success (`useSpawnRiskCapa`), invalidate + show the new linked CAPA.
     - **Gating — `capa.create` at THIS row's OWN process scope** (not the first-readable-process
       heuristic — the drawer has the row, so probe exactly): `usePermissions(row.process_id ? {level:
       "PROCESS", id: row.process_id} : undefined)` ‖ `perms.can("capa.create")` at SYSTEM (org-level
       row). **Keyed on `capa.create`, never process-count.** **NOT gated on `headEditable`** — the
       spawn is operational and works at any head state (S-risk-3).
  5. **Edit affordances — gated on `register.manage` at the row's process AND `headEditable`** (the
     drawer receives `headEditable` from the page). When editable: an "Edit risk" button → an
     `EditRiskModal` (or inline fields) PATCHing score/type/description/treatment/effectiveness/process.
     When `!headEditable`: the row is read-only (the page banner explains why); **no dead buttons**
     (quiet absence — the ObjectiveDetailPage idiom).
- **No server `capabilities` block on a risk row** (unlike Objective/CAPA) — so the drawer gates on the
  `usePermissions` probe (`register.manage` / `capa.create` at the row's process) AND the head state
  (`GET /risks/register`), **not** a per-entity capability flag. Documented so a reviewer doesn't expect
  `row.capabilities`.

### 3.6 — The spawn response shape (for the MSW fixture / type)

`POST /risks/{id}/capa` returns `_capa_full(...)` → the `Capa` shape **without `stages`**:
`{ id, identifier, title, source:"risk", severity, process_id, close_state:"Raised", cycle_marker:0,
origin_finding_id:null, raised_by:null, created_at }`. The web `Capa` interface already matches
(`stages?` optional); the only delta is `CapaSource` must include `"risk"` (§3.1).

### 3.7 — Home PLAN card (`features/home/PlanCard.tsx` — add a Risk line) + the tile

The `PlanCard` today composes objectives-on-target + overdue-reviews. **Add a third line:** the
high-risk count from **`useRiskSummary()`** (GET /risks/summary — the governing, org-level controlled
read).

- `const rk = useRiskSummary();` — `forbidden`/`isError`/`data` like the sibling reads.
- When loaded: `const rag = rk.data.high_risk > 0 ? "red" : "green";` (a high/critical risk is an
  action signal — `countRag(rk.data.high_risk, "red")`); push a `<StatLine value={rk.data.high_risk}
  label="high / critical risks" tone={rag} />`. When `!rk.data.published` → a neutral line ("no
  published register yet", tone `neutral`) so a brand-new working register doesn't read a misleading
  "0 high-risk".
- **`allForbidden`** folds in the risk read: `sc.forbidden && cl.forbidden && rk.forbidden` (the tile
  shows `TileNoAccess` only when EVERY actionable read is forbidden — a `register.read`-less Employee
  still sees the objectives/reviews lines). Worst-RAG rolls the risk RAG into the headline.
- **`useHomeAsOf`** — add `useRiskSummary()` to the freshness-stamp read set (it shares the
  `["risks-summary"]` cache key with the tile → no extra fetch; an errored/forbidden read contributes no
  stamp).

> *Owner-confirmable micro-decision (non-blocking):* the Risk signal lives **inside the existing PLAN
> `QuadrantCard`** as a third `StatLine` (clause 6.1 is PLAN, alongside objectives clause 6.2), rather
> than a new quadrant — the four-quadrant PDCA wheel is fixed (doc 11 §5.1). The card's `openTo` stays
> `/objectives`; the Risk line is informational. (A dedicated "Open risk register →" is reachable via
> the LeftRail PLAN entry + the matrix on the register page.)

### 3.8 — Hooks + mutations (`features/risk/{hooks,mutations}.ts`)

- `useRisks()` → `["risks"]`, `retry:false`, `forbidden`. `useRisk(id)` → `["risk", id]`, `enabled`,
  `retry:false`, `forbidden`. `useRiskSummary()` → `["risks-summary"]`, `retry:false`, `forbidden`.
  `useRiskRegisterStatus()` → `["risk-register"]`, `retry:false` (no forbidden — any member may read).
- `useCreateRisk()` → `POST /risks` → invalidate `["risks"]` + `["risk-register"]` (the head may have
  been lazily created) + `["risks-summary"]` (harmless; governing unchanged until publish).
- `useUpdateRisk(id)` → `PATCH /risks/{id}` → invalidate `["risk", id]` + `["risks"]`.
- `useSpawnRiskCapa(id)` → `POST /risks/{id}/capa` → invalidate `["risk", id]` + `["risks"]` +
  `["capas"]` (the new CAPA appears on the board). Server-idempotent (201/200 both land in `onSuccess`).
- `useProcesses` is reused from `features/objectives/hooks` (the readable-process source for the picker).

### 3.9 — Route + nav (`App.tsx`, `LeftRail.tsx`)

- `App.tsx`: import `RisksRegisterPage`, add `<Route path="risks" element={<RisksRegisterPage />} />`
  inside the `/` AppShell block (after `improvement`). A single list route — the drawer is `?risk=`
  query-param, no `/risks/:id` route (the improvement `?initiative=` precedent).
- `LeftRail.tsx`: add a PLAN entry `{ to: "/risks", label: "Risk register", prefix: "/risks", gate:
  "register.read" }` to `NAV.PLAN` (alongside Objectives). Gated on `register.read` (the calm-403 still
  lives on the page for the unconditional case).

## 4 · Authz & gating discipline (the load-bearing rules — Codex WILL probe these)

- **The "New risk" + edit buttons gate on `register.manage` at PROCESS scope** (first-readable-process
  probe for "New"; the row's own `process_id` for the drawer edit), **never on process-count**. The
  spawn button gates on **`capa.create`** at the row's process, never on count. (S-capa-raise-process.)
- **`GET /risks` is already filter-not-403** → a bound Process-Owner renders the page with only their
  rows; a no-grant caller gets `200`+empty (calm empty register, not a crash). The page's `forbidden`
  flag is for a 403 on the *register-status* / an unexpected error, not the list (which never 403s).
- **The spawn button is NOT gated on `headEditable`** (operational, any head state); **all content
  edits ARE** (`POST`/`PATCH` 409 when the head is Effective). The read-only banner explains it.
- **`GET /risks/summary` is org-level register.read (SYSTEM)** — 403 for a no-grant caller; the Home
  tile degrades calmly (`forbidden` → no Risk line, never a crash). Do **not** per-process-filter it
  (the named cross-cutting deferral; consistency with the MR consumer).
- **The FE never re-grades a band** — rows render the server `band`/`band_tone`/`band_rank`; only the
  FE-computed **matrix reference cells** use a v1-mirrored threshold table (unit-pinned), which is a
  display of the band structure, not a verdict on a row.

## 5 · Test traps (the carried web false-PASS traps — `.claude/rules` "Web SPA testing")

- **`import { expect, it } from "vitest"`** in every test file using a jest-dom matcher (the bare
  global `expect` is jest-typed → a `tsc`-only failure invisible to a per-file vitest pass).
- **A required Mantine field's label gets an asterisk → `getByLabelText("Process")` exact FAILS** →
  query the required picker by its **placeholder** ("Pick the owning process"). The "New risk" open
  button and the modal submit both match `/New risk/` → use exact `{ name: "New risk" }` (or distinct
  labels) for the submit. The band `SegmentedControl` filter `aria-label` ("Filter by band") must be
  **distinct** from any in-drawer band control while both are mounted.
- **Pin every MSW fixture to the REAL serializer via `satisfies <Type>`** — copy the `_risk` shape
  (§1), the `summarize_register` + `published` shape (§2), the `_register_status` shape, and the
  `_capa_full` shape (§3.6) from the actual backend; never hand-type. The spawn fixture is a `Capa` with
  `source:"risk"`, `close_state:"Raised"`, the band-derived `severity`.
- **No persistently-mounted modal** — conditionally mount `{createOpen && <NewRiskModal …/>}` and
  `{editOpen && <EditRiskModal …/>}` so close discards the draft (the RaiseCapaModal idiom). The drawer
  is prop-driven (`riskId !== null`), not always mounted.
- **The global `scrollIntoView` / `matchMedia` / `ResizeObserver` stubs** in `test/setup.ts` already
  cover Mantine `Select`/`Combobox`; new `Select`s (severity-less here, but the process picker) rely on
  them.
- **Run the FULL `/check-web`** (eslint + strict `tsc --noEmit` [`noUncheckedIndexedAccess`] + build +
  the whole vitest suite) before the PR — a per-file vitest pass misses the `tsc`-only + cross-file
  traps. If the full parallel run flakes with "document is not defined", use `--pool=forks
  --poolOptions.forks.singleFork=true` for a clean signal.
- **MSW `onUnhandledRequest: "error"`** — every new endpoint the SPA hits (`/risks`, `/risks/summary`,
  `/risks/register`, `/risks/:id`, `/risks/:id/capa`) needs a default handler or the test errors.
- **Distinct accessible names** across the matrix cells, the scorecard chips, and the row badges (a
  repeated `aria-label` breaks `getByLabelText`); the matrix uses per-cell `<title>` + one `role="img"`
  summary, the scorecard uses StatusBadge `kind`-prefixed names.

## 6 · Slice plan

1. **S-risk-4a — `GET /risks/summary`** (BE; `feat/s-risk-4a`). The endpoint in `api/risk.py` (mounted
   before `/risks/{risk_id}`), the `published` wrapper, openapi `RiskRegisterSummary`, the 4 api
   integration tests (§2). `/check-api` + `/check-contracts`; **diff-critic + @codex** (no
   migration-reviewer — no migration). Squash-merge on green CI + threads resolved + owner go.
   `finish-slice` docs.
2. **S-risk-4b — the SPA** (FE; `feat/s-risk-4b`). Types + labels + matrix helper (+ unit test);
   `RisksRegisterPage` + `RiskScorecardBand` + `RiskMatrix` + `RiskDetailDrawer` + `NewRiskModal` +
   `EditRiskModal`; the Home PLAN line + `useHomeAsOf`; hooks/mutations; the route + LeftRail entry;
   component tests with MSW fixtures pinned to the real serializers. `/check-web`; **diff-critic +
   web-test-trap-reviewer + @codex**. Squash-merge on the same gate. `finish-slice` docs.
3. **`docs(s-risk-4)`** follow-up PR (the established family-doc convention) — the back-prop reconciles
   (doc 13 high-risk dashboard as-built; doc 15 `/risks/summary`; doc 16 register family FE shipped;
   doc 18 slice ledger) + slice-history + the capped CLAUDE.md learning + the memory resume note close.

## 7 · Rejected alternatives & named deferrals

- **Rejected — the page scorecard uses `GET /risks/summary`.** The page is the working view; its
  scorecard must agree with its own (per-process-filtered, live) table → client-side rollup. The summary
  endpoint is the governing controlled read for Home/MR/doc-13 (§3.3).
- **Rejected — per-process-filter `GET /risks/summary`.** Org-level register.read, consistent with the
  MR consumer (S-risk-2); the per-process-deny question is the already-named cross-cutting deferral.
- **Rejected — a `/risks/:id` detail page.** A `?risk=` drawer (the CapaDrawer/InitiativeDrawer
  precedent) keeps the register page as the single surface; matches the family's row-list shape.
- **Rejected — the matrix grades cells from the governing criteria fetched per-render.** v1 has one
  golden-pinned `scoring_method`; a FE-mirrored threshold table (unit-pinned) is the reference heatmap.
  Criteria-from-server is a v1.x residual (a new `scoring_method`).
- **Rejected — the steward publish/start-revision/release UI** (F-1) — the named register-steward
  role/UI deferral; rides SYSTEM overrides in v1.
- **Deferred (named, not faked):** the register-steward lifecycle UI (F-1); a per-risk **clause picker**
  in the New/Edit modal (`clause_id` accepted by the backend, not surfaced in v1 — keeps the modal lean,
  at parity with the create-in-process clause-step residual); the matrix criteria-from-server (new
  `scoring_method`); `subject.risk_rating` workflow routing (no resolver — backend deferral); the
  cross-cutting "MR/dashboard summaries honor per-process denies" decision (S-risk-2); a matrix
  click-to-filter interaction (heatmap is read-only in v1).

## 8 · Open for owner (non-blocking — flag in the 4b PR, don't block on)

- The exact **scorecard band set** (show `unscored` always vs only when `>0` — v1 never produces it;
  recommend omit when 0, like the objectives `unmeasured` is always shown — either is fine).
- The **Home Risk line copy** ("high / critical risks" vs "high-risk items") and whether it's a third
  `StatLine` in the PLAN card vs a standalone signal (recommend the third line; §3.7).
- The **matrix axis orientation** (Likelihood-X / Severity-Y as specced vs the transpose) — cosmetic;
  recommend Likelihood-X / Severity-Y (top-right = Critical).
