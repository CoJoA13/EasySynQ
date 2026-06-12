# S-home-1 — PDCA Home dashboard (QMS Health) — design

> Status: approved design (2026-06-11). The **finale of the v1 web track**: a calm four-quadrant
> PLAN/DO/CHECK/ACT wheel that replaces the `HomePage.tsx` placeholder, composing LIVE signals from
> the families that have now all landed (objectives ✅, acks ✅, audits/findings/CAPA/NCR ✅, drift ✅,
> compliance checklist ✅). **Front-end-only** — it rides already-shipped read endpoints, adds no
> migration / key / endpoint / contract. Mirrors the S-obj-2 and S-ack-2 front-end-only trailing-slice
> shape, but with a twist: it composes **several** existing serializers rather than one new backend
> surface. Implementation plan to follow under `docs/superpowers/plans/`.

## 1. Context & scope

**What the prior slices shipped (the read surfaces this composes):**
- **Quality Objectives** (S-obj-1/2): `GET /api/v1/objectives/scorecard` → `{total, on_target, by_rag{green,amber,red,unmeasured}, objectives[]}`, server-computed direction-aware RAG.
- **Acknowledgements** (S-ack-1/2): `useAckCount()` → the caller's open `DOC_ACK` task count (`GET /api/v1/tasks?assignee=me&state=PENDING&type=DOC_ACK`, self-scoped).
- **Audits/Findings/CAPA/NCR/Complaints** (S-aud-*/S-web-7*): flat list reads `GET /audits`, `GET /capas`, `GET /ncrs`, `GET /complaints`.
- **Compliance checklist** (S-web-6): `GET /api/v1/reports/compliance-checklist` → `{framework, rollup{total,covered,partial,gap,overdue_review}, rows[]}`.
- **Drift** (S-drift-2/3): `GET /api/v1/admin/drift/status` → mirror/blob scan health + `superseded_copies`.

**What THIS slice builds:** `apps/web/src/features/home/HomePage.tsx` is rewritten from its placeholder
into the **QMS Health wheel**: a header health-summary band + a 2×2 grid of PLAN/DO/CHECK/ACT region
cards (counts + RAG only) + a My-Tasks rail. Each tile reads its signals from the endpoints above,
rolls them up client-side (pure rules), and degrades independently and calmly when its read is
forbidden. Doc 11 §5.1 (the PDCA wheel mock + DP-2/DP-3) and doc 13 §5.1 (the per-quadrant signal map +
"persona: All, role-scoped; the default home") are the design source of truth; N9 (status-against-a-rule,
never an auto-compliance verdict) and N6 (no charts/sparklines) are the load-bearing constraints.

**It is front-end-only:**
- **No migration** (head stays `0049`).
- **No new permission key** — every signal rides an already-seeded read key; catalog stays **100**.
- **No new endpoint** — composes existing reads. One tiny new *web hook* (`useMyTasks`) over the
  already-existing `GET /tasks` self-scoped list; no API change.
- **No contract change** — `openapi.yaml` untouched (no new endpoint).
- **No `App.tsx` route change** — the index route (`<Route index element={<HomePage />} />`) already
  mounts `HomePage`; we replace its content.
- **No `LeftRail` change** — Home is unconditional (everyone lands here); gating lives on the *tiles*.

**Slice position:** this is the last planned slice of the v1 web track. After it lands, the v1 web track
is essentially feature-complete. Still parked beyond it (unchanged): owner-assignment (binds Employee
PROCESS-scope grants), the DCR UI, the admin audit-log screen, and the §9.3 Management-Review dashboard
(doc 13 §5.2 — most of its §9.3.2 input entities don't exist yet).

## 2. Non-goals / named deferrals (carry forward — name, don't fake)

- **"Next management review in N days"** (the doc-11 header chrome) — **omitted**: there is no
  `ManagementReview` entity in v1. The header shows only the honest, rule-based coverage summary.
- **"High risks open"** (the doc-11/13 PLAN signal) — **omitted**: the `risk_opportunity` register is not
  surfaced by any read endpoint in v1. PLAN ships objectives + overdue-reviews only.
- **"Documents pending approval" (org-wide)** (the doc-13 DO signal) — **omitted**: no org-wide
  document-approval count endpoint exists. Personal pending approvals already surface in the My-Tasks
  rail and `/tasks`. DO instead surfaces controlled-document *integrity* (drift) + the caller's acks.
- **Precise "open nonconformities" count** (CHECK) — **deferred** (owner decision 2026-06-11): there is
  no org-wide findings endpoint (findings are per-audit only), and the findings payload does not expose
  the linked CAPA's `close_state`, so a true open-NC count would need an N+1 `audits→findings` fan-out
  that *still* over-counts NCs whose CAPA is already Closed. CHECK ships audits-in-progress + mandatory
  coverage; open-NC lands when an org-wide findings rollup endpoint exists.
- **KPI trend charts / sparklines** — **deferred** (N6; the objectives measurement history is already a
  plain table). No chart-class widget on the wheel.
- **"Certified-ready" health verdict** — **deliberately NOT rendered** (N9): the doc-11 ASCII mock shows
  it, but that is precisely the auto-compliance judgment N9 forbids. Replaced by a coverage *status*
  ("18 / 20 mandatory items current") with the explicit microcopy "status against configured thresholds
  — not a compliance verdict."
- **Density control + Process Map header chrome** (doc-11 mock) — **out of scope**: there is no density
  system in v1, and the process-map surface is separate.
- **Org-configurable RAG thresholds** (doc 13 §4.3) — **v1.x**: this slice ships sensible *coded-default*
  thresholds in `rag.ts`; making them org-tunable + audited is a later backend slice. (N9 still holds —
  the rules are explicit and read at render, never an asserted verdict.)
- **The 6.2 ★ compliance-checklist node stays not-COVERED** — objective lifecycle/release is unwired
  (S-obj-2 deferral), so the CHECK coverage tile will honestly read e.g. 18/20, **not** papered over.
- **Redis-cached server-side aggregation** (doc 13 §4.4) — **not this slice**: pure front-end
  composition (owner decision). A server `qms-health` rollup endpoint is a possible future optimization.

## 3. Data contract — pinned to the real serializers

> ⚠ **The #1 false-PASS rule.** Every MSW fixture and TS type below is copied from the **as-built**
> `apps/api/.../api/*.py` serializer, not the mockup or a hand-typed guess. Use `satisfies <Type>` on
> every fixture so strict `tsc` enforces the shape. The dashboard adds **no** new endpoint — it pins the
> *existing* serializers it reads. Decimal-string-vs-number and the bare-array-vs-`{data}` envelope
> distinctions below are real and bite.

### 3.1 Endpoints (verbatim, the modules noted)

| # | Method + path | Gate (scope) | Envelope | Used for |
|---|---|---|---|---|
| 1 | `GET /api/v1/objectives/scorecard` (`api/objectives.py`) | `objective.read` (PROCESS, SYSTEM fallback) | object | PLAN objectives |
| 2 | `GET /api/v1/reports/compliance-checklist` (`api/reports.py`) | `report.compliance_checklist.read` (SYSTEM) | object | header coverage · PLAN overdue · CHECK coverage |
| 3 | `GET /api/v1/admin/drift/status` (`api/drift.py`) | `drift.read` (SYSTEM) | object | DO integrity + superseded copies |
| 4 | `GET /api/v1/audits` (`api/audits.py`) | `audit.read` (PROCESS) | `{data:[…]}` | CHECK audits in progress |
| 5 | `GET /api/v1/capas` (`api/capa.py`) | `capa.read` (PROCESS) | `{data:[…]}` | ACT CAPAs open |
| 6 | `GET /api/v1/ncrs` (`api/capa.py`) | `ncr.read` (PROCESS) | `{data:[…]}` | ACT NCRs awaiting disposition |
| 7 | `GET /api/v1/complaints` (`api/capa.py`) | `record.read` (ARTIFACT) | `{data:[…]}` | ACT complaints awaiting triage |
| 8 | `GET /api/v1/tasks?assignee=me&state=PENDING&type=DOC_ACK` (`api/workflow.py`) | `get_current_user` only (self-scoped) | **bare array** | DO acks awaiting (`useAckCount`) |
| 9 | `GET /api/v1/tasks?assignee=me&state=PENDING` (`api/workflow.py`) | `get_current_user` only (self-scoped) | **bare array** | My-Tasks rail (`useMyTasks`) |

**Notes that bite:**
- Endpoints **1–3** return a single **object**; **4–7** wrap in **`{data:[…]}`**; **8–9** return a
  **bare top-level array** (NOT `{data}`). MSW fixtures must match each exactly.
- `/objectives/scorecard` is declared **before** `/objectives/{id}` (str-convertor route order) — MSW
  handler order must register the static path before any `:id` path.
- `GET /tasks` LIST **omits `subject_type`/`subject_id`** (detail-only) — the My-Tasks rail therefore
  cannot show a document name without a per-task detail fetch; it shows task type + action + due instead.
- The CAPA **list** rows carry `raised_by: null` and **no `stages` key** (detail-only) — the dashboard
  needs neither, but the fixture must omit `stages`.
- A complaint has **no `created_at`** field and a nullable `severity`/`spawned_capa_id`.
- The compliance checklist live response has **no `projected_*` keys** (those exist only on the ingestion
  pre-commit projection path) — do not put them in the fixture.
- Drift scan `status` is **`CLEAN | DIVERGENT | FAILED`** (NOT "DETECTED"); `MIRROR_STALE`/`MIRROR_TAMPER`
  are keys inside the open `counts` JSONB bag, never top-level. `blob_coverage.failing` is the live D1
  alarm latch. `scans.MIRROR`/`scans.BLOB_REHASH` may be `null` before that scanner's first run (treat
  null as "not yet scanned", not alarm).
- All `severity` values are Title-case (`"Critical" | "Major" | "Minor"`); NCR `disposition` uses the
  literal token `"return"` (the Python member is `RETURN_`).

### 3.2 TypeScript types (lib/types.ts — **all already present**)

The dashboard reads **existing** types — no new `lib/types.ts` interface is required:
`ObjectiveScorecard` (`{total, on_target, by_rag{green,amber,red,unmeasured}, objectives: Objective[]}`),
`ComplianceChecklist` (`{framework, rollup{total,covered,partial,gap,overdue_review}, rows[]}`),
`DriftStatus` (`{scans{MIRROR,BLOB_REHASH}, blob_coverage{total,never_verified,failing,oldest_verified_at}, superseded_copies{versions,copies}}`),
`Audit[]` (`state: "Scheduled"|"Planned"|"InProgress"|"FindingsDraft"|"Reported"|"Closing"|"Closed"`),
`Capa[]` (`close_state: 8-value enum`), `Ncr[]` (`disposition: NcrDisposition | null`),
`Complaint[]` (`spawned_capa_id: string | null`), `Task[]` (`type: TaskType`, no `subject_*` on LIST rows).

The only new TS is in `features/home/`: small derived view-model types for the per-quadrant rollups
(`QuadrantStatus`, `Rag`) defined locally in `rag.ts`, not in `lib/types.ts`.

### 3.3 Computation rules (`features/home/rag.ts` — pure, read-not-recomputed, unit-tested)

Every RAG is a **pure function of already-server-computed signals**, read at render. N9: status against
a coded rule, never an asserted verdict; N6: no forecast/SPC. Coded-default thresholds (org-tunable is
v1.x). The objectives' own `by_rag` is **read verbatim** — never recomputed.

- `planObjectivesRag(by_rag)`: `red` if `red > 0`; else `amber` if `amber > 0`; else `green` if
  `green > 0`; else `unmeasured`/neutral (total 0 or all unmeasured).
- `coverageRag(rollup)`: `red` if `gap > 0`; else `amber` if `covered < total`; else `green`. (Drives
  the header summary **and** the CHECK quadrant.)
- `overdueRag(overdue_review)`: `amber` if `> 0`; else `green`.
- `driftRag(status)`: `red` if `blob_coverage.failing > 0` OR any `scans.*.status === "DIVERGENT"`;
  else `amber` if any `scans.*.status === "FAILED"` (infra error → attention); else `green` if both
  present and `CLEAN`; else neutral (a `null` scan = not yet scanned).
- `actRag({ncrsAwaiting, capasOpen, complaintsAwaiting})`: `red` if `ncrsAwaiting > 0`; else `amber` if
  `capasOpen > 0 || complaintsAwaiting > 0`; else `green`.
- Quadrant headline RAG = the "worst" of its visible signals' RAGs (red ≻ amber ≻ green ≻ neutral),
  computed over **only the non-forbidden** signals (a forbidden signal does not drag a tile to red).
  **RAG-driving vs informational:** only the rule-backed signals drive a tile's headline RAG — PLAN =
  `planObjectivesRag` + `overdueRag`, DO = `driftRag`, CHECK = `coverageRag`, ACT = `actRag`. The
  count-only signals (DO superseded-copies + acks, CHECK open-audits) are **informational/neutral** —
  they display a count but never drag the tile to amber/red (superseded copies in circulation are tracked
  awareness, not an alarm; acks are self-scoped). The header summary RAG = `coverageRag`.
- Counts: `openAudits = audits.filter(a => a.state !== "Closed").length`;
  `capasOpen = capas.filter(c => !["Closed","Rejected"].includes(c.close_state)).length`;
  `ncrsAwaiting = ncrs.filter(n => n.disposition === null).length`;
  `complaintsAwaiting = complaints.filter(c => c.spawned_capa_id === null).length`.

## 4. Authorization & gating

| Key | Gates | UI affordance |
|---|---|---|
| (none) | Home nav entry + the page itself + My-Tasks rail | Always rendered — everyone lands here |
| `objective.read` | PLAN objectives signal | Line shown if `can`/200; omitted+"scoped" on 403 |
| `report.compliance_checklist.read` | Header coverage · PLAN overdue · CHECK coverage | Same calm degrade |
| `drift.read` | DO integrity + superseded copies | Same; **demo holds this** → DO lights up |
| `audit.read` | CHECK audits-in-progress | Same |
| `capa.read` | ACT CAPAs open | Same |
| `ncr.read` | ACT NCRs awaiting disposition | Same |
| `record.read` | ACT complaints awaiting triage | Same |

- The **Home nav entry stays unconditional** — gating lives on the tiles, not the rail.
- Each read hook returns `{...query, forbidden}` (`ApiError.status === 403`, `retry: false`); a forbidden
  signal **omits its line** (or renders "scoped to your access"); a tile whose every signal is forbidden
  renders a calm "No access to this section" panel; the wheel never crashes.
- No write affordances on this surface (read-only dashboard) — nothing to hide/disable.
- **Live-smoke note:** the bare `demo` (System Administrator) login holds **only `drift.read`** among
  these — so out of the box only **DO + My-Tasks** populate. For a full-wheel live smoke, grant SYSTEM
  overrides on the **live login's `app_user` row** (org short_code **AHT**) for: `objective.read`,
  `report.compliance_checklist.read`, `audit.read`, `capa.read`, `ncr.read`, `record.read`. (Acks +
  tasks are self-scoped — no grant needed.)

## 5. UX design (owner-approved 2026-06-11)

Page wrapper `<Container size="lg" py="md">` + `<Stack gap="lg">` (the `/objectives` / `/compliance`
precedent). All status follows DP-7: **glyph + label + color** (never color alone). RAG → Mantine color
via `objectives/labels.ts` `RAG_COLOR` (`green→green, amber→yellow, red→red, unmeasured→gray`). PDCA
accents use the existing `--es-plan/-do/-check/-act` tokens (`theme/tokens.css`).

### 5.1 Header health summary — `HealthSummary`
`<Title order={1}>QMS health</Title>` (keeps the existing `HomePage.test` `/qms health/i` heading match)
+ a `<Paper withBorder>` band: the ★ mandatory-coverage status from `useComplianceChecklist().rollup`
("18 / 20 mandatory items current") with a `coverageRag` badge and the microcopy "status against
configured thresholds — not a compliance verdict" (N9). The band is the coverage drill-through →
`/compliance` (DP-3; this is where the ★ checklist signal opens, leaving each quadrant's single `[Open ▸]`
for its own lens). 403 → "Coverage scoped to your access" (calm, gray). Loading → a skeleton value.
Reuses the one `["compliance-checklist"]` query (also feeds PLAN/CHECK).

### 5.2 The 2×2 PDCA quadrants — `QuadrantCard` ×4 + `StatLine`
A `<SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">` of four `QuadrantCard`s (the doc-11 §5.1
"nav of four labeled regions" — a `role="group"`/`aria-label` per card, NOT a donut). Each card:
- A PDCA label chip (`PLAN · Cl 4–7` / `DO · Cl 7–8` / `CHECK · Cl 9` / `ACT · Cl 10`) in its accent hue
  + the quadrant headline RAG badge.
- 1–3 `StatLine`s (glyph + tabular count + label), each fed by one signal, each omitting itself on a
  forbidden/empty read.
- Exactly **one** accent action `[Open ▸]` (DP-2: one accent per region) → its drill route.

| Card | StatLines | Headline RAG | `[Open ▸]` |
|---|---|---|---|
| PLAN | `on_target / total` objectives on target (+ by_rag chips) · `overdue_review` reviews overdue | worst of objectives/overdue | `/objectives` |
| DO | mirror & blob integrity (clean / N issues) · `superseded_copies.copies` copies in circulation · `useAckCount` acks awaiting you | `driftRag` | `/drift` |
| CHECK | open audits (`state!=="Closed"`) · `covered / total` mandatory clauses covered | `coverageRag` | `/audits` |
| ACT | CAPAs open · NCRs awaiting disposition · complaints awaiting triage | `actRag` | `/capa` |

Drill-through uses react-router `<Link>`/`useNavigate` to the existing routes (no new routes). DP-3:
counts here → the pre-filtered surface one click deeper.

### 5.3 My-Tasks rail — `MyTasksRail`
A full-width `<Paper withBorder>` below the grid: `useMyTasks()` → `GET /tasks?assignee=me&state=PENDING`
→ count + top 3 by `due_at` (nulls last). Each row: a task-type glyph + `action_expected`/`stage_key` +
relative due ("· due in 2d"). ⚠ **No document name** (the LIST omits `subject_*`) — names live one click
deeper. "See all my tasks ▸" → `/tasks`. Always visible (self-scoped, no key). Empty → "You're all
caught up." (the calm zero-state).

### 5.4 Degradation / empty / first-run states
- **Per-signal forbidden:** omit the line / show "scoped to your access" inline; never a red error.
- **Whole-tile forbidden** (all signals 403): a calm gray "No access to this section's data" panel
  inside the card (the card frame + label stay).
- **Loading:** per-tile skeleton lines (progressive paint — DP-2 / doc 13 §4.4 skeleton→value).
- **Tile read error (non-403):** a calm "Couldn't load — try again" inside the card (not a page crash).
- **Empty / first-run:** zero counts render honestly ("0 CAPAs open", "You're all caught up"); a freshly
  set-up org simply shows greens/zeros. (No elaborate onboarding guidance-cards in v1 — a possible later
  enhancement, named not built.)

## 6. Component architecture / file layout

```
apps/web/src/features/home/
  HomePage.tsx          # T: page — Container/Stack; composes HealthSummary + 4 QuadrantCards + MyTasksRail
  hooks.ts              # T: useMyTasks() (GET /tasks?assignee=me&state=PENDING → Task[], retry:false, forbidden)
  rag.ts                # T: pure RAG/rollup rules (§3.3) + QuadrantStatus/Rag types
  HealthSummary.tsx     # T: header ★-coverage band (useComplianceChecklist)
  QuadrantCard.tsx      # T: one PDCA region card (label chip + headline RAG + StatLines + one Open action)
  StatLine.tsx          # T: glyph + tabular count + label (DP-7); omittable
  MyTasksRail.tsx       # T: the My-Tasks preview (useMyTasks)
  *.test.tsx / rag.test.ts  # co-located vitest + MSW + jest-axe
```

- **Reuses (cross-feature import, allowed):** `features/objectives/hooks.ts` (`useObjectiveScorecard`),
  `features/compliance/useComplianceChecklist.ts`, `features/drift/hooks.ts` (`useDriftStatus`),
  `features/audits/hooks.ts` (`useAudits`), `features/capa/hooks.ts` (`useCapas/useNcrs/useComplaints`),
  `app/shell/useAckCount.ts`, `features/objectives/labels.ts` (`RAG_COLOR`/`RAG_LABEL`).
- **Routing:** none — the `/` index route already mounts `HomePage` (`App.tsx`).
- **Nav:** none — Home is unconditional (`LeftRail`).
- **Types:** none added to `lib/types.ts` (reuses existing; local view-model types live in `rag.ts`).
- **No new `lib/api.ts` surface** — `useApi()` covers `useMyTasks`.

## 7. Data flow & react-query keys

| Surface | Read | Key |
|---|---|---|
| Header coverage / PLAN overdue / CHECK coverage | `useComplianceChecklist()` (fetched **once**, cache-shared) | `["compliance-checklist"]` |
| PLAN objectives | `useObjectiveScorecard()` | `["objectives-scorecard", null]` |
| DO integrity/copies | `useDriftStatus()` | `["drift-status"]` |
| DO acks | `useAckCount()` | `["ack-count"]` |
| CHECK audits | `useAudits()` | `["audits"]` |
| ACT capas/ncrs/complaints | `useCapas()` / `useNcrs()` / `useComplaints()` | `["capas"]` / `["ncrs"]` / `["complaints"]` |
| My-Tasks rail | `useMyTasks()` | `["my-tasks"]` |

Read-only dashboard — **no mutations, no invalidation, no optimistic updates**. All reads `retry:false`
(calm-403). React-query dedups the shared `["compliance-checklist"]` query across header + PLAN + CHECK.

## 8. Error handling

- **403 →** the hook's `forbidden` flag (`ApiError.status === 403`, `retry:false`) → the per-signal/
  per-tile calm degrade (§5.4). Because the global test `QueryClient` hardcodes `retry:false`, prove the
  *production* `retry:false` with a production-defaults `QueryClient` in the hook test (the S-web-8 trap).
- **404 →** not expected on these list/rollup reads (no `:id`); treated as a generic tile error.
- **Non-403 error →** calm "Couldn't load — try again" inside the tile; never a page-level crash.
- **Empty data →** honest zero-states (§5.4), not an error.
- No write paths → no 422 mapping needed.

## 9. Testing strategy

vitest + MSW v2 + jest-axe; `renderWithProviders` + `TEST_AUTH`; per-test `server.use(...)` overrides.
- **Fixtures `satisfies`-pinned to §3.1** — copied from the real serializers; MSW handler order registers
  `/objectives/scorecard` before any `/objectives/:id`; the `/tasks` handlers return **bare arrays**.
- **Every component test `import { expect, it } from "vitest"`** (the jest-dom × vitest `expect` trap —
  only `tsc`/`/check-web` catches it, not per-file vitest).
- **`rag.ts` unit tests** — each pure rule across green/amber/red/neutral/empty inputs.
- **Coverage:** HealthSummary (happy/403/loading); each QuadrantCard (happy/empty/per-signal-403/
  whole-tile-403/error); StatLine (glyph+label+aria, omit); MyTasksRail (happy/empty/top-3 ordering);
  `useMyTasks` (happy + `forbidden`-on-403 with a production-defaults QueryClient); HomePage (renders the
  `/qms health/i` heading, the four labeled regions, axe-clean, and degrades to DO+rail-only when the
  content reads 403 — the demo-login shape).
- **Strict gate:** `noUncheckedIndexedAccess` + full `/check-web` (eslint + strict `tsc --noEmit` +
  build + the whole vitest suite). Track the web-test delta from the **627** baseline.

## 10. Build sequence (subagent-driven TDD; mirrors the S-obj-2 template)

Each task is the 5-step TDD cell: *write failing test → run (expect FAIL) → implement → run (expect PASS)
→ commit* (`feat(s-home-1): …`), with full test + implementation code inlined in the plan.

- **Phase 1 — Foundation:** `rag.ts` (pure rules + types) with `rag.test.ts`; MSW fixtures + handlers for
  all 9 reads (`satisfies`-pinned, scorecard-before-`:id`, `/tasks` bare arrays); `useMyTasks` hook +
  test. End with `tsc --noEmit` (the only shape gate) + baseline count capture.
- **Phase 2 — Presentational:** `StatLine`, `QuadrantCard`, `HealthSummary` (read-only, glyph+label+aria,
  per-signal omit), each with happy/empty/forbidden tests.
- **Phase 3 — Composition:** `HomePage` wires the existing hooks → the four quadrants + header; per-tile
  degrade tests; the demo-login (DO+rail-only) shape test.
- **Phase 4 — My-Tasks rail:** `MyTasksRail` + wire into `HomePage`; ordering/empty/self-scoped tests.
- **Phase 5 — Wire + close:** full `/check-web` → `diff-critic` on the branch diff → Chrome-MCP live
  smoke (SYSTEM overrides on the live demo `app_user` row, org AHT; verify DO+rail with bare demo, then
  the full wheel with overrides) → `slice-history.md` entry + a `CLAUDE.md` Recent-learnings line +
  web-test delta → PR → Codex triage (disregard multi-tenant nitpicks — moot under D1; **fix** any real
  error-state bug) → squash-merge on green CI + owner OK.

## 11. Verification & closeout

- **Web loop:** `/check-web` (eslint + strict `tsc --noEmit` + build + full vitest) green.
- **diff-critic** agent on the branch diff before the PR (the false-PASS hunt — fixture shapes, the
  forbidden-degrade paths, the RAG rules).
- **Contracts:** confirm `packages/contracts/openapi.yaml` is **unchanged** (no new endpoint) — the
  `contracts` CI job stays green with no diff.
- **Live smoke** via Chrome MCP (find-then-click in separate batches, text-first verification, client-
  side nav only): the bare-demo DO+rail shape, then the full wheel after the SYSTEM overrides; each
  `[Open ▸]` lands on its existing route.
- **Migration/api/integration CI** unaffected (no backend change) — green by construction.
